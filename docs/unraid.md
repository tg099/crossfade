# Crossfade on Unraid

Flow: **GitHub release → GitHub Actions builds the image → Unraid pulls it → updates run automatically at night.**

```
GitHub release v0.2.0 published
        │
        ▼
GitHub Actions: tests ─► image ghcr.io/tg099/crossfade:0.2.0, :0.2 and :latest
        │
        ▼
Unraid (CA Auto Update, 04:00): new :latest? ─► restart container
        │
        ▼
Crossfade starts: database backup ─► migrations ─► running
```

## 1. Create the container

1. Download [`unraid/crossfade.xml`](../unraid/crossfade.xml) and copy it to
   `/boot/config/plugins/dockerMan/templates-user/my-Crossfade.xml` (e.g. via the `flash` share).
2. **Docker → Add Container → Template: Crossfade.**
3. Fill in:
   - **Public URL**: `https://crossfade.yourdomain.com`
   - **Secret key**: generate once and keep it safe (e.g. in your password manager):
     ```sh
     python3 -c "import secrets; print(secrets.token_urlsafe(48))"
     ```
     or on Unraid: `openssl rand -base64 48`
4. **Apply.** Data then lives in `/mnt/user/appdata/crossfade` (included in the Appdata Backup plugin).

## 2. Nginx Proxy Manager

*Hosts → Proxy Hosts → Add Proxy Host*

| Field | Value |
|---|---|
| Domain Names | `crossfade.yourdomain.com` |
| Scheme / Forward Hostname / Port | `http` / IP of your Unraid server / `8765` |
| Block Common Exploits | on |
| Websockets Support | not needed |
| SSL | request a Let's Encrypt certificate, **Force SSL**, **HTTP/2**, **HSTS** on |

That's all: Crossfade marks its live progress (server-sent events) with `X-Accel-Buffering: no` itself, and Nginx
respects that. If NPM and Crossfade share a custom Docker network, you can use `Crossfade` as the hostname instead
of the IP.

Services that sign in through a redirect need your public URL in their redirect URI, e.g.
`https://crossfade.yourdomain.com/api/services/<service>/callback`. Which services need this and how to set them up:
[Setting up the services](../README.md#setting-up-the-services).

## 3. Automatic updates

Plugin **CA Auto Update Applications** (from Community Apps) → *Docker Auto Update Settings*:

- *Update Check Frequency*: daily, e.g. 04:00
- *Crossfade*: **Auto Update** on
- *Send notification*: on (so you see when a new version was installed)

Unraid then pulls `:latest` every night after a new release and restarts the container.
Syncs that are running at that moment are aborted and cleanly retried at the next interval.

**Prefer to decide yourself?** Use a fixed version in the template instead of `:latest`, e.g. `:0.2`
(only gets bug-fix releases 0.2.x), or leave Auto Update off for Crossfade and click "Update" in the Docker tab
when you want.

**Want to test new versions early?** Use `:testing` instead of `:latest`. It always points to the newest
release, including pre-releases (`X.Y.Z-rc.N`), and catches up with `:latest` on every stable release.

## 4. Backups and rolling back

- On **every start**, Crossfade writes a consistent copy of the database to
  `/mnt/user/appdata/crossfade/backups/` (the last 10, schema version in the file name).
- **Back to an older version:** edit the container → set *Repository* to the old version
  (e.g. `ghcr.io/tg099/crossfade:0.1.3`) → *Apply*. If the new version changed the database schema
  (`schemaN` in the file name went up), stop the container first and copy the matching backup back:
  ```sh
  cd /mnt/user/appdata/crossfade
  cp backups/crossfade-<timestamp>-schema<old>.sqlite3 crossfade.sqlite3
  rm -f crossfade.sqlite3-wal crossfade.sqlite3-shm
  ```
