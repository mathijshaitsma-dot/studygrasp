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

# Instellingen worden bij GEBRUIK gelezen, niet bij import. Dit bestand wordt
# vanuit core.py geïmporteerd vóórdat core zijn load_dotenv() draait; wie hier
# een os.getenv op moduleniveau zet, leest dus altijd een lege .env. Dat is
# precies wat er met GOOGLE_CLIENT_ID gebeurde: de Google-knop bleef weg hoe je
# hem ook instelde.
def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def session_days() -> int:
    try:
        return int(_env("SESSION_DAYS", "365"))
    except ValueError:
        return 365


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


def _claim_owner_if_unset(user_id: str) -> bool:
    """Ken het eigenaarsplan precies eenmaal toe.

    De vaste marker staat via cache_store ook in Supabase. Alleen naar de lokale
    users-map kijken is niet genoeg: die kan bij een nieuwe deploy leeg zijn,
    waarna anders een latere gebruiker ten onrechte opnieuw eigenaar wordt.
    """
    if cache_store.get_json("app_config", "owner"):
        return False
    cache_store.put_json("app_config", "owner", {
        "user_id": user_id,
        "claimed_at": time.time(),
    })
    return True


def _save_new_user(user: dict[str, Any]) -> None:
    """Bewaar een account en maak alleen het allereerste account eigenaar."""
    if _claim_owner_if_unset(user["id"]):
        user["plan"] = "owner"
    cache_store.put_json("users", user["id"], user)
    cache_store.put_json("user_email", _email_key(user["email"]), {"user_id": user["id"]})


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
    _save_new_user(user)
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


# --------------------------------------------------------- inloggen met Google ---
# Verificatie via Google's tokeninfo-endpoint in plaats van de JWT zelf na te
# rekenen: dat scheelt een extra afhankelijkheid (requests hebben we al) en
# Google controleert handtekening en vervaldatum dan voor ons. Wat wij nog wél
# moeten controleren is de `aud` — anders zou een token dat voor een héél andere
# app is uitgegeven hier ook werken.
GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"


def google_client_id() -> str:
    """OAuth-client-ID, pas lezen nadat core de project-.env heeft geladen."""
    return _env("GOOGLE_CLIENT_ID")


def google_enabled() -> bool:
    return bool(google_client_id())


def login_with_google(id_token: str) -> tuple[dict[str, Any], str]:
    client_id = google_client_id()
    if not client_id:
        _err(501, "GOOGLE_NOT_CONFIGURED", "Inloggen met Google is niet ingesteld.")
    import requests

    try:
        resp = requests.get(GOOGLE_TOKENINFO, params={"id_token": id_token or ""}, timeout=10)
        info = resp.json() if resp.ok else {}
    except Exception:
        info = {}
    if not info or info.get("aud") != client_id:
        _err(401, "GOOGLE_TOKEN_INVALID", "Inloggen met Google is niet gelukt. Probeer het opnieuw.")
    if str(info.get("email_verified", "")).lower() not in ("true", "1"):
        _err(401, "GOOGLE_EMAIL_UNVERIFIED", "Dit Google-account heeft geen geverifieerd e-mailadres.")

    email = (info.get("email") or "").strip().lower()
    if not email:
        _err(401, "GOOGLE_TOKEN_INVALID", "Inloggen met Google is niet gelukt. Probeer het opnieuw.")

    user = user_by_email(email)
    if not user:
        # Eerste keer via Google: account aanmaken zonder wachtwoord. Wie later
        # een wachtwoord wil, gebruikt gewoon "wachtwoord vergeten".
        user = {
            "id": secrets.token_hex(16),
            "email": email,
            "salt": secrets.token_bytes(16).hex(),
            "password_hash": "",          # leeg = kan niet met wachtwoord inloggen
            "plan": "free",
            "created_at": time.time(),
            "google_sub": info.get("sub"),
        }
        _save_new_user(user)
    return _public(user), _new_session(user["id"])


# ------------------------------------------------------- wachtwoord vergeten ---
RESET_TTL_SECONDS = 3600


def create_reset_token(email: str) -> Optional[tuple[dict[str, Any], str]]:
    """Maakt een hersteltoken. Geeft None als het e-mailadres niet bestaat — de
    aanroeper moet dan alsnog hetzelfde antwoord geven, anders kun je via dit
    formulier uitvinden wie er een account heeft."""
    user = user_by_email(email or "")
    if not user:
        return None
    token = secrets.token_urlsafe(32)
    cache_store.put_json("password_resets", _token_key(token), {
        "user_id": user["id"],
        "expires_at": time.time() + RESET_TTL_SECONDS,
    })
    return user, token


def reset_password(token: str, new_password: str) -> dict[str, Any]:
    if len(new_password or "") < MIN_PASSWORD_LEN:
        _err(400, "WEAK_PASSWORD", f"Kies een wachtwoord van minstens {MIN_PASSWORD_LEN} tekens.")
    key = _token_key(token or "")
    rec = cache_store.get_json("password_resets", key)
    if not rec or rec.get("expires_at", 0) < time.time():
        cache_store.delete_json("password_resets", key)
        _err(400, "RESET_TOKEN_INVALID", "Deze herstellink is verlopen of al gebruikt. Vraag een nieuwe aan.")
    user = get_user(rec.get("user_id", ""))
    if not user:
        _err(400, "RESET_TOKEN_INVALID", "Deze herstellink is verlopen of al gebruikt. Vraag een nieuwe aan.")

    salt = secrets.token_bytes(16)
    user["salt"] = salt.hex()
    user["password_hash"] = _hash_password(new_password, salt)
    cache_store.put_json("users", user["id"], user)
    cache_store.delete_json("password_resets", key)   # eenmalig bruikbaar
    _revoke_all_sessions(user["id"])                  # wie er nog inlogde, vliegt eruit
    return _public(user)


def _revoke_all_sessions(user_id: str) -> None:
    """Na een wachtwoordwijziging horen bestaande sessies te vervallen: anders
    blijft iemand die je account had overgenomen gewoon ingelogd."""
    for path in cache_store._dir("sessions").glob("*.json"):
        sess = cache_store.get_json("sessions", path.stem)
        if sess and sess.get("user_id") == user_id:
            cache_store.delete_json("sessions", path.stem)


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
        "expires_at": time.time() + session_days() * 86400,
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
    now = time.time()
    if sess.get("expires_at", 0) < now:
        cache_store.delete_json("sessions", key)
        return None
    # Een vertrouwd apparaat blijft ingelogd zolang het regelmatig wordt
    # gebruikt. Pas in de tweede helft van de looptijd verlengen, zodat we niet
    # bij elke API-call onnodig naar schijf/Supabase schrijven.
    ttl = session_days() * 86400
    if sess.get("expires_at", 0) - now < ttl / 2:
        sess["expires_at"] = now + ttl
        sess["last_seen_at"] = now
        cache_store.put_json("sessions", key, sess)
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
