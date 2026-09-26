"""Router: account. Registreren, inloggen, uitloggen en 'wie ben ik'."""
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth

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
    first = not auth.any_user_exists()
    user, token = auth.register(req.email, req.password)
    adopted = _adopt_legacy_data(user["id"]) if first else 0
    return {"ok": True, "user": user, "token": token, "adopted_documents": adopted}


@router.post("/auth/login")
def login(req: CredentialsRequest, request: Request):
    user, token = auth.login(req.email, req.password, request)
    return {"ok": True, "user": user, "token": token}


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
