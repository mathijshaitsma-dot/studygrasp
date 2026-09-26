"""Accounts en sessies.

Bewust zonder externe afhankelijkheden: wachtwoorden worden gehasht met scrypt
uit de standaardbibliotheek en sessies zijn willekeurige tokens. Geen cookies
maar een Bearer-token, omdat de frontend op een andere origin kan draaien dan de
API (en cookies dan alsnog CORS-gedoe geven).

Opslag via cache_store, zodat accounts net als de rest meeliften op de gedeelde
(L2-)opslag als die is ingesteld:
  users/{user_id}          -> het account
  user_email/{email_hash}  -> index e-mail -> user_id (voor inloggen)
  sessions/{token_hash}    -> lopende sessie

Van het token bewaren we alleen de hash. Wie de opslag in handen krijgt, kan
daarmee dus niet alsnog als iemand anders inloggen.

Dit bestand importeert bewust NIETS uit core: core heeft auth nodig (voor het
quotum), dus andersom zou een importcyclus geven.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from typing import Any, Optional

from fastapi import HTTPException, Request

import cache_store
import rate_limit

SESSION_DAYS = int(os.getenv("SESSION_DAYS", "60"))
MIN_PASSWORD_LEN = 8
# scrypt-parameters: n=2^14 met r=8 kost ~16MB en ~30ms per poging. Genoeg om
# brute-force duur te maken, laag genoeg om inloggen niet traag te laten voelen.
# maxmem moet expliciet: OpenSSL staat standaard maar 32MB toe en weigert anders.
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32, "maxmem": 64 * 1024 * 1024}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def _err(status: int, code: str, message: str):
    raise HTTPException(status_code=status,
                        detail={"ok": False, "error_code": code, "message": message, "details": {}})


def _email_key(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT).hex()


def _public(user: dict[str, Any]) -> dict[str, Any]:
    """Het account zoals de frontend het mag zien — nooit hash of salt."""
    return {
        "id": user["id"],
        "email": user.get("email"),
        "plan": user.get("plan", "free"),
        "created_at": user.get("created_at"),
    }


# ---------------------------------------------------------------- accounts ---

def get_user(user_id: str) -> Optional[dict[str, Any]]:
    return cache_store.get_json("users", user_id)


def user_by_email(email: str) -> Optional[dict[str, Any]]:
    idx = cache_store.get_json("user_email", _email_key(email))
    return get_user(idx["user_id"]) if idx and idx.get("user_id") else None


def any_user_exists() -> bool:
    """Is er al iemand geregistreerd? Gebruikt om bestaande data (van vóór de
    accounts) eenmalig aan de eerste gebruiker toe te kennen."""
    return any(cache_store._dir("users").glob("*.json"))


def register(email: str, password: str) -> tuple[dict[str, Any], str]:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        _err(400, "INVALID_EMAIL", "Vul een geldig e-mailadres in.")
    if len(password or "") < MIN_PASSWORD_LEN:
        _err(400, "WEAK_PASSWORD", f"Kies een wachtwoord van minstens {MIN_PASSWORD_LEN} tekens.")
    if user_by_email(email):
        _err(409, "EMAIL_TAKEN", "Er bestaat al een account met dit e-mailadres.")

    salt = secrets.token_bytes(16)
    user = {
        "id": secrets.token_hex(16),
        "email": email,
        "salt": salt.hex(),
        "password_hash": _hash_password(password, salt),
        "plan": "free",
        "created_at": time.time(),
    }
    cache_store.put_json("users", user["id"], user)
    cache_store.put_json("user_email", _email_key(email), {"user_id": user["id"]})
    return _public(user), _new_session(user["id"])


def login(email: str, password: str, request: Optional[Request] = None) -> tuple[dict[str, Any], str]:
    # Brute-force rem op IP: inloggen is goedkoop voor ons, maar raden mag niet
    # goedkoop zijn voor een aanvaller.
    ip = request.client.host if request is not None and request.client else "unknown"
    if not rate_limit.check(f"login:{ip}", max_per_window=20):
        _err(429, "RATE_LIMITED", "Te veel inlogpogingen — probeer het zo opnieuw.")

    user = user_by_email(email or "")
    # Altijd dezelfde melding en altijd hashen, zodat je niet aan het antwoord of
    # aan de responstijd kunt zien of een e-mailadres bestaat.
    reference = user or {"salt": secrets.token_bytes(16).hex(), "password_hash": ""}
    calc = _hash_password(password or "", bytes.fromhex(reference["salt"]))
    if not user or not hmac.compare_digest(calc, reference["password_hash"]):
        _err(401, "BAD_CREDENTIALS", "E-mailadres of wachtwoord klopt niet.")
    return _public(user), _new_session(user["id"])


def set_plan(user_id: str, plan: str) -> None:
    """Het plan hoort bij het account, niet bij een header die de client stuurt."""
    user = get_user(user_id)
    if user:
        user["plan"] = plan
        cache_store.put_json("users", user_id, user)


# ---------------------------------------------------------------- sessies ---

def _new_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    cache_store.put_json("sessions", _token_key(token), {
        "user_id": user_id,
        "created_at": time.time(),
        "expires_at": time.time() + SESSION_DAYS * 86400,
    })
    return token


def _token_from(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def user_for_request(request: Optional[Request]) -> Optional[dict[str, Any]]:
    """Het ingelogde account, of None. Verlopen sessies worden opgeruimd."""
    token = _token_from(request)
    if not token:
        return None
    key = _token_key(token)
    sess = cache_store.get_json("sessions", key)
    if not sess:
        return None
    if sess.get("expires_at", 0) < time.time():
        cache_store.delete_json("sessions", key)
        return None
    return get_user(sess.get("user_id", ""))


def require_user(request: Optional[Request]) -> dict[str, Any]:
    user = user_for_request(request)
    if not user:
        _err(401, "NOT_AUTHENTICATED", "Log in om verder te gaan.")
    return user


def require_user_id(request: Optional[Request]) -> str:
    return require_user(request)["id"]


def logout(request: Optional[Request]) -> None:
    token = _token_from(request)
    if token:
        cache_store.delete_json("sessions", _token_key(token))
