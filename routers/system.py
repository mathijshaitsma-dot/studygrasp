"""Router: system. Endpoints; gedeelde logica komt uit core."""
import os
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth
import mailer

router = APIRouter()


def cors_value_is_safe() -> bool:
    origins = [value.strip() for value in os.getenv("CORS_ORIGINS", "").split(",") if value.strip()]
    return bool(origins) and "*" not in origins and all(
        origin.startswith(("https://", "http://localhost", "http://127.0.0.1"))
        for origin in origins
    )




@router.get("/usage")
def get_usage(request: Request):
    """Accountgebonden maandcredits voor het actieve plan."""
    user = auth.require_user(request)
    uid, plan = user["id"], user.get("plan", "free")
    used, limit = usage.status(uid, plan)
    return {
        "ok": True,
        "enabled": usage.enabled(),
        "plan": plan,
        "used": used,
        "limit": limit,  # None = onbeperkt
        "remaining": None if limit is None else max(0, limit - used),
        "period": "month",
    }




# =========================================================
# ROUTES: BASIS
# =========================================================

@router.get("/health")
def health():
    # Bewust kaal: welke providers en modellen draaien is operationele info die
    # een willekeurige bezoeker niet hoeft te weten.
    return {
        "ok": True,
        "service": "StudyGrasp Backend v3",
        "version": "3.3.0",
        "features": {
            "streaming": True,
            "vision_high_res": True,
            "markdown_latex": True,
            "slide_context": True,
            "follow_up_chat": True,
            "document_summary": True,
            "quiz": True,
            "flashcards_srs": True,
            "exam_mode": True,
            "folders": True,
            "region_ask": True,
            "search": True,
            "smart_search": True,
            "progress": True,
            "upload_types": sorted(SUPPORTED_SUFFIXES.keys()),
            "response_cache": ENABLE_RESPONSE_CACHE,
        },
    }


@router.get("/health/ready")
def readiness():
    """Publieke, sleutelvrije productiecheck voor host en beheerder."""
    production = os.getenv("APP_ENV", "development").strip().lower() == "production"
    base_url = os.getenv("APP_BASE_URL", "").strip()
    checks = {
        "ai_provider": bool(ai_engine.available_models()),
        "persistent_data_dir": Path(BASE_DIR).is_absolute(),
        "cors_restricted": cors_value_is_safe(),
        "public_https_url": bool(base_url.startswith("https://")),
        "owner_email": bool(os.getenv("OWNER_EMAIL", "").strip()),
        "verified_registration": bool(
            auth.google_enabled() or (auth.email_registration_enabled() and mailer.configured())
        ),
        "email_verification": bool(
            not auth.email_registration_enabled() or mailer.configured()
        ),
        "password_email": mailer.configured(),
        "libreoffice": bool(find_libreoffice_executable()),
    }
    required = (
        "ai_provider", "persistent_data_dir", "cors_restricted",
        "public_https_url", "owner_email", "verified_registration", "email_verification",
    )
    ready = all(checks[name] for name in required) if production else checks["ai_provider"]
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ok": ready, "environment": "production" if production else "development", "checks": checks},
    )




@router.get("/health/deep")
def deep_health(request: Request):
    # Verraadt hoeveel API-sleutels en welke modellen je hebt: niet publiek.
    auth.require_user(request)
    return {
        "ok": True,
        "ai_providers": ai_engine.providers_status(),
        "libreoffice_found": bool(find_libreoffice_executable()),
        "models": ai_engine.available_models(),
        "base_dir": str(BASE_DIR.resolve()),
    }




@router.get("/health/ai-stats")
def ai_stats_endpoint(days: int = 7, request: Request = None):
    """Ops-inzicht: hoe vaak elke AI-fallback-laag wordt geraakt, foutratio, gemiddelde latency."""
    auth.require_user(request)
    return {"ok": True, **ai_stats.aggregate(days=max(1, min(days, 30)))}
