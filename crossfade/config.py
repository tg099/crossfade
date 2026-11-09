"""Runtime configuration from environment variables (see .env.example)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def env(name: str, default: str | None = None) -> str | None:
    """Environment variable CROSSFADE_<name>."""
    return os.environ.get(f"CROSSFADE_{name}", default)


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    data_dir: Path
    base_url: str               # public URL, e.g. https://crossfade.example.com
    secret_key: str             # encrypts stored credentials; never change after first start
    secure_cookies: bool
    web_dir: Path               # built Svelte app
    session_days: int
    worker_threads: int
    scheduler: bool             # run lines on their interval
    backup_on_start: bool       # copy the database to data/backups on every start
    backup_daily: bool          # and once a day while running
    update_check: bool          # ask GitHub twice a day whether there is a newer release
    version: str                # set at image build time
    mcp_enabled: bool           # personal-token MCP endpoint (no access until a user creates a token)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "crossfade.sqlite3"


def _secret(data_dir: Path) -> str:
    key = env("SECRET_KEY")
    if key:
        return key
    f = data_dir / "secret.key"
    if f.exists():
        return f.read_text().strip()
    key = secrets.token_urlsafe(48)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # never readable by others, not even briefly
    with os.fdopen(fd, "w") as out:
        out.write(key)
    return key


DEFAULT_SOURCE = "https://github.com/tg099/crossfade"


def load() -> Config:
    root = Path(__file__).resolve().parent.parent
    data_dir = Path(env("DATA_DIR", root / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    base_url = env("BASE_URL", "http://127.0.0.1:8765").rstrip("/")
    return Config(
        data_dir=data_dir,
        base_url=base_url,
        secret_key=_secret(data_dir),
        secure_cookies=_bool(env("SECURE_COOKIES"), base_url.startswith("https://")),
        web_dir=Path(env("WEB_DIR", root / "web" / "build")),
        session_days=int(env("SESSION_DAYS", "30")),
        worker_threads=int(env("WORKER_THREADS", "3")),
        scheduler=_bool(env("SCHEDULER"), True),
        backup_on_start=_bool(env("BACKUP_ON_START"), True),
        backup_daily=_bool(env("BACKUP_DAILY"), True),
        update_check=_bool(env("UPDATE_CHECK"), True),
        version=env("VERSION", "dev"),
        mcp_enabled=_bool(env("MCP_ENABLED"), True),
    )


config = load()
