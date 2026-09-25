# Jukebox

Télécommande web pour smartphone, pensée pour les invités : chercher dans tout le
catalogue Qobuz, lancer un album, ajouter des titres à la file d'attente, régler
le volume. Un simple code suffit, pas de compte ; l'app s'installe sur
l'écran d'accueil (PWA).

Jukebox ne joue rien lui-même : il pilote un lecteur de
[Lyrion Music Server](https://lyrion.org/) (LMS) équipé du plugin Qobuz. Il a été
écrit pour une enceinte KEF LSX reliée à LMS par le pont UPnP, et contourne
plusieurs défauts de cette enceinte (voir [docs/NOTES.md](docs/NOTES.md)), mais
fonctionne avec n'importe quel lecteur LMS.

## Fonctionnalités

- Recherche d'albums, d'artistes, de titres et de playlists, directement via
  l'API Qobuz (0,2 à 0,6 s) ou à défaut via le plugin Qobuz de LMS (1 à 2,5 s).
- Pages album, artiste et playlist : titres populaires, albums, EP et singles,
  lives, compilations, artistes similaires. Badge Hi-Res et année de sortie
  pour distinguer les éditions.
- Lecture d'un album depuis n'importe quelle piste, "Lire ensuite", "Ajouter à
  la file", file réordonnable, volume plafonné. Les titres ajoutés passent
  avant la suite de l'album en cours, et un titre lancé depuis une recherche
  part seul.
- État en temps réel (Server-Sent Events) : titre, position, file, volume, avec
  reconnexion automatique quand le téléphone sort de veille.
- Accueil avec les nouveautés, sélections, meilleures ventes et favoris du compte.
- Un veilleur côté serveur rattrape les démarrages bloqués de l'enceinte, saute
  les morceaux illisibles et ne reprend jamais la main sur une autre source
  (Qobuz Connect par exemple) qui joue sur l'enceinte.

## Architecture

```
téléphone --(HTTP/SSE)--> Jukebox (FastAPI) --(JSON-RPC)--> LMS --> lecteur / enceinte
                              |
                              +--(HTTPS)--> API Qobuz (recherche et pages, facultatif)
```

- `app/main.py` : API, flux temps réel, veilleur (`Poller`), pilotage de LMS.
- `app/qobuz.py` : recherche et pages via l'API Qobuz. La lecture reste confiée
  à LMS, à qui Jukebox passe des adresses `qobuz://<id>.flac`.
- `app/static/` : front en HTML/CSS/JS sans étape de build, servi tel quel.

## Installation

Prérequis : Docker, et un LMS joignable avec le plugin Qobuz configuré.

```bash
git clone https://github.com/apierrr/jukebox.git
cd jukebox
cp .env.example .env      # puis adapter
docker compose up -d --build
```

L'app répond sur `http://<machine>:8767`.

## Réglages (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `PORT` | `8767` | Port publié sur l'hôte |
| `LMS_URL` | `http://host.docker.internal:9000` | Adresse de LMS |
| `PLAYER_ID` | premier lecteur | Lecteur LMS à piloter (adresse MAC) |
| `APP_NAME` | `Jukebox` | Nom affiché |
| `GUEST_PIN` | vide (accès libre) | Code demandé aux invités |
| `MAX_VOLUME` | `80` | Volume maximum depuis l'app |
| `SECRET_KEY` | générée | Signature des cookies (`data/secret.key`) |
| `KEF_CONTROL_URL` | vide | Réveil de l'enceinte via [Kef_LSX_Control](https://github.com/apierrr/Kef_LSX_Control) |
| `KEF_WAKE_WAIT` | `14` | Attente maximale du réveil (s) |
| `KEF_UPNP_URL` | vide | Contrôle UPnP AVTransport de l'enceinte, pour le veilleur |
| `QOBUZ_APP_ID` | vide | Recherche via l'API Qobuz (voir ci-dessous) |

Après modification : `docker compose up -d`.

Code invité : le lien `https://<domaine>/?pin=<code>` connecte directement
(cookie de 180 jours) : pratique en QR code. Changer le code déconnecte tout le
monde.

## Recherche via l'API Qobuz

Sans réglage, la recherche passe par le plugin Qobuz de LMS. Pour la rendre
environ 5 fois plus rapide, Jukebox peut interroger l'API Qobuz directement ; il
lui faut un identifiant d'application (`QOBUZ_APP_ID`) et le jeton d'un compte,
lu dans un fichier `{"user_auth_token": "..."}` monté sur `/qobuz/credentials.json`,
par exemple celui d'une installation de
[qobuz-proxy](https://github.com/leolobato/qobuz-proxy), relu s'il change :

```yaml
# docker-compose.override.yml (ignoré par git)
services:
  jukebox:
    volumes:
      - /chemin/vers/credentials.json:/qobuz/credentials.json:ro
```

Si l'API ne répond pas, Jukebox repasse automatiquement par LMS.

## Exposer sur Internet

Derrière un reverse proxy ou un tunnel (Cloudflare par exemple), l'app devient installable
en HTTPS. Pour rejoindre le réseau Docker du proxy sans toucher au compose
versionné :

```yaml
# docker-compose.override.yml
services:
  jukebox:
    networks: [default, proxy]
networks:
  proxy:
    external: true
```

Sans `GUEST_PIN`, n'importe qui atteignant l'URL pilote la musique.

## Développement

- Le front n'a pas d'étape de build : modifier `app/static/`, puis incrémenter
  `V` dans `app/static/sw.js` pour que les PWA installées se mettent à jour.
- Journal du veilleur et des replis : `docker compose logs -f jukebox | grep -E "veilleur|repli"`.
- Notes techniques (enceinte KEF, pont UPnP, Qobuz Connect, API Qobuz) :
  [docs/NOTES.md](docs/NOTES.md).

## Licence

MIT, voir [LICENSE](LICENSE).
