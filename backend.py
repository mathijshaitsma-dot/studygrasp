"""
StudyGrasp Backend v3 — app-samenstelling.

De endpoints staan per domein in routers/ en de gedeelde logica (opslag,
AI-pijplijn, prompts, config) in core.py. Dit bestand doet alleen nog de
FastAPI-app opzetten: CORS, de routers aankoppelen en de foutafhandeling.
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# core importeren draait de config/logging-setup (load_dotenv, mkdir, provider-check).
from core import logger, prune_derived_cache, prune_expired_guest_data
from routers import documents, explain, study, wordlists, folders, exam, media, system, exercise, account, billing

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Maak bij iedere deploy oude, opnieuw renderbare volumecache vrij."""
    prune_expired_guest_data()
    prune_derived_cache(force=True, startup=True)
    yield


app = FastAPI(title="StudyGrasp Backend v3", version="3.4.0", lifespan=lifespan)

# Standaard alleen lokale ontwikkeling. Online zet je CORS_ORIGINS op je eigen
# domein; met "*" kan letterlijk elke website deze API namens een bezoeker
# aanroepen.
_DEFAULT_CORS = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000"
cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", _DEFAULT_CORS).split(",") if o.strip()]
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


@app.middleware("http")
async def public_security_headers(request, call_next):
    """Veilige browserdefaults voor de publieke app.

    Google Identity Services is de enige externe browsercode die we bewust
    toestaan; alle overige scripts, styles, fonts en afbeeldingen zijn lokaal.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(self), microphone=(self), geolocation=(), payment=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' https://accounts.google.com; "
        "style-src 'self' 'unsafe-inline' https://accounts.google.com; "
        "font-src 'self'; img-src 'self' data: blob: https://*.googleusercontent.com; "
        "connect-src 'self' https://accounts.google.com; "
        "frame-src https://accounts.google.com; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'",
    )
    # De frontend is een ES-module-graaf zonder buildstap: een versie-query op
    # het entry-script zou app.js twee keer laden (de views importeren "../app.js"
    # zonder query). Daarom verversen we niet via de URL maar via revalidatie —
    # de ETag van StaticFiles maakt dat alsnog goedkoop (304 zonder body).
    path = request.url.path
    if path in ("/", "/index.html") or path.startswith(("/js/", "/css/")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

# Elke router bevat de endpoints van één domein; ze delen alles via core.
for module in (account, billing, documents, explain, study, wordlists, folders, exam, media, system, exercise):
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


# In productie komen frontend en API bewust van dezelfde origin. Dit voorkomt
# CORS-fouten en vooral dat een publieke browser naar zijn eigen localhost gaat.
# De mount staat als laatste, zodat alle API-routes hierboven voorrang houden.
_frontend_dir = Path(__file__).resolve().parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
