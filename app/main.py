"""Jukebox — interface mobile pour LMS (Lyrion) + plugin Qobuz.

Un petit proxy FastAPI entre le navigateur et l'API JSON-RPC de LMS :
- normalise les menus du plugin Qobuz en objets simples (album, piste, dossier)
- expose l'état du lecteur en SSE (Server-Sent Events)
- protège l'accès par un code invité (PIN) optionnel
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import hmac
import json
import os
import re
import secrets
import time
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from . import qobuz
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
STATIC = BASE / "static"
DATA = Path(os.environ.get("DATA_DIR", BASE / "data"))
DATA.mkdir(parents=True, exist_ok=True)

LMS_URL = os.environ.get("LMS_URL", "http://host.docker.internal:9000").rstrip("/")
PLAYER = os.environ.get("PLAYER_ID", "").strip()
GUEST_PIN = os.environ.get("GUEST_PIN", "").strip()
MAX_VOLUME = max(1, min(100, int(os.environ.get("MAX_VOLUME", "100"))))
APP_NAME = os.environ.get("APP_NAME", "Jukebox")
# Contrôleur KEF (réveil de l'enceinte avant lecture). Vide = désactivé.
KEF_URL = os.environ.get("KEF_CONTROL_URL", "").rstrip("/")
KEF_WAKE_WAIT = float(os.environ.get("KEF_WAKE_WAIT", "14"))
# Contrôle UPnP de la KEF, pour savoir quelle source elle joue réellement
KEF_UPNP_URL = os.environ.get("KEF_UPNP_URL", "").strip()
COOKIE = "jukebox_auth"
QOBUZ_CDN = "https://static.qobuz.com/"


def _secret() -> bytes:
    env = os.environ.get("SECRET_KEY", "").strip()
    if env:
        return env.encode()
    f = DATA / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip().encode()


SECRET = _secret()


def _token() -> str:
    return hmac.new(SECRET, f"guest:{GUEST_PIN}".encode(), hashlib.sha256).hexdigest()[:40]


def is_authed(req: Request) -> bool:
    if not GUEST_PIN:
        return True
    return hmac.compare_digest(req.cookies.get(COOKIE, ""), _token())


def auth(req: Request) -> None:
    if not is_authed(req):
        raise HTTPException(401, "Code invité requis")


def client_ip(req: Request) -> str:
    return req.headers.get("cf-connecting-ip") or (req.client.host if req.client else "?")


# ---------------------------------------------------------------- LMS client
client: httpx.AsyncClient


async def lms(cmd: list[Any], player: str | None = None) -> dict:
    payload = {
        "id": 1,
        "method": "slim.request",
        "params": [PLAYER if player is None else player, [str(c) for c in cmd]],
    }
    try:
        r = await client.post(f"{LMS_URL}/jsonrpc.js", json=payload)
        r.raise_for_status()
        return r.json().get("result") or {}
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LMS injoignable : {e.__class__.__name__}")


async def ensure_player() -> None:
    """Si PLAYER_ID n'est pas fourni, prend le premier lecteur connu de LMS."""
    global PLAYER
    if PLAYER:
        return
    r = await lms(["players", 0, 5], player="")
    for p in r.get("players_loop", []):
        PLAYER = p["playerid"]
        return


# ------------------------------------------------------------ normalisation
def image_url(icon: str | None, size: int = 300) -> str | None:
    if not icon:
        return None
    icon = icon.strip()
    if icon.startswith("/imageproxy/") or icon.startswith("imageproxy/"):
        inner = icon.split("imageproxy/", 1)[1].rsplit("/image", 1)[0]
        icon = urllib.parse.unquote(inner)
    if icon.startswith(QOBUZ_CDN):
        return re.sub(r"_(50|160|300|600|max)\.jpg$", f"_{size}.jpg", icon)
    if icon.startswith("http://") or icon.startswith("https://"):
        return icon
    if not icon.startswith("/"):
        icon = "/" + icon
    return "/img?u=" + urllib.parse.quote(icon, safe="/")


def item_id_of(it: dict) -> str | None:
    params = it.get("params") or ((it.get("actions") or {}).get("go") or {}).get("params") or {}
    return params.get("item_id")


_HIRES = re.compile(r"\s*\(Hi-Res\)\s*$")
_YEAR = re.compile(r"\s*\((\d{4})\)$")


def clean_title(title: str) -> tuple[str, bool]:
    """Le plugin Qobuz (labelHiResAlbums) suffixe « (Hi-Res) » : on en fait un badge."""
    m = _HIRES.search(title)
    title = title[: m.start()] if m else title
    return re.sub(r"\s{2,}", " ", title).strip(), bool(m)


def norm(it: dict) -> dict | None:
    go = (it.get("actions") or {}).get("go") or {}
    if go.get("nextWindow"):  # actions ("ajouter aux favoris"...), pas de navigation
        return None
    if it.get("type") == "search":
        return None
    text = it.get("text") or it.get("name") or ""
    lines = [l.strip() for l in text.split("\n")]
    title, hires = clean_title(lines[0])
    # showYearWithAlbum : « Daft Punk (2013) » → « Daft Punk · 2013 »
    subtitle = " · ".join(_YEAR.sub(r" · \1", l) for l in lines[1:] if l)
    item_id = item_id_of(it)
    preset = it.get("presetParams") or {}
    icon = it.get("icon") or it.get("image") or preset.get("icon")
    # Les albums atteints via Artiste > Releases renvoient leurs pistes avec
    # goAction "playControl" (+ position dans playControlParams) au lieu de
    # "play"/itemplay. Sans ce cas, elles passaient pour des dossiers.
    play_ctl = it.get("playControlParams") or {}
    pos_raw = play_ctl.get("xmlbrowserPlayControl")
    if (
        it.get("goAction") in ("play", "playControl")
        or it.get("style") == "itemplay"
        or it.get("type") == "audio"
        or pos_raw is not None
    ):
        kind = "track"
    elif it.get("type") == "playlist":
        kind = "collection"
    elif it.get("type") in ("text", "textarea") or (not item_id and text):
        kind = "text"
    else:
        kind = "folder"
    if kind != "text" and not item_id:
        return None
    if kind == "text" and not title:
        return None
    return {
        "id": item_id,
        "kind": kind,
        "title": title,
        "subtitle": subtitle,
        "hires": hires,
        "image": image_url(icon, 300),
        "image_large": image_url(icon, 600),
        "url": preset.get("favorites_url"),
        "pos": int(pos_raw) if str(pos_raw).isdigit() else None,
    }


_cache: dict[str, tuple[float, Any]] = {}
_seen_tracks: dict[str, dict] = {}   # url qobuz://… -> {title, artist, album, image} vus en navigation


def remember_track(n: dict) -> None:
    if n.get("kind") != "track" or not n.get("url"):
        return
    if len(_seen_tracks) > 5000:
        for k in list(_seen_tracks)[:1000]:
            _seen_tracks.pop(k, None)
    artist, _, album = (n.get("subtitle") or "").partition(" - ")
    _seen_tracks[n["url"]] = {"title": n["title"], "artist": artist.strip(), "album": album.strip(), "image": n.get("image_large")}


def remember_qobuz_track(n: dict) -> None:
    """Mémoire des pistes pour les éléments venus de l'API Qobuz (qz:…)."""
    if n.get("kind") == "track" and n.get("url") and n.get("title"):
        _seen_tracks[n["url"]] = {"title": n["title"], "artist": n.get("artist", ""),
                                  "album": n.get("album", ""), "image": n.get("image_large")}


def cache_get(key: str, ttl: float):
    v = _cache.get(key)
    if v and time.time() - v[0] < ttl:
        return v[1]
    return None


def cache_put(key: str, value: Any) -> Any:
    if len(_cache) > 400:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
            _cache.pop(k, None)
    _cache[key] = (time.time(), value)
    return value


async def browse_items(item_id: str | None, start: int = 0, count: int = 50, search: str | None = None) -> dict:
    key = f"browse:{item_id}:{start}:{count}:{search}"
    hit = cache_get(key, 90)
    if hit:
        return hit
    cmd: list[Any] = ["qobuz", "items", start, count, "menu:qobuz"]
    if item_id:
        cmd.append(f"item_id:{item_id}")
    if search:
        cmd.append(f"search:{search}")
    r = await lms(cmd)
    items = [n for n in (norm(it) for it in r.get("item_loop", [])) if n]
    for n in items:
        remember_track(n)
        # Albums et playlists LMS → pages Qobuz directes (plus rapides, avec
        # présentation, qualité exacte et lien vers l'artiste)
        if n["kind"] == "collection" and qobuz.available():
            qz = qobuz.qobuz_id_from_image(n.get("image_large") or n.get("image"))
            if qz:
                n["id"] = qz
    title = [l.strip() for l in (r.get("title") or "").split("\n")]
    title[0], hires = clean_title(title[0])
    image = next((i["image_large"] for i in items if i["kind"] == "track" and i["image_large"]), None)
    return cache_put(key, {
        "id": item_id,
        "title": title[0],
        "subtitle": " · ".join(_YEAR.sub(r" · \1", l) for l in title[1:] if l).strip(" ·"),
        "hires": hires,
        "count": int(r.get("count") or len(items)),
        "offset": int(r.get("offset") or start),
        "items": items,
        "style": (r.get("window") or {}).get("windowStyle"),
        "image": image,
    })


_root: dict[str, str] = {}
ROOT_MATCH = [
    ("search", ("search",)), ("purchases", ("purchase",)), ("favorites", ("favorite", "favourite")),
    ("myplaylists", ("my playlists",)), ("qobuzplaylists", ("qobuz playlists",)),
    ("bestsellers", ("bestseller",)), ("new", ("new releases",)), ("press", ("press",)),
    ("selection", ("selection",)), ("genres", ("genre",)),
]


async def root_ids() -> dict[str, str]:
    if _root:
        return _root
    r = await lms(["qobuz", "items", 0, 50, "menu:qobuz"])
    for it in r.get("item_loop", []):
        t = (it.get("text") or "").lower()
        i = item_id_of(it)
        if not i:
            continue
        for key, needles in ROOT_MATCH:
            if key not in _root and any(n in t for n in needles):
                _root[key] = i
                break
    return _root


# ------------------------------------------------------------------ status
def track_image(t: dict) -> str | None:
    if t.get("artwork_url"):
        return image_url(t["artwork_url"], 600)
    cid = t.get("artwork_track_id") or t.get("coverid")
    if cid:
        return f"/img?u=/music/{cid}/cover.jpg"
    return None


COMBINED_TITLE = re.compile(r"^(?P<title>.+?) by (?P<artist>.+?) from (?P<album>.+)$")


def queue_entry(t: dict, index: int) -> dict:
    """Entrée de file normalisée ; complète ce que LMS ne fournit pas pour les flux Qobuz."""
    e = {
        "index": int(t.get("playlist index", index)),
        "id": t.get("id"),
        "title": t.get("title") or t.get("remote_title") or "Chargement…",
        "artist": t.get("artist") or t.get("trackartist") or "",
        "album": t.get("album") or "",
        "duration": float(t.get("duration") or 0),
        "image": track_image(t) if t.get("artwork_url") else None,
        "url": t.get("url"),
        "auto": (t.get("url") or "") in _queue_default,  # « par défaut » (suite d'un lancement)
    }
    seen = _seen_tracks.get(e["url"] or "")
    if not e["artist"]:
        m = COMBINED_TITLE.match(e["title"])
        if seen:
            e["title"], e["artist"], e["album"] = seen["title"], seen["artist"], seen["album"] or e["album"]
        elif m:  # « Titre by Artiste from Album » (libellé XMLBrowser du plugin)
            e["title"], e["artist"], e["album"] = m["title"], m["artist"], m["album"]
    if not e["image"]:
        e["image"] = (seen or {}).get("image") or track_image(t)
    return e


async def get_status() -> dict:
    # start=0 : la file est renvoyée dans l'ordre réel (0..N). Avec "-", LMS la
    # renvoie à partir de la piste courante, ce qui décalait la piste affichée.
    r = await lms(["status", 0, 1000, "tags:aAlKdcNuxyJ"])
    queue = [queue_entry(t, i) for i, t in enumerate(r.get("playlist_loop", []))]
    idx = int(r.get("playlist_cur_index") or 0)
    by_index = {q["index"]: q for q in queue}
    vol = int(r.get("mixer volume") or 0)
    return {
        "player": r.get("player_name"),
        "connected": int(r.get("player_connected") or 0),
        "power": int(r.get("power") or 0),
        "mode": r.get("mode") or "stop",
        "volume": abs(vol),
        "muted": vol < 0,
        "time": float(r.get("time") or 0),
        "duration": float(r.get("duration") or 0),
        "index": idx,
        "count": int(r.get("playlist_tracks") or 0),
        "repeat": int(r.get("playlist repeat") or 0),
        "shuffle": int(r.get("playlist shuffle") or 0),
        "current": by_index.get(idx),
        "queue": queue,
    }


# Anti-blocage : la KEF LSX ne signale pas la fin d'un flux au pont UPnP. LMS
# reste alors en "play" avec la position figée sur la fin de la piste et
# n'enchaîne jamais. Seul un saut explicite au titre suivant débloque (ni play,
# ni pause/play ne suffisent). Le veilleur ci-dessous le fait automatiquement.
STALL_NEAR_END = 4.0    # s figées en fin de piste avant de forcer
STALL_ANYWHERE = 25.0   # filet de sécurité si la durée est inconnue
STALL_COOLDOWN = 20.0   # ne pas enchaîner les relances

# Démarrage raté : après un saut ou un changement de piste, la KEF envoie
# parfois en retard le STOPPED de l'ancien flux ; le pont croit alors que le
# nouveau s'est arrêté (« stop on short track ») et n'envoie rien. LMS reste
# en lecture, position figée sur la cible (0:00 ou l'endroit du saut). On
# renvoie la même demande plutôt que de sauter une piste.
STALL_START = 4.0       # s figées en milieu de piste avant de relancer (si la KEF est arrêtée)
STALL_START_MAX = 15.0  # si la KEF dit démarrer (TRANSITIONING), on patiente jusque-là
START_RETRIES = 2       # relances par blocage, ensuite le filet habituel
INTENT_TTL = 20.0       # durée pendant laquelle une demande de Jukebox fait foi
OTHER_CHECK_EVERY = 3.0 # s entre deux vérifications de la source de la KEF


log = logging.getLogger("jukebox.veilleur")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # sinon une ligne par requête LMS (1/s en lecture)

_GET_MEDIA_INFO = (
    '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
    '<u:GetMediaInfo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID></u:GetMediaInfo></s:Body></s:Envelope>"
)


async def lms_owns_speaker(quiet: bool = False) -> bool:
    """La KEF joue-t-elle un flux de LMS ?

    Quand Qobuz Connect (ou une autre app UPnP) prend l'enceinte, LMS ne le
    voit pas : il reste « en lecture », position figée. Sans cette vérification
    le veilleur prendrait ça pour un blocage et reprendrait l'enceinte. Les flux
    du pont UPnP de LMS ont la forme http://<serveur>:<port>/bridge-N.<ext>.
    En cas de doute (enceinte muette, URI vide pendant une transition), on
    s'abstient : dans nos blocages, le pont a toujours déjà posé son URI.
    """
    if not KEF_UPNP_URL:
        return True
    try:
        r = await client.post(
            KEF_UPNP_URL,
            content=_GET_MEDIA_INFO,
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPACTION": '"urn:schemas-upnp-org:service:AVTransport:1#GetMediaInfo"',
            },
            timeout=3,
        )
    except httpx.HTTPError as e:
        if not quiet:
            log.info("KEF muette (%s) : abstention", e.__class__.__name__)
        return False
    m = re.search(r"<CurrentURI>([^<]*)</CurrentURI>", r.text)
    uri = m.group(1).strip() if m else ""
    if "/bridge-" in uri:
        return True
    if not quiet:
        log.info("la KEF joue une autre source (%s) : abstention", uri or "URI vide")
    return False


async def kef_transport_state() -> str | None:
    """État UPnP réel de la KEF : PLAYING, TRANSITIONING, STOPPED… (None si muette).

    Distingue un démarrage lent (TRANSITIONING : elle télécharge, il faut
    attendre) du vrai blocage (STOPPED alors que LMS croit jouer)."""
    if not KEF_UPNP_URL:
        return None
    try:
        r = await client.post(
            KEF_UPNP_URL,
            content=_GET_MEDIA_INFO.replace("GetMediaInfo", "GetTransportInfo"),
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPACTION": '"urn:schemas-upnp-org:service:AVTransport:1#GetTransportInfo"',
            },
            timeout=3,
        )
    except httpx.HTTPError:
        return None
    m = re.search(r"<CurrentTransportState>([^<]*)</CurrentTransportState>", r.text)
    return m.group(1).strip() if m else None


class Poller:
    def __init__(self) -> None:
        self.latest: dict | None = None
        self.ts = 0.0
        self.clients = 0
        self.error: str | None = None
        self.wake = asyncio.Event()
        self._stall_key: tuple | None = None
        self._stall_since = 0.0
        self._last_nudge = 0.0
        self._retries = 0
        self._waiting_logged = False
        # Qobuz Connect (ou autre) joue sur la KEF alors que LMS se croit en
        # lecture : on l'annonce comme mode "other", position gelée à l'endroit
        # où LMS jouait vraiment, pour pouvoir y reprendre.
        self._other = False
        self._other_checked = 0.0
        self._last_real_pos = 0.0
        self.resume_pos = 0.0
        # dernière demande de Jukebox qui relance un flux : (instant, index ou
        # None pour « la piste courante », position visée)
        self._intent: tuple[float, int | None, float] | None = None

    def note(self, index: int | None = None, pos: float = 0.0) -> None:
        self._intent = (time.time(), index, pos)
        self._retries = 0

    async def refresh(self) -> dict:
        st = await get_status()
        await self._track_other_source(st)
        self.latest = st
        self.ts = time.time()
        self.error = None
        try:
            await self._unstick(self.latest)
        except Exception:  # noqa: BLE001 — le veilleur ne doit jamais casser l'état
            pass
        return self.latest

    async def _track_other_source(self, st: dict) -> None:
        now = time.time()
        if st.get("mode") != "play":
            self._other = False
            return
        if now - self._other_checked >= OTHER_CHECK_EVERY or self._other:
            if now - self._other_checked >= OTHER_CHECK_EVERY:
                self._other_checked = now
                owned = await lms_owns_speaker(quiet=True)
                if not owned and not self._other:
                    self.resume_pos = self._last_real_pos
                    log.info("autre source sur la KEF : Jukebox passe en « autre source » (reprise à %.0f s)",
                             self.resume_pos)
                elif owned and self._other:
                    log.info("la KEF rejoue le flux de LMS")
                self._other = not owned
        if self._other:
            st["mode"] = "other"
            st["time"] = self.resume_pos
        else:
            self._last_real_pos = float(st.get("time") or 0)

    async def _unstick(self, st: dict) -> None:
        now = time.time()
        if st.get("mode") != "play":
            self._stall_key = None
            return
        key = (st.get("index"), round(float(st.get("time") or 0), 1))
        if key != self._stall_key:
            if self._stall_key and (key[0] != self._stall_key[0] or key[1] > self._stall_key[1]):
                self._retries = 0  # autre piste, ou la lecture avance : blocage terminé
                self._waiting_logged = False
            self._stall_key, self._stall_since = key, now
            return
        frozen = now - self._stall_since
        dur = float(st.get("duration") or 0)
        pos = float(st.get("time") or 0)
        idx = int(st.get("index") or 0)

        near_end = dur > 0 and pos >= dur - 10
        unplayable = False
        if not near_end and frozen >= STALL_START:
            if not await lms_owns_speaker():
                self._stall_key = None  # une autre source joue : ne pas la lui reprendre
                return
            state = await kef_transport_state()
            if state in ("TRANSITIONING", "PLAYING") and frozen < STALL_START_MAX:
                # Elle démarre (téléchargement en cours) : relancer interromprait
                # un démarrage qui aurait abouti. On patiente.
                if not self._waiting_logged:
                    log.info("figé %.0f s à %.1f s (piste %d) mais la KEF est en %s : on patiente",
                             frozen, pos, idx + 1, state)
                    self._waiting_logged = True
                return
            if self._retries >= START_RETRIES:
                unplayable = pos <= 0.5  # toujours rien après les relances : illisible
        if not near_end and frozen >= STALL_START and self._retries < START_RETRIES:
            self._retries += 1
            self._stall_key = None
            self._waiting_logged = False
            # LMS affiche en général la position visée, figée : elle fait foi.
            # S'il affiche 0:00 après un saut demandé depuis Jukebox, on reprend
            # la cible mémorisée (jamais une demande plus ancienne qu'INTENT_TTL).
            target = pos
            intent = self._intent
            if pos <= 0.5 and intent and now - intent[0] < INTENT_TTL and intent[1] in (None, idx):
                target = intent[2]
            log.info("figé %.0f s à %.1f s (piste %d), KEF %s : relance %d/%d vers %.1f s",
                     frozen, pos, idx + 1, state or "muette", self._retries, START_RETRIES, target)
            if target > 0.5:
                await lms(["time", target])
            else:
                await lms(["playlist", "index", idx])
            return
        # Bloqué à 0:00 malgré les relances, KEF arrêtée : morceau illisible
        # (fichier abîmé chez Qobuz/Akamai…) — inutile d'attendre STALL_ANYWHERE.
        if not ((near_end and frozen >= STALL_NEAR_END) or frozen >= STALL_ANYWHERE or unplayable):
            return
        if now - self._last_nudge < STALL_COOLDOWN:
            return
        if not await lms_owns_speaker():
            self._stall_key = None
            return
        self._last_nudge = now
        self._stall_key = None
        log.info("figé %.0f s à %.1f s (piste %d)%s : passage forcé à la suivante", frozen, pos, idx + 1,
                 " — illisible après relances" if unplayable else "")
        count = int(st.get("count") or 0)
        if idx + 1 < count or int(st.get("repeat") or 0):
            await lms(["playlist", "index", "+1"])
        else:
            await lms(["stop"])  # fin de file

    async def run(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception as e:  # noqa: BLE001
                self.error = getattr(e, "detail", None) or str(e)
            if self.clients > 0:
                await asyncio.sleep(1)
            else:
                # personne devant l'app : on surveille quand même la lecture,
                # sinon un blocage en fin de piste ne serait jamais rattrapé
                self.wake.clear()
                playing = (self.latest or {}).get("mode") == "play"
                # en lecture, sonder chaque seconde pour relancer vite un blocage
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=1 if playing else 30)
                except asyncio.TimeoutError:
                    pass

    async def fresh(self, max_age: float = 1.2) -> dict:
        if self.latest and time.time() - self.ts < max_age:
            return self.latest
        return await self.refresh()


poller = Poller()


# --------------------------------------------------------------------- app
@asynccontextmanager
async def lifespan(_: FastAPI):
    global client
    client = httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=5.0), follow_redirects=True)
    try:
        await ensure_player()
    except Exception:  # noqa: BLE001 — LMS peut être down au démarrage
        pass
    task = asyncio.create_task(poller.run())
    yield
    task.cancel()
    await client.aclose()


app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_SHELL_EXT = ("js", "css", "html", "webmanifest")


@app.middleware("http")
async def revalidate_shell(request: Request, call_next):
    """Coquille de l'app (JS/CSS/HTML) toujours revalidée : une nouvelle version
    déployée est prise en compte au rechargement, sans cache navigateur figé."""
    resp = await call_next(request)
    p = request.url.path
    if p == "/sw.js" or (p.startswith("/static/") and p.rsplit(".", 1)[-1] in _SHELL_EXT):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/")
async def index(req: Request, pin: str | None = None):
    if pin is not None and GUEST_PIN and hmac.compare_digest(pin.strip(), GUEST_PIN):
        resp = RedirectResponse("/", status_code=303)
        set_auth_cookie(req, resp)
        return resp
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
async def sw():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


def set_auth_cookie(req: Request, resp: Response) -> None:
    secure = req.url.scheme == "https" or req.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(COOKIE, _token(), max_age=180 * 86400, httponly=True, samesite="lax", secure=secure)


_attempts: dict[str, list[float]] = {}


@app.post("/api/auth")
async def api_auth(req: Request):
    if not GUEST_PIN:
        return {"ok": True}
    ip = client_ip(req)
    now = time.time()
    tries = [t for t in _attempts.get(ip, []) if now - t < 600]
    if len(tries) >= 8:
        raise HTTPException(429, "Trop d'essais, réessayez dans quelques minutes")
    body = await req.json()
    pin = str(body.get("pin", "")).strip()
    if not hmac.compare_digest(pin, GUEST_PIN):
        tries.append(now)
        _attempts[ip] = tries
        raise HTTPException(403, "Code incorrect")
    _attempts.pop(ip, None)
    resp = JSONResponse({"ok": True})
    set_auth_cookie(req, resp)
    return resp


@app.get("/api/me")
async def api_me(req: Request):
    return {
        "app": APP_NAME,
        "pin_required": bool(GUEST_PIN),
        "authenticated": is_authed(req),
        "max_volume": MAX_VOLUME,
        "player": PLAYER,
    }


HOME_SECTIONS = [("new", "Nouveautés"), ("selection", "Sélection Qobuz"), ("press", "Dans la presse"),
                 ("bestsellers", "Meilleures ventes"), ("favorites.1", "Albums favoris de la maison")]


def resolve(root: dict[str, str], key: str) -> str | None:
    base, _, rest = key.partition(".")
    if base not in root:
        return None
    return root[base] + ("." + rest if rest else "")


@app.get("/api/home")
async def api_home(req: Request):
    auth(req)
    hit = cache_get("home", 600)
    if hit:
        return hit
    root = await root_ids()
    wanted = [(resolve(root, k), label) for k, label in HOME_SECTIONS]
    wanted = [(i, l) for i, l in wanted if i]
    results = await asyncio.gather(*(browse_items(i, 0, 12) for i, _ in wanted), return_exceptions=True)
    sections = []
    for (i, label), r in zip(wanted, results):
        if isinstance(r, Exception) or not r["items"]:
            continue
        sections.append({"id": i, "title": label, "count": r["count"], "items": r["items"]})
    return cache_put("home", {"sections": sections})


@app.get("/api/library")
async def api_library(req: Request):
    auth(req)
    root = await root_ids()
    entries = [
        ("favorites.1", "Albums favoris", "Les albums aimés sur le compte Qobuz de la maison", "heart"),
        ("favorites.0", "Artistes favoris", "", "mic"),
        ("favorites.2", "Titres favoris", "", "note"),
        ("myplaylists", "Playlists de la maison", "", "list"),
        ("qobuzplaylists", "Playlists Qobuz", "Sélections éditoriales par thème", "sparkles"),
        ("genres", "Genres", "Nouveautés et meilleures ventes par genre", "grid"),
        ("bestsellers", "Meilleures ventes", "", "chart"),
        ("press", "Dans la presse", "", "news"),
        ("purchases", "Achats", "", "bag"),
    ]
    out = []
    for key, title, sub, icon in entries:
        i = resolve(root, key)
        if i:
            out.append({"id": i, "title": title, "subtitle": sub, "icon": icon})
    return {"entries": out}


@app.get("/api/browse")
async def api_browse(req: Request, id: str = "", start: int = 0, count: int = 100, search: str | None = None):
    auth(req)
    count = max(1, min(200, count))
    if id.startswith("qz:"):
        key = f"qz:{id}:{start}:{count}"
        hit = cache_get(key, 300)
        if hit:
            return hit
        try:
            page = await qobuz.browse(client, id, max(0, start), count)
        except qobuz.QobuzError as e:
            raise HTTPException(502, str(e))
        for it in page["items"]:
            remember_qobuz_track(it)
        return cache_put(key, page)
    page = await browse_items(id or None, max(0, start), count, search or None)
    artist = await qobuz_artist_for_lms_page(page)
    return artist or page


LMS_ARTIST_FOLDERS = {"releases", "songs", "biography", "similar artists"}


async def qobuz_artist_for_lms_page(page: dict) -> dict | None:
    """Une page d'artiste LMS (dossiers Releases / Songs / Biography / Similar
    Artists) est remplacée par la page artiste Qobuz, retrouvée par le nom."""
    folders = {(it.get("title") or "").lower() for it in page["items"] if it.get("kind") == "folder"}
    if len(folders & LMS_ARTIST_FOLDERS) < 3 or not qobuz.available() or not page.get("title"):
        return None
    key = f"lms-artist:{page['title']}"
    hit = cache_get(key, 3600)
    if hit:
        return hit
    try:
        aid = await qobuz.artist_id_by_name(client, page["title"])
        return cache_put(key, await qobuz.artist_page(client, aid)) if aid else None
    except qobuz.QobuzError as e:
        log.info("page artiste Qobuz introuvable pour « %s » (%s)", page["title"], e)
        return None


SEARCH_CATS = [("releases", "Albums"), ("artists", "Artistes"), ("songs", "Titres"), ("playlists", "Playlists")]


@app.get("/api/search")
async def api_search(req: Request, q: str = ""):
    auth(req)
    q = q.strip()
    if len(q) < 2:
        return {"q": q, "sections": []}
    key = f"search:{q.lower()}"
    hit = cache_get(key, 300)
    if hit:
        return hit
    if qobuz.available():
        try:
            sections = await qobuz.search(client, q)
            for sec in sections:
                for it in sec["items"]:
                    remember_qobuz_track(it)
            return cache_put(key, {"q": q, "sections": sections})
        except qobuz.QobuzError as e:
            log.info("recherche via l'API Qobuz impossible (%s) : repli sur LMS", e)
    root = await root_ids()
    sid = root.get("search", "0")
    cats = await lms(["qobuz", "items", 0, 20, "menu:qobuz", f"item_id:{sid}.0", f"search:{q}"])
    found = []
    for it in cats.get("item_loop", []):
        t = (it.get("text") or "").lower()
        i = item_id_of(it)
        for k, label in SEARCH_CATS:
            if i and k in t:
                found.append((k, label, i))
    results = await asyncio.gather(*(browse_items(i, 0, 12) for _, _, i in found), return_exceptions=True)
    sections = []
    for (k, label, i), r in zip(found, results):
        if isinstance(r, Exception) or not r["items"]:
            continue
        sections.append({"key": k, "title": label, "id": i, "count": r["count"], "items": r["items"]})
    return cache_put(key, {"q": q, "sections": sections})


@app.get("/api/status")
async def api_status(req: Request):
    auth(req)
    return await poller.fresh()


LIGHT_KEYS = ("player", "connected", "power", "mode", "volume", "muted", "duration", "index", "count", "repeat", "shuffle", "current")


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/events")
async def api_events(req: Request):
    auth(req)

    async def gen():
        poller.clients += 1
        poller.wake.set()
        last_state = None
        last_qsig = None
        last_ping = time.time()
        try:
            yield "retry: 3000\n\n"
            while True:
                if await req.is_disconnected():
                    break
                st = poller.latest
                if st:
                    light = {k: st.get(k) for k in LIGHT_KEYS}
                    light["time"] = int(st.get("time") or 0)
                    qsig = (st["count"], tuple((t["id"], t["title"]) for t in st["queue"]))
                    if qsig != last_qsig:
                        yield sse("queue", {"queue": st["queue"]})
                        last_qsig = qsig
                    if light != last_state:
                        yield sse("state", light)
                        last_state = light
                elif poller.error:
                    yield sse("lmserror", {"message": poller.error})
                if time.time() - last_ping > 10:
                    yield sse("ping", {"t": int(time.time())})  # le front surveille ce battement
                    last_ping = time.time()
                await asyncio.sleep(1)
        finally:
            poller.clients -= 1

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def wake_speaker() -> None:
    """Réveille la KEF avant une lecture. La LSX gen1 se met en veille et
    disparaît du pont UPnP ; lancer une lecture sur une enceinte endormie
    reste bloqué ~25 s. On la rallume et on attend qu'elle réponde."""
    if not KEF_URL:
        return
    try:
        r = await client.get(f"{KEF_URL}/api/state", timeout=4)
        if r.json().get("is_on"):
            return
    except httpx.HTTPError:
        return  # contrôleur KEF indisponible : ne pas bloquer la lecture
    try:
        await client.post(f"{KEF_URL}/api/source", json={"source": "Wifi"}, timeout=4)
    except httpx.HTTPError:
        return
    end = time.time() + KEF_WAKE_WAIT
    while time.time() < end:
        try:
            r = await client.get(f"{KEF_URL}/api/state", timeout=4)
            if r.json().get("is_on"):
                await asyncio.sleep(2.0)  # laisser le pont UPnP ré-ajouter le renderer
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.8)


# ------------------------------------------------------------ file d'attente
# Deux sortes de titres dans la file : ceux qu'on a demandés (« voulus ») et
# ceux qui viennent avec un lancement (« par défaut » : la suite de l'album, de
# la playlist ou des titres populaires). Un ajout se place après le titre en
# cours et les voulus qui le suivent, mais avant les titres par défaut : on
# écoute A, on ajoute B puis C : A, B, C, puis la suite de l'album.
_queue_default: set[str] = set()   # adresses des titres « par défaut »
_fill_task: asyncio.Task | None = None
SINGLE_TRACK = re.compile(r"^qobuz://\d+\.\w+$")


def _cancel_fill() -> None:
    """Le remplissage en arrière-plan d'un lancement précédent ne doit pas
    se mélanger à une nouvelle demande."""
    if _fill_task and not _fill_task.done():
        _fill_task.cancel()


async def _add_all(urls: list[str], cmd: str = "add") -> None:
    for u in urls:
        await lms(["playlist", cmd, u])


def _fill_later(urls: list[str]) -> None:
    global _fill_task
    if urls:
        _fill_task = asyncio.create_task(_add_all(urls))


async def _queue_urls() -> tuple[list[str], int]:
    st = await lms(["status", 0, 2000, "tags:u"])
    return [t.get("url") or "" for t in st.get("playlist_loop", [])], int(st.get("playlist_cur_index") or 0)


def _wanted_slot(urls: list[str], cur: int) -> int:
    """Où insérer un titre voulu : après le titre en cours et les voulus qui
    le suivent, juste avant le premier titre par défaut (ou en fin de file)."""
    p = cur + 1
    while p < len(urls) and urls[p] not in _queue_default:
        p += 1
    return p


async def _add_wanted(urls: list[str], was_empty: bool) -> None:
    queue, cur = await _queue_urls()
    slot = _wanted_slot(queue, cur) if queue else 0
    end = len(queue)
    for k, u in enumerate(urls):
        await lms(["playlist", "add", u])          # ajouté en fin de file...
        if slot < end:
            await lms(["playlist", "move", end + k, slot + k])  # ...puis remonté à sa place
    if was_empty:
        await lms(["play"])


async def _move_new_block(n0: int, slot: int) -> None:
    """Remonte avant les titres par défaut le bloc que LMS vient d'ajouter en
    fin de file (le plugin Qobuz remplit la file de façon asynchrone)."""
    n1, stable = n0, 0
    for _ in range(24):
        await asyncio.sleep(0.25)
        n = int((await lms(["status", "-", 1])).get("playlist_tracks") or 0)
        stable = stable + 1 if n == n1 and n > n0 else 0
        n1 = n
        if stable >= 2:
            break
    if slot < n0:
        for k in range(n1 - n0):
            await lms(["playlist", "move", n0 + k, slot + k])


async def _mark_default_after_current() -> None:
    """Après un lancement par LMS (album de l'accueil...), tout ce qui suit le
    titre en cours est « par défaut »."""
    global _queue_default
    urls, cur = await _queue_urls()
    _queue_default = set(urls[cur + 1:])


async def play_single(url: str) -> None:
    """Un titre seul, sans rien derrière (recherche, liste de résultats)."""
    global _queue_default
    _cancel_fill()
    _queue_default = set()
    await lms(["playlist", "clear"])
    await lms(["playlist", "add", url])
    await lms(["playlist", "index", 0])


async def qobuz_play(item_id: str, mode: str, body: dict, before: dict) -> None:
    """Met en file / lance un élément qz:... (piste, album, playlist, titres populaires).

    Un seul démarrage de flux : on remplit la file jusqu'à la piste voulue,
    on saute dessus, puis le reste (« par défaut ») s'ajoute en arrière-plan.
    """
    global _queue_default
    if item_id.startswith("qz:search:"):
        # une liste de résultats n'est pas un contexte : le titre seul
        target = str(body.get("url") or "")
        if mode in ("play", "context") and SINGLE_TRACK.match(target):
            await play_single(target)
            return
        raise HTTPException(400, "Choisissez un titre de la liste")
    try:
        tracks = await qobuz.track_urls(client, item_id)
    except qobuz.QobuzError as e:
        raise HTTPException(502, str(e))
    for t in tracks:
        remember_qobuz_track(t)
    urls = [t["url"] for t in tracks]
    if not urls:
        raise HTTPException(404, "Rien à lire")
    if mode in ("play", "context"):
        _cancel_fill()
        k = 0
        if mode == "context":
            target = str(body.get("url") or "")
            k = urls.index(target) if target in urls else max(0, min(len(urls) - 1, int(body.get("index") or 0)))
        _queue_default = set(urls[k + 1:])        # cas A et B : la suite est « par défaut »
        await lms(["playlist", "clear"])
        await _add_all(urls[: k + 1])
        await lms(["playlist", "index", k])
        _fill_later(urls[k + 1:])
    elif mode == "add":
        await _add_wanted(urls, before["count"] == 0)   # cas C : tout est « voulu »
    elif mode == "insert":
        # « Lire ensuite » : juste après le titre en cours ; à l'envers pour garder l'ordre
        await _add_all(list(reversed(urls)), "insert")


@app.post("/api/play")
async def api_play(req: Request):
    auth(req)
    body = await req.json()
    item_id = str(body.get("id") or "").strip()
    mode = body.get("mode", "add")
    if not item_id or mode not in ("play", "add", "insert", "context"):
        raise HTTPException(400, "Paramètres invalides")
    before = await poller.fresh(3)
    starts_playback = mode in ("play", "context") or (mode != "insert" and before["count"] == 0)
    if starts_playback:
        await wake_speaker()
        poller.note()
    url = str(body.get("url") or "")
    if item_id.startswith("qz:"):
        await qobuz_play(item_id, mode, body, before)
    elif mode != "context" and SINGLE_TRACK.match(url):
        # un titre issu de LMS : par son adresse, pour ne pas embarquer la
        # liste qui l'entoure (réglage « jouer tout l'album » de LMS)
        if mode == "play":
            await play_single(url)
        elif mode == "add":
            await _add_wanted([url], before["count"] == 0)
        else:
            await lms(["playlist", "insert", url])
    elif mode == "context":
        # lire un album/playlist LMS à partir d'une piste : charger tout puis cibler la piste
        _cancel_fill()
        cmd: list[Any] = ["qobuz", "playlist", "play", f"item_id:{item_id}", "menu:qobuz"]
        if body.get("search"):
            cmd.append(f"search:{body['search']}")
        await lms(cmd)
        target = url.strip()
        try:
            idx = int(body.get("index") or 0)
        except (TypeError, ValueError):
            idx = 0
        # la file se remplit de façon asynchrone : attendre que la piste visée soit là
        for _ in range(14):
            st = await lms(["status", 0, 500, "tags:u"])
            loop = st.get("playlist_loop", [])
            if target:
                hit = next((int(t.get("playlist index", -1)) for t in loop if t.get("url") == target), None)
                if hit is not None:
                    idx = hit
                    break
            elif len(loop) > idx:
                break
            await asyncio.sleep(0.3)
        if idx > 0:
            await lms(["playlist", "index", idx])
            for _ in range(10):  # confirmer que l'état s'est bien calé sur la piste visée
                await asyncio.sleep(0.25)
                chk = await lms(["status", "-", 1])
                if int(chk.get("playlist_cur_index", -1)) == idx:
                    break
        await _mark_default_after_current()
    elif mode == "add":
        # album/playlist LMS ajouté : tout est « voulu », placé avant les titres par défaut
        queue, cur = await _queue_urls()
        slot = _wanted_slot(queue, cur) if queue else 0
        cmd = ["qobuz", "playlist", "add", f"item_id:{item_id}", "menu:qobuz"]
        if body.get("search"):
            cmd.append(f"search:{body['search']}")
        await lms(cmd)
        await _move_new_block(len(queue), slot)
        if before["count"] == 0:
            await lms(["play"])  # file vide : on démarre la lecture
    else:
        cmd = ["qobuz", "playlist", mode, f"item_id:{item_id}", "menu:qobuz"]
        if body.get("search"):
            cmd.append(f"search:{body['search']}")
        if mode == "play":
            _cancel_fill()
        await lms(cmd)
        if mode == "play":
            await _mark_default_after_current()
    await asyncio.sleep(0.4)
    return await poller.refresh()


SIMPLE = {
    "play": ["play"], "pause": ["pause", 1], "toggle": ["pause"], "stop": ["stop"],
    "next": ["playlist", "index", "+1"], "clear": ["playlist", "clear"], "power_on": ["power", 1],
}


@app.post("/api/control")
async def api_control(req: Request):
    auth(req)
    body = await req.json()
    cmd = body.get("cmd")
    value = body.get("value")
    other = (poller.latest or {}).get("mode") == "other"
    if other and cmd in ("play", "toggle"):
        # reprendre l'enceinte là où Jukebox en était (un seul nouveau flux)
        target = poller.resume_pos
        poller.note(pos=target)
        await lms(["time", target] if target > 0.5 else ["playlist", "index", (poller.latest or {}).get("index", 0)])
    elif other and cmd == "pause":
        pass  # ne pas couper l'autre source : Jukebox n'a rien à mettre en pause
    elif cmd in SIMPLE:
        if cmd in ("play", "next"):
            poller.note()
        if cmd == "clear":
            _cancel_fill()
            _queue_default.clear()
        await lms(SIMPLE[cmd])
    elif cmd == "prev":
        st = await poller.fresh(3)
        poller.note()
        await lms(["time", 0] if st["time"] > 3 else ["playlist", "index", "-1"])
    elif cmd == "seek":
        target = max(0, int(float(value or 0)))
        poller.note(pos=target)
        await lms(["time", target])
    elif cmd == "volume":
        await lms(["mixer", "volume", max(0, min(MAX_VOLUME, int(float(value or 0))))])
    elif cmd == "jump":
        poller.note(index=int(value))
        await lms(["playlist", "index", int(value)])
    elif cmd == "clear_keep":
        # Vider la file en gardant le titre en cours : LMS n'a pas de commande
        # pour ça, on retire les titres un à un (après, puis avant le titre en
        # cours, pour que les index restent valides).
        _cancel_fill()
        _queue_default.clear()
        queue, cur = await _queue_urls()
        for i in range(len(queue) - 1, cur, -1):
            await lms(["playlist", "delete", i])
        for i in range(cur - 1, -1, -1):
            await lms(["playlist", "delete", i])
    elif cmd == "remove":
        await lms(["playlist", "delete", int(value)])
    elif cmd == "move":
        await lms(["playlist", "move", int(value[0]), int(value[1])])
    else:
        raise HTTPException(400, "Commande inconnue")
    await asyncio.sleep(0.25)
    return await poller.refresh()


ALLOWED_IMG = ("/imageproxy/", "/music/", "/html/", "/plugins/")


@app.get("/img")
async def img(u: str):
    if not u.startswith(ALLOWED_IMG):
        raise HTTPException(404)
    try:
        r = await client.get(LMS_URL + u)
    except httpx.HTTPError:
        raise HTTPException(502)
    if r.status_code != 200:
        raise HTTPException(404)
    return Response(content=r.content, media_type=r.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"})
