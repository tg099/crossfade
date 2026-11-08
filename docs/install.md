# Installing Crossfade

Crossfade ships as one Docker image, `ghcr.io/tg099/crossfade` (amd64 and arm64). Pick the setup that fits you:

| You have … | Use |
|---|---|
| A server with a domain, nothing else running on ports 80/443 | [A: Docker Compose with automatic HTTPS](#a-docker-compose-with-automatic-https) |
| A reverse proxy already (Nginx Proxy Manager, Traefik, Caddy, SWAG …) | [B: Behind your existing reverse proxy](#b-behind-your-existing-reverse-proxy) |
| Just want to try it on your own computer or home network | [C: Quick start with `docker run`](#c-quick-start-with-docker-run) |
| An Unraid server | [docs/unraid.md](unraid.md) |

All three need [Docker](https://docs.docker.com/engine/install/) (Docker Desktop on macOS/Windows works too).
Crossfade needs no database server and no other containers: everything lives in one data folder.

## Before you start: the secret key

Crossfade stores your service logins encrypted. The key for that is `CROSSFADE_SECRET_KEY`. Generate it once:

```sh
openssl rand -base64 48
# or: python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Save it in your password manager. **Never change it later**: with a different key, the stored logins can no
longer be decrypted and every user has to connect their services again.

If you don't set a key, Crossfade generates one on the first start and saves it as `secret.key` in its data folder.
That's fine for trying it out, but then your backups of the data folder contain the key along with the logins.

## A: Docker Compose with automatic HTTPS

Crossfade plus [Caddy](https://caddyserver.com), which gets and renews the HTTPS certificate for you.

**You need:** a server reachable from the internet, a domain or subdomain (e.g. `crossfade.example.com`) whose DNS
`A`/`AAAA` record points to the server, and ports 80 and 443 open.

1. Create a folder and download the three files:
   ```sh
   mkdir crossfade && cd crossfade
   curl -O https://raw.githubusercontent.com/tg099/crossfade/main/docker-compose.yml \
        -O https://raw.githubusercontent.com/tg099/crossfade/main/Caddyfile \
        -o .env https://raw.githubusercontent.com/tg099/crossfade/main/.env.example
   ```
2. Open `.env` and fill in two lines:
   ```ini
   CROSSFADE_DOMAIN=crossfade.example.com
   CROSSFADE_SECRET_KEY=<the key from above>
   ```
3. Start it:
   ```sh
   docker compose up -d
   ```
4. Open `https://crossfade.example.com`. The first visit asks you to create the **admin account**.
   The first start can take a minute while Caddy fetches the certificate.

Check that everything is running: `docker compose ps` (both containers *healthy*/*running*) and
`docker compose logs -f` for the logs.

## B: Behind your existing reverse proxy

Run only the Crossfade container and point your proxy to port 8765.

`docker-compose.yml`:

```yaml
services:
  crossfade:
    image: ghcr.io/tg099/crossfade:latest
    container_name: crossfade
    restart: unless-stopped
    environment:
      CROSSFADE_BASE_URL: https://crossfade.example.com   # the public URL, exactly as users open it
      CROSSFADE_SECRET_KEY: <the key from above>
    volumes:
      - crossfade-data:/data
    ports:
      - "8765:8765"        # or leave this out and put the container in your proxy's Docker network

volumes:
  crossfade-data:
```

```sh
docker compose up -d
```

Then add a proxy host for your domain that forwards to `http://<server-ip>:8765` (or `http://crossfade:8765` in a
shared Docker network) and turns on HTTPS. Things to keep in mind:

- **Live progress** uses server-sent events on `/api/events`. Crossfade sends `X-Accel-Buffering: no`, so Nginx
  and Nginx Proxy Manager need nothing extra. With Caddy, use `flush_interval -1` (see the [`Caddyfile`](../Caddyfile));
  with other proxies, turn off response buffering for that path.
- **WebSockets** are not needed.
- Crossfade trusts `X-Forwarded-For`/`-Proto` only from private networks. If other machines in your LAN can reach
  port 8765 directly, set `FORWARDED_ALLOW_IPS` to your proxy's address.

## C: Quick start with `docker run`

For trying Crossfade on your own computer or in your home network, without a domain:

```sh
docker run -d --name crossfade --restart unless-stopped \
  -p 8765:8765 \
  -v crossfade-data:/data \
  ghcr.io/tg099/crossfade:latest
```

Open <http://localhost:8765> (or `http://<ip-of-the-machine>:8765` from another device) and create the admin account.

Limits without HTTPS: services that sign in through a redirect (Spotify) only accept `https://` addresses or
`http://127.0.0.1`, so set those up from the machine itself or move to setup A or B. The sign-ins that work by
pasting a cookie or a request from the browser work everywhere.

When you're happy with it, add `-e CROSSFADE_BASE_URL=https://…` and put it behind HTTPS (setup A or B); your data
stays in the `crossfade-data` volume.

## After the installation

1. Sign in as admin. Under **Account → Users** you create accounts for the other people; they get a start password
   and choose their own at the first sign-in.
2. Each person connects their own services under **Account → Services**. Some services need a one-time step by the
   admin first: see [Setting up the services](../README.md#setting-up-the-services).
3. Create your first fade and start with the **preview**: it shows what a sync would do without changing anything.

## Updating

```sh
docker compose pull && docker compose up -d       # setups A and B
```

For `docker run`: `docker pull ghcr.io/tg099/crossfade:latest`, then remove and start the container again with
the same command. Before each start Crossfade backs up its database and then migrates it if needed.

Admins see a hint on the home page when a new version is out (Crossfade asks the GitHub releases twice a day; set
`CROSSFADE_UPDATE_CHECK=false` to turn that off).

Which tag to use:

| Tag | Gets |
|---|---|
| `latest` | every release (recommended) |
| `X.Y` (e.g. `0.2`) | bug fixes for that version only |
| `X.Y.Z` | exactly that version, never changes |
| `testing` | newest release including pre-releases |

New releases and what changed: [GitHub releases](https://github.com/tg099/crossfade/releases).

## Backups

Everything lives in `/data` in the container (the `crossfade-data` volume): the SQLite database with fades and
encrypted logins, and `backups/` with database backups: the last 10 from starts, 14 daily ones and up to 20 made by
hand. Admins can make, download and restore backups in the app (**Account → Backups**), also from a downloaded file
on a new server. Keep your secret key separately: a backup never contains it, and without it the stored logins
can't be read.

To copy the whole volume instead:

```sh
docker run --rm -v crossfade-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/crossfade-$(date +%F).tar.gz -C /data .
```

(With Compose, the volume is named after the folder, e.g. `crossfade_crossfade-data`; `docker volume ls` shows it.)

## Using a folder instead of a volume

The container runs as user `99:100` (the Unraid default). If you mount a host folder instead of a volume, make it
writable for that user first:

```sh
mkdir -p ./data && sudo chown 99:100 ./data
# then in docker-compose.yml: - ./data:/data
```

## Troubleshooting

| Problem | Fix |
|---|---|
| Caddy can't get a certificate | DNS must point to the server and ports 80/443 must be reachable from the internet; see `docker compose logs caddy`. |
| Sign-in works but you're logged out again right away | `CROSSFADE_BASE_URL` starts with `https://` but you open the site over `http://`. Use the HTTPS address, or for local testing leave `CROSSFADE_BASE_URL` unset. |
| Progress bar doesn't move during a sync | Your proxy buffers `/api/events`; turn off buffering (see setup B). |
| *"Permission denied"* on `/data` | The mounted folder isn't writable for `99:100`; see [Using a folder](#using-a-folder-instead-of-a-volume). |
| Restoring from a file fails with *"HTTP 413"* | Your proxy limits uploads (Nginx: 1 MB by default). Allow bigger bodies, e.g. `client_max_body_size 0;` (in Nginx Proxy Manager under *Advanced*). |
| Restoring says *"saved with another secret key"* | The backup comes from an instance with a different key. Start Crossfade with that key, then restore. |
| *"CROSSFADE_SECRET_KEY changed?"* | The key differs from the one the logins were stored with. Put the old key back. |
| Spotify login fails with a redirect error | The redirect URI in the Spotify dashboard must be exactly `https://<your-domain>/api/services/spotify/callback`. |

Still stuck? Ask in [Discussions](https://github.com/tg099/crossfade/discussions) or open an
[issue](https://github.com/tg099/crossfade/issues).

---

<a href="https://buymeacoffee.com/tg099"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy me a coffee" height="48"></a>

Crossfade is free and built in my spare time. If it saves you some work, a coffee keeps it going. Thank you!
