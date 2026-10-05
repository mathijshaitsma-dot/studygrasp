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


def _adopt_guest_data(request: Request, user_id: str) -> int:
    """Verhuis de tijdelijke gastwerkruimte naar het ingelogde account."""
    guest = auth.user_for_request(request)
    if not guest or not guest.get("guest"):
        return 0
    guest_id = guest["id"]

    account_folders = load_folders(user_id)
    used_folder_ids = {item["id"] for item in account_folders}
    folder_map: dict[str, str] = {}
    guest_folders = load_folders(guest_id)
    for folder in guest_folders:
        old_id = folder["id"]
        new_id = old_id if old_id not in used_folder_ids else sha256_text(f"{guest_id}|{old_id}|{time.time()}")[:12]
        folder_map[old_id] = new_id
        used_folder_ids.add(new_id)
    for folder in guest_folders:
        copied = dict(folder)
        copied["id"] = folder_map[folder["id"]]
        copied["parent_id"] = folder_map.get(folder.get("parent_id"), folder.get("parent_id"))
        copied["owner_id"] = user_id
        account_folders.append(copied)
    if guest_folders:
        save_folders(user_id, account_folders)
    cache_store.delete_json("folders", guest_id)

    moved = 0
    for file_hash in list(user_document_hashes(guest_id)):
        guest_meta = load_meta(guest_id, file_hash) or {}
        if not load_meta(user_id, file_hash):
            copied = dict(guest_meta)
            copied["owner_id"] = user_id
            copied["folder_id"] = folder_map.get(copied.get("folder_id"), copied.get("folder_id"))
            save_meta(user_id, file_hash, copied)
            moved += 1
        for namespace in ("notes", "study"):
            old_key = user_key(guest_id, file_hash)
            new_key = user_key(user_id, file_hash)
            data = cache_store.get_json(namespace, old_key)
            if data is not None and cache_store.get_json(namespace, new_key) is None:
                cache_store.put_json(namespace, new_key, data)
            cache_store.delete_json(namespace, old_key)
        delete_meta(guest_id, file_hash)

    used_wordlist_ids = {item["id"] for item in load_wordlist_index(user_id)}
    for entry in list(load_wordlist_index(guest_id)):
        old_id = entry["id"]
        wordlist = load_wordlist(guest_id, old_id)
        if not wordlist:
            continue
        new_id = old_id if old_id not in used_wordlist_ids else sha256_text(f"{guest_id}|{old_id}|wordlist")[:12]
        wordlist = dict(wordlist)
        wordlist["id"] = new_id
        wordlist["owner_id"] = user_id
        save_wordlist(user_id, wordlist)
        cache_store.delete_json("wordlists", user_key(guest_id, old_id))
        used_wordlist_ids.add(new_id)
    cache_store.delete_json("wordlists", user_key(guest_id, "index"))
    auth.discard_guest_session(request)
    return moved


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
    if not mailer.configured():
        raise_api_error(503, "EMAIL_NOT_CONFIGURED", "E-mailregistratie is momenteel niet beschikbaar.")
    email, token = auth.begin_email_registration(req.email, req.password)
    base = (os.getenv("APP_BASE_URL", "").strip() or str(request.base_url).rstrip("/"))
    sent = mailer.send_email_verification(email, f"{base}/#/verify-email/{token}")
    if not sent:
        auth.cancel_email_registration(token)
        raise_api_error(503, "EMAIL_SEND_FAILED", "De verificatiemail kon niet worden verzonden. Probeer het opnieuw.")
    return {"ok": True, "verification_required": True}


@router.post("/auth/login")
def login(req: CredentialsRequest, request: Request):
    user, token = auth.login(req.email, req.password, request)
    adopted = _adopt_guest_data(request, user["id"])
    return {"ok": True, "user": user, "token": token, "adopted_documents": adopted}


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=10, max_length=4000)


class ForgotRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)


class ResetRequest(BaseModel):
    token: str = Field(min_length=10, max_length=400)
    password: str = Field(min_length=1, max_length=200)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=10, max_length=400)


@router.post("/auth/guest")
def guest_session(request: Request):
    ip = request.client.host if request is not None and request.client else "unknown"
    if not rate_limit.check(f"guest:{ip}", max_per_window=30, window_s=3600):
        raise_api_error(429, "RATE_LIMITED", "Te veel nieuwe gastsessies — probeer het later opnieuw.")
    user, token = auth.create_guest_session()
    return {"ok": True, "user": user, "token": token}


@router.get("/auth/config")
def auth_config():
    """Wat kan de frontend aanbieden? Zo verschijnt de Google-knop vanzelf zodra
    GOOGLE_CLIENT_ID is ingesteld, en blijft hij weg zolang dat niet zo is."""
    return {
        "ok": True,
        "google_client_id": auth.google_client_id() or None,
        "email_registration": auth.email_registration_enabled() and mailer.configured(),
        "password_reset": mailer.configured(),
    }


@router.post("/auth/verify-email")
def verify_email(req: VerifyEmailRequest, request: Request):
    first = not auth.any_user_exists()
    user, token = auth.complete_email_registration(req.token)
    adopted = _adopt_guest_data(request, user["id"])
    if first:
        adopted += _adopt_legacy_data(user["id"])
    return {"ok": True, "user": user, "token": token, "adopted_documents": adopted}


@router.post("/auth/google")
def google_login(req: GoogleLoginRequest, request: Request):
    ip = request.client.host if request is not None and request.client else "unknown"
    if not rate_limit.check(f"google-login:{ip}", max_per_window=30):
        raise_api_error(429, "RATE_LIMITED", "Te veel inlogpogingen — probeer het zo opnieuw.")
    user, token = auth.login_with_google(req.id_token)
    adopted = _adopt_guest_data(request, user["id"])
    # Ook via Google kan iemand de éérste gebruiker zijn.
    if len(list(cache_store._dir("users").glob("*.json"))) == 1:
        adopted += _adopt_legacy_data(user["id"])
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
    if user.get("guest"):
        return {"ok": True, "user": user, "usage": {"used": 0, "limit": 0}}
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
