"""Notifications to the outside: ntfy, webhooks (Discord and Slack understood) and e-mail.

Every user sets up their own channels and picks the events per channel. A lasting problem (a login
that stopped working, a sync waiting for confirmation) is reported once and again only after it was
resolved in between; `notify_state` remembers what was sent.
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

# event -> what it is about (the UI has its own texts for these)
EVENTS = ("auth", "expiring", "confirm", "issues", "errors", "social")
KINDS = ("ntfy", "webhook", "email")
NTFY_DEFAULT = "https://ntfy.sh"
TOPIC = re.compile(r"[A-Za-z0-9_-]{1,64}")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify")
# tests replace this to see what would go out
_deliver_hook = None


class ChannelError(ValueError):
    """A channel's settings are invalid."""


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
    return _deliver(db, channel, {"event": "test", "title": "Test from Crossfade",
                                  "message": "Notifications on this channel work.", "url": config.base_url,
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
def emit(db: Database, user_id: int, event: str, key: str, title: str, message: str, path: str = "/",
         value: str | None = None, urgent: bool = False, repeat: bool = False, sync: bool = False) -> bool:
    """Report something to a user's channels. With the same `key` (and `value`) it is sent only once
    until `resolve` clears it; `repeat` sends every time. Returns whether it was sent."""
    assert event in EVENTS, event
    if not repeat:
        old = db.one("SELECT value FROM notify_state WHERE user_id=? AND key=?", (user_id, key))
        if old is not None and old["value"] == value:
            return False
        db.run("INSERT INTO notify_state(user_id,key,value,sent_at) VALUES(?,?,?,?) ON CONFLICT(user_id,key) "
               "DO UPDATE SET value=excluded.value, sent_at=excluded.sent_at", (user_id, key, value, time.time()))
    _dispatch(db, user_id, {"event": event, "title": title, "message": message, "url": config.base_url + path,
                            "urgent": urgent, "ts": time.time(),
                            "tag": {"auth": "warning", "expiring": "hourglass", "confirm": "raised_hand",
                                    "issues": "mag", "errors": "x", "social": "wave"}[event]}, sync)
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
    emit(db, a["user_id"], "auth", f"auth:{account_id}", f"{name}: sign in again",
         f"Your {name} login stopped working, so syncs with it are paused. {message or ''}".strip(),
         path="/services?service=" + a["service"], urgent=True)


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
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
    emit(db, a["user_id"], "expiring", f"expiring:{account_id}", f"{name} login ends {when}",
         f"Your {name} login ends {when}. Renew it in Crossfade so your syncs keep running.",
         path="/services?service=" + a["service"], value=str(int(expires_at)))


def run_finished(db: Database, line_id: int, status: str, message: str | None, trigger: str) -> None:
    """After a real sync: tell the owner when it waits for them or keeps failing, and every
    participant about new open songs in their own collections."""
    line = db.one("SELECT id, user_id, name FROM lines WHERE id=?", (line_id,))
    if not line or trigger == "preview":
        return
    owner, name, path = line["user_id"], line["name"], f"/fades/{line_id}"
    if status in ("needs_confirm", "blocked"):
        emit(db, owner, "confirm", f"confirm:{line_id}", f"{name} is waiting for you",
             message or "A sync needs your confirmation.", path=path, urgent=True)
    elif status in ("ok", "partial"):
        resolve(db, owner, f"confirm:{line_id}")
    if status == "error":
        recent = [r["status"] for r in db.all("SELECT status FROM runs WHERE line_id=? AND trigger<>'preview' "
                                              "ORDER BY id DESC LIMIT 3", (line_id,))]
        if len(recent) == 3 and all(s == "error" for s in recent):
            emit(db, owner, "errors", f"errors:{line_id}", f"{name} keeps failing",
                 f"The last three syncs failed. Last error: {message or 'unknown'}", path=path)
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
                emit(db, uid, "issues", f"issues:{line_id}", f"{name}: {new} song{'s' if new != 1 else ''} to check",
                     f"Crossfade couldn't find {new} song{'s' if new != 1 else ''} for certain. "
                     "Pick the right version in the fade.", path=path, value=str(n))
            elif n == 0:
                resolve(db, uid, f"issues:{line_id}")
            else:
                remember(db, uid, f"issues:{line_id}", str(n))


def social(db: Database, user_id: int, title: str, message: str, path: str = "/") -> None:
    emit(db, user_id, "social", "social", title, message, path=path, repeat=True)


def person(db: Database, user_id: int) -> str:
    return db.val("SELECT display_name FROM users WHERE id=?", (user_id,)) or "Someone"
