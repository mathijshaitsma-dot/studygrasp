"""Router: system. Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)

router = APIRouter()




@router.get("/usage")
def get_usage(request: Request):
    """Hoeveel verse generaties de gebruiker vandaag nog heeft. De frontend
    gebruikt dit voor een subtiele teller en de upgrade-melding."""
    uid, plan = usage.identify(request)
    used, limit = usage.status(uid, plan)
    return {
        "ok": True,
        "enabled": usage.enabled(),
        "plan": plan,
        "used": used,
        "limit": limit,  # None = onbeperkt
        "remaining": None if limit is None else max(0, limit - used),
    }




# =========================================================
# ROUTES: BASIS
# =========================================================

@router.get("/")
def root():
    return {
        "ok": True,
        "service": "StudyCopilot Backend v3",
        "version": "3.3.0",
        "models": ai_engine.available_models(),
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
def deep_health():
    return {
        "ok": True,
        "ai_providers": ai_engine.providers_status(),
        "libreoffice_found": bool(find_libreoffice_executable()),
        "models": ai_engine.available_models(),
        "base_dir": str(BASE_DIR.resolve()),
    }




@router.get("/health/ai-stats")
def ai_stats_endpoint(days: int = 7):
    """Ops-inzicht: hoe vaak elke AI-fallback-laag wordt geraakt, foutratio, gemiddelde latency."""
    return {"ok": True, **ai_stats.aggregate(days=max(1, min(days, 30)))}
