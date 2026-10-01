"""
StudyGrasp — gedeelde kern (core)
===================================

Alle logica die de endpoints (in routers/) delen. Geen FastAPI-app en geen
routes: dit is de laag eronder. De routers doen `from core import *` en roepen
deze helpers aan.

Leidend principe voor kwaliteit: uitleg van hetzelfde niveau als Gemini's
AI-modus — het model kijkt naar een hoge-resolutie dia-afbeelding, schrijft
vrije markdown met LaTeX, streamt per dia (SSE) en kent de context (dia X van N,
vorige dia's). Leidend principe voor kosten: elke dure generatie wordt op inhoud
gecachet (L1 lokaal + L2 Supabase, gedeeld over álle gebruikers), dubbele
gelijktijdige generaties worden ontdubbeld, en er wordt niets speculatief
gegenereerd wat gebruikers zelden opvragen.

Inhoud (in volgorde):
- CONFIG            env-instellingen, mappen, logging
- MODELS            gedeelde pydantic-modellen (PageInfo, ExplainRequest, ...)
- GENERIEKE HELPERS hashes, json-io, foutafhandeling (raise_api_error)
- DOCUMENT-OPSLAG   metadata + eigendomscheck via ingelogd account
- TEKST-EXTRACTIE   pdf/pptx/docx/afbeelding -> tekst
- RENDEREN          pptx/docx -> pdf -> dia-afbeeldingen (LibreOffice/PyMuPDF)
- TUTOR-PROMPT      het systeeminstructie-hart van de uitlegkwaliteit
- AI-FALLBACK       generate/stream via ai_engine (provider-fallback)
- UITLEG-CACHE      cache-keys + in-flight-dedup
- FREEMIUM          accountcredits (eerste toegang per exacte inhoud telt)
- PREFETCH          volgende dia / studiemateriaal alvast (kostenbewust)
- STUDIE-OPSLAG     study-data, flashcards/SRS, wordlists, folders, exam, notities
- TTS / ZOEKEN      voorlezen en full-text zoeken

NB: dit bestand is bewust één samenhangende module. Een verdere opsplitsing in
een core/-package (base + ai) is mogelijk, maar de AI-pijplijn is nauw verweven
met opslag/rendering; het levert vooral navigatiegemak op en geen gedrag.
"""

import io
import os
import re
import json
import time
import shutil
import hashlib
import logging
import threading
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional, Literal, Any, Iterator

from concurrent.futures import ThreadPoolExecutor

import fitz  # PyMuPDF
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from pptx import Presentation

import ai_engine
import auth
import ai_stats
import cache_store
import rate_limit
import usage
from ai_engine import Message, text_part


# =========================================================
# CONFIG
# =========================================================

BASE_PATH = Path(__file__).resolve().parent
# Omgevingsvariabelen van de hostingprovider hebben altijd voorrang. Lokaal
# vult .env alleen waarden aan die nog niet door het proces zijn ingesteld.
load_dotenv(dotenv_path=BASE_PATH / ".env", override=False)

# De AI draait via ai_engine: één laag met meerdere providers (Gemini-keys
# roterend, daarna Groq en OpenRouter) en automatische fallback bij limieten.
# Configuratie via .env: GEMINI_API_KEYS / GROQ_API_KEY / OPENROUTER_API_KEY,
# volgorde via AI_PROVIDER_ORDER, modellen via GEMINI_MODELS / GROQ_MODELS /
# OPENROUTER_MODELS.
if not ai_engine.available_models():
    raise ValueError(
        "Geen enkele AI-provider geconfigureerd. Zet minstens één key in .env: "
        "GEMINI_API_KEYS (of GEMINI_API_KEY), GROQ_API_KEY of OPENROUTER_API_KEY."
    )

TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.2"))

# Hoeveel dia's er na elke uitleg automatisch vooruit worden gegenereerd,
# zodat doorklikken (bijna) instant voelt.
# Standaard 1 dia vooruit: prefetchen voelt instant bij doorklikken, maar 3
# vooruit genereren verbrandt tokens voor dia's die veel gebruikers nooit zien.
# Wie meer cache-warmte wil boven zuinigheid zet dit hoger.
PREFETCH_AHEAD = int(os.getenv("PREFETCH_AHEAD", "1"))

# Hoeveel dia's er direct na een upload alvast worden uitgelegd (met de
# standaardinstellingen), zodat het openen van het document instant voelt.
# 1 is genoeg: zodra de gebruiker dia 1 opent (ook uit cache) prefetcht
# /explain de volgende dia al — dia 2 vooraf genereren was dubbel werk.
# Online standaard uit: een upload hoort nog geen betaalde AI-call te doen
# voordat de gebruiker bewust een uitleg opent. Dat voorkomt dat scripts via
# veel unieke uploads providerkosten veroorzaken zonder credits te gebruiken.
PREFETCH_ON_UPLOAD = int(os.getenv("PREFETCH_ON_UPLOAD", "0"))

# Na de upload alvast flashcards genereren? STANDAARD UIT.
# Flashcards zijn opt-in (de gebruiker klikt bewust op het tabblad). Ze bij
# elke upload speculatief genereren kost AI-tokens voor materiaal dat de meeste
# gebruikers nooit openen — puur verspilde kosten op schaal, zonder dat iemand
# het resultaat ziet. Ze worden nu bij de eerste keer openen gegenereerd en zijn
# daarna gecachet (gedeeld over alle gebruikers), dus geen kwaliteitsverlies —
# alleen die ene eerste keer even wachten. Zet op "true" om de oude cache-warming
# terug te krijgen (bv. als je zeker weet dat flashcards intensief gebruikt worden).
PREFETCH_STUDY_ON_UPLOAD = os.getenv("PREFETCH_STUDY_ON_UPLOAD", "false").lower() == "true"

# Hoeveel prefetch-taken (uitleg/quiz/flashcards) er tegelijk mogen draaien.
PREFETCH_WORKERS = int(os.getenv("PREFETCH_WORKERS", "3"))

# Eigen, ruime IP-noodrem voor de /prefetch-endpoint. Prefetch omzeilt bewust het
# quotum (speculatief warmen mag niet van maandcredits af), maar zonder énige rem
# zou een client onbeperkt achtergrondgeneraties kunnen afvuren — puur op jouw
# API-rekening. Deze limiet zit in een APARTE bucket (sleutel "prefetch:<ip>"),
# los van de gewone AI-rate-limit, zodat warmen nooit de échte uitleg-aanvragen
# van diezelfde gebruiker verdringt. Royaal gekozen: alleen misbruik afremmen.
PREFETCH_RATE_MAX_PER_MIN = int(os.getenv("PREFETCH_RATE_MAX_PER_MIN", "40"))

# De publieke /prefetch-route is een pure snelheidsoptimalisatie en omzeilt het
# productquotum. Daarom standaard uit; gewone uitleg en intern één dia vooruit
# warmen blijven werken. Alleen bewust aanzetten in een vertrouwde omgeving.
ENABLE_SPECULATIVE_PREFETCH = os.getenv("ENABLE_SPECULATIVE_PREFETCH", "false").lower() == "true"

PROMPT_VERSION = "v5.14"  # onderdeel van de cache-key: prompt gewijzigd => cache ongeldig
QUESTION_PROMPT_VERSION = "questions-v2-no-page-recall"

BASE_DIR = Path(os.getenv("BACKEND_CACHE_DIR", "backend_cache_v3"))
UPLOAD_DIR = BASE_DIR / "uploads"
IMAGE_DIR = BASE_DIR / "images"
PDF_DIR = BASE_DIR / "converted_pdf"
TEXT_CACHE_DIR = BASE_DIR / "text_cache"
META_DIR = BASE_DIR / "meta"
AI_CACHE_DIR = BASE_DIR / "ai_cache"
STUDY_DIR = BASE_DIR / "study"  # flashcards, spaced repetition, quizvoortgang
TTS_DIR = BASE_DIR / "tts_cache"  # voorgelezen audio (mp3), gecachet per tekst+stem

for folder in [UPLOAD_DIR, IMAGE_DIR, PDF_DIR, TEXT_CACHE_DIR, META_DIR, AI_CACHE_DIR, STUDY_DIR, TTS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

ENABLE_RESPONSE_CACHE = os.getenv("ENABLE_RESPONSE_CACHE", "true").lower() == "true"

# Weergave-resolutie voor de frontend en (hogere) resolutie voor het AI-model.
PDF_RENDER_SCALE_DISPLAY = float(os.getenv("PDF_RENDER_SCALE_DISPLAY", "1.5"))
PDF_RENDER_SCALE_AI = float(os.getenv("PDF_RENDER_SCALE_AI", "2.2"))

# Dia's worden als JPEG opgeslagen: 3-8x kleiner dan PNG bij gelijke leesbaarheid.
# Dat betekent snellere laadtijd in de browser én snellere uploads naar Gemini
# (kleinere afbeelding = merkbaar snellere eerste tokens).
SLIDE_JPEG_QUALITY = int(os.getenv("SLIDE_JPEG_QUALITY", "85"))

MAX_SLIDE_TEXT = int(os.getenv("MAX_SLIDE_TEXT", "4000"))
# Als het model de dia-afbeelding meekrijgt is de tekst alleen een leeshulp
# voor slecht leesbare stukken; een kortere fallback scheelt dan tokens zonder
# kwaliteitsverlies. Zonder afbeelding geldt de volledige MAX_SLIDE_TEXT.
MAX_SLIDE_TEXT_VISION = int(os.getenv("MAX_SLIDE_TEXT_VISION", "1200"))
MAX_PREV_SLIDE_TEXT = int(os.getenv("MAX_PREV_SLIDE_TEXT", "600"))

# Vervolgvragen: hoeveel chatgeschiedenis er maximaal mee teruggestuurd wordt.
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "10"))
MAX_HISTORY_CHARS = int(os.getenv("MAX_HISTORY_CHARS", "3000"))

# Bovengrens voor een upload — zonder dit kan iemand ongelimiteerd grote
# bestanden blijven posten en zo schijfruimte/verwerkingstijd opsouperen.
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "80"))
# Losse, ruimere noodrem op uploads (zwaarder dan een cache-hit, maar geen
# AI-generatie) — los van de rate-limit op de dure AI-endpoints.
RATE_LIMIT_UPLOAD_MAX_PER_MIN = int(os.getenv("RATE_LIMIT_UPLOAD_MAX_PER_MIN", "10"))

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("studygrasp-v3")


# =========================================================
# MODELS
# =========================================================

DocStatus = Literal["uploaded", "processing", "ready", "partial", "failed"]


class PageInfo(BaseModel):
    index: int
    label: str
    image_ready: bool = False
    image_url: str
    text_preview: str = ""


class UploadResponse(BaseModel):
    ok: bool = True
    file_hash: str
    file_name: str
    file_type: Literal["pdf", "ppt", "pptx", "docx", "image"]
    total_pages: int
    status: DocStatus
    note: Optional[str] = None
    pages: list[PageInfo]
    # Alleen gezet bij een foto-upload die lastig te lezen lijkt (wazig/donker):
    # {"issues": ["blurry"|"dark", ...]}. De frontend waarschuwt dan vriendelijk.
    image_quality: Optional[dict[str, Any]] = None


class ChatTurn(BaseModel):
    role: Literal["user", "assistant", "model"]
    content: str


class ExplainRequest(BaseModel):
    file_hash: str
    page_index: int
    # "auto" = antwoord in de taal van de dia zelf. Of expliciet: "Nederlands", "English", ...
    language: str = "auto"
    detail_level: Literal["short", "normal", "long"] = "normal"
    # explain = normale docent-uitleg, simple = extra eenvoudige taal,
    # study = compacte leersamenvatting (wat moet je onthouden voor het tentamen).
    mode: Literal["explain", "simple", "study"] = "explain"
    audience_level: Literal["beginner", "intermediate", "advanced"] = "intermediate"
    # Vervolgvraag over deze dia (chat). history = eerdere beurten over deze dia,
    # inclusief de eerdere uitleg als assistant-beurt.
    question: Optional[str] = None
    history: list[ChatTurn] = Field(default_factory=list)
    stream: bool = True
    force_refresh: bool = False
    # Alleen uit de cache lezen: nooit genereren, nooit quotum afschrijven.
    # De frontend gebruikt dit om de uitleg van de vólgende dia op te halen als
    # die al klaarstaat, en daarmee de voorleesaudio voor te warmen. Staat hij er
    # nog niet, dan komt er gewoon niets terug en gebeurt er niets.
    cache_only: bool = False


# =========================================================
# GENERIEKE HELPERS
# =========================================================

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def clean_text(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= max_len else text[:max_len].rstrip() + " […]"


def raise_api_error(status_code: int, error_code: str, message: str, details: Optional[dict] = None):
    raise HTTPException(
        status_code=status_code,
        detail={"ok": False, "error_code": error_code, "message": message, "details": details or {}},
    )


# Rauwe exception-tekst (stacktraces, provider-HTTP-bodies, interne paden) hoort
# niet in een client-response: standaard weglaten en alleen serverside loggen.
# Zet DEBUG_ERROR_DETAILS=true om hem tijdens ontwikkelen wél mee te sturen.
DEBUG_ERROR_DETAILS = os.getenv("DEBUG_ERROR_DETAILS", "false").strip().lower() == "true"


def debug_reason(error: object) -> dict[str, Any]:
    return {"reason": str(error)} if DEBUG_ERROR_DETAILS else {}


# =========================================================
# DOCUMENT OPSLAG / METADATA
# =========================================================

# ---------------------------------------------------------------------------
# GEBRUIKERSGEBONDEN OPSLAG
# ---------------------------------------------------------------------------
# Alles wat van één gebruiker is (documenten, notities, studievoortgang, mappen,
# woordenlijsten) krijgt een sleutel met het account-id ervoor. Wat puur van de
# INHOUD afhangt en geen persoonsgegevens bevat, blijft juist gedeeld: de
# gerenderde dia-afbeeldingen, de tekstextractie, de AI-uitleg en de voorlees-
# audio. Dat is geen slordigheid maar de reden dat dit betaalbaar blijft —
# dezelfde dia wordt nooit twee keer gegenereerd, ook niet als tien studenten
# hetzelfde college uploaden. Uit die cache valt niets over een persoon af te
# leiden: hij is volledig bepaald door de bestandsinhoud.
#
# Scheidingsteken is "__": user-id's en hashes zijn hex, dus botsen kan niet, en
# ':' of '|' mag niet in Windows-bestandsnamen.
def user_key(user_id: str, key: str) -> str:
    return f"{user_id}__{key}"


def load_meta(user_id: str, file_hash: str) -> Optional[dict[str, Any]]:
    # Via cache_store (namespace "meta") i.p.v. de kale load_json: zonder dit
    # overleefde documentmetadata geen deploy, zelfs met Supabase ingesteld.
    return cache_store.get_json("meta", user_key(user_id, file_hash))


def save_meta(user_id: str, file_hash: str, payload: dict[str, Any]) -> None:
    cache_store.put_json("meta", user_key(user_id, file_hash), payload)


def delete_meta(user_id: str, file_hash: str) -> None:
    cache_store.delete_json("meta", user_key(user_id, file_hash))


def user_document_hashes(user_id: str) -> list[str]:
    """De file-hashes die in de bibliotheek van deze gebruiker zitten."""
    prefix = f"{user_id}__"
    return [p.stem[len(prefix):] for p in META_DIR.glob(f"{prefix}*.json")]


def set_document_status(user_id: str, file_hash: str, status: DocStatus, note: Optional[str] = None) -> None:
    meta = load_meta(user_id, file_hash) or {}
    meta["status"] = status
    if note:
        meta["note"] = note
    save_meta(user_id, file_hash, meta)


def ensure_document_exists(user_id: str, file_hash: str) -> dict[str, Any]:
    """Bestaat dit document in de bibliotheek van DEZE gebruiker? Zo niet, dan
    krijgt hij dezelfde 404 als bij een document dat helemaal niet bestaat —
    bewust, want anders kun je met een gokje afleiden of iemand anders een
    bepaald bestand heeft."""
    meta = load_meta(user_id, file_hash)
    if not meta:
        raise_api_error(404, "DOCUMENT_NOT_FOUND", "Document niet gevonden.")
    return meta


# =========================================================
# ACCOUNTIDENTITEIT EN EIGENDOM
# =========================================================
# De geverifieerde sessie is de enige bron voor het account-id. Persoonlijke
# stores gebruiken dit id in hun sleutel, zodat documenten, voortgang en
# studiegegevens niet tussen accounts kunnen lekken.

def request_user_id(request: Optional[Request]) -> Optional[str]:
    """Het id van het ingelogde account, of None. Komt uit de sessie — een
    zelfgekozen header telt niet meer mee."""
    user = auth.user_for_request(request)
    return user["id"] if user else None


def check_owner(obj: dict[str, Any], request: Optional[Request]) -> None:
    """Niet meer nodig: sinds accounts staat alle gebruikersdata in de naamruimte
    van de eigenaar, dus je kúnt niet bij die van een ander. Bewust laten staan
    als no-op zodat oude aanroepen niet stilletijgend iets anders gaan doen."""
    return None


# suffix -> logisch bestandstype
SUPPORTED_SUFFIXES: dict[str, str] = {
    ".pdf": "pdf",
    ".ppt": "ppt",
    ".pptx": "pptx",
    ".docx": "docx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
}

FileType = Literal["pdf", "ppt", "pptx", "docx", "image"]


def file_signature_ok(suffix: str, data: bytes) -> bool:
    """Controleert of de échte bytes bij de extensie passen (magic bytes). Zo
    kan een .pdf die eigenlijk iets anders is niet ongemerkt de verwerking in —
    scheelt kapotte conversies en sluit een simpele vermommings-truc uit."""
    head = data[:16]
    if suffix == ".pdf":
        return head.startswith(b"%PDF")
    if suffix == ".ppt":
        # Het klassieke PowerPoint-formaat gebruikt de OLE/CFB-container.
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if suffix in (".pptx", ".docx"):
        # Office-bestanden zijn ZIP-containers: PK\x03\x04 (of lege/multi-part ZIP).
        return head[:2] == b"PK"
    if suffix == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if suffix == ".webp":
        return head[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def assess_image_quality(data: bytes) -> Optional[dict[str, Any]]:
    """Snelle kwaliteitsheuristiek op een geüploade FOTO (alleen zinvol voor de
    snel-foto-knop; een PDF/PowerPoint is altijd scherp). Geeft None als de foto
    prima is, of {"issues": [...]} met codes "blurry" en/of "dark" als hij lastig
    te lezen is — de app waarschuwt dan en biedt "opnieuw maken" aan. NOOIT
    blokkerend: dit is een vriendelijke tip, geen harde eis. Drempels zijn
    instelbaar (IMG_SHARPNESS_MIN / IMG_BRIGHTNESS_MIN) en bewust conservatief:
    liever een wazige foto missen dan een goede foto afkeuren."""
    try:
        from PIL import Image, ImageFilter, ImageStat
        im = Image.open(io.BytesIO(data)).convert("L")
    except Exception:
        return None  # onleesbaar als afbeelding => geen oordeel, niet hinderen
    im.thumbnail((1024, 1024))  # stabiele, resolutie-onafhankelijke meting
    brightness = ImageStat.Stat(im).mean[0]  # 0 (zwart) .. 255 (wit)
    # Ruis-robuuste scherpte: eerst licht ontruizen (Gaussian blur), dán de
    # variantie van de Laplaciaan. Echte structuur overleeft die lichte blur;
    # sensorruis (bij weinig licht) wordt onderdrukt, zodat ruis niet als
    # "scherpte" meetelt — anders scoort juist een korrelige foto te hoog.
    smooth = im.filter(ImageFilter.GaussianBlur(1))
    lap = smooth.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1))
    sharpness = ImageStat.Stat(lap).var[0]

    issues: list[str] = []
    if sharpness < float(os.getenv("IMG_SHARPNESS_MIN", "120")):
        issues.append("blurry")
    if brightness < float(os.getenv("IMG_BRIGHTNESS_MIN", "100")):
        issues.append("dark")
    if not issues:
        return None
    return {"issues": issues, "sharpness": round(sharpness, 1), "brightness": round(brightness, 1)}


def page_label_for(file_type: str) -> str:
    return "dia" if file_type in ("ppt", "pptx") else "pagina"


def get_document_info(file_hash: str) -> tuple[str, Path]:
    for suffix, file_type in SUPPORTED_SUFFIXES.items():
        path = UPLOAD_DIR / f"{file_hash}{suffix}"
        if path.exists():
            return file_type, path
    # Lokale kopie kwijt (bv. na een deploy zonder persistente schijf) — het
    # brondocument terughalen uit de Supabase-backup als die er is.
    for suffix, file_type in SUPPORTED_SUFFIXES.items():
        path = cache_store.blob_local_path("uploads", f"{file_hash}{suffix}")
        if path is not None:
            return file_type, path
    raise_api_error(404, "DOCUMENT_NOT_FOUND", "Bestand niet gevonden.")


# =========================================================
# TEKST-EXTRACTIE
# =========================================================

def extract_pdf_texts(path: Path) -> list[str]:
    doc = fitz.open(str(path))
    try:
        return [(doc.load_page(i).get_text("text") or "").strip() for i in range(len(doc))]
    finally:
        doc.close()


def extract_shape_text(shape) -> str:
    parts: list[str] = []
    try:
        if hasattr(shape, "text") and (shape.text or "").strip():
            parts.append(shape.text.strip())
    except Exception:
        pass
    try:
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    if (cell.text or "").strip():
                        parts.append(cell.text.strip())
    except Exception:
        pass
    try:
        if hasattr(shape, "shapes"):
            for inner in shape.shapes:
                inner_text = extract_shape_text(inner)
                if inner_text:
                    parts.append(inner_text)
    except Exception:
        pass
    return "\n".join(dict.fromkeys(parts)).strip()


def extract_pptx_texts(path: Path) -> list[str]:
    prs = Presentation(str(path))
    texts = []
    for slide in prs.slides:
        parts = [extract_shape_text(shape) for shape in slide.shapes]
        texts.append("\n".join(p for p in parts if p).strip())
    return texts


def text_cache_path(file_hash: str) -> Path:
    return TEXT_CACHE_DIR / f"{file_hash}.json"


def extract_texts_for(file_type: str, path: Path, file_hash: str) -> list[str]:
    if file_type == "pdf":
        return extract_pdf_texts(path)
    if file_type == "pptx":
        return extract_pptx_texts(path)
    if file_type == "ppt":
        # python-pptx ondersteunt alleen OOXML (.pptx). Klassieke binaire
        # presentaties worden daarom eerst door LibreOffice naar PDF omgezet.
        pdf_path = get_pdf_for_document(file_hash)
        if not pdf_path:
            raise RuntimeError("PPT kon niet naar PDF worden omgezet (LibreOffice nodig).")
        return extract_pdf_texts(pdf_path)
    if file_type == "image":
        return [""]  # geen tekstlaag; de AI leest de afbeelding zelf
    # docx: tekst uit de geconverteerde PDF halen (geeft ook het juiste aantal pagina's)
    pdf_path = get_pdf_for_document(file_hash)
    if not pdf_path:
        raise RuntimeError("DOCX kon niet naar PDF worden omgezet (LibreOffice nodig).")
    return extract_pdf_texts(pdf_path)


@lru_cache(maxsize=64)
def _document_texts_cached(file_hash: str) -> tuple[str, tuple[str, ...]]:
    """In-memory cache: de teksten zijn content-addressed (hash) en dus onveranderlijk,
    zodat niet elke request opnieuw het volledige tekst-JSON van schijf hoeft te lezen."""
    cached = load_json(text_cache_path(file_hash))
    if cached and cached.get("file_type") in ("pdf", "ppt", "pptx", "docx", "image") and isinstance(cached.get("texts"), list):
        return cached["file_type"], tuple(str(t) for t in cached["texts"])

    file_type, path = get_document_info(file_hash)
    texts = extract_texts_for(file_type, path, file_hash)
    save_json(text_cache_path(file_hash), {"file_type": file_type, "texts": texts})
    return file_type, tuple(texts)


def get_document_texts(file_hash: str) -> tuple[str, list[str]]:
    file_type, texts = _document_texts_cached(file_hash)
    return file_type, list(texts)


# =========================================================
# PPTX -> PDF EN AFBEELDINGEN RENDEREN
# =========================================================

def find_libreoffice_executable() -> Optional[str]:
    candidates = [
        shutil.which("libreoffice"),
        shutil.which("soffice"),
        "/usr/bin/libreoffice",
        "/usr/bin/soffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


# De conversie draait sinds v3.3 op de achtergrond ná de upload-response; deze
# lock voorkomt dat een gelijktijdig verzoek (bijv. /slide-image) LibreOffice
# een tweede keer op hetzelfde bestand loslaat.
_convert_locks_guard = threading.Lock()
_convert_locks: dict[str, threading.Lock] = {}


def _conversion_lock(file_hash: str) -> threading.Lock:
    with _convert_locks_guard:
        return _convert_locks.setdefault(file_hash, threading.Lock())


def convert_office_to_pdf(path: Path, file_hash: str) -> Optional[Path]:
    """Zet PPT, PPTX of DOCX om naar PDF via LibreOffice."""
    out_dir = PDF_DIR / file_hash
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{file_hash}.pdf"
    if out_pdf.exists():
        return out_pdf

    with _conversion_lock(file_hash):
        return _convert_office_to_pdf_locked(path, out_dir, out_pdf)


def _convert_office_to_pdf_locked(path: Path, out_dir: Path, out_pdf: Path) -> Optional[Path]:
    if out_pdf.exists():  # net al geconverteerd door een ander verzoek
        return out_pdf

    libreoffice = find_libreoffice_executable()
    if not libreoffice:
        logger.warning("LibreOffice niet gevonden; %s kan niet worden omgezet.", path.suffix)
        return None

    try:
        subprocess.run(
            [libreoffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(path)],
            check=True, capture_output=True, timeout=180,
        )
        candidates = list(out_dir.glob("*.pdf"))
        if not candidates:
            return None
        if candidates[0] != out_pdf:
            candidates[0].replace(out_pdf)
        return out_pdf
    except Exception:
        logger.exception("Office->PDF conversie mislukt voor %s", path)
        return None


def image_to_pdf(path: Path, file_hash: str) -> Optional[Path]:
    """Verpakt een losse afbeelding (foto van aantekeningen/bord) in een 1-pagina-PDF,
    zodat de hele bestaande render/uitleg-pipeline er gewoon mee werkt."""
    out_dir = PDF_DIR / file_hash
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{file_hash}.pdf"
    if out_pdf.exists():
        return out_pdf
    try:
        from PIL import Image
        with Image.open(path) as img:
            img.convert("RGB").save(out_pdf, "PDF", resolution=150)
        return out_pdf
    except Exception:
        logger.exception("Afbeelding->PDF mislukt voor %s", path)
        return None


def get_pdf_for_document(file_hash: str) -> Optional[Path]:
    file_type, original_path = get_document_info(file_hash)
    if file_type == "pdf":
        return original_path
    if file_type == "image":
        return image_to_pdf(original_path, file_hash)
    return convert_office_to_pdf(original_path, file_hash)


Resolution = Literal["display", "ai"]


def page_image_path(file_hash: str, page_index: int, resolution: Resolution) -> Path:
    return IMAGE_DIR / file_hash / resolution / f"page_{page_index}.jpg"


def render_scale_for(resolution: Resolution) -> float:
    return PDF_RENDER_SCALE_AI if resolution == "ai" else PDF_RENDER_SCALE_DISPLAY


def ensure_slide_image(file_hash: str, page_index: int, resolution: Resolution = "display") -> Optional[Path]:
    target = page_image_path(file_hash, page_index, resolution)
    if target.exists():
        return target

    pdf_path = get_pdf_for_document(file_hash)
    if not pdf_path or not pdf_path.exists():
        return None

    scale = render_scale_for(resolution)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(str(pdf_path))
        try:
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            pix.save(str(target), jpg_quality=SLIDE_JPEG_QUALITY)
        finally:
            doc.close()
        return target
    except Exception:
        logger.exception("Renderen mislukt: %s pagina %s", file_hash, page_index)
        return None


def prerender_display_range(file_hash: str, start: int, end: int) -> None:
    """Rendert een reeks display-afbeeldingen met de PDF één keer open
    (veel sneller dan per pagina openen via ensure_slide_image)."""
    missing = [i for i in range(max(0, start), end)
               if not page_image_path(file_hash, i, "display").exists()]
    if not missing:
        return
    pdf_path = get_pdf_for_document(file_hash)
    if not pdf_path or not pdf_path.exists():
        return
    scale = render_scale_for("display")
    try:
        doc = fitz.open(str(pdf_path))
        try:
            for i in missing:
                if i >= len(doc):
                    break
                target = page_image_path(file_hash, i, "display")
                target.parent.mkdir(parents=True, exist_ok=True)
                pix = doc.load_page(i).get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                pix.save(str(target), jpg_quality=SLIDE_JPEG_QUALITY)
        finally:
            doc.close()
    except Exception:
        logger.exception("Bulk-prerender mislukt voor %s", file_hash)


def slide_image_url(file_hash: str, page_index: int) -> str:
    return f"/slide-image/{file_hash}/{page_index}"


# =========================================================
# DE TUTOR-PROMPT (het hart van de kwaliteit)
# =========================================================

def build_system_instruction(
    language: str,
    detail_level: str,
    is_last_page: bool,
    mode: str = "explain",
    audience_level: str = "intermediate",
) -> str:
    if language.strip().lower() in ("", "auto"):
        language_rule = (
            "Write your ENTIRE response in the same language as the slide itself "
            "(detect it from the slide). If the slide is mixed-language, use the dominant language."
        )
    else:
        language_rule = f"Write your ENTIRE response in {language}, regardless of the slide's language."

    detail_rule = {
        "short": (
            "HARD LIMIT: at most 60 words, one single paragraph. No headers, no bullets, no sections. "
            "One sentence naming the point of the slide, then 1-3 sentences with only the key conclusion "
            "or insight (the answer, the condition, the one thing to remember). At most ONE formula, and "
            "only if the formula itself is the point. Do not describe visuals, do not list suggestions."
        ),
        "normal": (
            "ADAPTIVE LENGTH — completeness inside the slide's scope matters more than hitting a tiny word count. "
            "A normal substantive slide will usually need 120-220 words; a simple slide may need only 60-110, "
            "while a genuinely dense process, comparison, table or derivation may use up to 300 words. "
            "Never pad, but never omit a necessary step, definition, branch, panel or causal link merely to stay short. "
            "Open with the core idea, then teach all supporting steps needed to understand it. The student sees the "
            "slide next to your text: explain what the meaningful elements DO and HOW they connect instead of "
            "transcribing labels. Use short paragraphs and at most 3 useful sections. For derivations, show every "
            "conceptual step but compress routine arithmetic. A sparse or administrative slide still gets only "
            "1-3 sentences. Remove repetition, side cases and nice-to-know trivia — not explanatory substance."
        ),
        "long": "Be thorough: give a full walkthrough with underlying reasoning, all derivation steps and common misconceptions.",
    }[detail_level]

    mode_rule = {
        "explain": "",
        "simple": (
            "SIMPLE MODE: explain in extra simple language. Short sentences, everyday words, "
            "explain every technical term the moment you use it, and use one recognizable analogy "
            "where it genuinely helps. Keep the math, but walk through it more gently."
        ),
        "study": (
            "STUDY MODE: produce a compact study summary instead of a narrative walkthrough. "
            "Structure: what this slide teaches (1 sentence), the things you MUST remember as short bullets, "
            "the key formula(s) in LaTeX, and one exam tip. No long paragraphs."
        ),
    }[mode]

    audience_rule = {
        "beginner": (
            "The student is a beginner: use simple wording and spell out implicit steps. "
            "The first time a genuinely hard term appears (e.g. fistel, atresie, aspiratie), "
            "explain it in plain words right there in the same sentence — a short appositive "
            "like 'een fistel, oftewel een abnormale verbinding tussen twee organen' — not just "
            "an abbreviation or a Latin synonym. Only the truly unfamiliar terms, kept brief so "
            "you stay within the length budget."
        ),
        "intermediate": (
            "The student has intermediate knowledge: normal technical vocabulary is fine and you need not "
            "define standard terms (at most a synonym once in parentheses). Do briefly justify the key steps "
            "and the 'why' behind them, but don't spell out the basics."
        ),
        "advanced": (
            "The student is advanced: be markedly more compact than for the other levels. Assume fluency with "
            "the fundamentals and spend your words on interpretation, nuance, trade-offs, edge cases or why it "
            "matters — not on restating what an advanced student already knows."
        ),
    }[audience_level]

    if detail_level == "short":
        ending_rule = (
            "This is the LAST page: close with one warm sentence. Do not suggest a next slide."
            if is_last_page else
            "End the moment the point is made. NEVER end with a question or an invitation to think "
            "something out — everything the student needs from this slide belongs in the explanation itself."
        )
        structure_rules = f"""STRUCTURE OF YOUR EXPLANATION
- ONE single paragraph, nothing else. No greetings, no filler, no headers, no bullets.
- Sentence 1: the point of this slide. Then straight to the key conclusion or insight.
- {ending_rule}"""
        derivation_rule = "- Do not walk through derivations; give only the resulting formula or conclusion if it is the point of the slide."
        visuals_rules = """VISUALS
- Mention a figure only if it carries the point of the slide; give its single takeaway in one sentence."""
    else:
        ending_rule = (
            "This is the LAST page: teach only its content and stop. Do not add a farewell, good-luck sentence, "
            "document recap or suggestion for a next slide."
            if is_last_page else
            "Stop as soon as the point is taught. Do not add a summary that merely repeats the opening. "
            "NEVER end with a question, mini-exercise, invitation or generic exam filler. You cannot see the "
            "next slide, so never state or guess what comes next."
        )
        structure_rules = f"""STRUCTURE — choose the form that teaches THIS slide best; do not force one template
- Open by teaching the core idea directly, in varied wording. Do NOT open by announcing or describing the slide itself in ANY language — never start with the equivalent of "this slide/diagram/image shows / explains / is about / introduces / describes ..." (NL "Deze slide ...", EN "This slide ...", FR "Cette diapositive ...", ES "Esta diapositiva ...", DE "Diese Folie ..."). Begin with the actual subject matter, and don't let the opening sentence just preview what your bullets then repeat.
- Fit the shape to the content and vary it across slides: flowing prose for a concept or an argument; a bulleted list ONLY when the slide really enumerates items (symptoms, steps, options); a short worked example when a small calculation makes it click. Do not pour every slide into the same header-plus-bullets mold. A bold lead-in on a list item is optional — never let a "term: one sentence" list flatten reasoning into a glossary.
- Teach, don't just describe: show the key step or the "why" (e.g. derive the vertex from x = -b/(2a), don't just state "the top is at 1.5"), and name a common trap in a few words; when two items look alike (aspiratiepneumonie vs. luchtweginfectie), spell out the difference.
- Build a complete mental model, not a caption. Connect starting state -> trigger -> intermediate change(s) -> result. If the slide also visibly shows regulation, reversal or shutdown, include that too. Do not jump from the first label straight to the final outcome.
- Respect the scope promised by the title. If the title announces several types but the current slide teaches only one, say briefly where this one fits without inventing or teaching absent types. If several items ARE actually taught on the current slide, cover every essential one.
- Treat visual emphasis as teaching emphasis: a box, circle, arrow, colour contrast or enlarged item normally identifies the main learning target. If exactly one case, row or answer is explicitly highlighted, explain ONLY that item using its conclusion plus 2-3 visible clues. Do not repeat names, labels, numbers or diagnoses from unhighlighted cases. This rule overrides any general instruction to compare similar items.
- Keep it scannable and let it breathe (short paragraphs, a blank line between parts), but scannability serves understanding — never drop the reasoning just to make a tidy list.
- Match length to substance: a rich slide earns more, a thin or administrative slide gets only a few sentences. Never pad to fill a template.
- Headers (when you use them) in sentence case for the answer's language. An emoji is optional and at most ONE at the END of a header — but use NONE on serious, clinical or somber topics, where it reads as flippant.
- {ending_rule}"""
        derivation_rule = (
            "- When a derivation matters, walk through it step by step: one displayed equation per step with one short sentence of reasoning."
            if detail_level == "long" else
            "- For derivations: only the essential steps and the conclusion; summarize routine algebra in words."
        )
        visuals_rules = """VISUALS (graphs, diagrams, block schemes)
- Say what is on the axes, what the curves/branches do, and what the colors, zones or markers mean.
- Point to concrete specifics you can actually see (a colour, a label, a marked point, an arrow, the "R" on the X-ray) rather than a generic "de figuur toont ...".
- Tell the student where to look first and what the ONE takeaway of the figure is.
- Be precise about visual claims: a curve that comes close to a point does not necessarily pass through it. If something is genuinely ambiguous in the image, say so instead of guessing.
- Never confuse a LOWER plateau with reaching a plateau EARLIER; describe vertical value and horizontal position separately. For derived graph quantities, apply the definition to each curve's own reference value (for example $K_M$ is read at half of that curve's own $V_{max}$).
- For a multi-panel figure, give one causal sentence that links the panels and covers each panel's distinct contribution. For a cyclic process diagram, prefer one compact cause-to-effect sequence over a numbered inventory.
- For a process diagram, walk through it in the visual order (usually left-to-right or top-to-bottom): name the resting state, the activating event, what changes or separates, which target is affected, and the resulting response. Explain state labels such as active/inactive and abbreviations that are necessary for following the mechanism.
- Arrows are relationships, not decoration: put the causal meaning of every essential arrow into words. Use precise verbs (binds, changes shape, exchanges, phosphorylates, activates, inhibits) instead of vague shortcuts such as "something happens" or "it splits".
- Compare adjacent panels explicitly before describing the transition. If one complex becomes two visibly separate active parts, say that the parts separate and keep them distinct; do not later describe them as one moving unit. If only one part contacts the next target, attribute that action only to that part.
- Do not infer that something is embedded in a membrane merely because it is drawn next to it. Distinguish transmembrane, membrane-associated and cytosolic components only when the drawing, labels or supplied text supports that distinction.
- Distinguish arrows BETWEEN panels (usually time/state progression) from arrows INSIDE a panel (often movement, activation or inhibition). Never turn a panel-transition arrow into a claim that a molecule physically moves.
- Count components before and after each transition. If one complex is visibly separated into multiple active parts, the final wording must name those parts separately or call them "the separated parts"; a later singular reference to the original complex is then inaccurate.

CHARTS (draw a graph only when it GENUINELY helps understanding)
- You MAY include AT MOST ONE chart, and only when seeing it plotted makes the concept click (the shape of a function, a trend, a comparison) — never decorative, never for an administrative or purely textual slide. When in doubt, leave it out.
- Emit the chart as a single fenced code block that starts with ```chart and contains ONLY this JSON:
```chart
{"kind":"function","title":"...","xlabel":"x","ylabel":"y","fn":"x^2 - 3*x + 2","domain":[-2,5]}
```
  For a mathematical function use kind "function": give the expression in "fn" (variable x; allowed: + - * / ^, parentheses, sin cos tan asin acos atan sqrt exp log ln abs, pi, e) and the visible range in "domain":[min,max]. Do NOT compute the points yourself — the app evaluates the function exactly.
  For data/statistics use kind "line", "bar" or "scatter" with "labels":[...] and "series":[{"label":"...","points":[[x,y],...]}] (values taken from the material, not invented).
- The chart supplements your words; still explain the takeaway in text. Keep the JSON minimal and valid."""

    # STUDY / "Kernpunten": een aparte, strakke structuur die de normale
    # uitleg-structuur volledig vervangt — anders leest het als een gewone uitleg.
    if mode == "study" and detail_level != "short":
        structure_rules = """STRUCTURE — this is the "key points" view: distil to the essentials only, NOT a full explanation
- No narrative opener and NO closing question. Go straight to the takeaways.
- List ONLY what a student must remember from THIS slide, as short bullets (a few words up to one line each). Cut everything non-essential.
- Use at most 2 short headers if the slide has clearly distinct parts; otherwise one plain bullet list. Keep the slide's own logic (both branches of a condition).
- Include the key formula(s) in LaTeX if present, and at most ONE short exam tip ("Op het tentamen: ...") only when it genuinely helps.
- Be SHORT: aim well under 100 words — roughly half a normal explanation. Never a paragraph of prose."""
        detail_rule = "Tight key-points only; well under 100 words. No narrative, no wrap-up question."
        mode_rule = ""  # de study-instructie zit nu volledig in de structuur hierboven

    if detail_level == "normal" and mode != "study":
        final_contract = """FINAL OUTPUT CONTRACT — check this immediately before returning the answer
- Use the shortest length that still teaches the slide completely: usually 120-220 words, 60-110 for genuinely simple slides, and at most 300 for dense multi-step material. Never print a word count.
- Teach the central learning objective AND every supporting step needed to understand it. If one item is visually highlighted, discuss only that item, but explain that item fully.
- Before returning, mentally trace the explanation against the image from start to finish. A beginner should not need to guess what an arrow means, why a state changes, how a conclusion follows, or what an essential unfamiliar term means.
- Prefer a clear cause-and-effect chain over a compressed catalogue. Use short paragraphs; bullets only when they make a genuine list or comparison easier to follow.
- Start with this slide's specific teaching point, never with a ranking, prevalence claim or broad textbook fact. Keep visible facts and added background unmistakably separate.
- Do not use importance adjectives such as "largest", "most important", "major" or "common" unless the current material supports them and they serve the learning goal. If a heading promises a numbered category and the body teaches one member, position it directly as "one of those [number] types".
- Every concrete claim must be visible in the current slide or necessary and supported by supplied earlier context. Delete merely plausible additions.
- For experimental results, say "wijst op"/"supports" rather than "bewijst"/"proves" and never infer a patient-specific result without explicit evidence.
- A class/category percentage never belongs automatically to the example printed under it. Use two separate clauses: "88% has a class II mutation; F508del is one example" — never "88% has F508del".
- For a control image, state only the visible baseline change unless its biological cause is explicitly established. Do not append a treatment implication to a classification slide.
- No repeated conclusion, greeting, farewell or filler. Do not end early just because the main conclusion has been named. Return only the finished explanation."""
    else:
        final_contract = "Return only the finished explanation and obey the length and structure rules above."

    return f"""You are an outstanding university tutor inside a study app. The student sees the slide image on the left of the screen and your explanation on the right. You explain lecture slides one at a time, as if you are a calm, sharp teacher walking through the deck with the student.

THE SLIDE IMAGE IS YOUR PRIMARY SOURCE OF TRUTH.
Look at it carefully: titles, formulas, graphs, diagrams, tables, colors, arrows, handwritten annotations, circled answers. The extracted text you also receive is only a fallback for hard-to-read parts — the layout and visuals only exist in the image.

SILENT ACCURACY PASS — do this internally before writing; never print this checklist
1. State the ONE learning objective of this slide in your own mind.
   If the title is a question, that exact question defines the scope: answer it directly and do not widen to the whole surrounding process.
2. Identify the exact visual evidence for every number, label, relation and conclusion you intend to mention.
3. Separate slide evidence from outside knowledge. Include outside knowledge only when indispensable to understand the central mechanism. Do not add a disease label, treatment application, prognosis or diagnosis merely because it is commonly associated with the pictured method. Introduce indispensable context briefly instead of pretending it is shown.
   Do not add illustrative organs, diseases, scenarios or examples that are absent from the current slide and supplied context.
4. Check boxes, arrows, colours, axes, legends, units, footnotes and the meaning of percentages. If groups may overlap (for example "at least one"), say so; never imply that overlapping percentages must total 100%. A percentage printed for a class/category belongs to that entire category, never automatically to the example item printed beneath it.
5. For every before/after or multi-panel process, silently inventory the visible components in EACH panel and compare their states. Do not merge two shapes that separate, omit a component that changes, or claim that a component moves or binds unless the arrow/layout actually supports it.
6. Remove any claim you cannot verify, every non-essential side case and every repeated conclusion. Also remove true-but-unneeded trivia, rankings and prevalence claims (such as "the largest family") when they do not help explain this slide.
7. Perform a coverage check: did you explain every meaning-bearing part needed to answer the title or learning goal, including each essential arrow, branch, panel, row or equation? Accuracy comes first; completeness within that scope comes second; brevity comes third.

NON-NEGOTIABLE SOURCE DISCIPLINE
- Silently make a ledger of: visible component -> visible location -> initial state -> visible change -> resulting relation. Base the walkthrough on that ledger.
- Every sentence must either explain a visible meaning-bearing element/relationship, define a necessary term, or supply the one minimal fact without which the visible mechanism cannot be understood. Delete rankings, prevalence, historical facts and textbook trivia even when true.
- Never present a standard textbook detail as if it is shown. Molecular names, nucleotide exchanges, subunit identities and intermediate steps that are absent from the current image/text may be added only when indispensable; introduce them explicitly as brief background and never let them replace the visible explanation.
- Location words require evidence. Do not say several components are "in the membrane" merely because they are drawn nearby; name each location only when supported, or use the neutral phrase "at the cell membrane" for the system as a whole.
- Do not finish with a summary that repeats the opening. Use those words to explain a missing link instead.

STAY FAITHFUL TO THE MATERIAL — do not distort or invent
- Keep the slide's own logic intact. If a point has two branches ("presence OR absence of gas", "if X then A, otherwise B"), explain BOTH — never silently drop half of a stated condition, because that changes the meaning.
- Never invent a missing value or claim that a value is absent without checking the image and extracted text. If those two sources genuinely conflict, say what is legible and name the uncertainty in one short sentence.
- For tables or collections of clinical cases, centre the explanation on the row/case that is visually emphasised or required by the title. Do not diagnose or narrate every other case unless the slide explicitly asks for a full comparison.
- For one highlighted clinical case, use exactly this content shape: state the diagnosis/conclusion, then explain only 2-3 visible clues. Do not add an analogy, biochemical mechanism, treatment or comparison with the unhighlighted cases unless the current slide explicitly asks for it.
- Never assign a case letter or row label unless that label itself is clearly legible. Otherwise call it "the highlighted/boxed case" in the answer's language.
- A single measurement cannot prove that a value is stable, chronic or lifelong. Do not infer absent symptoms, a chance discovery or a causal role for BMI unless the current case explicitly states it; say only that the visible value fits the supplied diagnostic range.
- Do not add treatment advice, prognosis or complication risk to a diagnostic case unless the current slide explicitly makes it part of the learning objective.
- For a classification diagram, use at most one introductory sentence, then follow its visible process from left to right. Preserve the slide's exact distinctions (for example no functional protein versus misfolding/transport versus gating versus conductance); do not generalise them into near-synonyms.
- For experiments and graphs, distinguish observation from interpretation: first state only the visible change and the control comparison, then give the minimal mechanism supported by it. Use "wijst op"/"supports" rather than "bewijst"/"proves" unless the design truly establishes the claimed conclusion. Do not explain a tiny apparent difference that may be image noise.
- Describe a control as the baseline/reference condition shown. Do not reduce "control" to "no treatment" or assign a biological reason for its appearance unless the labels or supplied context establish that reason.
- Never turn an organoid, cell or group result into a claim about a specific patient unless the slide or supplied context explicitly links the sample to that patient.
- You can see only the CURRENT slide plus short summaries of PREVIOUS slides. Never state or guess what a LATER slide contains, and refer back to an earlier slide only when the given context truly supports it — do not claim continuity ("zoals we eerder zagen") that you cannot verify.
- READABILITY OF THE IMAGE — this is critical. If the slide photo is blurry, dark, noisy, skewed, low-resolution or otherwise hard to read, or if you cannot actually make out specific labels, values, symbols or connections, SAY SO in one short sentence and explain only what you can genuinely see. Do NOT fill in specific names, numbers, formulas, answers or a specific configuration from what such a slide "usually" contains — recognising a familiar shape (a graph, a circuit, a structure) is NOT the same as having read it. When you are inferring the type from a general shape rather than reading the details, phrase it as a likelihood ("dit lijkt op ...") and invite the student to check the labels on the slide themselves. On a clearly legible slide, stay fully confident and do not hedge.

{structure_rules}

MATHEMATICS — STRICT RULES
- Render ALL mathematics as LaTeX: inline math in $...$ and standalone equations in $$...$$ on their own line.
- Every variable, Greek letter or symbol in running text also goes in $...$: write "$\\zeta$" not "zeta", "$\\omega_n$" not "wn".
- Never write formulas as plain text or unicode approximations.
{derivation_rule}

{visuals_rules}

MULTIPLE-CHOICE QUESTIONS AND EXERCISES ON THE SLIDE
- State the correct answer immediately and clearly (e.g. "Het juiste antwoord is **B**."), then explain step by step why, and briefly why the tempting alternatives are wrong if relevant.
- If an answer is circled or marked by hand on the slide, treat that as the teacher's marking and verify it against your own reasoning.

HANDWRITTEN ANNOTATIONS
- Treat handwriting as the teacher's notes made during the lecture: read it, use it, and weave it into the explanation ("De handgeschreven notitie geeft aan dat ...").
- If handwriting is illegible, do not invent content.

TONE AND LENGTH
- Direct, warm and didactic, like an excellent teacher. Active sentences. No academic jargon walls, no "Op deze dia zien we..." padding, no closing disclaimers.
- ONE consistent voice throughout. Explain in plain declarative sentences ("De sonde krult op in de bovenste zak"). You may point to the image, but do it consistently — do not switch back and forth between formal prose and scattered "Kijk naar..." instructions.
- TERMINOLOGY: stay consistent — introduce a term once and then keep using that SAME term; never alternate between synonyms. HOW deeply you explain a hard term depends on the audience level below (beginner: a brief plain-words explanation in the sentence; intermediate: at most a synonym in parentheses once; advanced: just the precise term).
- At beginner level, define an unfamiliar technical term used in the slide title in 3-8 plain words the first time it appears.
- {detail_rule}
- {audience_rule}
{f"- {mode_rule}" if mode_rule else ""}
- Administrative slides (title page, agenda, break, Wooclap, references) get only a few friendly sentences — never a fabricated deep-dive.

LANGUAGE
- {language_rule}
- The explanation must read as if originally written in that language, never as a translation.

{final_contract}

Return pure markdown only: no meta-commentary about these instructions, and do not wrap your whole answer in a code fence or in JSON. The ONLY code fence you may use is a single ```chart block as described above, and only when a chart genuinely helps."""


def build_context_message(
    *,
    file_name: str,
    file_type: str,
    page_index: int,
    total_pages: int,
    slide_text: str,
    previous_texts: list[str],
    has_image: bool = True,
) -> str:
    label = page_label_for(file_type).capitalize()
    parts = [
        f"Document: {file_name}",
        f"{label} {page_index + 1} van {total_pages}.",
    ]

    if previous_texts:
        prev_blocks = []
        for offset, prev in enumerate(previous_texts, start=1):
            idx = page_index + 1 - (len(previous_texts) - offset + 1)
            trimmed = truncate(clean_text(prev), MAX_PREV_SLIDE_TEXT)
            if trimmed:
                prev_blocks.append(f"[{label} {idx}]\n{trimmed}")
        if prev_blocks:
            parts.append(
                "Context van de vorige dia's (alleen om op voort te bouwen, niet om opnieuw uit te leggen):\n"
                + "\n\n".join(prev_blocks)
            )

    slide_text = truncate(clean_text(slide_text), MAX_SLIDE_TEXT_VISION if has_image else MAX_SLIDE_TEXT)
    if slide_text:
        parts.append(f"Geëxtraheerde tekst van de huidige {label.lower()} (fallback voor de afbeelding):\n{slide_text}")
    else:
        parts.append(f"Er is geen tekst uit deze {label.lower()} geëxtraheerd; baseer je volledig op de afbeelding.")

    parts.append("Leg deze dia nu uit aan de student.")
    return "\n\n".join(parts)


# =========================================================
# AI-AANROEP MET PROVIDER-FALLBACK (via ai_engine)
# =========================================================

def image_part(image_path: Path) -> ai_engine.Part:
    return ai_engine.image_part(image_path)


def build_contents(
    *,
    ai_image: Optional[Path],
    context_message: str,
    question: Optional[str],
    history: list[ChatTurn],
) -> list[Message]:
    first_parts: list[ai_engine.Part] = []
    if ai_image and ai_image.exists():
        first_parts.append(image_part(ai_image))
    first_parts.append(text_part(context_message))

    contents = [Message(role="user", parts=first_parts)]

    # Alleen de laatste beurten meesturen: oudere chat over dezelfde dia voegt
    # zelden context toe maar kost elke vervolgvraag opnieuw tokens.
    for turn in history[-MAX_HISTORY_TURNS:]:
        role = "model" if turn.role in ("assistant", "model") else "user"
        content = truncate((turn.content or "").strip(), MAX_HISTORY_CHARS)
        if content:
            contents.append(Message(role=role, parts=[text_part(content)]))

    if question and question.strip():
        contents.append(
            Message(
                role="user",
                parts=[text_part(
                    "Vervolgvraag van de student over deze dia. Beantwoord alleen deze vraag, "
                    "direct en to-the-point, in dezelfde taal en stijl als je uitleg:\n\n"
                    + question.strip()
                )],
            )
        )

    return contents


# Sommige fallback-modellen verpakken het hele antwoord in ```-fences (Gemini
# doet dat niet). Dit strippen houdt de output van alle providers identiek,
# zodat een provider-wissel niet zichtbaar is. Alleen "lege" of markdown-tags
# worden gestript; ```python e.d. is een echt codeblok en blijft staan.
_FENCE_OPEN_RE = re.compile(r"^```(?:markdown|md)?[ \t]*\r?\n", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"\r?\n?```[ \t]*$")


def strip_wrapping_fences(markdown: str) -> str:
    text = (markdown or "").strip()
    if text.startswith("```") and text.endswith("```"):
        opened = _FENCE_OPEN_RE.sub("", text, count=1)
        if opened != text:
            return _FENCE_CLOSE_RE.sub("", opened).strip()
    return text


def _clean_markdown_stream(chunks: Iterator[str]) -> Iterator[str]:
    """Streamende variant van strip_wrapping_fences: houdt het begin vast tot
    duidelijk is of het antwoord in een fence is verpakt, plus een klein
    staartje om een eventuele sluitfence weg te knippen."""
    head, deciding, opened, tail = "", True, False, ""
    for text in chunks:
        if deciding:
            head += text
            probe = head.lstrip()
            if not probe or (probe.startswith("`") and "\n" not in probe and len(probe) < 24):
                continue  # nog te weinig tekst om over een openingsfence te beslissen
            match = _FENCE_OPEN_RE.match(probe)
            if match:
                probe = probe[match.end():]
                opened = True
            deciding = False
            text, head = probe, ""
        if not text:
            continue
        tail += text
        if len(tail) > 8:
            yield tail[:-8]
            tail = tail[-8:]
    if deciding:
        final = strip_wrapping_fences(head)
        if final:
            yield final
        return
    if opened:
        tail = _FENCE_CLOSE_RE.sub("", tail)
    if tail:
        yield tail


def generate_markdown(contents: list[Message], system_instruction: str) -> tuple[str, str]:
    """Niet-streamend genereren, met automatische provider-fallback. Geeft (markdown, model) terug."""
    last_error: Optional[Exception] = None
    for candidate in ai_engine.candidates():
        started = time.perf_counter()
        try:
            markdown = strip_wrapping_fences(candidate.generate(contents, system_instruction, TEMPERATURE))
            if not markdown:
                raise RuntimeError("Leeg antwoord van model")
            ai_engine.report_success(candidate, latency_ms=(time.perf_counter() - started) * 1000)
            return markdown, candidate.label
        except Exception as e:
            ai_engine.report_failure(candidate, e)
            last_error = e

    raise_api_error(
        502, "AI_GENERATION_FAILED",
        humanize_ai_error(last_error),
        {**debug_reason(last_error), "models_tried": ai_engine.available_models()},
    )


def humanize_ai_error(error: Optional[Exception]) -> str:
    return ai_engine.humanize_error(error)


def sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def stream_markdown(
    contents: list[Message],
    system_instruction: str,
    cache_key: Optional[str],
    used_vision: bool = True,
) -> Iterator[str]:
    """SSE-generator met provider-fallback. Events: start / delta / done / error.
    Het start-event komt direct, zodat de frontend meteen weet dat de
    verbinding staat en 'de AI kijkt naar de dia' kan tonen."""
    last_error: Optional[Exception] = None
    yield sse_event({"type": "start"})

    for candidate in ai_engine.candidates():
        chunks: list[str] = []
        started = time.perf_counter()
        try:
            for text in _clean_markdown_stream(candidate.stream(contents, system_instruction, TEMPERATURE)):
                if text:
                    chunks.append(text)
                    yield sse_event({"type": "delta", "text": text})

            markdown = "".join(chunks).strip()
            if not markdown:
                raise RuntimeError("Leeg antwoord van model")

            ai_engine.report_success(candidate, latency_ms=(time.perf_counter() - started) * 1000)
            if cache_key:
                save_explanation_cache(cache_key, markdown, candidate.label, used_vision)
            yield sse_event({"type": "done", "model": candidate.label, "cached": False})
            return

        except Exception as e:
            ai_engine.report_failure(candidate, e)
            last_error = e
            if chunks:
                # Er is al tekst naar de client gestuurd; opnieuw beginnen met een
                # ander model zou dubbele tekst geven. Netjes afbreken.
                yield sse_event({"type": "error", "code": "AI_STREAM_INTERRUPTED",
                                 "message": "De uitleg is halverwege afgebroken. Probeer het opnieuw."})
                return

    yield sse_event({
        "type": "error",
        "code": "AI_GENERATION_FAILED",
        "message": humanize_ai_error(last_error),
        **({"details": str(last_error)} if DEBUG_ERROR_DETAILS else {}),
    })


# =========================================================
# UITLEG-CACHE
# =========================================================

def render_signature() -> str:
    """Vervangt de oude byte-hash van de AI-afbeelding in de cache-key.
    De afbeelding is volledig bepaald door (file_hash, page, schaal, formaat),
    dus dit is even correct maar zonder MB's te lezen en te hashen per request."""
    return f"jpg{SLIDE_JPEG_QUALITY}@{PDF_RENDER_SCALE_AI}"


def make_explanation_cache_key(
    *,
    file_hash: str,
    page_index: int,
    language: str,
    detail_level: str,
    mode: str = "explain",
    audience_level: str = "intermediate",
) -> str:
    raw = "|".join([
        "explain", PROMPT_VERSION, file_hash, str(page_index),
        language.strip().lower(), detail_level, mode, audience_level, render_signature(),
    ])
    return sha256_text(raw)


def explanation_cache_key_for(req: "ExplainRequest") -> Optional[str]:
    """Cache-key voor een normale uitleg; vervolgvragen/chat worden nooit gecachet."""
    if req.question or req.history:
        return None
    return make_explanation_cache_key(
        file_hash=req.file_hash,
        page_index=req.page_index,
        language=req.language,
        detail_level=req.detail_level,
        mode=req.mode,
        audience_level=req.audience_level,
    )


# De uitleg-cache loopt via cache_store: L1 lokaal (snel) + L2 Supabase
# (permanent en gedeeld), zodat een eenmaal gegenereerde uitleg deploys
# overleeft en voor iedere gebruiker gratis is.
def load_explanation_cache(key: str) -> Optional[dict[str, Any]]:
    if not ENABLE_RESPONSE_CACHE:
        return None
    return cache_store.get_json("ai_cache", key)


def save_explanation_cache(key: str, markdown: str, model_name: str, used_vision: bool = True) -> None:
    if not ENABLE_RESPONSE_CACHE:
        return
    cache_store.put_json("ai_cache", key, {
        "markdown": markdown,
        "model": model_name,
        "used_vision": used_vision,
        "created_at": time.time(),
    })


# Voorkomt dat dezelfde uitleg meerdere keren tegelijk wordt gegenereerd
# (bijv. prefetch en een klik van de gebruiker die elkaar kruisen).
# Wachtende requests krijgen een Event dat afgaat zodra de generatie klaar is,
# in plaats van elke halve seconde de schijf te pollen.
_inflight_lock = threading.Lock()
_inflight_events: dict[str, threading.Event] = {}


def claim_generation(cache_key: str) -> tuple[threading.Event, bool]:
    """(event, True) = wij mogen genereren; (event, False) = iemand anders is al bezig,
    wacht op het event en lees daarna de cache."""
    with _inflight_lock:
        existing = _inflight_events.get(cache_key)
        if existing is not None:
            return existing, False
        event = threading.Event()
        _inflight_events[cache_key] = event
        return event, True


def release_generation(cache_key: str) -> None:
    with _inflight_lock:
        event = _inflight_events.pop(cache_key, None)
    if event is not None:
        event.set()


# =========================================================
# FREEMIUM-METERING
# =========================================================
# De algemene AI-cache voorkomt dubbele provider-kosten. Credits zijn echter
# accountgebonden: de eerste toegang tot een exacte cache-key kost credits,
# daarna blijft die inhoud voor dat account ontgrendeld.

def quota_gate(
    request: Request,
    cost: int = 1,
    unlock_key: Optional[str] = None,
    force: bool = False,
) -> tuple[str, str]:
    """Controleer én verbruik StudyGrasp-credits.

    ``unlock_key`` maakt dezelfde exacte inhoud na de eerste betaling gratis
    voor dit account. ``force`` (opnieuw genereren) rekent opnieuw af, maar
    markeert de resulterende gewone inhoud wel als ontgrendeld.
    """
    # IP-noodrem als extra laag: begrenst ook één account dat losgaat.
    client_ip = request.client.host if request is not None and request.client else "unknown"
    if not rate_limit.check(client_ip):
        raise_api_error(429, "RATE_LIMITED", "Te veel aanvragen kort na elkaar — even wachten.", {})
    user = auth.require_user(request)
    user_id, plan = user["id"], user.get("plan", "free")
    effective_unlock = None if force else unlock_key
    if not usage.consume(user_id, plan, cost=cost, unlock_key=effective_unlock):
        used, limit = usage.status(user_id, plan)
        raise_api_error(
            429, "QUOTA_EXCEEDED",
            "Je AI-credits zijn op. Wacht op je volgende tegoed of kies een ruimer plan.",
            {"plan": plan, "limit": limit, "used": used, "cost": cost},
        )
    if force and unlock_key:
        usage.mark_unlocked(user_id, unlock_key)
    return user_id, plan


def post_upload_processing(user_id: str, file_hash: str) -> None:
    """Na de upload-response: eventuele Office->PDF-conversie, dan de dia's die
    de gebruiker meteen ziet, parallel de eerste uitleg(gen) + het
    studeer-materiaal (quiz/flashcards), en daarna de rest van de dia's."""
    try:
        meta = load_meta(user_id, file_hash) or {}
        total = int(meta.get("total_pages", 0))
        if total <= 0:
            return
        get_pdf_for_document(file_hash)  # pptx/docx: conversie gebeurt nu hier, niet in /upload
        prerender_display_range(file_hash, 0, min(4, total))
        base_req = ExplainRequest(file_hash=file_hash, page_index=0)
        for i in range(min(PREFETCH_ON_UPLOAD, total)):
            _prefetch_pool.submit(prefetch_one_page, user_id, base_req, i)
        if PREFETCH_STUDY_ON_UPLOAD:
            _prefetch_pool.submit(prefetch_study_material, file_hash)
        prerender_display_range(file_hash, 4, total)
    except Exception:
        logger.exception("Post-upload verwerking mislukt voor %s", file_hash)


# =========================================================
# ROUTES: EXPLAIN (de kern)
# =========================================================

def prepare_explain_inputs(user_id: str, req: ExplainRequest) -> dict[str, Any]:
    meta = ensure_document_exists(user_id, req.file_hash)
    file_type, texts = get_document_texts(req.file_hash)
    total_pages = len(texts)

    if req.page_index < 0 or req.page_index >= total_pages:
        raise_api_error(400, "INVALID_PAGE_INDEX", "Ongeldige page_index.",
                        {"page_index": req.page_index, "total_pages": total_pages})

    ai_image = ensure_slide_image(req.file_hash, req.page_index, "ai")

    previous_texts = [
        texts[i] for i in range(max(0, req.page_index - 2), req.page_index)
        if (texts[i] or "").strip()
    ]

    context_message = build_context_message(
        file_name=str(meta.get("file_name", "onbekend document")),
        file_type=file_type,
        page_index=req.page_index,
        total_pages=total_pages,
        slide_text=texts[req.page_index],
        previous_texts=previous_texts,
        has_image=ai_image is not None,
    )

    system_instruction = build_system_instruction(
        language=req.language,
        detail_level=req.detail_level,
        is_last_page=(req.page_index == total_pages - 1),
        mode=req.mode,
        audience_level=req.audience_level,
    )

    contents = build_contents(
        ai_image=ai_image,
        context_message=context_message,
        question=req.question,
        history=req.history,
    )

    return {
        "contents": contents,
        "system_instruction": system_instruction,
        "cache_key": explanation_cache_key_for(req),
        "used_vision": ai_image is not None,
        "file_type": file_type,
        "total_pages": total_pages,
    }


# Prefetch-taken draaien in een eigen kleine pool, zodat meerdere dia's (en het
# studeer-materiaal) parallel gegenereerd worden in plaats van één voor één.
_prefetch_pool = ThreadPoolExecutor(max_workers=max(1, PREFETCH_WORKERS), thread_name_prefix="prefetch")


def prefetch_one_page(user_id: str, base_req: ExplainRequest, page_index: int) -> None:
    """Genereer en cache de uitleg van één dia op de achtergrond."""
    try:
        req = base_req.model_copy(update={
            "page_index": page_index, "question": None, "history": [],
            "stream": False, "force_refresh": False,
        })
        cache_key = explanation_cache_key_for(req)
        if not cache_key or load_explanation_cache(cache_key):
            return
        _, claimed = claim_generation(cache_key)
        if not claimed:
            return
        try:
            ensure_slide_image(req.file_hash, page_index, "display")
            prepared = prepare_explain_inputs(user_id, req)
            markdown, model_name = generate_markdown(prepared["contents"], prepared["system_instruction"])
            save_explanation_cache(cache_key, markdown, model_name, prepared["used_vision"])
            logger.info("Prefetch klaar: pagina %s van %s", page_index + 1, req.file_hash[:12])
        finally:
            release_generation(cache_key)
    except Exception:
        logger.exception("Prefetch mislukt voor %s pagina %s", base_req.file_hash, page_index)


def prefetch_ahead(user_id: str, base_req: ExplainRequest, total_pages: int, ahead: Optional[int] = None) -> None:
    """De volgende dia's alvast genereren, parallel. In de standaardmodus
    PREFETCH_AHEAD diep; in Simpel/Studeer-modus maar 1 (mensen schakelen daar
    vaak even naartoe om te vergelijken — 3 dia's vooruit genereren is dan
    meestal weggegooide tokens, en 1 vooruit voelt nog steeds instant)."""
    if ahead is None:
        ahead = PREFETCH_AHEAD if base_req.mode == "explain" else min(1, PREFETCH_AHEAD)
    for i in range(base_req.page_index + 1, min(base_req.page_index + 1 + ahead, total_pages)):
        _prefetch_pool.submit(prefetch_one_page, user_id, base_req, i)


def cached_sse_response(cached: dict[str, Any]) -> StreamingResponse:
    def cached_stream():
        yield sse_event({"type": "delta", "text": cached["markdown"]})
        yield sse_event({"type": "done", "model": cached.get("model"), "cached": True})
    return StreamingResponse(cached_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# =========================================================
# GEDEELDE STUDEER-HELPERS
# =========================================================

# Studeerdata (flashcards + spaced-repetition-planning) loopt via cache_store,
# zodat de duur gegenereerde flashcards deploys overleven en gedeeld zijn — geen
# tokens meer voor kaarten die al eens gemaakt zijn. NB: de SRS-planning is nu
# per document (net als voorheen server-side); bij echte accounts hoort die
# voortgang per gebruiker opgeslagen te worden.
def load_study_data(user_id: str, file_hash: str) -> dict[str, Any]:
    data = cache_store.get_json("study", user_key(user_id, file_hash)) or {}
    # Flashcards worden per taal opgeslagen (in "sets"), want een Engelse kaart is
    # een andere kaart dan een Nederlandse — inclusief eigen herhaalplanning.
    data.setdefault("sets", {})
    # Migratie van de oude, taal-loze opzet: die kaarten zijn altijd met de
    # standaard "auto" gegenereerd, dus verhuizen ze naar de "auto"-set.
    if data.get("flashcards"):
        data["sets"].setdefault("auto", {
            "flashcards": data.get("flashcards", []),
            "srs": data.get("srs", {}),
        })
    data.pop("flashcards", None)
    data.pop("srs", None)
    return data


def flashcard_set(data: dict[str, Any], language: str) -> dict[str, Any]:
    """De flashcard-set (kaarten + SRS-planning) voor één taal."""
    return data["sets"].setdefault(language or "auto", {"flashcards": [], "srs": {}})


def save_study_data(user_id: str, file_hash: str, data: dict[str, Any]) -> None:
    cache_store.put_json("study", user_key(user_id, file_hash), data)


def build_document_digest(file_hash: str, max_total: int = 24000) -> tuple[str, str, int]:
    """Compacte tekstweergave van het hele document, voor samenvatting/quiz/flashcards."""
    file_type, texts = get_document_texts(file_hash)
    label = page_label_for(file_type).capitalize()
    per_page = max(200, min(800, max_total // max(1, len(texts))))
    blocks = []
    for i, t in enumerate(texts):
        t = clean_text(t)
        if t:
            blocks.append(f"[{label} {i + 1}]\n{truncate(t, per_page)}")
    return "\n\n".join(blocks)[:max_total], file_type, len(texts)


def document_image_parts(file_hash: str, total_pages: int, max_images: int = 8) -> list[ai_engine.Part]:
    """Dia-afbeeldingen als bijlage voor documenten met weinig tekst (bijv. gescande slides)."""
    parts: list[ai_engine.Part] = []
    step = max(1, total_pages // max_images)
    for i in range(0, total_pages, step):
        if len(parts) >= max_images:
            break
        path = ensure_slide_image(file_hash, i, "ai")
        if path and path.exists():
            parts.append(image_part(path))
    return parts


def language_rule_for(language: str) -> str:
    if language.strip().lower() in ("", "auto"):
        return (
            "Write your ENTIRE output in the same language as the document itself "
            "(detect it from the content). It must read as if originally written in that language."
        )
    return f"Write your ENTIRE output in {language}. It must read as if originally written in that language."


def generate_structured(
    contents: list[Message],
    system_instruction: str,
    schema: type[BaseModel],
) -> BaseModel:
    """Structured JSON-output (voor quiz/flashcards/nakijken), met provider-fallback."""
    last_error: Optional[Exception] = None
    for candidate in ai_engine.candidates():
        started = time.perf_counter()
        try:
            raw = candidate.generate(contents, system_instruction, 0.3, schema=schema)
            result = schema.model_validate_json(ai_engine.extract_json(raw))
            ai_engine.report_success(candidate, latency_ms=(time.perf_counter() - started) * 1000)
            return result
        except Exception as e:
            ai_engine.report_failure(candidate, e)
            last_error = e
    raise_api_error(502, "AI_GENERATION_FAILED", humanize_ai_error(last_error), debug_reason(last_error))


# =========================================================
# SAMENVATTING VAN HET HELE DOCUMENT
# =========================================================

class SummaryRequest(BaseModel):
    file_hash: str
    language: str = "auto"
    stream: bool = True
    force_refresh: bool = False


def build_summary_system(language: str) -> str:
    return f"""You are an outstanding university tutor writing a study summary of a COMPLETE lecture document for a student.

You receive the extracted text of every page (and possibly some page images). Write one coherent, well-structured markdown summary that lets the student review the whole lecture quickly.

STRUCTURE
- Start with the title/topic of the lecture and 2-3 sentences of big picture: what is this lecture about and why does it matter.
- Then walk through the content in logical sections with short markdown headers (###), in the order of the lecture. Merge administrative pages (agenda, breaks, Wooclap, references) into nothing — skip them.
- Under each section: compact bullets with the concepts that matter, not a retelling of every page.
- Include a section "🧮 Belangrijkste formules" (if the lecture has formulas) with each key formula in display LaTeX ($$...$$) plus one line about what it means.
- End with "🎯 Dit moet je kunnen" — a bullet list of the concrete skills/knowledge the student must master, and one exam tip.

RULES
- All math in LaTeX: $...$ inline, $$...$$ for standalone formulas.
- Be precise and didactic; no filler, no meta-commentary.
- Reference page numbers sparingly like (p. 12) so the student can jump back.
- {language_rule_for(language)}

Return pure markdown only."""


# =========================================================
# OVERHOORMODUS (QUIZ)
# =========================================================

class QuizQuestion(BaseModel):
    id: int
    type: Literal["mc", "open"]
    question: str
    options: list[str] = Field(default_factory=list)
    correct_option: Optional[int] = None  # index in options (alleen mc)
    model_answer: str = ""                # kort modelantwoord (alleen open)
    page_index: Optional[int] = None      # 0-gebaseerd; naar welke pagina dit verwijst
    file_hash: Optional[str] = None       # bronbestand; gevuld bij een map-brede quiz
    doc_index: Optional[int] = None       # 1-gebaseerd documentnummer bij een map-brede quiz
    difficulty: Literal["easy", "medium", "hard"] = "medium"


class QuizSet(BaseModel):
    questions: list[QuizQuestion] = Field(default_factory=list)


class QuizGenerateRequest(BaseModel):
    file_hash: Optional[str] = None
    folder_id: Optional[str] = None
    page_index: Optional[int] = None  # None = heel document
    count: int = Field(default=8, ge=1, le=20)
    question_type: Literal["mixed", "mc", "open"] = "mixed"
    difficulty: Literal["mixed", "easy", "medium", "hard"] = "mixed"
    language: str = "auto"
    force_refresh: bool = False


def build_quiz_system(language: str, question_type: str, difficulty: str, count: int) -> str:
    type_rule = {
        "mixed": "Mix multiple-choice (4 options) and open questions, roughly half/half.",
        "mc": "Only multiple-choice questions with exactly 4 plausible options.",
        "open": "Only open questions that require explaining or calculating.",
    }[question_type]
    diff_rule = {
        "mixed": "Vary the difficulty from easy recall to harder application/insight questions.",
        "easy": "Keep questions at recall/recognition level.",
        "medium": "Focus on understanding and simple application.",
        "hard": "Focus on application, analysis and combining concepts.",
    }[difficulty]
    return f"""You are a university tutor creating practice questions ("overhoren") from lecture material.

RULES
- Create exactly {count} questions that test whether the student truly understands the material — not trivia about layout or metadata.
- Ask about the SUBJECT MATTER itself. Never ask where something appears, what is mentioned/shown on a particular page or slide, what a heading says, or any other question that requires remembering page/slide/document numbers or layout.
- {type_rule}
- {diff_rule}
- For multiple-choice: exactly 4 options, one clearly correct (set correct_option to its 0-based index), distractors must be plausible misconceptions.
- For open questions: set model_answer to a short, correct model answer.
- Set page_index only as hidden source metadata for the app (0-based; input page numbers are 1-based). Never mention that page or slide number in the question or answer. For multi-document material, also set doc_index to its 1-based document number.
- All math in LaTeX ($...$ / $$...$$), also inside options and answers.
- Never invent content that is not in the material.
- {language_rule_for(language)}

Return only JSON matching the schema."""


class QuizGradeRequest(BaseModel):
    file_hash: str
    question: str
    student_answer: str
    model_answer: Optional[str] = None
    page_index: Optional[int] = None
    language: str = "auto"


# Fouttype-taxonomie (stabiele Engelse sleutels; de labels vertaalt de frontend).
# Waaróm ging een antwoord fout? Dat bepaalt de juiste remediëring — heel iets
# anders bij "vraag verkeerd gelezen" dan bij "concept niet begrepen".
ErrorType = Literal["concept", "detail", "formula", "misread", "connection", "other"]


class QuizGradeResult(BaseModel):
    verdict: Literal["correct", "partial", "incorrect"]
    score: int = Field(ge=0, le=100)
    feedback: str  # markdown, met LaTeX
    # Alleen bij partial/incorrect: het type fout, voor gerichte herhaling.
    error_type: Optional[ErrorType] = None


# ---- Herstelvragen: gerichte oefening na een specifieke fout ----
class RecoveryRequest(BaseModel):
    file_hash: str
    concept: str = ""
    error_type: Optional[ErrorType] = None
    question: str = ""                    # de tentamenvraag die fout ging
    model_answer: Optional[str] = None
    student_answer: Optional[str] = None
    page_index: Optional[int] = None
    language: str = "auto"


# Per fouttype een aanpak: waar de herstelvragen op moeten mikken.
ERROR_TYPE_GUIDANCE = {
    "concept": "The student misunderstood the underlying concept. Rebuild it from the ground up and contrast it with what it is commonly confused with.",
    "detail": "The student knew the idea but forgot an essential detail, condition or exception. Drill exactly those details and edge cases.",
    "formula": "The student applied a formula or method incorrectly (wrong variable, step, sign or unit). Practise applying the method correctly, step by step, with concrete numbers.",
    "misread": "The student misread the question. Reward careful reading: precise wording, what is actually being asked, distinguishing near-identical phrasings.",
    "connection": "The student failed to connect concepts. Require linking two or more ideas, or transferring the idea to a new situation.",
    "other": "Reinforce the concept and its correct application with a few short questions.",
}


def build_recovery_system(language: str, error_type: Optional[str]) -> str:
    guidance = ERROR_TYPE_GUIDANCE.get(error_type or "other", ERROR_TYPE_GUIDANCE["other"])
    return f"""You are a supportive tutor creating a few SHORT recovery questions for a student who just made one specific mistake.

THE MISTAKE TO REMEDIATE
{guidance}

RULES
- Create 2-3 short OPEN questions (type "open"), easier than an exam, that directly practise the way OUT of this specific mistake.
- Build a small ladder: start very approachable, end near the level of the original question.
- For each question: model_answer is a short, complete worked answer (include the steps, LaTeX for math).
- Stay strictly within the given concept and material; never invent facts that are not supported by it.
- Set difficulty to "easy" or "medium". Leave options empty and correct_option null (these are open questions).
- {language_rule_for(language)}

Return only JSON matching the schema."""


# =========================================================
# FLASHCARDS + SPACED REPETITION
# =========================================================

class Flashcard(BaseModel):
    id: int
    front: str
    back: str
    page_index: Optional[int] = None


class FlashcardSet(BaseModel):
    cards: list[Flashcard] = Field(default_factory=list)


class FlashcardGenerateRequest(BaseModel):
    file_hash: str
    language: str = "auto"
    max_cards: int = Field(default=25, ge=5, le=60)
    force_refresh: bool = False


def _flashcards_generate_inner(user_id: str, req: FlashcardGenerateRequest, data: dict[str, Any]) -> dict[str, Any]:
    digest, _, total_pages = build_document_digest(req.file_hash)
    parts: list[Any] = []
    if len(digest) < 400:
        parts.extend(document_image_parts(req.file_hash, total_pages))
    parts.append(text_part(
        f"Materiaal (heel document, {total_pages} pagina's):\n\n"
        f"{digest if digest else '(geen tekstlaag; gebruik de afbeeldingen)'}\n\n"
        "Maak hier nu flashcards van."
    ))

    system_instruction = f"""You are a tutor creating flashcards from lecture material, following proven flashcard principles.

RULES
- Create at most {req.max_cards} cards covering the material that is worth memorizing: definitions, formulas, key relationships, "what happens if", reading rules for graphs.
- One atomic fact per card. front = a short question or cue; back = the short answer.
- No cards about layout, agenda, breaks or metadata.
- Math in LaTeX ($...$ / $$...$$) on both sides.
- Set page_index to the 0-based page the card comes from (input page numbers are 1-based: subtract 1).
- {language_rule_for(req.language)}

Return only JSON matching the schema."""

    result: FlashcardSet = generate_structured(
        [Message(role="user", parts=parts)], system_instruction, FlashcardSet,
    )

    now = time.time()
    cards = []
    for i, card in enumerate(result.cards[:req.max_cards]):
        card_dict = card.model_dump()
        card_dict["id"] = i
        cards.append(card_dict)

    fset = flashcard_set(data, req.language)
    fset["flashcards"] = cards
    fset["srs"] = {
        str(c["id"]): {"interval": 0.0, "ease": 2.5, "reps": 0, "due_at": now}
        for c in cards
    }
    save_study_data(user_id, req.file_hash, data)
    return {"ok": True, "cards": cards, "cached": False}


def prefetch_study_material(file_hash: str) -> None:
    """Flashcards alvast genereren direct na de upload, met exact dezelfde
    standaardinstellingen (en dus cache-key) als de frontend, zodat de tab
    'Flashcards' instant opent. De quiz wordt bewust niet geprefetcht: de
    gebruiker stelt die eerst in, en elke afwijkende instelling zou de vooraf
    gegenereerde set (en dus de tokens) weggooien."""
    try:
        flashcards_generate(FlashcardGenerateRequest(file_hash=file_hash))
        logger.info("Prefetch flashcards klaar voor %s", file_hash[:12])
    except Exception as e:
        logger.warning("Flashcards-prefetch mislukt voor %s: %s", file_hash[:12], str(e)[:200])


class FlashcardReviewRequest(BaseModel):
    file_hash: str
    card_id: int
    rating: Literal["again", "hard", "good", "easy"]
    language: str = "auto"


# Licht SM-2-schema: eenvoudig, voorspelbaar en goed genoeg. Gedeeld door
# flashcards en woordenlijsten zodat beide exact dezelfde herhaalplanning volgen.
def apply_sm2(state: Optional[dict[str, Any]], rating: str, now: float) -> dict[str, Any]:
    state = state or {"interval": 0.0, "ease": 2.5, "reps": 0, "due_at": now}
    interval, ease, reps = float(state.get("interval", 0.0)), float(state.get("ease", 2.5)), int(state.get("reps", 0))
    if rating == "again":
        interval, reps, ease = 0.0, 0, max(1.3, ease - 0.2)
        due_at = now + 10 * 60  # over 10 minuten opnieuw
    elif rating == "hard":
        interval = max(1.0, interval * 1.2)
        ease = max(1.3, ease - 0.15)
        reps += 1
        due_at = now + interval * 86400
    elif rating == "good":
        interval = 1.0 if reps == 0 else interval * ease
        reps += 1
        due_at = now + interval * 86400
    else:  # easy
        interval = max(2.0, interval * ease * 1.3)
        ease += 0.15
        reps += 1
        due_at = now + interval * 86400
    return {"interval": interval, "ease": ease, "reps": reps, "due_at": due_at}


# =========================================================
# NOTITIES (per dia)
# =========================================================
# Spiegelt de bestaande client-only opslag in frontend/js/stats.js
# (localStorage "sc.study.{hash}") zodat notities/markeringen ook overleven
# na een cache-wipe en zichtbaar zijn op een ander apparaat/browser. De
# frontend blijft localStorage gebruiken als snelle/offline-eerste laag en
# synct hiermee op de achtergrond.

def load_notes_data(user_id: str, file_hash: str) -> dict[str, Any]:
    data = cache_store.get_json("notes", user_key(user_id, file_hash)) or {}
    data.setdefault("pages", {})
    return data


def save_notes_data(user_id: str, file_hash: str, data: dict[str, Any]) -> None:
    cache_store.put_json("notes", user_key(user_id, file_hash), data)


class NoteUpdateRequest(BaseModel):
    page_index: int
    note: Optional[str] = None
    star: Optional[bool] = None
    unclear: Optional[bool] = None


# =========================================================
# WOORDENLIJSTEN (begrippen oefenen, eigen zichtbaar onderdeel)
# =========================================================
# Een woordenlijst is géén document: het is een op zichzelf staande term/
# definitie-lijst met eigen spaced repetition (dezelfde apply_sm2 als
# flashcards). Opslag via cache_store, namespace "wordlists": één index-doc +
# per lijst een eigen doc. Kaart-ids zijn stabiel (next_id-teller) zodat
# bewerken/herordenen de SRS-planning niet corrumpeert.

def load_wordlist_index(user_id: str) -> list[dict[str, Any]]:
    data = cache_store.get_json("wordlists", user_key(user_id, "index"))
    return (data or {}).get("lists", [])


def save_wordlist_index(user_id: str, lists: list[dict[str, Any]]) -> None:
    cache_store.put_json("wordlists", user_key(user_id, "index"), {"lists": lists})


def load_wordlist(user_id: str, list_id: str) -> Optional[dict[str, Any]]:
    return cache_store.get_json("wordlists", user_key(user_id, list_id))


def save_wordlist(user_id: str, wl: dict[str, Any]) -> None:
    cache_store.put_json("wordlists", user_key(user_id, wl["id"]), wl)
    # index bijwerken (naam/aantal/taal)
    lists = load_wordlist_index(user_id)
    entry = {"id": wl["id"], "name": wl["name"], "owner_id": user_id,
             "created_at": wl.get("created_at"), "language": wl.get("language", "auto"),
             "count": len(wl.get("cards", []))}
    lists = [e for e in lists if e["id"] != wl["id"]]
    lists.append(entry)
    save_wordlist_index(user_id, lists)


_wordlists_lock = threading.Lock()


class WordCard(BaseModel):
    term: str
    definition: str


class WordlistCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    language: str = "auto"
    cards: list[WordCard] = Field(default_factory=list)


def _seed_cards(cards: list[dict], start_id: int, srs: dict, now: float):
    """Wijst stabiele ids toe aan nieuwe kaarten en seedt hun SRS. Geeft
    (kaarten-met-id, volgende_vrije_id) terug."""
    out = []
    nid = start_id
    for c in cards:
        cid = nid
        nid += 1
        out.append({"id": cid, "term": c["term"], "definition": c["definition"]})
        srs[str(cid)] = {"interval": 0.0, "ease": 2.5, "reps": 0, "due_at": now}
    return out, nid


def _wordlist_public(wl: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    cards = []
    due = 0
    for c in wl.get("cards", []):
        st = wl.get("srs", {}).get(str(c["id"]), {})
        due_at = st.get("due_at", now)
        is_due = due_at <= now
        if is_due:
            due += 1
        cards.append({**c, "due_at": due_at, "is_due": is_due,
                      "interval_days": round(st.get("interval", 0.0), 2), "reps": st.get("reps", 0)})
    return {"id": wl["id"], "name": wl["name"], "language": wl.get("language", "auto"),
            "cards": cards, "due_count": due, "total": len(cards)}


class WordlistUpdateRequest(BaseModel):
    name: Optional[str] = None
    cards: Optional[list[WordCard]] = None  # volledige nieuwe set (met behoud van SRS waar term ongewijzigd)


class WordlistReviewRequest(BaseModel):
    card_id: int
    rating: Literal["again", "hard", "good", "easy"]


class VocabPair(BaseModel):
    term: str
    definition: str


class VocabSet(BaseModel):
    pairs: list[VocabPair] = Field(default_factory=list)


class WordlistGenerateRequest(BaseModel):
    file_hash: Optional[str] = None
    folder_id: Optional[str] = None
    language: str = "auto"
    max_terms: int = Field(default=30, ge=5, le=100)
    name: Optional[str] = None


# =========================================================
# SELECTEER-EN-VRAAG (regio op de dia)
# =========================================================

class RegionBox(BaseModel):
    x: float = Field(ge=0.0, le=1.0)       # linksboven, genormaliseerd 0..1
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)


class RegionAskRequest(BaseModel):
    file_hash: str
    page_index: int
    box: RegionBox
    question: str = ""
    language: str = "auto"
    stream: bool = True


# =========================================================
# VOORLEZEN (TTS) — neurale stemmen via edge-tts
# =========================================================
# De Web Speech API in de browser klinkt op Windows robotachtig. edge-tts
# gebruikt de gratis neurale stemmen van Microsoft Edge (geen key nodig).
# Audio wordt per (stem, tekst) op schijf gecachet: nogmaals luisteren of een
# tweede student met dezelfde dia = 0 seconden en 0 netwerkverkeer.

TTS_VOICES = {
    "nl": os.getenv("TTS_VOICE_NL", "nl-NL-MaartenNeural"),
    "en": os.getenv("TTS_VOICE_EN", "en-US-AriaNeural"),
    "de": os.getenv("TTS_VOICE_DE", "de-DE-KatjaNeural"),
    "fr": os.getenv("TTS_VOICE_FR", "fr-FR-DeniseNeural"),
    "es": os.getenv("TTS_VOICE_ES", "es-ES-ElviraNeural"),
}


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    language: str = "nl-NL"  # locale zoals "nl-NL"; alleen de eerste 2 letters tellen


# =========================================================
# VOORTGANG ONTHOUDEN
# =========================================================

class ProgressRequest(BaseModel):
    page_index: int


# =========================================================
# MAPPEN (VAKKEN)
# =========================================================
# Een map bundelt documenten van één vak. De map zelf is een klein JSON-bestand;
# het lidmaatschap staat als folder_id in de document-metadata, zodat een
# document verwijderen of verplaatsen geen tweede administratie hoeft bij te
# werken.

FOLDERS_FILE = BASE_DIR / "folders.json"  # oude locatie; alleen nog gelezen voor de eenmalige migratie
_folders_lock = threading.Lock()


def load_folders(user_id: str) -> list[dict[str, Any]]:
    # Via cache_store (namespace "folders", key "index") zodat mappen ook
    # overleven als de server opnieuw wordt uitgerold — voorheen stond dit in
    # één kaal JSON-bestand buiten cache_store om. Eenmalige, zelf-herstellende
    # migratie: bestaat er nog geen cache_store-record, maar wél het oude
    # bestand, migreer die inhoud er dan meteen in.
    data = cache_store.get_json("folders", user_id)
    if data is None:
        data = {"folders": []}
    return data.get("folders", [])


def save_folders(user_id: str, folders: list[dict[str, Any]]) -> None:
    cache_store.put_json("folders", user_id, {"folders": folders})


def find_folder(user_id: str, folder_id: str) -> Optional[dict[str, Any]]:
    return next((f for f in load_folders(user_id) if f["id"] == folder_id), None)


# ---- geneste mappen ----------------------------------------------------------
# Een map kan in een andere map staan (`parent_id`); zonder parent_id staat hij
# bovenin. De boom is alleen zo diep als hieronder toegestaan, zodat een
# verdwaalde verwijzing nooit een eindeloze wandeling kan worden. Alle lopers
# hieronder zijn bovendien cyclusbestendig (via `seen`), want de opslag is een
# platte lijst en een kapotte parent_id mag de app niet laten hangen.
MAX_FOLDER_DEPTH = 5


def folder_depth(user_id: str, folder_id: Optional[str],
                 folders: Optional[list[dict[str, Any]]] = None) -> int:
    """0 voor een map bovenin, 1 voor een map daarin, enzovoort."""
    if not folder_id:
        return -1
    by_id = {f["id"]: f for f in (folders if folders is not None else load_folders(user_id))}
    depth, seen, cur = 0, set(), by_id.get(folder_id)
    while cur and cur.get("parent_id") and cur["id"] not in seen:
        seen.add(cur["id"])
        cur = by_id.get(cur["parent_id"])
        depth += 1
    return depth


def folder_descendant_ids(user_id: str, folder_id: str,
                          folders: Optional[list[dict[str, Any]]] = None) -> list[str]:
    """De map zelf plus alles wat eronder hangt, van boven naar beneden."""
    all_folders = folders if folders is not None else load_folders(user_id)
    children: dict[Optional[str], list[str]] = {}
    for f in all_folders:
        children.setdefault(f.get("parent_id"), []).append(f["id"])
    out, queue, seen = [], [folder_id], {folder_id}
    while queue:
        current = queue.pop(0)
        out.append(current)
        for child in children.get(current, []):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return out


def folder_path(user_id: str, folder_id: str,
                folders: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """Kruimelpad van bovenin naar deze map (inclusief de map zelf)."""
    by_id = {f["id"]: f for f in (folders if folders is not None else load_folders(user_id))}
    chain, seen, cur = [], set(), by_id.get(folder_id)
    while cur and cur["id"] not in seen:
        seen.add(cur["id"])
        chain.append({"id": cur["id"], "name": cur.get("name", "")})
        cur = by_id.get(cur.get("parent_id"))
    chain.reverse()
    return chain


# Wat telt als lesmateriaal (collegestof)? Opgaven (kind="exercise") en losse
# huiswerkfoto's (kind="quick") zijn géén bronmateriaal: ze mogen niet meetellen
# in tentamengeneratie, voortgang, zoekresultaten of dia-verwijzingen. Deze regel
# staat bewust op één plek, zodat elke lijst dezelfde definitie gebruikt.
def is_material(meta: Optional[dict[str, Any]]) -> bool:
    return bool(meta) and bool(meta.get("file_hash")) and meta.get("kind") not in ("exercise", "quick")


def folder_document_hashes(user_id: str, folder_id: str, recursive: bool = True) -> list[str]:
    """Lesmateriaal in een map, oudste upload eerst (colleges in volgorde).
    Opgaven en snel-foto's zitten er bewust niet bij — zie is_material.
    Kijkt alleen in de bibliotheek van deze gebruiker.

    recursive=True (de standaard) telt submappen mee: een samenvatting of
    tentamen "over dit vak" hoort over alles te gaan wat je erin hebt gezet,
    ook als je het per college in submapjes hebt geordend. Zet het op False waar
    je juist de directe inhoud wilt, zoals de kaartjes op de mapweergave zelf.
    """
    wanted = set(folder_descendant_ids(user_id, folder_id)) if recursive else {folder_id}
    docs = []
    for file_hash in user_document_hashes(user_id):
        meta = load_meta(user_id, file_hash)
        if is_material(meta) and meta.get("folder_id") in wanted:
            docs.append(meta)
    docs.sort(key=lambda m: m.get("uploaded_at") or 0)
    return [m["file_hash"] for m in docs]


def build_folder_material(user_id: str, hashes: list[str], total_budget: int = 30000) -> tuple[str, list[Any]]:
    """Eén tekstblok met de inhoud van meerdere documenten, plus eventuele
    dia-afbeeldingen. Het totaalbudget is vast, zodat een map met tien colleges
    niet tien keer zoveel tokens kost als één document. Gedeeld door de
    tentamengenerator en de mapsamenvatting, zodat beide dezelfde stof zien."""
    per_doc = max(4000, total_budget // max(1, len(hashes)))
    parts: list[Any] = []
    blocks = []
    for i, h in enumerate(hashes, start=1):
        digest, _, total_pages = build_document_digest(h, max_total=per_doc)
        meta = load_meta(user_id, h) or {}
        name = meta.get("file_name", f"document {i}")
        if len(hashes) > 1:
            blocks.append(f"=== Document {i}: {name} ({total_pages} pagina's) ===\n\n{digest}")
        else:
            blocks.append(digest)
            if len(digest) < 400:
                parts.extend(document_image_parts(h, total_pages))
    return "\n\n".join(blocks)[:total_budget], parts


class FolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_id: Optional[str] = None     # None = bovenin


class FolderParentRequest(BaseModel):
    parent_id: Optional[str] = None     # None = naar boven halen


class FolderSummaryRequest(BaseModel):
    folder_id: str
    language: str = "auto"
    stream: bool = True
    force_refresh: bool = False


class DocumentFolderRequest(BaseModel):
    folder_id: Optional[str] = None  # None = uit de map halen


# =========================================================
# TENTAMENMODUS
# =========================================================
# Verschil met de overhoormodus: vragen op écht tentamenniveau (toepassen,
# rekenen, combineren van concepten — niet alleen reproductie), over één
# document óf een hele map (vak). Elke vraag krijgt een 'concept'-label; op
# basis van je fouten per concept bouwt de backend een herhaalplanning
# (spaced repetition over concepten in plaats van losse kaarten).

class ExamQuestion(BaseModel):
    id: int
    type: Literal["mc", "open"]
    question: str
    options: list[str] = Field(default_factory=list)
    correct_option: Optional[int] = None    # index in options (alleen mc)
    model_answer: str = ""                  # uitgewerkt modelantwoord (open)
    explanation: str = ""                   # korte toelichting waarom het antwoord klopt
    concept: str = ""                       # kort label van het getoetste concept
    page_index: Optional[int] = None        # 0-gebaseerd
    doc_index: Optional[int] = None         # 1-gebaseerd; alleen bij map-tentamens
    difficulty: Literal["easy", "medium", "hard"] = "medium"


class ExamSet(BaseModel):
    questions: list[ExamQuestion] = Field(default_factory=list)


class ExamGenerateRequest(BaseModel):
    file_hash: Optional[str] = None
    folder_id: Optional[str] = None
    count: int = Field(default=10, ge=4, le=25)
    language: str = "auto"
    force_refresh: bool = False


def exam_scope(user_id: str, req_hash: Optional[str], req_folder: Optional[str]) -> tuple[str, list[str], str]:
    """Geeft (scope_id, document-hashes, weergavenaam) voor een tentamen-scope."""
    if req_folder:
        folder = find_folder(user_id, req_folder)
        if not folder:
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        hashes = folder_document_hashes(user_id, req_folder)
        if not hashes:
            raise_api_error(400, "FOLDER_EMPTY", "Deze map bevat nog geen documenten.")
        return f"folder:{req_folder}", hashes, folder["name"]
    if req_hash:
        meta = ensure_document_exists(user_id, req_hash)
        return f"doc:{req_hash}", [req_hash], str(meta.get("file_name", "document"))
    raise_api_error(400, "MISSING_SCOPE", "Geef een file_hash of folder_id op.")


def build_exam_system(language: str, count: int) -> str:
    return f"""You are a strict but fair university examiner. You create a realistic PRACTICE EXAM from lecture material — the same level, style and depth as a real university exam on this material.

RULES
- Create exactly {count} questions at genuine exam level. Distribution: roughly 30% understanding, 50% APPLICATION (solve, calculate, predict, interpret a scenario) and 20% analysis/combining multiple concepts. Avoid pure recall of definitions unless the definition itself is exam-critical.
- Ask about the SUBJECT MATTER itself. Never ask where something appears, what is mentioned/shown on a particular page or slide, what a heading says, or any other question that requires remembering page/slide/document numbers or layout.
- If the material is quantitative, include calculation questions with concrete numbers; if conceptual, use realistic scenarios and case-based questions ("what happens if...", "which conclusion follows...").
- Mix: about 40% multiple-choice (exactly 4 options, distractors are plausible misconceptions or typical calculation errors) and 60% open questions.
- For open questions: model_answer is a complete worked answer (steps included, LaTeX for math). For MC: set correct_option (0-based).
- explanation: 1-3 sentences on why the answer is correct (and for MC why the tempting distractor is wrong).
- concept: a short label (2-5 words) naming the concept/skill being tested, e.g. "Nyquist-criterium toepassen". Reuse the exact same label when two questions test the same concept.
- page_index is hidden source metadata for the app (0-based; input page numbers are 1-based). Never mention page, slide or document numbers in the question or answer. If the material contains multiple documents, also set doc_index to the 1-based document number.
- All math in LaTeX ($...$ / $$...$$), also inside options and answers.
- Base every question strictly on the material; never invent content that is not there.
- {language_rule_for(language)}

Return only JSON matching the schema."""


# ---- foutentracking + herhaalplanning ----

def exam_data_key(scope_id: str) -> str:
    # De cache-key wordt een bestandsnaam; scope_id bevat ":" en dat mag niet
    # op Windows — dus hashen.
    return f"exam_{sha256_text(scope_id)}"


def load_exam_data(scope_id: str) -> dict[str, Any]:
    data = cache_store.get_json("study", exam_data_key(scope_id)) or {}
    data.setdefault("attempts", [])   # [{at, score, count}]
    data.setdefault("concepts", {})   # key -> {label, file_hash, page_index, right, wrong, interval, due_at}
    return data


def save_exam_data(scope_id: str, data: dict[str, Any]) -> None:
    cache_store.put_json("study", exam_data_key(scope_id), data)


def build_review_plan(data: dict[str, Any]) -> dict[str, Any]:
    """Concepten gebucket op wanneer je ze moet herhalen, zwakste eerst."""
    now = time.time()
    day = 86400.0
    buckets = {"due_now": [], "tomorrow": [], "this_week": [], "later": [], "mastered": []}
    for key, c in data["concepts"].items():
        total = c.get("right", 0) + c.get("wrong", 0)
        mastery = (c.get("right", 0) / total) if total else 0.0
        errors = c.get("errors") or {}
        top_error = max(errors, key=errors.get) if errors else None
        item = {
            "concept": c.get("label") or key,
            "file_hash": c.get("file_hash"),
            "page_index": c.get("page_index"),
            "right": c.get("right", 0),
            "wrong": c.get("wrong", 0),
            "mastery": round(mastery, 2),
            "due_at": c.get("due_at", now),
            "errors": errors,          # {fouttype: aantal}
            "top_error": top_error,    # meest gemaakte fout bij dit concept
        }
        due_in = item["due_at"] - now
        if mastery >= 0.85 and c.get("interval", 0) >= 14:
            buckets["mastered"].append(item)
        elif due_in <= 0:
            buckets["due_now"].append(item)
        elif due_in <= 1.5 * day:
            buckets["tomorrow"].append(item)
        elif due_in <= 7 * day:
            buckets["this_week"].append(item)
        else:
            buckets["later"].append(item)
    for items in buckets.values():
        items.sort(key=lambda i: (i["mastery"], i["due_at"]))
    return buckets


class ExamResultItem(BaseModel):
    concept: str = ""
    file_hash: Optional[str] = None
    page_index: Optional[int] = None
    correct: bool
    score: int = Field(default=0, ge=0, le=100)
    error_type: Optional[ErrorType] = None


class ExamAttemptRequest(BaseModel):
    file_hash: Optional[str] = None
    folder_id: Optional[str] = None
    results: list[ExamResultItem]


# =========================================================
# OPGAVEN: koppel een opgave aan het college en vind de juiste dia's
# =========================================================
# Je uploadt een opgavenblad/oefententamen (foto of PDF) gekoppeld aan één
# college (of een vak). De app helpt je met begeleidende hints en — de kern —
# laat zien wélke dia's je nodig hebt, zodat je bij een moeilijke opgave even
# kunt terugkijken hoe het ook alweer zat. Alleen bronmateriaal (colleges) telt
# als zoekgebied; opgaven en losse foto's worden uitgesloten.

class ExerciseLocateRequest(BaseModel):
    exercise_hash: str                       # het geüploade opgave-document
    page_index: int = 0                      # welke pagina van de opgave
    question_text: Optional[str] = None      # één specifieke (deel)vraag als zoekvraag
    source_file_hash: Optional[str] = None   # het college waar de opgave bij hoort
    folder_id: Optional[str] = None          # of het vak (map)
    widen: bool = False                      # breder zoeken in het hele vak
    language: str = "auto"


class LocatedSlide(BaseModel):
    doc_index: int = 1   # 1-gebaseerde index in de meegegeven documenten
    page: int            # 1-gebaseerd paginanummer
    why: str = ""        # één regel: waarom deze dia helpt


class ExerciseLocateResult(BaseModel):
    slides: list[LocatedSlide] = Field(default_factory=list)


class ExerciseHelpRequest(BaseModel):
    exercise_hash: str
    page_index: int = 0
    question_text: Optional[str] = None  # de (deel)vraag waar de student aan werkt
    source_file_hash: Optional[str] = None
    folder_id: Optional[str] = None
    widen: bool = False
    question: Optional[str] = None   # optionele eigen vraag van de student ("wat snap ik niet")
    language: str = "auto"
    stream: bool = True


# ---- Opgave in losse (deel)vragen splitsen via vision ----
class ExerciseQuestion(BaseModel):
    number: str = ""   # label zoals gedrukt ("1", "2a", "3.1")
    text: str          # de volledige vraagtekst, getrouw overgenomen


class ExerciseQuestionSet(BaseModel):
    questions: list[ExerciseQuestion] = Field(default_factory=list)


class ExerciseQuestionsRequest(BaseModel):
    exercise_hash: str
    language: str = "auto"


def build_exercise_parse_system(language: str) -> str:
    return f"""You extract the individual questions from a student's exercise sheet or past exam (given as page images and/or text).

RULES
- Return each distinct question or sub-question as a separate item, in reading order.
- number: the question's label exactly as printed (e.g. "1", "2a", "3.1"); if a part is unlabelled, use its position ("1", "2", ...).
- text: the full question text, transcribed faithfully — include given values and any formulas (LaTeX for math). Do NOT solve it and do NOT invent questions that are not there.
- Ignore titles, general instructions, point values and page numbers — only the actual questions.
- Transcribe in the document's own language.
- {language_rule_for(language)}

Return only JSON matching the schema."""


def exercise_material_hashes(user_id: str, source_file_hash: Optional[str], folder_id: Optional[str],
                             widen: bool) -> list[str]:
    """De bron-decks waarin we naar de juiste dia's zoeken. Alleen echt
    lesmateriaal (folder_document_hashes filtert opgaven en snel-foto's al weg).
    widen=True → het hele vak; anders het gekoppelde college."""
    if widen and folder_id:
        return folder_document_hashes(user_id, folder_id)
    if source_file_hash:
        return [source_file_hash]
    if folder_id:
        return folder_document_hashes(user_id, folder_id)
    return []


# Boven dit aantal pagina's past het materiaal niet meer in één digest: dan
# selecteren we eerst de kansrijke pagina's voor. Onder deze grens (een normaal
# college) gaat gewoon het hele document mee, precies zoals voorheen.
SHORTLIST_THRESHOLD_PAGES = 80
SHORTLIST_MAX_PAGES = 45


def shortlist_pages(hashes: list[str], query: str) -> list[tuple[int, int, str]]:
    """Voorselectie op woordoverlap: de pagina's die het meest op de opgave lijken,
    als (doc_index (1-gebaseerd), page_index (0-gebaseerd), tekst).

    Nodig voor dikke bronnen: bij een boek van 1200 pagina's paste alleen het
    begin in de digest, waardoor 'waar staat dit?' nooit verder dan de eerste
    pagina's kon wijzen. Zelfde lexicale scoring als /search — geen embeddings."""
    terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) >= 4]
    if not terms:
        return []
    scored: list[tuple[int, int, int, str]] = []
    for di, h in enumerate(hashes, start=1):
        try:
            _, texts = get_document_texts(h)
        except Exception:
            continue
        for pi, text in enumerate(texts):
            low = (text or "").lower()
            if not low:
                continue
            score = sum(low.count(t) for t in terms)
            if score > 0:
                scored.append((score, di, pi, text))
    scored.sort(key=lambda s: -s[0])
    return [(di, pi, text) for _, di, pi, text in scored[:SHORTLIST_MAX_PAGES]]


def build_material_blocks(user_id: str, hashes: list[str], total_budget: int = 16000,
                          query: Optional[str] = None) -> str:
    """Gelabelde digest van de bron-decks: per document een blok, met de
    pagina-labels ([Slide N]/[Page N]) zodat het model dia's per document+pagina
    kan citeren. Bij veel pagina's (een boek) worden eerst de kansrijke pagina's
    voorgeselecteerd op basis van `query`; anders gaat alles mee zoals voorheen."""
    if not hashes:
        return ""

    total_pages = 0
    for h in hashes:
        total_pages += int((load_meta(user_id, h) or {}).get("total_pages") or 0)

    if query and total_pages > SHORTLIST_THRESHOLD_PAGES:
        picked = shortlist_pages(hashes, query)
        if picked:
            per_page = max(200, total_budget // len(picked))
            by_doc: dict[int, list[str]] = {}
            for di, pi, text in sorted(picked, key=lambda p: (p[0], p[1])):
                label = page_label_for((load_meta(user_id, hashes[di - 1]) or {}).get("file_type", "pdf")).capitalize()
                by_doc.setdefault(di, []).append(f"[{label} {pi + 1}]\n{truncate(clean_text(text), per_page)}")
            blocks = []
            for di, pages in by_doc.items():
                name = (load_meta(user_id, hashes[di - 1]) or {}).get("file_name", f"document {di}")
                blocks.append(f"=== Document {di}: {name} ===\n" + "\n\n".join(pages))
            return "\n\n".join(blocks)[:total_budget]

    per_doc = max(2500, total_budget // len(hashes))
    blocks = []
    for i, h in enumerate(hashes, start=1):
        digest, _, _ = build_document_digest(h, max_total=per_doc)
        meta = load_meta(user_id, h) or {}
        name = meta.get("file_name", f"document {i}")
        blocks.append(f"=== Document {i}: {name} ===\n{digest}")
    return "\n\n".join(blocks)[:total_budget]


def build_locate_system(language: str, multi: bool) -> str:
    doc_rule = (
        "Each source page is labelled [Slide N] or [Page N] inside a document block "
        "'=== Document i: name ==='. For every relevant slide return doc_index (the i of its block) and page (the N)."
        if multi else
        "Each source page is labelled [Slide N] or [Page N]. Return doc_index 1 and page = N for every relevant slide."
    )
    return f"""You help a student who is stuck on an exercise find WHERE in their own lecture material the needed theory is explained. You do NOT solve the exercise here.

TASK
- Read the exercise (text and/or attached image) and the lecture material below.
- Find the slides/pages that DIRECTLY explain the concept, formula or method needed to solve THIS exercise.
- Return 0–3 slides: if you find relevant ones, return them (up to 3); if you find NONE you are confident about, return an empty list.
- {doc_rule}
- why: ONE short sentence naming what is on that slide that helps (e.g. "the definition and formula of the Nyquist criterion").
- Honesty over guessing: an empty result is BETTER than a slide you are unsure about. Never invent or hallucinate a page.
- {language_rule_for(language)}

Return only JSON matching the schema."""


def build_exercise_help_system(language: str) -> str:
    return f"""You are an outstanding tutor. Give a COMPLETE, perfectly followable, step-by-step worked solution to the exercise, all the way to the final answer, so the student both understands it and can reproduce it.

RULES
- Solve it COMPLETELY and end with the final answer — this is the most important rule. Actually carry out every step: do the substitutions, the algebra and the arithmetic. NEVER stop at "now substitute and compute" or leave the last step "as an exercise": perform that computation and reach the concrete final result, stated on its own line in bold (e.g. **Antwoord: ...**).
- Lay it out as numbered steps. Each step shows the real math (the expressions and how they transform), with a short reason why. Show the intermediate algebra so it is easy to follow.
- If the student asked about one specific (sub)question, solve exactly that one; otherwise solve the whole exercise.
- Be efficient and clear — the math itself does the teaching: no filler, no restating the question, no long meta-commentary.
- All math in LaTeX ($...$ inline, $$...$$ for a displayed line). Stay grounded in correct theory and the given material; if something needed is missing, state the assumption briefly and continue to the final answer.
- {language_rule_for(language)}

Return pure markdown only."""
