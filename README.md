# Crossfade

[![CI](https://github.com/tg099/crossfade/actions/workflows/ci.yml/badge.svg)](https://github.com/tg099/crossfade/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/tg099/crossfade?sort=semver)](https://github.com/tg099/crossfade/releases)
[![Image](https://img.shields.io/badge/ghcr.io-tg099%2Fcrossfade-2496ED?logo=docker&logoColor=white)](https://github.com/tg099/crossfade/pkgs/container/crossfade)
[![License: AGPL-3.0](https://img.shields.io/github/license/tg099/crossfade)](LICENSE)

**English** · [Deutsch](README.de.md)

A self-hosted web app that keeps your music collections in sync across music streaming services
([see which ones](#supported-services)): one way, both ways or in groups, including deletions.
Several people share one instance, each with their own services and fades. Only admins create and manage users.

<p align="center">
  <img src="docs/screenshots/desktop-light.png" alt="Home: your fades, this week's new songs and what was just added" width="100%">
</p>

| A fade | Your library | On the phone |
|---|---|---|
| ![A fade with its collections and stats, dark mode](docs/screenshots/desktop-dark.png) | ![All songs across services in one list](docs/screenshots/library-dark.png) | ![Home on a phone](docs/screenshots/phone-light.png) |

## Concepts

- **Collection**: the liked songs, the library or a playlist on one service.
- **Fade**: a group of collections that are kept in sync. Each collection in a fade has a role:
  - *both directions*: sends its own changes and receives the others'
  - *sends*: source only
  - *receives*: target only; anything you remove there yourself is not added again
- The same collection can be part of several fades; the editor warns if it receives deletions from two fades.

| Setup | Example |
|---|---|
| One-way | Playlist "Gym" on service A *sends* → playlist "Gym" on service B *receives* |
| Bidirectional | Liked songs on service A ↔ liked songs on service B |
| Group | Liked songs on A ↔ on B ↔ on C |

Friends on the same instance can share a fade: each person adds one of their own collections and only ever sees
the fade itself, never the other person's library.

## Supported services

| Service | Collections | Sign-in | Interface |
|---|---|---|---|
| Apple Music | Favourite songs, library, playlists | Cookie from the web player | Unofficial (the web player's) |
| Spotify | Liked songs, playlists | Spotify login; the admin registers an app once | Official Web API |
| YouTube Music | Liked music, library songs, playlists | Request copied from the browser, or a Google code (then liked videos and playlists only) | Unofficial (the web app's); official YouTube Data API with a Google code |

How to connect each one is in [Setting up the services](#setting-up-the-services). A new service is one module in
`crossfade/providers/`; requests are welcome as [issues](https://github.com/tg099/crossfade/issues).

## Run with Docker (recommended)

Requirements: a server with Docker and a domain whose DNS points to it (ports 80/443 open).

```sh
curl -O https://raw.githubusercontent.com/tg099/crossfade/main/docker-compose.yml \
     -O https://raw.githubusercontent.com/tg099/crossfade/main/Caddyfile \
     -o .env https://raw.githubusercontent.com/tg099/crossfade/main/.env.example
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # → CROSSFADE_SECRET_KEY in .env
# set CROSSFADE_DOMAIN in .env
docker compose up -d
```

Then open `https://<your-domain>` and create the admin account. Caddy obtains the HTTPS certificate automatically.

- Images: `ghcr.io/tg099/crossfade` for `linux/amd64` and `linux/arm64`. Tags: `latest`, `X.Y` (bug fixes only),
  `X.Y.Z` and `testing` (newest release including pre-releases). A new image is built for every [GitHub release](https://github.com/tg099/crossfade/releases).
- Data (SQLite, encrypted logins) lives in the `crossfade-data` volume, so back it up regularly. Crossfade also
  keeps the last 10 database snapshots in `backups/` (one per start).
- **Never change `CROSSFADE_SECRET_KEY`**, or stored logins can no longer be decrypted.
- Already running a reverse proxy (Traefik, Nginx Proxy Manager …)? Start only the `crossfade` service, forward to
  port 8765, set `CROSSFADE_BASE_URL=https://…` and disable buffering for `/api/events` (server-sent events).
  Forwarded headers (`X-Forwarded-For`/`-Proto`) are trusted from private networks only; set `FORWARDED_ALLOW_IPS`
  to your proxy's address if other machines in your LAN can reach port 8765 directly.
- Unraid: see [docs/unraid.md](docs/unraid.md).
- Build from source: uncomment `build: .` in `docker-compose.yml` and run `docker compose up -d --build`.

## Setting up the services

Each person connects their own accounts in the app (**Account → Services**); the app walks through the steps.
Some services need a one-time setup by the admin first.

### Spotify (once, admin)

1. [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) → create an app (Web API).
2. Redirect URI: `https://<your-domain>/api/services/spotify/callback`
3. Under *User Management*, add the Spotify accounts of all users.
4. Enter the client ID in Crossfade under **Account → App credentials** (or set `SPOTIFY_CLIENT_ID`).

Spotify's limits for apps in development mode (since 2026): accounts need **Premium**, at most **5 users** per app.

### YouTube Music

- **Via the browser** (always works): on music.youtube.com, "Copy as cURL" on a request and paste it.
  The app shows the steps.
- **With a Google code** (optional): create a Google Cloud project with the YouTube Data API v3 and an OAuth
  client of type "TVs and Limited Input devices", then enter client ID/secret under **Account → App credentials**.
  Google sometimes rejects this flow for YouTube Music; the browser method still works then.

### Apple Music

Copy the `media-user-token` cookie from music.apple.com and paste it. No Apple developer account needed;
the app uses the same interface as Apple's web player.

## How syncing works

Each fade remembers which songs it knows and where it last saw them.

1. All collections are read; songs are recognised as the same song across services
   (ISRC, title, artist, duration).
2. New in a sending collection → added to all receiving ones
   (searched in the target's catalogue, results are cached).
3. Gone from a sending collection where it was seen before → removed everywhere
   (if *Pass on deletions* is on).
4. Uncertain matches end up as **open songs** in the fade: pick a suggestion, paste a link or ignore them.

### Safeguards

- The **first sync** only merges, it never deletes.
- Only songs that were previously **seen** in a collection can be deleted. A song that was just added but isn't
  shown by the service yet counts as "pending", not as deleted.
- An **empty list** from a service although songs are known there → the sync stops.
- **Safety limit** per fade: more deletions than allowed → waits for confirmation. Automatic syncs never confirm.
- Failed deletions are retried on the next sync instead of being reverted.
- **Preview** shows every sync without changing anything.

## Security

- Service logins are stored encrypted with a key derived from `CROSSFADE_SECRET_KEY`.
- Passwords hashed with scrypt, sessions as HttpOnly cookies (Secure over HTTPS), CSRF protection via a required
  header, strict Content Security Policy, login rate limiting.
- No self-registration: only admins create users. New users get a start password and must choose their own at
  first sign-in.

Found a vulnerability? Please [report it privately](https://github.com/tg099/crossfade/security/advisories/new),
not as a public issue.

## Development

```sh
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                 # engine and API tests
cd web && npm install && npm run dev          # frontend with proxy to :8765
.venv/bin/uvicorn crossfade.api.app:app --port 8765 --reload
```

Locally without Docker: `./start.sh` (builds the frontend if needed and serves http://127.0.0.1:8765).

Please don't test syncs against your real accounts: use a scratch `CROSSFADE_DATA_DIR` with test data.

| Path | Contents |
|---|---|
| `crossfade/engine.py` | Sync logic of a fade |
| `crossfade/identity.py` | Song identity across services, catalogue search |
| `crossfade/matcher.py` | Matching by title, artist, duration, ISRC |
| `crossfade/providers/` | One module per [supported service](#supported-services) |
| `crossfade/worker.py` | Schedule, background syncs, live progress |
| `crossfade/api/` | HTTP API, sign-in, user management |
| `web/` | UI (SvelteKit, static build) |

Environment variables: see `.env.example`. Additionally `CROSSFADE_DATA_DIR`, `CROSSFADE_BASE_URL`,
`CROSSFADE_WEB_DIR`, `CROSSFADE_SECURE_COOKIES` and `CROSSFADE_SOURCE_URL`.

**Running a modified version?** The AGPL asks you to offer its source to your users. Crossfade links
"Source code" on the sign-in page and in the account; point it to your fork with `CROSSFADE_SOURCE_URL`.

The UI uses a Liquid Glass design for phone and desktop, with light/dark/system themes and German and English
(texts in `web/src/lib/ui/lang/`, both files share the same keys).

### Releasing

The release tag is the only place the version lives; there is nothing to bump in the code.

1. GitHub → *Releases* → *Draft a new release* → new tag `vX.Y.Z` on `main` → *Generate release notes*
   (grouped by PR labels: `breaking`, `enhancement`, `bug`, `security`, `dependencies`) → *Publish*.
2. The *Release image* workflow runs the tests, builds the image, starts it once as a smoke test, scans it
   for critical vulnerabilities and only then pushes `X.Y.Z`, `X.Y`, `latest` and `testing` for amd64 and arm64
   (pre-releases, tag `vX.Y.Z-rc.N` + *Set as a pre-release*, only get `X.Y.Z-…` and `testing`), with SBOM and signed build provenance.

Verify an image: `gh attestation verify oci://ghcr.io/tg099/crossfade:X.Y.Z -R tg099/crossfade`.

## Contributing

Bugs and feature ideas are welcome as [issues](https://github.com/tg099/crossfade/issues), questions in
[Discussions](https://github.com/tg099/crossfade/discussions). Pull requests are squash-merged.

## Disclaimer

Crossfade is not affiliated with any of the supported services or the companies behind them. Services marked
*unofficial* in the [list](#supported-services) are accessed through their web interfaces, which can change at any
time; use Crossfade at your own risk and in line with each service's terms.

## License

[GNU Affero General Public License v3.0](LICENSE).
