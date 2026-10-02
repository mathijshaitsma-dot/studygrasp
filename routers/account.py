"""Router: account. Registreren, inloggen, uitloggen en 'wie ben ik'."""
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth
import mailer

router = APIRouter()


class CredentialsRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


def _adopt_legacy_data(user_id: str) -> int:
    """Data van vóór de accounts heeft nog geen eigenaar. Die kennen we eenmalig
    toe aan de éérste gebruiker die zich registreert — in de praktijk de maker
    zelf, die de app tot nu toe alleen gebruikte. Zonder dit zou alles wat er al
    stond onzichtbaar worden. Latere gebruikers krijgen hier niets van: zodra er
    één account bestaat, gebeurt dit nooit meer."""
    moved = 0
    for path in list(META_DIR.glob("*.json")):
        if "__" in path.stem:
            continue                      # heeft al een eigenaar
        meta = load_json(path)
        if not meta or not meta.get("file_hash"):
            continue
        file_hash = meta["file_hash"]
        save_meta(user_id, file_hash, meta)
        # bijbehorende studievoortgang en notities meeverhuizen
        for ns in ("study", "notes"):
            old = cache_store.get_json(ns, file_hash)
            if old is not None:
                cache_store.put_json(ns, user_key(user_id, file_hash), old)
                cache_store.delete_json(ns, file_hash)
        path.unlink(missing_ok=True)
        moved += 1
    # mappen stonden onder de vaste sleutel "index"
    legacy_folders = cache_store.get_json("folders", "index")
    if legacy_folders is not None:
        cache_store.put_json("folders", user_id, legacy_folders)
        cache_store.delete_json("folders", "index")
    return moved


@router.post("/auth/register")
def register(req: CredentialsRequest, request: Request):
    ip = request.client.host if request is not None and request.client else "unknown"
    register_limit = int(os.getenv("RATE_LIMIT_REGISTER_MAX_PER_HOUR", "5"))
    if not rate_limit.check(f"register:{ip}", max_per_window=register_limit, window_s=3600):
        raise_api_error(429, "RATE_LIMITED", "Te veel accounts aangemaakt — probeer het later opnieuw.")
    first = not auth.any_user_exists()
    user, token = auth.register(req.email, req.password)
    adopted = _adopt_legacy_data(user["id"]) if first else 0
    return {"ok": True, "user": user, "token": token, "adopted_documents": adopted}


@router.post("/auth/login")
def login(req: CredentialsRequest, request: Request):
    user, token = auth.login(req.email, req.password, request)
    return {"ok": True, "user": user, "token": token}


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=10, max_length=4000)


class ForgotRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)


class ResetRequest(BaseModel):
    token: str = Field(min_length=10, max_length=400)
    password: str = Field(min_length=1, max_length=200)


@router.get("/auth/config")
def auth_config():
    """Wat kan de frontend aanbieden? Zo verschijnt de Google-knop vanzelf zodra
    GOOGLE_CLIENT_ID is ingesteld, en blijft hij weg zolang dat niet zo is."""
    return {
        "ok": True,
        "google_client_id": auth.google_client_id() or None,
        "email_registration": auth.email_registration_enabled(),
        "password_reset": mailer.configured(),
    }


@router.post("/auth/google")
def google_login(req: GoogleLoginRequest, request: Request):
    ip = request.client.host if request is not None and request.client else "unknown"
    if not rate_limit.check(f"google-login:{ip}", max_per_window=30):
        raise_api_error(429, "RATE_LIMITED", "Te veel inlogpogingen — probeer het zo opnieuw.")
    user, token = auth.login_with_google(req.id_token)
    adopted = 0
    # Ook via Google kan iemand de éérste gebruiker zijn.
    if len(list(cache_store._dir("users").glob("*.json"))) == 1:
        adopted = _adopt_legacy_data(user["id"])
    return {"ok": True, "user": user, "token": token, "adopted_documents": adopted}


@router.post("/auth/forgot")
def forgot_password(req: ForgotRequest, request: Request):
    """Vraagt een herstelmail aan. Het antwoord is ALTIJD hetzelfde, of het
    adres nu bestaat of niet — anders is dit formulier een manier om uit te
    vinden wie er een account heeft."""
    ip = request.client.host if request is not None and request.client else "unknown"
    if not rate_limit.check(f"forgot:{ip}", max_per_window=10):
        raise_api_error(429, "RATE_LIMITED", "Te veel aanvragen — probeer het zo opnieuw.")

    made = auth.create_reset_token(req.email)
    if made:
        user, token = made
        base = (os.getenv("APP_BASE_URL", "").strip() or str(request.base_url).rstrip("/"))
        mailer.send_password_reset(user["email"], f"{base}/#/reset/{token}")
    return {"ok": True, "sent": True}


@router.post("/auth/reset")
def reset_password(req: ResetRequest):
    user = auth.reset_password(req.token, req.password)
    return {"ok": True, "user": user}


@router.post("/auth/logout")
def logout(request: Request):
    auth.logout(request)
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request):
    """Wie ben ik, en hoeveel tegoed heb ik nog? De frontend gebruikt dit om te
    bepalen of hij het inlogscherm of de app moet tonen."""
    user = auth.require_user(request)
    used, limit = usage.status(user["id"], user.get("plan", "free"))
    return {"ok": True, "user": user, "usage": {"used": used, "limit": limit}}


@router.get("/admin/accounts")
def admin_accounts(request: Request):
    """Privacybewust eigenaarsoverzicht; nooit hashes, salts of tokens."""
    auth.require_owner(request)
    stored_users = [(user_id, user) for user_id, user in cache_store.list_json("users")
                    if user.get("email")]
    usage_by_user = usage.statuses([
        (user_id, user.get("plan", "free")) for user_id, user in stored_users
    ])
    accounts = []
    for user_id, user in stored_users:
        plan = user.get("plan", "free")
        used, limit = usage_by_user[user_id]
        accounts.append({
            "id": user_id,
            "email": user.get("email"),
            "created_at": user.get("created_at"),
            "plan": plan,
            "signup_method": "google" if user.get("google_sub") else "email",
            "usage": {"used": used, "limit": limit},
        })
    accounts.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
    return {"ok": True, "total": len(accounts), "accounts": accounts}
