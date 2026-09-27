"""Router: system. Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth

router = APIRouter()




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

@router.get("/")
def root():
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
            "progress": True,
            "upload_types": sorted(SUPPORTED_SUFFIXES.keys()),
            "response_cache": ENABLE_RESPONSE_CACHE,
        },
    }




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
