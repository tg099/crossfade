"""Notifications to the outside: ntfy, webhooks (Discord and Slack understood) and e-mail.

Every user sets up their own channels and picks the events per channel. A lasting problem (a login
that stopped working, a sync waiting for confirmation) is reported once and again only after it was
resolved in between; `notify_state` remembers what was sent.

Messages go out in the recipient's language (their setting, else the browser language they last used),
unlike the rest of the backend, which speaks English only. Details that come from a service (an error
text) stay as the service said them.
"""
from __future__ import annotations

import json
import logging
import re
import smtplib
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from urllib.parse import urlparse

import httpx

from .config import config
from .db import Database
from .models import SERVICE_NAMES
from .security import decrypt, encrypt

log = logging.getLogger("crossfade.notify")

# event -> what it is about (the UI has its own texts for these); "update" only reaches admins
EVENTS = ("auth", "expiring", "confirm", "issues", "errors", "social", "update")
KINDS = ("ntfy", "webhook", "email")
NTFY_DEFAULT = "https://ntfy.sh"
TOPIC = re.compile(r"[A-Za-z0-9_-]{1,64}")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify")
# tests replace this to see what would go out
_deliver_hook = None


class ChannelError(ValueError):
    """A channel's settings are invalid."""


# ---------------------------------------------------------------------- texts
LANGS = ("en", "de")


def _n(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


# key -> lang -> (title, message); a value is a format string or a function of the variables
TEXTS: dict[str, dict[str, tuple]] = {
    "test": {
        "en": ("Test from Crossfade", "Notifications on this channel work."),
        "de": ("Test von Crossfade", "Benachrichtigungen über diesen Kanal kommen an."),
    },
    "smtp_test": {
        "en": ("Test from Crossfade", "The mail server works."),
        "de": ("Test von Crossfade", "Der Mailserver funktioniert."),
    },
    "auth": {
        "en": ("{service}: sign in again", "Your {service} login stopped working, so syncs with it are paused. {detail}"),
        "de": ("{service}: bitte neu anmelden",
               "Deine Anmeldung bei {service} geht nicht mehr, deshalb pausieren die Syncs damit. {detail}"),
    },
    "expiring": {
        "en": (lambda v: f"{v['service']} login ends {_when('en', v['days'])}",
               lambda v: f"Your {v['service']} login ends {_when('en', v['days'])}. "
                         "Renew it in Crossfade so your syncs keep running."),
        "de": (lambda v: f"{v['service']}-Anmeldung läuft {_when('de', v['days'])} ab",
               lambda v: f"Deine Anmeldung bei {v['service']} läuft {_when('de', v['days'])} ab. "
                         "Erneuere sie in Crossfade, damit deine Syncs weiterlaufen."),
    },
    "confirm_held": {
        "en": ("{fade} is waiting for you",
               lambda v: f"{v['n']} {_n(v['n'], 'deletion is', 'deletions are')} waiting for your confirmation."),
        "de": ("{fade} wartet auf dich",
               lambda v: f"{v['n']} {_n(v['n'], 'Löschung wartet', 'Löschungen warten')} auf deine Bestätigung."),
    },
    "confirm_blocked": {
        "en": ("{fade} is waiting for you", "A sync was stopped to be safe. {detail}"),
        "de": ("{fade} wartet auf dich", "Ein Sync wurde zur Sicherheit angehalten. {detail}"),
    },
    "errors": {
        "en": ("{fade} keeps failing", "The last three syncs failed. Last error: {detail}"),
        "de": ("{fade} scheitert immer wieder", "Die letzten drei Syncs sind fehlgeschlagen. Letzter Fehler: {detail}"),
    },
    "issues": {
        "en": (lambda v: f"{v['fade']}: {v['n']} {_n(v['n'], 'song', 'songs')} to check",
               lambda v: f"Crossfade couldn't find {v['n']} {_n(v['n'], 'song', 'songs')} for certain. "
                         "Pick the right version in the fade."),
        "de": (lambda v: f"{v['fade']}: {v['n']} {_n(v['n'], 'Song', 'Songs')} zum Prüfen",
               lambda v: f"Crossfade hat {v['n']} {_n(v['n'], 'Song', 'Songs')} nicht sicher gefunden. "
                         "Such dir im Fade die richtige Version aus."),
    },
    "friend_request": {
        "en": ("New friend request", "{name} (@{username}) wants to be friends on Crossfade."),
        "de": ("Neue Freundschaftsanfrage", "{name} (@{username}) möchte auf Crossfade mit dir befreundet sein."),
    },
    "friend_accepted": {
        "en": ("{name} is your friend now", "{name} accepted your friend request."),
        "de": ("{name} und du seid jetzt befreundet", "{name} hat deine Freundschaftsanfrage angenommen."),
    },
    "fade_invite": {
        "en": ("Invite to {fade}", "{name} invites you to the fade {fade}."),
        "de": ("Einladung zu {fade}", "{name} lädt dich in den Fade {fade} ein."),
    },
    "fade_request": {
        "en": ("{name} wants to join {fade}", "{name} picked a collection for {fade}. Approve it in the fade."),
        "de": ("{name} möchte zu {fade}", "{name} hat eine Sammlung für {fade} ausgesucht. Bestätige sie im Fade."),
    },
    "fade_joined": {
        "en": ("You're in {fade}", "{name} let your collection into {fade}."),
        "de": ("Du bist in {fade}", "{name} hat deine Sammlung in {fade} aufgenommen."),
    },
    "update": {
        "en": ("Crossfade {latest} is out", "You're running {current}. See what's new in the release notes."),
        "de": ("Crossfade {latest} ist da", "Du nutzt gerade {current}. Was neu ist, steht in den Release Notes."),
    },
    "assistant_confirm": {
        "en": ("{connection} is waiting for you",
               lambda v: f"Your assistant wants to {ASSISTANT_DELETES['en'].get(v['op'], 'delete something')}. "
                         "Confirm or cancel it in Crossfade."),
        "de": ("{connection} wartet auf dich",
               lambda v: f"Dein Assistent möchte {ASSISTANT_DELETES['de'].get(v['op'], 'etwas löschen')}. "
                         "Bestätige oder verwirf das in Crossfade."),
    },
}

# what an assistant's proposal would delete, for the approval notification
ASSISTANT_DELETES: dict[str, dict[str, str]] = {
    "en": {"fade_delete": "delete a fade", "remove_songs": "remove songs", "dedupe": "remove duplicates",
           "playlists": "delete or merge playlists", "restore": "reset a collection to an earlier state",
           "sync": "carry out held deletions", "fade_create": "create a fade that syncs deletions",
           "fade_update": "let a fade delete more"},
    "de": {"fade_delete": "einen Fade löschen", "remove_songs": "Songs entfernen", "dedupe": "Duplikate entfernen",
           "playlists": "Playlists löschen oder zusammenführen",
           "restore": "eine Sammlung auf einen früheren Stand zurücksetzen",
           "sync": "zurückgehaltene Löschungen ausführen", "fade_create": "einen Fade mit Löschabgleich anlegen",
           "fade_update": "einem Fade mehr Löschungen erlauben"},
}


def _when(lang: str, days: int) -> str:
    if lang == "de":
        return "heute" if days == 0 else "morgen" if days == 1 else f"in {days} Tagen"
    return "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"


def language(db: Database, user_id: int) -> str:
    """The language a user reads their notifications in: their setting, else their browser's."""
    row = db.one("SELECT lang, browser_lang FROM users WHERE id=?", (user_id,))
    if not row:
        return "en"
    for v in (row["lang"], row["browser_lang"]):
        if v in LANGS:
            return v
    return "en"


def text(lang: str, key: str, **v) -> tuple[str, str]:
    """Title and message of a notification in this language."""
    entry = TEXTS[key].get(lang) or TEXTS[key]["en"]
    out = []
    for part in entry:
        s = part(v) if callable(part) else part.format(**v)
        out.append(" ".join(s.split()))      # an empty {detail} leaves no stray space
    return out[0], out[1]


# ---------------------------------------------------------------------- channel settings
def clean_config(kind: str, cfg: dict) -> dict:
    """Validate what the user entered for a channel; returns the stored form."""
    if kind == "ntfy":
        server = (cfg.get("server") or NTFY_DEFAULT).strip().rstrip("/")
        _http_url(server, "ntfy server")
        topic = (cfg.get("topic") or "").strip()
        if not TOPIC.fullmatch(topic):
            raise ChannelError("Topic: 1–64 letters, digits, hyphens or underscores.")
        token = (cfg.get("token") or "").strip() or None
        return {"server": server, "topic": topic, "token": token}
    if kind == "webhook":
        url = (cfg.get("url") or "").strip()
        _http_url(url, "Webhook URL")
        return {"url": url}
    if kind == "email":
        to = (cfg.get("to") or "").strip()
        if not EMAIL.fullmatch(to) or len(to) > 254:
            raise ChannelError("That doesn't look like an e-mail address.")
        return {"to": to}
    raise ChannelError("Unknown channel type.")


def _http_url(url: str, what: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc or len(url) > 2000:
        raise ChannelError(f"{what} must be an http(s) address.")


def public_config(kind: str, cfg: dict) -> dict:
    """What the UI may show again: never the access token, a webhook only shortened (it is the secret)."""
    if kind == "ntfy":
        return {"server": cfg["server"], "topic": cfg["topic"], "token_set": bool(cfg.get("token"))}
    if kind == "webhook":
        u = urlparse(cfg["url"])
        return {"url_hint": f"{u.scheme}://{u.netloc}/…"}
    return {"to": cfg["to"]}


def webhook_flavor(url: str) -> str:
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path
    if host.endswith(("discord.com", "discordapp.com")) and path.startswith("/api/webhooks/"):
        return "discord"
    if host == "hooks.slack.com":
        return "slack"
    return "json"


# ---------------------------------------------------------------------- e-mail (admin)
SMTP_KEYS = ("host", "port", "security", "username", "sender")


def smtp_settings(db: Database) -> dict | None:
    s = db.setting("smtp")
    if not s or not s.get("host"):
        return None
    blob = db.setting("smtp_password")
    return {**s, "password": decrypt(blob) if blob else None}


def save_smtp(db: Database, body: dict) -> None:
    host = (body.get("host") or "").strip()
    if not host:
        db.set_setting("smtp", None)
        db.set_setting("smtp_password", None)
        return
    security = body.get("security") or "starttls"
    if security not in ("starttls", "ssl", "none"):
        raise ChannelError("Security: starttls, ssl or none.")
    port = int(body.get("port") or (465 if security == "ssl" else 587))
    sender = (body.get("sender") or "").strip()
    if not EMAIL.search(sender):
        raise ChannelError("The sender needs an e-mail address.")
    db.set_setting("smtp", {"host": host, "port": port, "security": security,
                            "username": (body.get("username") or "").strip() or None, "sender": sender})
    if body.get("password") is not None:     # None = keep, "" = remove
        db.set_setting("smtp_password", encrypt(body["password"]) if body["password"] else None)


# ---------------------------------------------------------------------- delivery
def _send(kind: str, cfg: dict, msg: dict, smtp: dict | None) -> None:
    title, text, link = msg["title"], msg["message"], msg.get("url")
    if kind == "ntfy":
        headers = {"Authorization": f"Bearer {cfg['token']}"} if cfg.get("token") else {}
        body = {"topic": cfg["topic"], "title": title, "message": text, "tags": [msg.get("tag") or "musical_note"],
                "priority": 4 if msg.get("urgent") else 3}
        if link:
            body["click"] = link
        r = httpx.post(cfg["server"] + "/", json=body, headers=headers, timeout=20)
        r.raise_for_status()
    elif kind == "webhook":
        flavor = webhook_flavor(cfg["url"])
        line = f"{text}\n{link}" if link else text
        if flavor == "discord":
            body = {"content": f"**{title}**\n{line}"[:2000], "username": "Crossfade"}
        elif flavor == "slack":
            body = {"text": f"*{title}*\n{line}"}
        else:
            body = {"source": "crossfade", "event": msg["event"], "title": title, "message": text, "url": link,
                    "ts": msg["ts"]}
        r = httpx.post(cfg["url"], json=body, timeout=20)
        r.raise_for_status()
    elif kind == "email":
        if not smtp:
            raise RuntimeError("E-mail isn't set up on this instance.")
        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = f"Crossfade: {title}", smtp["sender"], cfg["to"]
        m.set_content(f"{text}\n\n{link}\n" if link else f"{text}\n")
        ctx = ssl.create_default_context()
        if smtp["security"] == "ssl":
            server = smtplib.SMTP_SSL(smtp["host"], smtp["port"], timeout=20, context=ctx)
        else:
            server = smtplib.SMTP(smtp["host"], smtp["port"], timeout=20)
        with server:
            if smtp["security"] == "starttls":
                server.starttls(context=ctx)
            if smtp.get("username"):
                server.login(smtp["username"], smtp.get("password") or "")
            server.send_message(m)


def _deliver(db: Database, channel: dict, msg: dict) -> str | None:
    """Send one message on one channel; returns the error text (None when it went out)."""
    try:
        cfg = decrypt(channel["config"])
        if _deliver_hook:
            _deliver_hook(channel, cfg, msg)
        else:
            _send(channel["kind"], cfg, msg, smtp_settings(db) if channel["kind"] == "email" else None)
        err = None
    except httpx.HTTPStatusError as e:
        err = f"HTTP {e.response.status_code}"
    except Exception as e:  # noqa: BLE001 – any failure is shown on the channel, never raised
        err = f"{type(e).__name__}: {e}"[:300]
    db.run("UPDATE notify_channels SET last_sent_at=?, last_error=? WHERE id=?",
           (time.time() if err is None else channel["last_sent_at"], err, channel["id"]))
    if err:
        log.warning("notification on channel %s failed: %s", channel["id"], err)
    return err


def send_test(db: Database, channel: dict) -> str | None:
    title, message = text(language(db, channel["user_id"]), "test")
    return _deliver(db, channel, {"event": "test", "title": title, "message": message, "url": config.base_url,
                                  "ts": time.time()})


def _dispatch(db: Database, user_id: int, msg: dict, sync: bool = False) -> None:
    channels = [c for c in db.all("SELECT * FROM notify_channels WHERE user_id=? AND enabled=1", (user_id,))
                if msg["event"] in json.loads(c["events"])]
    for ch in channels:
        if sync:
            _deliver(db, ch, msg)
        else:
            _pool.submit(_deliver, db, ch, msg)


# ---------------------------------------------------------------------- events
def emit(db: Database, user_id: int, event: str, key: str, msg: str, path: str = "/",
         value: str | None = None, urgent: bool = False, repeat: bool = False, sync: bool = False,
         url: str | None = None, **v) -> bool:
    """Report something to a user's channels: `msg` names the text in TEXTS, filled with `v` in the
    user's language. With the same `key` (and `value`) it is sent only once until `resolve` clears
    it; `repeat` sends every time. Returns whether it was sent."""
    assert event in EVENTS, event
    title, message = text(language(db, user_id), msg, **v)
    if not repeat:
        old = db.one("SELECT value FROM notify_state WHERE user_id=? AND key=?", (user_id, key))
        if old is not None and old["value"] == value:
            return False
        db.run("INSERT INTO notify_state(user_id,key,value,sent_at) VALUES(?,?,?,?) ON CONFLICT(user_id,key) "
               "DO UPDATE SET value=excluded.value, sent_at=excluded.sent_at", (user_id, key, value, time.time()))
    _dispatch(db, user_id, {"event": event, "title": title, "message": message, "url": url or config.base_url + path,
                            "urgent": urgent, "ts": time.time(),
                            "tag": {"auth": "warning", "expiring": "hourglass", "confirm": "raised_hand",
                                    "issues": "mag", "errors": "x", "social": "wave", "update": "tada"}[event]}, sync)
    return True


def resolve(db: Database, user_id: int, key: str) -> None:
    db.run("DELETE FROM notify_state WHERE user_id=? AND key=?", (user_id, key))


def remember(db: Database, user_id: int, key: str, value: str) -> None:
    """Store the current value without sending (a count that went down)."""
    db.run("INSERT INTO notify_state(user_id,key,value,sent_at) VALUES(?,?,?,?) ON CONFLICT(user_id,key) "
           "DO UPDATE SET value=excluded.value", (user_id, key, value, time.time()))


def safely(fn, *args, **kwargs) -> None:
    """Notifications never break what triggered them."""
    try:
        fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        log.exception("notification failed")


# -- the things Crossfade reports ------------------------------------------------------------
def _account(db: Database, account_id: int) -> dict | None:
    return db.one("SELECT id, user_id, service, display_name, status FROM accounts WHERE id=?", (account_id,))


def account_failed(db: Database, account_id: int, message: str | None) -> None:
    a = _account(db, account_id)
    if not a:
        return
    name = SERVICE_NAMES.get(a["service"], a["service"])
    emit(db, a["user_id"], "auth", f"auth:{account_id}", "auth", path="/services?service=" + a["service"],
         urgent=True, service=name, detail=message or "")


def account_ok(db: Database, account_id: int) -> None:
    a = _account(db, account_id)
    if a:
        resolve(db, a["user_id"], f"auth:{account_id}")


def account_expiring(db: Database, account_id: int, expires_at: float) -> None:
    a = _account(db, account_id)
    if not a:
        return
    name = SERVICE_NAMES.get(a["service"], a["service"])
    days = max(0, round((expires_at - time.time()) / 86400))
    emit(db, a["user_id"], "expiring", f"expiring:{account_id}", "expiring", path="/services?service=" + a["service"],
         value=str(int(expires_at)), service=name, days=days)


def run_finished(db: Database, line_id: int, status: str, message: str | None, trigger: str) -> None:
    """After a real sync: tell the owner when it waits for them or keeps failing, and every
    participant about new open songs in their own collections."""
    line = db.one("SELECT id, user_id, name, last_summary FROM lines WHERE id=?", (line_id,))
    if not line or trigger == "preview":
        return
    owner, name, path = line["user_id"], line["name"], f"/fades/{line_id}"
    if status == "needs_confirm":
        held = (json.loads(line["last_summary"] or "{}") or {}).get("held") or 0
        emit(db, owner, "confirm", f"confirm:{line_id}", "confirm_held", path=path, urgent=True, fade=name, n=held)
    elif status == "blocked":
        emit(db, owner, "confirm", f"confirm:{line_id}", "confirm_blocked", path=path, urgent=True, fade=name,
             detail=message or "")
    elif status in ("ok", "partial"):
        resolve(db, owner, f"confirm:{line_id}")
    if status == "error":
        recent = [r["status"] for r in db.all("SELECT status FROM runs WHERE line_id=? AND trigger<>'preview' "
                                              "ORDER BY id DESC LIMIT 3", (line_id,))]
        if len(recent) == 3 and all(s == "error" for s in recent):
            emit(db, owner, "errors", f"errors:{line_id}", "errors", path=path, fade=name, detail=message or "–")
    elif status in ("ok", "partial", "needs_confirm"):
        resolve(db, owner, f"errors:{line_id}")
    if status in ("ok", "partial", "needs_confirm"):
        rows = db.all("""SELECT a.user_id, COUNT(*) AS n FROM issues i JOIN line_members m ON m.id=i.member_id
                         JOIN accounts a ON a.id=m.account_id WHERE i.line_id=? AND i.kind='unmatched'
                         GROUP BY a.user_id""", (line_id,))
        counts = {r["user_id"]: r["n"] for r in rows}
        for uid in {r["user_id"] for r in db.all("SELECT user_id FROM notify_state WHERE key=?",
                                                  (f"issues:{line_id}",))} | set(counts):
            n = counts.get(uid, 0)
            old = db.val("SELECT value FROM notify_state WHERE user_id=? AND key=?", (uid, f"issues:{line_id}"))
            if n > int(old or 0):
                new = n - int(old or 0)
                emit(db, uid, "issues", f"issues:{line_id}", "issues", path=path, value=str(n), fade=name, n=new)
            elif n == 0:
                resolve(db, uid, f"issues:{line_id}")
            else:
                remember(db, uid, f"issues:{line_id}", str(n))


def assistant_waiting(db: Database, user_id: int, action_id: str, operation: str, connection: str) -> None:
    """An assistant prepared something that deletes: only the person can let it happen."""
    emit(db, user_id, "confirm", f"mcp:{action_id}", "assistant_confirm", path=f"/assistant?action={action_id}",
         urgent=True, repeat=True, connection=connection, op=operation)


def social(db: Database, user_id: int, msg: str, path: str = "/", **v) -> None:
    emit(db, user_id, "social", "social", msg, path=path, repeat=True, **v)


def person(db: Database, user_id: int) -> str:
    return db.val("SELECT display_name FROM users WHERE id=?", (user_id,)) or "Someone"
