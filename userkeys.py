"""A user's own AI provider key, encrypted before it is stored.

The encryption key comes from NS_SECRET (or, if that isn't set, from the database password),
so it lives only in the server's environment and never in the database next to the data.
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

import projects

PROVIDERS = ("gemini", "groq", "openrouter")


def _fernet() -> Fernet:
    secret = os.environ.get("NS_SECRET") or os.environ.get("DATABASE_PASSWORD") or os.environ.get("DATABASE_URL") or "local"
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("naturalsketch-user-keys:" + secret).encode()).digest()))


def save(owner: str, provider: str, model: str, key: str) -> None:
    projects.set_user_key(owner, provider, model, _fernet().encrypt(key.encode()).decode(), "…" + key[-4:])


def load(owner: str | None) -> dict | None:
    """{"provider", "model", "key"} for use in a build, or None."""
    row = projects.get_user_key(owner) if owner else None
    if not row:
        return None
    try:
        key = _fernet().decrypt(row["key_enc"].encode()).decode()
    except InvalidToken:  # the server secret changed: the stored key can't be read any more
        return None
    return {"provider": row["provider"], "model": row["model"], "key": key}


def info(owner: str | None) -> dict | None:
    """What the account page may show: never the key itself, only its last four characters."""
    row = projects.get_user_key(owner) if owner else None
    return {"provider": row["provider"], "model": row["model"], "hint": row["hint"]} if row else None
