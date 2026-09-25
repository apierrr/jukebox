# Notes techniques

Ce qui n'est pas évident à la lecture du code : les défauts de l'enceinte KEF
LSX (1re génération) en UPnP, et les choix qui en découlent. Mesures faites à
volume nul sur l'installation d'origine (LMS 9.1, pont UPnPBridge 3.4.1).

## L'enceinte KEF LSX en UPnP

- Pas de signal de fin de piste : la LSX ne passe jamais à `STOPPED` en fin
  de flux et accepte `SetNextAVTransportURI` sans jamais jouer la piste
  annoncée. Via le pont UPnP de LMS, la lecture reste donc figée en fin de
  piste.
- Arrêt annoncé en retard : après `Stop` + `SetAVTransportURI` + `Play`
  rapprochés (saut dans une piste, piste suivante...), la LSX signale parfois
  l'arrêt de l'*ancien* flux après le démarrage du nouveau : le pont conclut
  que le nouveau s'est arrêté (`stop on short track` dans ses journaux) et
  n'envoie rien. Fréquence très variable d'une série à l'autre.
- Démarrage : 2,5 à 3 s entre `Play` et le son, parfois 5 à 6 s.
- Pas de `Pause` ni de `Seek` en UPnP (HTTP 500 / erreur 710).
- Veille après 60 min sans son : elle disparaît alors du pont UPnP ; une
  lecture sur enceinte endormie reste bloquée ~25 s. Jukebox la réveille avant
  de lancer (`KEF_CONTROL_URL`), ~13 s à froid.

## Réglages recommandés côté LMS

- Pont UPnPBridge en mode flow (`<mode>flc:0,r:96000,s:24,flow</mode>`
  dans `upnpbridge.xml`, LMS arrêté pendant l'édition) : toute la file part
  dans un seul flux continu, la LSX n'a plus de fin de piste à signaler. Blanc
  entre deux pistes : environ 12 s avant, 1 s après. Fréquence fixe imposée par le mode flow.
- `accept_nexturi` à `0` (inutile en flow, indispensable sans).
- Plugin Qobuz : `showYearWithAlbum`, `labelHiResAlbums`,
  `appendVersionToTitle` à 1 (`pref plugin.qobuz:<clé> 1`) pour distinguer les
  éditions d'un même album quand la recherche passe par LMS. Jukebox transforme
  "(Hi-Res)" en badge et "Artiste (2013)" en "Artiste, 2013".
- LMS résout les noms avec son propre client DNS (`AnyEvent::DNS`) : un
  `extra_hosts` / `/etc/hosts` n'a aucun effet sur lui.

## Le veilleur (`Poller._unstick`)

Il tourne en permanence, app ouverte ou non (sondage toutes les secondes
pendant la lecture), et intervient quel que soit le client qui a lancé la
musique :

1. Position figée en cours de piste (`STALL_START`, 4 s) : il lit l'état
   réel de l'enceinte (`GetTransportInfo` sur `KEF_UPNP_URL`).
   `TRANSITIONING`/`PLAYING` : elle démarre, il patiente jusqu'à
   `STALL_START_MAX` (15 s). `STOPPED` : vrai blocage, il relance la même
   demande (position affichée par LMS, ou cible mémorisée d'un saut fait
   depuis Jukebox si LMS affiche 0:00), au plus `START_RETRIES` (2) fois.
2. Toujours rien à 0:00 après les relances, enceinte arrêtée : morceau
   illisible (fichier abîmé côté Qobuz/CDN), passage à la piste suivante.
3. Filets : figé près de la fin (`STALL_NEAR_END`) ou très longtemps
   (`STALL_ANYWHERE`), passage à la piste suivante.
4. Jamais contre une autre source : avant d'agir, `GetMediaInfo` doit
   montrer un flux du pont LMS (`.../bridge-N.flac`). URI vide ou étrangère,
   enceinte muette : il s'abstient.

Chaque décision est journalisée (`jukebox.veilleur`). Résultats : démarrages
4-5 s, vrais blocages rattrapés en ~9,5 s (contre ~30 s et un saut de piste
sans veilleur).

## Cohabitation avec Qobuz Connect

Quand un autre contrôleur (Qobuz Connect via qobuz-proxy par exemple) prend l'enceinte,
LMS ne le voit pas : il reste "en lecture" et le pont recopie la position de
l'enceinte comme si c'était la sienne. Jukebox vérifie la source toutes les
3 s (`OTHER_CHECK_EVERY`) et publie alors `mode: "other"`, position gelée là où
LMS jouait vraiment : l'app affiche "Autre source en cours", lecture reprend
l'enceinte à cette position, pause ne coupe pas l'autre source.

## API Qobuz directe (`app/qobuz.py`)

- Mêmes appels que qobuz-dl / qobuz-proxy : `album|artist|track|playlist/search`
  (en parallèle, ~0,4 s), `album/get`, `playlist/get`, `artist/page` (page
  artiste complète, ~0,1 s), `artist/getReleasesList` (pagination par
  `has_more`). En-têtes `X-App-Id` + `X-User-Auth-Token`, sans signature.
- Deux formats de réponse coexistent (recherche / `artist/page` : `name` ou
  `name.display`, `release_date_original` ou `dates.original`, photo d'artiste
  en `images.portrait.hash`) : les aides `_name`, `_year`, `_hires`,
  `_portrait` acceptent les deux.
- Identifiants d'éléments : `qz:album:<id>`, `qz:artist:<id>`,
  `qz:playlist:<id>`, `qz:track:<id>`, `qz:artisttop:<id>`,
  `qz:releases:<artiste>:<type>`, `qz:search:<type>:<requête>`.
- La recherche d'artistes de Qobuz compare les noms à *toute* la requête
  ("angele brol" donne "Angela Brown") : les artistes des albums et titres
  trouvés passent devant, puis ceux dont le nom figure dans la requête.
- Lecture : file remplie jusqu'à la piste visée, un seul `playlist index` (un
  seul démarrage de flux), reste de l'album ajouté en arrière-plan (tâche
  annulée si une autre lecture est demandée entre-temps).

## Front et temps réel

- État poussé en SSE (`/api/events`) à chaque changement, plus un `ping`
  toutes les 10 s. Le front se reconnecte et relit `/api/status` sans nouvelles
  depuis 25 s, au retour au premier plan, sur `pageshow` et `online`. La barre
  de progression n'avance localement que si le serveur a parlé il y a moins de
  5 s.
- L'état LMS est lu avec `status 0 N` (index absolus) : `status -` renvoie la
  file à partir de la piste courante et décalait l'affichage.
- Coquille (HTML/CSS/JS) servie en `Cache-Control: no-cache` ; le service
  worker se recharge à chaque nouvelle valeur de `V` dans `sw.js`.
- Rangées horizontales : `scroll-padding-inline` doit égaler le retrait latéral,
  sinon l'aimantation (`scroll-snap`) colle la première carte au bord.
