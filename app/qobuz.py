"""Accès direct à l'API Qobuz pour la recherche et les pages album/artiste/playlist.

Le plugin Qobuz de LMS met 1,3 à 2,5 s par recherche ; l'API interrogée
directement répond en 0,2 à 0,4 s. La lecture, elle, reste confiée à LMS : il
suffit de lui passer des adresses qobuz://<id>.flac, dont il retrouve seul les
métadonnées.

Les éléments produits ont exactement la forme de ceux de `norm()` dans
main.py (id, kind, title, subtitle, image…), ce qui laisse le front inchangé.
Leurs identifiants commencent par « qz: » : qz:album:<id>, qz:artist:<id>,
qz:playlist:<id>, qz:track:<id>, et qz:search:<type>:<requête> pour « Tout voir ».

Identifiants : ceux de qobuz-proxy (jeton relu dans son credentials.json,
monté en lecture seule), avec son identifiant d'application.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

API = "https://www.qobuz.com/api.json/0.2"
APP_ID = os.environ.get("QOBUZ_APP_ID", "").strip()
CREDENTIALS = Path(os.environ.get("QOBUZ_CREDENTIALS", "/qobuz/credentials.json"))

# type Qobuz → (clé de section attendue par le front, libellé)
SEARCH_TYPES = [("album", "releases", "Albums"), ("artist", "artists", "Artistes"),
                ("track", "songs", "Titres"), ("playlist", "playlists", "Playlists")]


class QobuzError(Exception):
    pass


_token_cache: tuple[float, str] | None = None


def _token() -> str:
    """Jeton utilisateur, relu si qobuz-proxy a réécrit son fichier."""
    global _token_cache
    try:
        mtime = CREDENTIALS.stat().st_mtime
    except OSError as e:
        raise QobuzError(f"identifiants Qobuz introuvables ({e.__class__.__name__})") from e
    if not _token_cache or _token_cache[0] != mtime:
        data = json.loads(CREDENTIALS.read_text())
        _token_cache = (mtime, data.get("user_auth_token", ""))
    return _token_cache[1]


def available() -> bool:
    return bool(APP_ID) and CREDENTIALS.exists()


async def get(client: httpx.AsyncClient, endpoint: str, **params: Any) -> dict:
    if not APP_ID:
        raise QobuzError("QOBUZ_APP_ID non défini")
    try:
        r = await client.get(
            f"{API}/{endpoint}",
            params=params,
            headers={"X-App-Id": APP_ID, "X-User-Auth-Token": _token()},
            timeout=8,
        )
    except httpx.HTTPError as e:
        raise QobuzError(f"API Qobuz injoignable ({e.__class__.__name__})") from e
    if r.status_code != 200:
        raise QobuzError(f"API Qobuz : HTTP {r.status_code} sur {endpoint}")
    return r.json()


# --------------------------------------------------------------- éléments
# L'API a deux formats : celui de la recherche (name, release_date_original,
# hires_streamable) et celui d'artist/page (name.display, dates.original,
# rights / audio_info, photo en hash). Les aides ci-dessous acceptent les deux.
PORTRAIT = "https://static.qobuz.com/images/artists/covers/{size}/{hash}.{fmt}"


def _name(x: dict | None) -> str:
    n = (x or {}).get("name")
    return (n.get("display") or "") if isinstance(n, dict) else (n or "")


def _year(album: dict) -> str:
    return (album.get("release_date_original") or (album.get("dates") or {}).get("original") or "")[:4]


def _hires(x: dict) -> bool:
    return bool(
        x.get("hires_streamable")
        or (x.get("rights") or {}).get("hires_streamable")
        or ((x.get("audio_info") or {}).get("maximum_bit_depth") or 0) > 16
    )


def _quality(x: dict) -> str | None:
    """Qualité Hi-Res lisible, ex. « 24 bits / 96 kHz » (None en qualité CD)."""
    info = x.get("audio_info") or {}
    bits = x.get("maximum_bit_depth") or info.get("maximum_bit_depth")
    rate = x.get("maximum_sampling_rate") or info.get("maximum_sampling_rate")
    if not bits or not rate or bits <= 16:
        return None
    return f"{bits} bits / {rate:g} kHz".replace(".", ",")


def _plain(text: str | None) -> str:
    """HTML de Qobuz (présentations, biographies) → paragraphes en texte brut."""
    if not text:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    paras = (re.sub(r"[ \t\u00a0]+", " ", p).strip() for p in re.split(r"\n\s*\n", text))
    return "\n\n".join(p for p in paras if p)


def _duration(seconds: int | None) -> str | None:
    if not seconds:
        return None
    h, m = divmod(round(seconds / 60), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def album_about(a: dict) -> dict | None:
    """Texte et fiche d'un album pour le bouton « i »."""
    date = a.get("release_date_original") or ""
    facts = [
        ("Label", (a.get("label") or {}).get("name")),
        ("Genre", (a.get("genre") or {}).get("name")),
        ("Sortie", "/".join(reversed(date.split("-"))) if date else None),
        ("Qualité", _quality(a) or "CD, 16 bits / 44,1 kHz"),
        ("Durée", _duration(a.get("duration"))),
        ("Distinctions", ", ".join(x.get("name") for x in a.get("awards") or [] if x.get("name"))),
        ("Copyright", a.get("copyright")),
    ]
    about = {"title": a.get("title") or "", "text": _plain(a.get("description")),
             "facts": [[k, v] for k, v in facts if v]}
    return about if about["text"] or about["facts"] else None


def playlist_about(p: dict) -> dict | None:
    """Texte et fiche d'une playlist pour le bouton « i »."""
    facts = [
        ("Par", (p.get("owner") or {}).get("name")),
        ("Titres", str(p["tracks_count"]) if p.get("tracks_count") else None),
        ("Durée", _duration(p.get("duration"))),
        ("Genres", ", ".join(g.get("name") for g in p.get("genres") or [] if g.get("name"))),
    ]
    about = {"title": p.get("name") or p.get("title") or "", "text": _plain(p.get("description")),
             "facts": [[k, v] for k, v in facts if v]}
    return about if about["text"] or about["facts"] else None


def _portrait(x: dict, size: str) -> str | None:
    p = (x.get("images") or {}).get("portrait") or {}
    return PORTRAIT.format(size=size, hash=p["hash"], fmt=p.get("format") or "jpg") if p.get("hash") else None


def _with_version(title: str, version: str | None) -> str:
    return f"{title} ({version})" if version else title


def _join(*parts: str | None) -> str:
    return " · ".join(p for p in parts if p)


def track_url(track_id: Any) -> str:
    return f"qobuz://{track_id}.flac"


def album_item(a: dict) -> dict:
    img = a.get("image") or {}
    return {
        "id": f"qz:album:{a['id']}",
        "kind": "collection",
        "title": _with_version(a.get("title") or "", a.get("version")),
        "subtitle": _join(_name(a.get("artist")), _year(a)),
        "hires": _hires(a),
        "quality": _quality(a),
        "image": img.get("small") or img.get("thumbnail"),
        "image_large": img.get("large") or img.get("small"),
        "url": None,
        "pos": None,
    }


def artist_item(ar: dict) -> dict:
    img = ar.get("image") or {}
    pic = img.get("medium") or img.get("small") or ar.get("picture") or _portrait(ar, "medium")
    return {
        "id": f"qz:artist:{ar['id']}",
        "kind": "folder",
        "title": _name(ar),
        "subtitle": "",
        "hires": False,
        "image": pic,
        "image_large": img.get("large") or _portrait(ar, "large") or pic,
        "url": None,
        "pos": None,
    }


def track_item(t: dict, album: dict | None = None, pos: int | None = None) -> dict:
    album = album or t.get("album") or {}
    img = album.get("image") or {}
    performer = _name(t.get("performer")) or _name(t.get("artist")) or _name(album.get("artist"))
    return {
        "id": f"qz:track:{t['id']}",
        "kind": "track",
        "title": _with_version(t.get("title") or "", t.get("version")),
        "subtitle": _join(performer, album.get("title")),
        "hires": _hires(t),
        "quality": _quality(t),
        "image": img.get("small") or img.get("thumbnail"),
        "image_large": img.get("large") or img.get("small"),
        "url": track_url(t["id"]),
        "pos": pos,
        # pour la mémoire des pistes de la file (titre avant que LMS ne l'ait)
        "artist": performer or "",
        "album": album.get("title") or "",
    }


def playlist_item(p: dict) -> dict:
    imgs = p.get("images300") or p.get("images") or p.get("image_rectangle") or []
    if isinstance(imgs, dict):  # format artist/page : {"rectangle": [...], "covers": [...]}
        imgs = imgs.get("covers") or imgs.get("rectangle") or next(iter(imgs.values()), [])
    n = p.get("tracks_count")
    return {
        "id": f"qz:playlist:{p['id']}",
        "kind": "collection",
        "title": p.get("name") or p.get("title") or "",
        "subtitle": _join((p.get("owner") or {}).get("name"), f"{n} titres" if n else None),
        "hires": False,
        "image": imgs[0] if imgs else None,
        "image_large": imgs[0] if imgs else None,
        "url": None,
        "pos": None,
    }


BUILDERS = {"album": album_item, "artist": artist_item, "track": track_item, "playlist": playlist_item}


# --------------------------------------------------------------- recherche
def _fold(s: str) -> str:
    """Minuscules sans accents : « Angèle » et « angele » se valent."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _artists(data: dict, albums: list[dict], tracks: list[dict], q: str) -> list[dict]:
    """La recherche d'artistes de Qobuz cherche des noms proches de TOUTE la
    requête : « angele brol » donne « Angela Brown » et pas Angèle. On met en
    tête les artistes des albums et titres trouvés, puis ceux de la recherche,
    et d'abord ceux dont le nom figure dans la requête."""
    seen: dict[Any, dict] = {}
    for a in albums[:6]:
        ar = a.get("artist") or {}
        if ar.get("id") and ar["id"] not in seen:
            seen[ar["id"]] = ar
    for t in tracks[:6]:
        ar = t.get("performer") or {}
        if ar.get("id") and ar["id"] not in seen:
            seen[ar["id"]] = ar
    for ar in data.get("items", []):
        if ar.get("id") in seen:
            seen[ar["id"]] = {**seen[ar["id"]], **ar}  # la recherche a l'image
        elif ar.get("id"):
            seen[ar["id"]] = ar
    fq = _fold(q)
    ranked = sorted(seen.values(), key=lambda ar: 0 if ar.get("name") and _fold(ar["name"]) in fq else 1)
    return ranked


async def search(client: httpx.AsyncClient, q: str, limit: int = 12) -> list[dict]:
    """Les quatre recherches typées en parallèle (~0,4 s au total)."""
    results = await asyncio.gather(
        *(get(client, f"{kind}/search", query=q, limit=limit) for kind, _, _ in SEARCH_TYPES)
    )
    raw = {kind: (data.get(f"{kind}s") or {}) for (kind, _, _), data in zip(SEARCH_TYPES, results)}
    raw["artist"] = {
        "items": _artists(raw["artist"], raw["album"].get("items", []), raw["track"].get("items", []), q),
        "total": raw["artist"].get("total"),
    }
    sections = []
    for kind, key, label in SEARCH_TYPES:
        block = raw[kind]
        items = [BUILDERS[kind](x) for x in block.get("items", [])][:limit]
        if items:
            sections.append({
                "key": key,
                "title": label,
                "id": f"qz:search:{kind}:{urllib.parse.quote(q, safe='')}",
                "count": int(block.get("total") or len(items)),
                "items": items,
            })
    return sections


# --------------------------------------------------------------- pages
def _page(item_id: str, title: str, subtitle: str, items: list[dict], count: int,
          start: int, image: str | None = None, hires: bool = False) -> dict:
    return {"id": item_id, "title": title, "subtitle": subtitle, "count": count,
            "offset": start, "items": items, "style": None, "image": image, "hires": hires}


async def browse(client: httpx.AsyncClient, item_id: str, start: int = 0, count: int = 100) -> dict:
    parts = item_id.split(":", 3)
    kind = parts[1] if len(parts) > 1 else ""

    if kind == "album":
        a = await get(client, "album/get", album_id=parts[2])
        tracks = (a.get("tracks") or {}).get("items", [])
        items = [track_item(t, a, pos=k) for k, t in enumerate(tracks)]
        head = album_item(a)
        page = _page(item_id, head["title"], head["subtitle"], items, len(items), 0,
                     head["image_large"], head["hires"])
        page["quality"] = head["quality"]
        page["about"] = album_about(a)
        artist = a.get("artist") or {}
        page["artist"] = {"id": f"qz:artist:{artist['id']}", "name": _name(artist)} if artist.get("id") else None
        return page

    if kind == "playlist":
        p = await get(client, "playlist/get", playlist_id=parts[2], extra="tracks",
                      offset=start, limit=count)
        block = p.get("tracks") or {}
        items = [track_item(t, pos=start + k) for k, t in enumerate(block.get("items", []))]
        head = playlist_item(p)
        page = _page(item_id, head["title"], head["subtitle"], items,
                     int(block.get("total") or len(items)), start, head["image_large"])
        page["about"] = playlist_about(p) if start == 0 else None
        return page

    if kind == "artist":
        return await artist_page(client, parts[2])

    if kind == "artisttop":
        p = await get(client, "artist/page", artist_id=parts[2])
        items = [track_item(t, pos=k) for k, t in enumerate(p.get("top_tracks") or [])]
        return _page(item_id, f"{_name(p)} — Titres populaires", "", items, len(items), 0,
                     _portrait(p, "large"))

    if kind == "releases" and len(parts) == 4:
        # qz:releases:<artiste>:<type> ; l'API ne donne pas de total, seulement has_more
        aid, rtype = parts[2], parts[3]
        data = await get(client, "artist/getReleasesList", artist_id=aid, release_type=rtype,
                         sort="release_date", offset=start, limit=min(count, 50))
        items = [album_item(x) for x in data.get("items", [])]
        total = start + len(items) + (1 if data.get("has_more") else 0)
        return _page(item_id, dict(RELEASE_TYPES).get(rtype, "Sorties"), "", items, total, start)

    if kind == "search" and len(parts) == 4:
        stype, q = parts[2], urllib.parse.unquote(parts[3])
        data = await get(client, f"{stype}/search", query=q, offset=start, limit=min(count, 50))
        block = data.get(f"{stype}s") or {}
        items = [BUILDERS[stype](x) for x in block.get("items", [])]
        return _page(item_id, q, "", items, int(block.get("total") or len(items)), start)

    raise QobuzError(f"identifiant inconnu : {item_id}")


# Les pochettes et images de playlists servies par LMS contiennent l'identifiant
# Qobuz : …/images/covers/ya/59/<album>_300.jpg, …/images/playlists/<id>_….jpg
_COVER_ID = re.compile(r"/images/covers/[^/]+/[^/]+/([a-z0-9]+)_\d+\.jpg")
_PLAYLIST_ID = re.compile(r"/images/playlists/(\d+)_")


def qobuz_id_from_image(image: str | None) -> str | None:
    """« qz:album:<id> » ou « qz:playlist:<id> » d'après l'image d'un élément LMS."""
    if not image:
        return None
    m = _PLAYLIST_ID.search(image)
    if m:
        return f"qz:playlist:{m.group(1)}"
    m = _COVER_ID.search(image)
    return f"qz:album:{m.group(1)}" if m else None


async def artist_id_by_name(client: httpx.AsyncClient, name: str) -> str | None:
    """Identifiant Qobuz d'un artiste d'après son nom exact (accents ignorés)."""
    data = await get(client, "artist/search", query=name, limit=10)
    want = _fold(name)
    for ar in (data.get("artists") or {}).get("items", []):
        if _fold(_name(ar)) == want:
            return str(ar["id"])
    return None


RELEASE_TYPES = [("album", "Albums"), ("epSingle", "EP & singles"), ("live", "Live"),
                 ("compilation", "Compilations"), ("other", "Autres sorties")]


async def artist_page(client: httpx.AsyncClient, artist_id: str) -> dict:
    """Page artiste en sections, comme le lecteur web de Qobuz (artist/page, ~0,15 s)."""
    p = await get(client, "artist/page", artist_id=artist_id, sort="release_date")
    sections = []
    top = [track_item(t, pos=k) for k, t in enumerate(p.get("top_tracks") or [])]
    if top:
        sections.append({"key": "songs", "title": "Titres populaires", "id": f"qz:artisttop:{artist_id}",
                         "count": len(top), "items": top[:8]})
    releases = {r.get("type"): r for r in p.get("releases") or []}
    for rtype, label in RELEASE_TYPES:
        block = releases.get(rtype) or {}
        items = [album_item(x) for x in block.get("items") or []]
        if items:
            sections.append({"key": "releases", "title": label, "id": f"qz:releases:{artist_id}:{rtype}",
                             "count": len(items), "items": items})
    similar = [artist_item(x) for x in ((p.get("similar_artists") or {}).get("items") or [])[:12]]
    if similar:
        sections.append({"key": "artists", "title": "Artistes similaires", "id": None,
                         "count": len(similar), "items": similar})
    playlists = [playlist_item(x) for x in (p.get("playlists") or {}).get("items") or []]
    if playlists:
        sections.append({"key": "playlists", "title": "Playlists", "id": None,
                         "count": len(playlists), "items": playlists})
    page = _page(f"qz:artist:{artist_id}", _name(p), "", [], 0, 0, _portrait(p, "large"))
    page["sections"] = sections
    bio = _plain((p.get("biography") or {}).get("content"))
    page["about"] = {"title": _name(p), "text": bio, "facts": []} if bio else None
    return page


async def track_urls(client: httpx.AsyncClient, item_id: str) -> list[dict]:
    """Pistes (éléments complets) à mettre en file pour un qz:album/playlist/track."""
    kind = item_id.split(":", 2)[1]
    if kind == "track":
        # LMS retrouve seul les métadonnées d'une adresse qobuz:// : pas d'appel
        return [{"url": track_url(item_id.split(":", 2)[2]), "kind": "track", "title": ""}]
    if kind in ("album", "playlist", "artisttop"):
        page = await browse(client, item_id, 0, 500)
        return page["items"]
    raise QobuzError(f"lecture impossible pour {item_id}")
