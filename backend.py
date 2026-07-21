"""
StudyCopilot Backend v3 — app-samenstelling.

De endpoints staan per domein in routers/ en de gedeelde logica (opslag,
AI-pijplijn, prompts, config) in core.py. Dit bestand doet alleen nog de
FastAPI-app opzetten: CORS, de routers aankoppelen en de foutafhandeling.
"""
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# core importeren draait de config/logging-setup (load_dotenv, mkdir, provider-check).
from core import logger
from routers import documents, explain, study, wordlists, folders, exam, media, system

app = FastAPI(title="StudyCopilot Backend v3", version="3.4.0")

cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
if cors_origins == ["*"]:
    logger.warning(
        "CORS_ORIGINS staat op '*' — elke website kan deze API vanuit de browser aanspreken. "
        "Prima tijdens ontwikkelen, maar zet dit vast op je echte frontend-domein(en) voordat je live gaat "
        "(bv. CORS_ORIGINS=https://jouwapp.nl)."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["*"],
    allow_credentials=os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true",
    allow_methods=["*"],
    allow_headers=["*"],
    # Zonder dit blokkeert Chrome (Private Network Access) verzoeken van een
    # publieke https-site (zoals een Lovable-app) naar localhost => "Failed to fetch".
    allow_private_network=True,
)

# Elke router bevat de endpoints van één domein; ze delen alles via core.
for module in (documents, explain, study, wordlists, folders, exam, media, system):
    app.include_router(module.router)


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error_code" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error_code": "HTTP_ERROR", "message": str(detail), "details": {}},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_, exc: Exception):
    logger.exception("Onverwachte backendfout: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error_code": "INTERNAL_SERVER_ERROR", "message": "Er ging iets mis in de backend.", "details": {}},
    )
