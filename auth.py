"""Accounts, via Supabase Auth.

The browser signs people in with supabase-js (email + password, or Google). It hands the
access token to the server once (POST /api/session), which keeps it in an HttpOnly cookie on
the builder's own domain. Every API call is then checked here: the token is a JWT signed by
Supabase, verified with the project's public signing keys, so no secret is needed.

Without SUPABASE_URL and SUPABASE_ANON_KEY the server runs in local single-user mode.
"""

import os
import re

import jwt
from jwt import PyJWKClient


def _supabase_url() -> str:
    """SUPABASE_URL, or worked out from the Supabase pooler DATABASE_URL (postgres.<project>@...)."""
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if url:
        return url
    match = re.search(r"postgres\.([a-z0-9]{15,30})@[^/]*supabase\.com", os.environ.get("DATABASE_URL", ""))
    return f"https://{match.group(1)}.supabase.co" if match else ""


SUPABASE_URL = _supabase_url()
# The public "anon" / "publishable" key: safe to show in the browser.
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
# Only for Supabase projects still signing tokens with the legacy shared secret (HS256).
JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
# Accounts with no daily limit; the first of them also inherits projects made before accounts existed.
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("NS_ADMIN_EMAIL", "").split(",") if e.strip()}
# Builds + chat edits per account per day (0 = unlimited). Everyone shares the server's AI keys.
DAILY_LIMIT = int(os.environ.get("NS_DAILY_LIMIT", "20") or 0)

ENABLED = bool(SUPABASE_URL and SUPABASE_ANON_KEY)
LOCAL_USER = {"id": None, "email": "", "name": "You", "avatar": "", "admin": True}

_jwks = PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=3600) if SUPABASE_URL else None


def verify(token: str | None) -> dict | None:
    """The signed-in user for this access token, or None if it's missing, expired or forged."""
    if not ENABLED or not token:
        return None
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg == "HS256":
            if not JWT_SECRET:
                return None
            key = JWT_SECRET
        elif alg in ("ES256", "RS256"):
            key = _jwks.get_signing_key_from_jwt(token).key
        else:
            return None
        claims = jwt.decode(
            token, key, algorithms=[alg], audience="authenticated",
            issuer=f"{SUPABASE_URL}/auth/v1", leeway=30,
        )
    except Exception:
        return None
    if claims.get("role") != "authenticated" or not claims.get("sub"):
        return None
    meta = claims.get("user_metadata") or {}
    email = (claims.get("email") or "").lower()
    return {
        "id": claims["sub"],
        "email": email,
        "name": meta.get("full_name") or meta.get("name") or email.split("@")[0],
        "avatar": meta.get("avatar_url") or meta.get("picture") or "",
        "admin": email in ADMIN_EMAILS,
        "exp": int(claims.get("exp") or 0),
    }


def public_config() -> dict | None:
    """What the browser needs to start supabase-js."""
    return {"url": SUPABASE_URL, "anon_key": SUPABASE_ANON_KEY} if ENABLED else None
