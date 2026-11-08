"""Password hashing, opaque tokens and encryption of stored service credentials."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .config import config

_SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:  # noqa: BLE001
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Never change this salt: every stored login is encrypted with a key derived from it.
_KEY_SALT = "crossfade:"
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256((_KEY_SALT + config.secret_key).encode()).digest()))


def encrypt(data: Any) -> str:
    return _fernet.encrypt(json.dumps(data).encode()).decode()


def decrypt(blob: str | None) -> Any:
    if not blob:
        return None
    try:
        return json.loads(_fernet.decrypt(blob.encode()))
    except InvalidToken as e:
        raise RuntimeError("Stored logins can't be decrypted. Was "
                           "CROSSFADE_SECRET_KEY changed?") from e
