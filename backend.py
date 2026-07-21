"""
StudyCopilot Backend v3
=======================

Eén doel: uitleg van dezelfde kwaliteit als Gemini's AI-modus.

Hoe dat werkt (en waarom v2 dat niet haalde):
- Het model KIJKT naar een hoge-resolutie afbeelding van de dia (grafieken,
  handschrift, omcirkelde antwoorden) in plaats van alleen tekst te lezen.
- Het model schrijft VRIJE markdown met LaTeX ($...$ / $$...$$) in plaats van
  in een strak JSON-schema geperst te worden.
- Eén enkele model-call per dia, gestreamd (SSE), zodat de uitleg "typend"
  binnenkomt zoals bij Gemini.
- De uitleg kent de context: documentnaam, dia X van N, en de inhoud van de
  vorige dia's, zodat het als een doorlopende les voelt.
- Automatische taaldetectie: standaard antwoordt de tutor in de taal van de
  dia zelf.

Endpoints:
- POST   /upload                              pdf/pptx uploaden
- GET    /document/{file_hash}                metadata + pagina's
- GET    /slide-image/{file_hash}/{page}      dia-afbeelding (PNG)
- POST   /explain                             uitleg (SSE-stream of JSON)
- POST   /prefetch/{file_hash}/{page}         volgende dia alvast genereren
- DELETE /document/{file_hash}                document + cache verwijderen
- GET    /folders  + POST/PATCH/DELETE        mappen (vakken) beheren
- POST   /document/{file_hash}/folder         document in een map zetten
- POST   /exam/generate                       oefententamen (document of hele map)
- POST   /exam/attempt                        tentamenresultaat registreren -> herhaalplanning
- GET    /exam/plan                           herhaalplanning op basis van je fouten
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
import ai_stats
import cache_store
import rate_limit
import usage
from ai_engine import Message, text_part


# =========================================================
# CONFIG
# =========================================================

BASE_PATH = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_PATH / ".env", override=True)

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

TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.4"))

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
PREFETCH_ON_UPLOAD = int(os.getenv("PREFETCH_ON_UPLOAD", "1"))

# Na de upload ook alvast flashcards genereren (vaste instellingen => de cache
# raakt gegarandeerd). De quiz wordt niet meer geprefetcht: die heeft een
# instelscherm en elke afwijkende instelling maakte de vooraf gegenereerde set
# waardeloos — dat waren structureel weggegooide tokens.
PREFETCH_STUDY_ON_UPLOAD = os.getenv("PREFETCH_STUDY_ON_UPLOAD", "true").lower() == "true"

# Hoeveel prefetch-taken (uitleg/quiz/flashcards) er tegelijk mogen draaien.
PREFETCH_WORKERS = int(os.getenv("PREFETCH_WORKERS", "3"))

PROMPT_VERSION = "v3.4"  # onderdeel van de cache-key: prompt gewijzigd => cache ongeldig

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
logger = logging.getLogger("studycopilot-v3")

# =========================================================
# APP
# =========================================================

app = FastAPI(title="StudyCopilot Backend v3", version="3.3.0")

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
    file_type: Literal["pdf", "pptx", "docx", "image"]
    total_pages: int
    status: DocStatus
    note: Optional[str] = None
    pages: list[PageInfo]


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


# =========================================================
# DOCUMENT OPSLAG / METADATA
# =========================================================

def load_meta(file_hash: str) -> Optional[dict[str, Any]]:
    # Via cache_store (namespace "meta" = zelfde map als het oude META_DIR, dus
    # geen migratie nodig) i.p.v. de kale load_json: zonder dit overleefde
    # documentmetadata geen deploy, zelfs met Supabase ingesteld.
    return cache_store.get_json("meta", file_hash)


def save_meta(file_hash: str, payload: dict[str, Any]) -> None:
    cache_store.put_json("meta", file_hash, payload)


def set_document_status(file_hash: str, status: DocStatus, note: Optional[str] = None) -> None:
    meta = load_meta(file_hash) or {}
    meta["status"] = status
    if note:
        meta["note"] = note
    save_meta(file_hash, meta)


def ensure_document_exists(file_hash: str) -> dict[str, Any]:
    meta = load_meta(file_hash)
    if not meta:
        raise_api_error(404, "DOCUMENT_NOT_FOUND", "Document niet gevonden.")
    return meta


# =========================================================
# ZACHTE EIGENDOMS-CHECK (geen accounts — zie ai_engine/i18n-commentaar elders)
# =========================================================
# Er zijn nog geen accounts, maar de frontend stuurt al een stabiele anonieme
# X-User-Id mee op elk verzoek (state.js "sc.uid"). Die hergebruiken we als
# lichte eigendomscheck op destructieve acties, zodat niet zomaar iedereen die
# een hash/folder-id kent (bv. via een gedeelde link) andermans document of map
# kan verwijderen/hernoemen. Dit is GEEN echte beveiliging — de header is
# triviaal te vervalsen — maar een drempel tegen per-ongeluk misbruik, in lijn
# met de bewust uitgestelde accounts. Objecten van vóór deze wijziging hebben
# geen owner_id en blijven daarom voor iedereen bewerkbaar (geen onverwachte
# lockout van bestaande data).

def request_user_id(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    uid = (request.headers.get("x-user-id") or "").strip()
    return uid or None


def check_owner(obj: dict[str, Any], request: Optional[Request]) -> None:
    owner = obj.get("owner_id")
    if not owner:
        return
    if request_user_id(request) != owner:
        raise_api_error(403, "NOT_OWNER", "Alleen wie dit heeft aangemaakt kan dit wijzigen of verwijderen.")


# suffix -> logisch bestandstype
SUPPORTED_SUFFIXES: dict[str, str] = {
    ".pdf": "pdf",
    ".pptx": "pptx",
    ".docx": "docx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
}

FileType = Literal["pdf", "pptx", "docx", "image"]


def page_label_for(file_type: str) -> str:
    return "dia" if file_type == "pptx" else "pagina"


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
    if cached and cached.get("file_type") in ("pdf", "pptx", "docx", "image") and isinstance(cached.get("texts"), list):
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
    """Zet PPTX of DOCX om naar PDF via LibreOffice."""
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
            "LENGTH BUDGET — this matters as much as correctness. Target 90-180 words; never exceed 220. "
            "Sentence 1 names the point of the slide, then move IMMEDIATELY into teaching the core content — "
            "no inventory of what is on the slide, no describing every element. The student sees the slide "
            "next to your text: explain what it MEANS, never transcribe it. "
            "Use at most 2 short sections (or none for simple slides). For derivations show only the essential "
            "steps (max 2 displayed equations) plus the conclusion; summarize trivial algebra in half a sentence. "
            "A sparse or administrative slide gets 1-3 sentences. "
            "If you are tempted to add another section or step: cut it — the student can always ask a follow-up question."
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
            "The student is a beginner: use simple wording, spell out implicit steps, "
            "avoid unexplained jargon."
        ),
        "intermediate": "The student has intermediate knowledge: normal technical vocabulary is fine.",
        "advanced": (
            "The student is advanced: be more compact, use precise technical language and "
            "focus on the reasoning and interpretation rather than the basics."
        ),
    }[audience_level]

    if detail_level == "short":
        ending_rule = (
            "This is the LAST page: close with one warm sentence. Do not suggest a next slide."
            if is_last_page else
            "End with at most ONE short follow-up question inside the same paragraph, or nothing at all."
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
            "This is the LAST page: close the session with a brief wrap-up of the key takeaways "
            "and wish the student good luck. Do not suggest a next slide."
            if is_last_page else
            "End with ONE short, natural bridge sentence or question (e.g. continue to the next slide, "
            "or one thing to try). Never a menu of options."
        )
        structure_rules = f"""STRUCTURE OF YOUR EXPLANATION
- Open with ONE sentence that says what the point of this slide is (e.g. "Deze slide introduceert ..."). No greetings, no filler.
- Then move IMMEDIATELY into teaching the content the slide is meant to convey. Use at most a couple of short markdown headers (### or bold), optionally with a single fitting emoji (📊 🧮 🎯 ⚠️ 🔍), and compact bullets. Every sentence must teach something; never pad, never inventory the slide.
- If the slide builds on a previous slide, say so briefly and make the connection.
- {ending_rule}"""
        derivation_rule = (
            "- When a derivation matters, walk through it step by step: one displayed equation per step with one short sentence of reasoning."
            if detail_level == "long" else
            "- For derivations: only the essential steps and the conclusion; summarize routine algebra in words."
        )
        visuals_rules = """VISUALS (graphs, diagrams, block schemes)
- Say what is on the axes, what the curves/branches do, and what the colors, zones or markers mean.
- Tell the student where to look first and what the ONE takeaway of the figure is.
- Be precise about visual claims: a curve that comes close to a point does not necessarily pass through it. If something is genuinely ambiguous in the image, say so instead of guessing.

CHARTS (draw a graph only when it GENUINELY helps understanding)
- You MAY include AT MOST ONE chart, and only when seeing it plotted makes the concept click (the shape of a function, a trend, a comparison) — never decorative, never for an administrative or purely textual slide. When in doubt, leave it out.
- Emit the chart as a single fenced code block that starts with ```chart and contains ONLY this JSON:
```chart
{"kind":"function","title":"...","xlabel":"x","ylabel":"y","fn":"x^2 - 3*x + 2","domain":[-2,5]}
```
  For a mathematical function use kind "function": give the expression in "fn" (variable x; allowed: + - * / ^, parentheses, sin cos tan asin acos atan sqrt exp log ln abs, pi, e) and the visible range in "domain":[min,max]. Do NOT compute the points yourself — the app evaluates the function exactly.
  For data/statistics use kind "line", "bar" or "scatter" with "labels":[...] and "series":[{"label":"...","points":[[x,y],...]}] (values taken from the material, not invented).
- The chart supplements your words; still explain the takeaway in text. Keep the JSON minimal and valid."""

    return f"""You are an outstanding university tutor inside a study app. The student sees the slide image on the left of the screen and your explanation on the right. You explain lecture slides one at a time, as if you are a calm, sharp teacher walking through the deck with the student.

THE SLIDE IMAGE IS YOUR PRIMARY SOURCE OF TRUTH.
Look at it carefully: titles, formulas, graphs, diagrams, tables, colors, arrows, handwritten annotations, circled answers. The extracted text you also receive is only a fallback for hard-to-read parts — the layout and visuals only exist in the image.

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
- {detail_rule}
- {audience_rule}
{f"- {mode_rule}" if mode_rule else ""}
- Administrative slides (title page, agenda, break, Wooclap, references) get only a few friendly sentences — never a fabricated deep-dive.

LANGUAGE
- {language_rule}
- The explanation must read as if originally written in that language, never as a translation.

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
        {"reason": str(last_error), "models_tried": ai_engine.available_models()},
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
                yield sse_event({"type": "error", "message": "De uitleg is halverwege afgebroken. Probeer het opnieuw."})
                return

    yield sse_event({
        "type": "error",
        "message": humanize_ai_error(last_error),
        "details": str(last_error),
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
# Alleen VERSE generaties (cache-misses) gaan langs de gate. Cache-hits zijn
# gratis en worden nooit geteld — daardoor blijft de kost per gratis gebruiker
# begrensd terwijl populaire (gecachte) vakken onbeperkt voelen. Staat metering
# uit (ENABLE_QUOTA=false, standaard), dan laat de gate alles door.

def quota_gate(request: Request) -> tuple[str, str]:
    """Controleer tegoed vóór een verse generatie. Geeft (user_id, plan) terug;
    roep na de generatie usage.record(user_id, plan) aan om af te schrijven."""
    # IP-gebaseerde noodrem, altijd aan (i.t.t. de quota die standaard uit
    # staat) — de quota zelf is te omzeilen door een nieuwe X-User-Id te sturen,
    # dit niet.
    client_ip = request.client.host if request is not None and request.client else "unknown"
    if not rate_limit.check(client_ip):
        raise_api_error(429, "RATE_LIMITED", "Te veel aanvragen kort na elkaar — even wachten.", {})
    user_id, plan = usage.identify(request)
    if not usage.allowed(user_id, plan):
        used, limit = usage.status(user_id, plan)
        raise_api_error(
            429, "QUOTA_EXCEEDED",
            "Je gratis tegoed voor vandaag is op. Upgrade voor onbeperkte AI-uitleg.",
            {"plan": plan, "limit": limit, "used": used},
        )
    return user_id, plan


@app.get("/usage")
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

@app.get("/")
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


@app.get("/health/deep")
def deep_health():
    return {
        "ok": True,
        "ai_providers": ai_engine.providers_status(),
        "libreoffice_found": bool(find_libreoffice_executable()),
        "models": ai_engine.available_models(),
        "base_dir": str(BASE_DIR.resolve()),
    }


@app.get("/health/ai-stats")
def ai_stats_endpoint(days: int = 7):
    """Ops-inzicht: hoe vaak elke AI-fallback-laag wordt geraakt, foutratio, gemiddelde latency."""
    return {"ok": True, **ai_stats.aggregate(days=max(1, min(days, 30)))}


# =========================================================
# ROUTES: UPLOAD / DOCUMENT / AFBEELDING
# =========================================================

@app.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...), kind: Optional[str] = Form(default=None),
                 background_tasks: BackgroundTasks = None, request: Request = None):
    start = time.perf_counter()

    client_ip = request.client.host if request is not None and request.client else "unknown"
    if not rate_limit.check(f"upload:{client_ip}", max_per_window=RATE_LIMIT_UPLOAD_MAX_PER_MIN):
        raise_api_error(429, "RATE_LIMITED", "Te veel uploads kort na elkaar — even wachten.", {})

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise_api_error(
            400, "UNSUPPORTED_FILE_TYPE",
            "Dit bestandstype wordt niet ondersteund.",
            {"supported": sorted(SUPPORTED_SUFFIXES.keys())},
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise_api_error(400, "EMPTY_FILE", "Leeg bestand ontvangen.")
    if len(file_bytes) > MAX_UPLOAD_MB * 1024 * 1024:
        raise_api_error(
            413, "FILE_TOO_LARGE", f"Bestand is groter dan de limiet van {MAX_UPLOAD_MB}MB.",
            {"max_mb": MAX_UPLOAD_MB},
        )

    file_hash = sha256_bytes(file_bytes)
    saved_path = UPLOAD_DIR / f"{file_hash}{suffix}"
    if not saved_path.exists():
        saved_path.write_bytes(file_bytes)
    # Best-effort backup van het brondocument zelf (niet alleen de afgeleide
    # cache) — zonder dit is een geüpload college weg zodra de lokale schijf
    # het is (deploy, schijfcorruptie, ...).
    cache_store.put_blob("uploads", f"{file_hash}{suffix}", file_bytes, file.content_type or "application/octet-stream")

    file_type = SUPPORTED_SUFFIXES[suffix]
    note: Optional[str] = None
    status: DocStatus = "ready"

    if file_type == "pptx":
        # De echte conversie gebeurt ná de response in post_upload_processing,
        # zodat de upload niet op LibreOffice hoeft te wachten (tekst-extractie
        # via python-pptx heeft de PDF niet nodig).
        if not find_libreoffice_executable():
            status = "partial"
            note = (
                "De PowerPoint kon niet naar afbeeldingen worden omgezet (LibreOffice niet beschikbaar). "
                "De AI werkt nu alleen op tekst; grafieken en figuren worden niet gezien."
            )
    elif file_type == "docx":
        if not convert_office_to_pdf(saved_path, file_hash):
            saved_path.unlink(missing_ok=True)
            raise_api_error(
                500, "DOCX_CONVERSION_FAILED",
                "Het Word-document kon niet worden omgezet (LibreOffice is hiervoor nodig).",
            )
    elif file_type == "image":
        if not image_to_pdf(saved_path, file_hash):
            saved_path.unlink(missing_ok=True)
            raise_api_error(500, "IMAGE_CONVERSION_FAILED", "De afbeelding kon niet worden verwerkt.")

    try:
        texts = extract_texts_for(file_type, saved_path, file_hash)
    except Exception as e:
        raise_api_error(500, "TEXT_EXTRACTION_FAILED", "De tekst kon niet uit het bestand worden gelezen.", {"reason": str(e)})

    save_json(text_cache_path(file_hash), {"file_type": file_type, "texts": texts})

    total_pages = len(texts)
    # Zelfde bytes eerder al door iemand anders geüpload? Eigendom niet
    # overschrijven — anders zou een re-upload van identieke content ownership
    # kunnen "stelen".
    existing_meta = load_meta(file_hash)
    owner_id = existing_meta.get("owner_id") if existing_meta else request_user_id(request)
    save_meta(file_hash, {
        "file_hash": file_hash,
        "file_name": file.filename or f"{file_hash}{suffix}",
        "file_type": file_type,
        "total_pages": total_pages,
        "status": status,
        "note": note,
        "uploaded_at": time.time(),
        "owner_id": owner_id,
        # Blijft behouden bij een re-upload van identieke bytes.
        "folder_id": existing_meta.get("folder_id") if existing_meta else None,
        "last_page_index": existing_meta.get("last_page_index") if existing_meta else None,
        "last_opened_at": existing_meta.get("last_opened_at") if existing_meta else None,
        # "quick" = losse huiswerkfoto (snel-foto-flow); wordt uit de gewone
        # documentenlijst gefilterd zodat die niet vervuilt.
        "kind": (kind or (existing_meta.get("kind") if existing_meta else None)),
    })

    # Op de achtergrond: dia's alvast renderen en de eerste uitleg(gen) alvast
    # genereren, zodat het openen van het document instant voelt.
    if background_tasks:
        background_tasks.add_task(post_upload_processing, file_hash)

    label = page_label_for(file_type)
    pages = [
        PageInfo(
            index=i,
            label=f"{label} {i + 1}",
            image_ready=page_image_path(file_hash, i, "display").exists(),
            image_url=slide_image_url(file_hash, i),
            text_preview=(texts[i] or "")[:180],
        )
        for i in range(total_pages)
    ]

    logger.info("Upload %s klaar in %.2fs (%s pagina's)", file_hash[:12], time.perf_counter() - start, total_pages)

    return UploadResponse(
        file_hash=file_hash,
        file_name=file.filename or f"{file_hash}{suffix}",
        file_type=file_type,
        total_pages=total_pages,
        status=status,
        note=note,
        pages=pages,
    )


def post_upload_processing(file_hash: str) -> None:
    """Na de upload-response: eventuele Office->PDF-conversie, dan de dia's die
    de gebruiker meteen ziet, parallel de eerste uitleg(gen) + het
    studeer-materiaal (quiz/flashcards), en daarna de rest van de dia's."""
    try:
        meta = load_meta(file_hash) or {}
        total = int(meta.get("total_pages", 0))
        if total <= 0:
            return
        get_pdf_for_document(file_hash)  # pptx/docx: conversie gebeurt nu hier, niet in /upload
        prerender_display_range(file_hash, 0, min(4, total))
        base_req = ExplainRequest(file_hash=file_hash, page_index=0)
        for i in range(min(PREFETCH_ON_UPLOAD, total)):
            _prefetch_pool.submit(prefetch_one_page, base_req, i)
        if PREFETCH_STUDY_ON_UPLOAD:
            _prefetch_pool.submit(prefetch_study_material, file_hash)
        prerender_display_range(file_hash, 4, total)
    except Exception:
        logger.exception("Post-upload verwerking mislukt voor %s", file_hash)


@app.get("/documents")
def list_documents():
    """Alle eerder geüploade documenten, nieuwste eerst — voor een geschiedenis-overzicht."""
    documents = []
    for path in META_DIR.glob("*.json"):
        meta = load_json(path)
        if not meta or not meta.get("file_hash"):
            continue
        file_hash = meta["file_hash"]
        documents.append({
            "file_hash": file_hash,
            "file_name": meta.get("file_name"),
            "file_type": meta.get("file_type"),
            "total_pages": meta.get("total_pages", 0),
            "status": meta.get("status", "uploaded"),
            "uploaded_at": meta.get("uploaded_at"),
            "last_opened_at": meta.get("last_opened_at", meta.get("uploaded_at")),
            "last_page_index": meta.get("last_page_index", 0),
            "folder_id": meta.get("folder_id"),
            "kind": meta.get("kind"),
            "thumbnail_url": slide_image_url(file_hash, 0),
        })
    documents.sort(key=lambda d: d.get("last_opened_at") or 0, reverse=True)
    return {"ok": True, "documents": documents}


@app.get("/document/{file_hash}")
def get_document(file_hash: str):
    meta = ensure_document_exists(file_hash)
    meta["last_opened_at"] = time.time()
    save_meta(file_hash, meta)
    file_type, texts = get_document_texts(file_hash)
    label = page_label_for(file_type)

    pages = [
        PageInfo(
            index=i,
            label=f"{label} {i + 1}",
            image_ready=page_image_path(file_hash, i, "display").exists(),
            image_url=slide_image_url(file_hash, i),
            text_preview=(texts[i] or "")[:180],
        ).model_dump()
        for i in range(len(texts))
    ]

    return {
        "ok": True,
        "file_hash": file_hash,
        "file_name": meta.get("file_name"),
        "file_type": file_type,
        "total_pages": len(texts),
        "status": meta.get("status", "uploaded"),
        "note": meta.get("note"),
        "last_page_index": meta.get("last_page_index", 0),
        "pages": pages,
    }


@app.get("/slide-image/{file_hash}/{page_index}")
def slide_image(
    file_hash: str,
    page_index: int,
    resolution: Literal["display", "ai", "normal", "high"] = Query(default="display"),
):
    meta = ensure_document_exists(file_hash)
    total_pages = int(meta.get("total_pages") or 0)
    if page_index < 0 or (total_pages and page_index >= total_pages):
        raise_api_error(400, "INVALID_PAGE_INDEX", "Ongeldige page_index.")

    # backwards-compatibel: normal->display, high->ai
    res: Resolution = "ai" if resolution in ("ai", "high") else "display"
    image_path = ensure_slide_image(file_hash, page_index, res)
    if not image_path:
        raise_api_error(404, "IMAGE_NOT_AVAILABLE", "Dia-afbeelding niet beschikbaar.")
    return FileResponse(
        image_path,
        media_type="image/jpeg",
        # De URL is content-addressed (hash bepaalt de inhoud), dus de browser
        # mag de afbeelding voor altijd cachen => heropenen laadt instant.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.delete("/document/{file_hash}")
def delete_document(file_hash: str, request: Request = None):
    meta = ensure_document_exists(file_hash)
    check_owner(meta, request)
    for suffix in SUPPORTED_SUFFIXES:
        path = UPLOAD_DIR / f"{file_hash}{suffix}"
        if path.exists():
            path.unlink()
        cache_store.delete_blob("uploads", f"{file_hash}{suffix}")
    for directory in (IMAGE_DIR / file_hash, PDF_DIR / file_hash):
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
    if text_cache_path(file_hash).exists():
        text_cache_path(file_hash).unlink()
    cache_store.delete_json("meta", file_hash)
    cache_store.delete_json("notes", file_hash)
    _document_texts_cached.cache_clear()
    # Uitleg-cache-keys zijn hashes zonder document-koppeling; losse cache-bestanden
    # zijn klein en onschadelijk, dus die laten we staan.
    return {"ok": True, "file_hash": file_hash}


# =========================================================
# ROUTES: EXPLAIN (de kern)
# =========================================================

def prepare_explain_inputs(req: ExplainRequest) -> dict[str, Any]:
    meta = ensure_document_exists(req.file_hash)
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


def prefetch_one_page(base_req: ExplainRequest, page_index: int) -> None:
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
            prepared = prepare_explain_inputs(req)
            markdown, model_name = generate_markdown(prepared["contents"], prepared["system_instruction"])
            save_explanation_cache(cache_key, markdown, model_name, prepared["used_vision"])
            logger.info("Prefetch klaar: pagina %s van %s", page_index + 1, req.file_hash[:12])
        finally:
            release_generation(cache_key)
    except Exception:
        logger.exception("Prefetch mislukt voor %s pagina %s", base_req.file_hash, page_index)


def prefetch_ahead(base_req: ExplainRequest, total_pages: int, ahead: Optional[int] = None) -> None:
    """De volgende dia's alvast genereren, parallel. In de standaardmodus
    PREFETCH_AHEAD diep; in Simpel/Studeer-modus maar 1 (mensen schakelen daar
    vaak even naartoe om te vergelijken — 3 dia's vooruit genereren is dan
    meestal weggegooide tokens, en 1 vooruit voelt nog steeds instant)."""
    if ahead is None:
        ahead = PREFETCH_AHEAD if base_req.mode == "explain" else min(1, PREFETCH_AHEAD)
    for i in range(base_req.page_index + 1, min(base_req.page_index + 1 + ahead, total_pages)):
        _prefetch_pool.submit(prefetch_one_page, base_req, i)


def cached_sse_response(cached: dict[str, Any]) -> StreamingResponse:
    def cached_stream():
        yield sse_event({"type": "delta", "text": cached["markdown"]})
        yield sse_event({"type": "done", "model": cached.get("model"), "cached": True})
    return StreamingResponse(cached_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/explain")
def explain(req: ExplainRequest, background_tasks: BackgroundTasks, request: Request):
    meta = ensure_document_exists(req.file_hash)
    total_pages = int(meta.get("total_pages") or 0)
    if not total_pages:
        total_pages = len(get_document_texts(req.file_hash)[1])
    if req.page_index < 0 or req.page_index >= total_pages:
        raise_api_error(400, "INVALID_PAGE_INDEX", "Ongeldige page_index.",
                        {"page_index": req.page_index, "total_pages": total_pages})

    cache_key = explanation_cache_key_for(req)

    # Volgende dia's alvast genereren zodat doorklikken (bijna) instant voelt.
    # Alleen bij een normale uitleg, niet bij vervolgvragen in de chat.
    if not req.question and not req.history:
        background_tasks.add_task(prefetch_ahead, req, total_pages)

    # Cache-hit: direct terugsturen, zonder afbeeldingen te renderen of
    # prompts te bouwen (dit pad kost nu alleen één kleine JSON-read).
    if cache_key and not req.force_refresh:
        cached = load_explanation_cache(cache_key)
        if cached and cached.get("markdown"):
            if req.stream:
                return cached_sse_response(cached)
            return {
                "ok": True,
                "markdown": cached["markdown"],
                "model": cached.get("model"),
                "cached": True,
                "used_vision": cached.get("used_vision", True),
            }

    # Cache-miss → verse generatie: tegoed controleren en afschrijven.
    uid, plan = quota_gate(request)
    usage.record(uid, plan)

    prepared = prepare_explain_inputs(req)

    if req.stream:
        def stream_with_dedup() -> Iterator[str]:
            claimed = False
            try:
                if cache_key:
                    event, claimed = claim_generation(cache_key)
                    if not claimed:
                        # Deze dia wordt al gegenereerd (bijv. door prefetch): wacht op
                        # het event in plaats van dezelfde uitleg dubbel te genereren.
                        yield sse_event({"type": "start", "status": "waiting"})
                        event.wait(timeout=180)
                        cached = load_explanation_cache(cache_key)
                        if cached and cached.get("markdown"):
                            yield sse_event({"type": "delta", "text": cached["markdown"]})
                            yield sse_event({"type": "done", "model": cached.get("model"), "cached": True})
                            return
                        # De andere generatie is mislukt of duurde te lang: zelf proberen.
                        event, claimed = claim_generation(cache_key)
                yield from stream_markdown(
                    prepared["contents"], prepared["system_instruction"],
                    cache_key if claimed else None, prepared["used_vision"],
                )
            finally:
                if claimed:
                    release_generation(cache_key)

        return StreamingResponse(
            stream_with_dedup(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    start = time.perf_counter()
    claimed = False
    try:
        if cache_key:
            event, claimed = claim_generation(cache_key)
            if not claimed:
                event.wait(timeout=180)
                cached = load_explanation_cache(cache_key)
                if cached and cached.get("markdown"):
                    return {
                        "ok": True,
                        "markdown": cached["markdown"],
                        "model": cached.get("model"),
                        "cached": True,
                        "used_vision": cached.get("used_vision", True),
                    }
                event, claimed = claim_generation(cache_key)
        markdown, model_name = generate_markdown(prepared["contents"], prepared["system_instruction"])
        if cache_key:
            save_explanation_cache(cache_key, markdown, model_name, prepared["used_vision"])
    finally:
        if claimed:
            release_generation(cache_key)

    return {
        "ok": True,
        "markdown": markdown,
        "model": model_name,
        "cached": False,
        "used_vision": prepared["used_vision"],
        "duration_seconds": round(time.perf_counter() - start, 2),
    }


@app.post("/prefetch/{file_hash}/{page_index}")
def prefetch(
    file_hash: str,
    page_index: int,
    background_tasks: BackgroundTasks,
    language: str = Query(default="auto"),
    detail_level: Literal["short", "normal", "long"] = Query(default="normal"),
):
    """Genereer de uitleg van een dia alvast op de achtergrond (bijv. de volgende dia)."""
    ensure_document_exists(file_hash)
    _, texts = get_document_texts(file_hash)
    if page_index < 0 or page_index >= len(texts):
        return {"ok": True, "prefetched": False, "reason": "buiten bereik"}

    base_req = ExplainRequest(
        file_hash=file_hash, page_index=page_index,
        language=language, detail_level=detail_level, stream=False,
    )
    background_tasks.add_task(prefetch_one_page, base_req, page_index)
    return {"ok": True, "prefetched": True, "page_index": page_index}


# =========================================================
# GEDEELDE STUDEER-HELPERS
# =========================================================

# Studeerdata (flashcards + spaced-repetition-planning) loopt via cache_store,
# zodat de duur gegenereerde flashcards deploys overleven en gedeeld zijn — geen
# tokens meer voor kaarten die al eens gemaakt zijn. NB: de SRS-planning is nu
# per document (net als voorheen server-side); bij echte accounts hoort die
# voortgang per gebruiker opgeslagen te worden.
def load_study_data(file_hash: str) -> dict[str, Any]:
    data = cache_store.get_json("study", file_hash) or {}
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


def save_study_data(file_hash: str, data: dict[str, Any]) -> None:
    cache_store.put_json("study", file_hash, data)


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
    raise_api_error(502, "AI_GENERATION_FAILED", humanize_ai_error(last_error), {"reason": str(last_error)})


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


@app.post("/summary")
def summarize(req: SummaryRequest, request: Request):
    ensure_document_exists(req.file_hash)
    digest, file_type, total_pages = build_document_digest(req.file_hash)

    cache_key = sha256_text("|".join([
        "summary", PROMPT_VERSION, req.file_hash, req.language.strip().lower(),
    ]))

    if not req.force_refresh:
        cached = load_explanation_cache(cache_key)
        if cached and cached.get("markdown"):
            if req.stream:
                def cached_stream():
                    yield sse_event({"type": "delta", "text": cached["markdown"]})
                    yield sse_event({"type": "done", "model": cached.get("model"), "cached": True})
                return StreamingResponse(cached_stream(), media_type="text/event-stream",
                                         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            return {"ok": True, "markdown": cached["markdown"], "model": cached.get("model"), "cached": True}

    uid, plan = quota_gate(request)
    usage.record(uid, plan)

    parts: list[Any] = []
    if len(digest) < 400:
        # Nauwelijks tekstlaag (gescand document / afbeeldingen): geef pagina-afbeeldingen mee.
        parts.extend(document_image_parts(req.file_hash, total_pages))
    meta = load_meta(req.file_hash) or {}
    parts.append(text_part(
        f"Document: {meta.get('file_name', 'onbekend')} ({total_pages} pagina's).\n\n"
        f"Inhoud per pagina:\n\n{digest if digest else '(geen tekstlaag; gebruik de afbeeldingen)'}\n\n"
        "Schrijf nu de studiesamenvatting van dit hele document."
    ))
    contents = [Message(role="user", parts=parts)]
    system_instruction = build_summary_system(req.language)

    if req.stream:
        return StreamingResponse(
            stream_markdown(contents, system_instruction, cache_key),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    markdown, model_name = generate_markdown(contents, system_instruction)
    save_explanation_cache(cache_key, markdown, model_name)
    return {"ok": True, "markdown": markdown, "model": model_name, "cached": False}


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
    difficulty: Literal["easy", "medium", "hard"] = "medium"


class QuizSet(BaseModel):
    questions: list[QuizQuestion] = Field(default_factory=list)


class QuizGenerateRequest(BaseModel):
    file_hash: str
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
- {type_rule}
- {diff_rule}
- For multiple-choice: exactly 4 options, one clearly correct (set correct_option to its 0-based index), distractors must be plausible misconceptions.
- For open questions: set model_answer to a short, correct model answer.
- Set page_index to the 0-based page the question is mainly about (the page numbers in the input are 1-based: subtract 1).
- All math in LaTeX ($...$ / $$...$$), also inside options and answers.
- Never invent content that is not in the material.
- {language_rule_for(language)}

Return only JSON matching the schema."""


# request is optioneel: de prefetch roept deze functie ook intern aan (zonder
# HTTP-request en dus zonder quota — cache-warming telt niet tegen de gebruiker).
@app.post("/quiz/generate")
def quiz_generate(req: QuizGenerateRequest, request: Request = None):
    ensure_document_exists(req.file_hash)
    _, texts = get_document_texts(req.file_hash)

    cache_key = sha256_text("|".join([
        "quiz", PROMPT_VERSION, req.file_hash, str(req.page_index),
        str(req.count), req.question_type, req.difficulty, req.language.strip().lower(),
    ]))

    def cached_response() -> Optional[dict[str, Any]]:
        cached = cache_store.get_json("ai_cache", cache_key)
        if cached and cached.get("questions"):
            return {"ok": True, "questions": cached["questions"], "cached": True}
        return None

    if not req.force_refresh:
        hit = cached_response()
        if hit:
            return hit

    if request is not None:
        uid, plan = quota_gate(request)
        usage.record(uid, plan)

    # Dedup: als de prefetch (of een andere klik) deze set al genereert,
    # wachten we daarop in plaats van dubbel te genereren.
    claim_key = f"quiz|{cache_key}"
    claimed = False
    try:
        event, claimed = claim_generation(claim_key)
        if not claimed:
            event.wait(timeout=240)
            hit = cached_response()
            if hit:
                return hit
            event, claimed = claim_generation(claim_key)

        parts: list[Any] = []
        if req.page_index is not None:
            if req.page_index < 0 or req.page_index >= len(texts):
                raise_api_error(400, "INVALID_PAGE_INDEX", "Ongeldige page_index.")
            image = ensure_slide_image(req.file_hash, req.page_index, "ai")
            if image:
                parts.append(image_part(image))
            parts.append(text_part(
                f"Materiaal: pagina {req.page_index + 1} (afbeelding + tekst).\n\n"
                f"{truncate(clean_text(texts[req.page_index]), MAX_SLIDE_TEXT)}\n\n"
                f"Maak hier nu {req.count} oefenvragen over."
            ))
        else:
            digest, _, total_pages = build_document_digest(req.file_hash)
            if len(digest) < 400:
                parts.extend(document_image_parts(req.file_hash, total_pages))
            parts.append(text_part(
                f"Materiaal (heel document, {total_pages} pagina's):\n\n"
                f"{digest if digest else '(geen tekstlaag; gebruik de afbeeldingen)'}\n\n"
                f"Maak hier nu {req.count} oefenvragen over, verspreid over het hele document."
            ))

        contents = [Message(role="user", parts=parts)]
        result: QuizSet = generate_structured(
            contents,
            build_quiz_system(req.language, req.question_type, req.difficulty, req.count),
            QuizSet,
        )

        questions = [q.model_dump() for q in result.questions][:req.count]
        cache_store.put_json("ai_cache", cache_key, {"questions": questions, "created_at": time.time()})
        return {"ok": True, "questions": questions, "cached": False}
    finally:
        if claimed:
            release_generation(claim_key)


class QuizGradeRequest(BaseModel):
    file_hash: str
    question: str
    student_answer: str
    model_answer: Optional[str] = None
    page_index: Optional[int] = None
    language: str = "auto"


class QuizGradeResult(BaseModel):
    verdict: Literal["correct", "partial", "incorrect"]
    score: int = Field(ge=0, le=100)
    feedback: str  # markdown, met LaTeX


@app.post("/quiz/grade")
def quiz_grade(req: QuizGradeRequest, request: Request):
    ensure_document_exists(req.file_hash)
    uid, plan = quota_gate(request)
    usage.record(uid, plan)

    parts: list[Any] = []
    # De dia-afbeelding alleen meesturen als er géén modelantwoord is: mét
    # modelantwoord beoordeelt de tutor daartegen en voegt de afbeelding vrijwel
    # niets toe, terwijl die wel de duurste tokens van de hele call is.
    if req.page_index is not None and not (req.model_answer or "").strip():
        image = ensure_slide_image(req.file_hash, req.page_index, "ai")
        if image:
            parts.append(image_part(image))

    context = f"Vraag: {req.question}\n"
    if req.model_answer:
        context += f"Modelantwoord: {req.model_answer}\n"
    context += f"Antwoord van de student: {req.student_answer}"
    parts.append(text_part(context))

    system_instruction = f"""You are a fair, encouraging university tutor grading one practice-question answer.

RULES
- Judge on content, not wording: a differently phrased but correct answer is correct.
- verdict: "correct" (essentially right), "partial" (part right, something essential missing/wrong), "incorrect".
- score: 0-100 matching the verdict.
- feedback (markdown, LaTeX for math): first say clearly whether it is right; then in 1-4 sentences what was good, what was missing or wrong, and the correct reasoning. Encourage, never belittle.
- If a page image is attached, use it to verify the correct answer.
- {language_rule_for(req.language)}

Return only JSON matching the schema."""

    result: QuizGradeResult = generate_structured(
        [Message(role="user", parts=parts)], system_instruction, QuizGradeResult,
    )
    return {"ok": True, **result.model_dump()}


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


# request optioneel: ook intern aangeroepen door de prefetch (geen quota).
@app.post("/flashcards/generate")
def flashcards_generate(req: FlashcardGenerateRequest, request: Request = None):
    ensure_document_exists(req.file_hash)
    data = load_study_data(req.file_hash)
    fset = flashcard_set(data, req.language)

    if fset["flashcards"] and not req.force_refresh:
        return {"ok": True, "cards": fset["flashcards"], "cached": True}

    if request is not None:
        uid, plan = quota_gate(request)
        usage.record(uid, plan)

    # Dedup per (document, taal): een Engelse en Nederlandse set mogen parallel.
    claim_key = f"flashcards|{req.file_hash}|{req.language}"
    claimed = False
    try:
        event, claimed = claim_generation(claim_key)
        if not claimed:
            event.wait(timeout=240)
            data = load_study_data(req.file_hash)
            fset = flashcard_set(data, req.language)
            if fset["flashcards"] and not req.force_refresh:
                return {"ok": True, "cards": fset["flashcards"], "cached": True}
            event, claimed = claim_generation(claim_key)

        return _flashcards_generate_inner(req, data)
    finally:
        if claimed:
            release_generation(claim_key)


def _flashcards_generate_inner(req: FlashcardGenerateRequest, data: dict[str, Any]) -> dict[str, Any]:
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
    save_study_data(req.file_hash, data)
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


@app.get("/flashcards/{file_hash}")
def flashcards_get(file_hash: str, language: str = Query(default="auto")):
    ensure_document_exists(file_hash)
    data = load_study_data(file_hash)
    fset = flashcard_set(data, language)
    now = time.time()
    cards = []
    due_count = 0
    for card in fset["flashcards"]:
        state = fset["srs"].get(str(card["id"]), {})
        due_at = state.get("due_at", now)
        is_due = due_at <= now
        if is_due:
            due_count += 1
        cards.append({**card, "due_at": due_at, "is_due": is_due,
                      "interval_days": round(state.get("interval", 0.0), 2),
                      "reps": state.get("reps", 0)})
    return {"ok": True, "cards": cards, "due_count": due_count, "total": len(cards)}


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


@app.post("/flashcards/review")
def flashcards_review(req: FlashcardReviewRequest):
    ensure_document_exists(req.file_hash)
    data = load_study_data(req.file_hash)
    fset = flashcard_set(data, req.language)
    key = str(req.card_id)
    if not any(c["id"] == req.card_id for c in fset["flashcards"]):
        raise_api_error(404, "CARD_NOT_FOUND", "Flashcard niet gevonden.")

    new_state = apply_sm2(fset["srs"].get(key), req.rating, time.time())
    fset["srs"][key] = new_state
    save_study_data(req.file_hash, data)
    return {"ok": True, "card_id": req.card_id, "next_due_at": new_state["due_at"],
            "interval_days": round(new_state["interval"], 2)}


# =========================================================
# NOTITIES (per dia)
# =========================================================
# Spiegelt de bestaande client-only opslag in frontend/js/stats.js
# (localStorage "sc.study.{hash}") zodat notities/markeringen ook overleven
# na een cache-wipe en zichtbaar zijn op een ander apparaat/browser. De
# frontend blijft localStorage gebruiken als snelle/offline-eerste laag en
# synct hiermee op de achtergrond.

def load_notes_data(file_hash: str) -> dict[str, Any]:
    data = cache_store.get_json("notes", file_hash) or {}
    data.setdefault("pages", {})
    return data


def save_notes_data(file_hash: str, data: dict[str, Any]) -> None:
    cache_store.put_json("notes", file_hash, data)


@app.get("/document/{file_hash}/notes")
def get_notes(file_hash: str):
    ensure_document_exists(file_hash)
    return {"ok": True, **load_notes_data(file_hash)}


class NoteUpdateRequest(BaseModel):
    page_index: int
    note: Optional[str] = None
    star: Optional[bool] = None
    unclear: Optional[bool] = None


@app.post("/document/{file_hash}/notes")
def update_notes(file_hash: str, req: NoteUpdateRequest):
    ensure_document_exists(file_hash)
    data = load_notes_data(file_hash)
    key = str(req.page_index)
    entry = data["pages"].setdefault(key, {})
    if req.note is not None:
        if req.note.strip():
            entry["note"] = req.note
        else:
            entry.pop("note", None)
    if req.star is not None:
        entry["star"] = req.star
    if req.unclear is not None:
        entry["unclear"] = req.unclear
    if not entry:
        data["pages"].pop(key, None)
    save_notes_data(file_hash, data)
    return {"ok": True, "page_index": req.page_index, "entry": data["pages"].get(key, {})}


# =========================================================
# WOORDENLIJSTEN (begrippen oefenen, eigen zichtbaar onderdeel)
# =========================================================
# Een woordenlijst is géén document: het is een op zichzelf staande term/
# definitie-lijst met eigen spaced repetition (dezelfde apply_sm2 als
# flashcards). Opslag via cache_store, namespace "wordlists": één index-doc +
# per lijst een eigen doc. Kaart-ids zijn stabiel (next_id-teller) zodat
# bewerken/herordenen de SRS-planning niet corrumpeert.

def load_wordlist_index() -> list[dict[str, Any]]:
    data = cache_store.get_json("wordlists", "index")
    return (data or {}).get("lists", [])


def save_wordlist_index(lists: list[dict[str, Any]]) -> None:
    cache_store.put_json("wordlists", "index", {"lists": lists})


def load_wordlist(list_id: str) -> Optional[dict[str, Any]]:
    return cache_store.get_json("wordlists", list_id)


def save_wordlist(wl: dict[str, Any]) -> None:
    cache_store.put_json("wordlists", wl["id"], wl)
    # index bijwerken (naam/aantal/taal)
    lists = load_wordlist_index()
    entry = {"id": wl["id"], "name": wl["name"], "owner_id": wl.get("owner_id"),
             "created_at": wl.get("created_at"), "language": wl.get("language", "auto"),
             "count": len(wl.get("cards", []))}
    lists = [e for e in lists if e["id"] != wl["id"]]
    lists.append(entry)
    save_wordlist_index(lists)


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


@app.get("/wordlists")
def wordlists_list():
    lists = sorted(load_wordlist_index(), key=lambda e: e.get("created_at") or 0, reverse=True)
    return {"ok": True, "wordlists": lists}


@app.post("/wordlists")
def wordlists_create(req: WordlistCreateRequest, request: Request = None):
    now = time.time()
    list_id = sha256_text(f"{req.name}|{now}")[:12]
    srs: dict[str, Any] = {}
    cards, next_id = _seed_cards([c.model_dump() for c in req.cards], 0, srs, now)
    wl = {"id": list_id, "name": req.name.strip(), "owner_id": request_user_id(request),
          "language": req.language, "created_at": now, "next_id": next_id,
          "cards": cards, "srs": srs}
    with _wordlists_lock:
        save_wordlist(wl)
    return {"ok": True, "wordlist": _wordlist_public(wl)}


@app.get("/wordlists/{list_id}")
def wordlists_get(list_id: str):
    wl = load_wordlist(list_id)
    if not wl:
        raise_api_error(404, "WORDLIST_NOT_FOUND", "Woordenlijst niet gevonden.")
    return {"ok": True, "wordlist": _wordlist_public(wl)}


class WordlistUpdateRequest(BaseModel):
    name: Optional[str] = None
    cards: Optional[list[WordCard]] = None  # volledige nieuwe set (met behoud van SRS waar term ongewijzigd)


@app.patch("/wordlists/{list_id}")
def wordlists_update(list_id: str, req: WordlistUpdateRequest, request: Request = None):
    with _wordlists_lock:
        wl = load_wordlist(list_id)
        if not wl:
            raise_api_error(404, "WORDLIST_NOT_FOUND", "Woordenlijst niet gevonden.")
        check_owner(wl, request)
        if req.name is not None and req.name.strip():
            wl["name"] = req.name.strip()
        if req.cards is not None:
            now = time.time()
            # Kaarten matchen op (term+definitie) om SRS te behouden bij bewerken;
            # echt nieuwe kaarten krijgen een nieuw stabiel id.
            old_by_content = {(c["term"], c["definition"]): c["id"] for c in wl.get("cards", [])}
            old_srs = wl.get("srs", {})
            new_cards = []
            new_srs = {}
            next_id = wl.get("next_id", 0)
            for c in req.cards:
                key = (c.term, c.definition)
                if key in old_by_content:
                    cid = old_by_content[key]
                    new_srs[str(cid)] = old_srs.get(str(cid), {"interval": 0.0, "ease": 2.5, "reps": 0, "due_at": now})
                else:
                    cid = next_id
                    next_id += 1
                    new_srs[str(cid)] = {"interval": 0.0, "ease": 2.5, "reps": 0, "due_at": now}
                new_cards.append({"id": cid, "term": c.term, "definition": c.definition})
            wl["cards"] = new_cards
            wl["srs"] = new_srs
            wl["next_id"] = next_id
        save_wordlist(wl)
    return {"ok": True, "wordlist": _wordlist_public(wl)}


@app.delete("/wordlists/{list_id}")
def wordlists_delete(list_id: str, request: Request = None):
    with _wordlists_lock:
        wl = load_wordlist(list_id)
        if not wl:
            raise_api_error(404, "WORDLIST_NOT_FOUND", "Woordenlijst niet gevonden.")
        check_owner(wl, request)
        cache_store.delete_json("wordlists", list_id)
        save_wordlist_index([e for e in load_wordlist_index() if e["id"] != list_id])
    return {"ok": True, "id": list_id}


class WordlistReviewRequest(BaseModel):
    card_id: int
    rating: Literal["again", "hard", "good", "easy"]


@app.post("/wordlists/{list_id}/review")
def wordlists_review(list_id: str, req: WordlistReviewRequest):
    with _wordlists_lock:
        wl = load_wordlist(list_id)
        if not wl:
            raise_api_error(404, "WORDLIST_NOT_FOUND", "Woordenlijst niet gevonden.")
        if not any(c["id"] == req.card_id for c in wl.get("cards", [])):
            raise_api_error(404, "CARD_NOT_FOUND", "Kaart niet gevonden.")
        new_state = apply_sm2(wl.get("srs", {}).get(str(req.card_id)), req.rating, time.time())
        wl.setdefault("srs", {})[str(req.card_id)] = new_state
        save_wordlist(wl)
    return {"ok": True, "card_id": req.card_id, "next_due_at": new_state["due_at"],
            "interval_days": round(new_state["interval"], 2)}


class VocabPair(BaseModel):
    term: str
    definition: str


class VocabSet(BaseModel):
    pairs: list[VocabPair] = Field(default_factory=list)


class WordlistGenerateRequest(BaseModel):
    file_hash: str
    language: str = "auto"
    max_terms: int = Field(default=30, ge=5, le=100)
    name: Optional[str] = None


@app.post("/wordlists/generate")
def wordlists_generate(req: WordlistGenerateRequest, request: Request = None):
    """AI haalt term/definitie-paren uit een geüpload document (werkt ook op een
    foto van een woordenlijst, want die is ook een 1-pagina-document)."""
    meta = ensure_document_exists(req.file_hash)
    if request is not None:
        uid, plan = quota_gate(request)
        usage.record(uid, plan)

    digest, _, total_pages = build_document_digest(req.file_hash)
    parts: list[Any] = []
    if len(digest) < 400:
        parts.extend(document_image_parts(req.file_hash, total_pages))
    parts.append(text_part(
        f"Materiaal ({total_pages} pagina's):\n\n"
        f"{digest if digest else '(geen tekstlaag; gebruik de afbeeldingen)'}\n\n"
        "Haal hier nu de te leren begrippen uit als term/definitie-paren."
    ))

    system_instruction = f"""You extract a VOCABULARY / TERM LIST from study material for memorization.

RULES
- Produce at most {req.max_terms} pairs. Each pair: term = the word/concept to learn, definition = its short meaning or translation.
- If the material is a bilingual word list (e.g. a language course), keep both columns: term = the foreign word, definition = the translation. Read EVERY row of the list.
- If the material is subject matter (biology, law, ...), pick the key terms and give a crisp definition each.
- Keep definitions short (one line). No cards about layout, agenda or metadata.
- Math in LaTeX ($...$) where needed.
- {language_rule_for(req.language)}

Return only JSON matching the schema."""

    result: VocabSet = generate_structured(
        [Message(role="user", parts=parts)], system_instruction, VocabSet,
    )

    now = time.time()
    srs: dict[str, Any] = {}
    cards, next_id = _seed_cards(
        [{"term": p.term, "definition": p.definition} for p in result.pairs[:req.max_terms]], 0, srs, now)
    if not cards:
        raise_api_error(422, "NO_TERMS_FOUND", "Geen begrippen gevonden in dit materiaal.")

    name = (req.name or "").strip() or f"Woordenlijst — {meta.get('file_name', 'document')}"
    list_id = sha256_text(f"{name}|{now}")[:12]
    wl = {"id": list_id, "name": name, "owner_id": request_user_id(request),
          "language": req.language, "created_at": now, "next_id": next_id,
          "cards": cards, "srs": srs}
    with _wordlists_lock:
        save_wordlist(wl)
    return {"ok": True, "wordlist": _wordlist_public(wl)}


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


@app.post("/ask-region")
def ask_region(req: RegionAskRequest, request: Request):
    ensure_document_exists(req.file_hash)
    uid, plan = quota_gate(request)
    usage.record(uid, plan)
    _, texts = get_document_texts(req.file_hash)
    if req.page_index < 0 or req.page_index >= len(texts):
        raise_api_error(400, "INVALID_PAGE_INDEX", "Ongeldige page_index.")

    full_image = ensure_slide_image(req.file_hash, req.page_index, "ai")
    if not full_image:
        raise_api_error(404, "IMAGE_NOT_AVAILABLE", "Dia-afbeelding niet beschikbaar.")

    from PIL import Image
    with Image.open(full_image) as img:
        w, h = img.size
        left = int(req.box.x * w)
        top = int(req.box.y * h)
        right = min(w, int((req.box.x + req.box.width) * w))
        bottom = min(h, int((req.box.y + req.box.height) * h))
        if right - left < 8 or bottom - top < 8:
            raise_api_error(400, "REGION_TOO_SMALL", "Het geselecteerde gebied is te klein.")
        crop = img.crop((left, top, right, bottom))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        crop_bytes = buf.getvalue()

    question = req.question.strip() or "Leg dit gemarkeerde deel van de dia uit."
    parts = [
        image_part(full_image),
        ai_engine.image_part_bytes(crop_bytes, "image/png"),
        text_part(
            "De eerste afbeelding is de volledige dia (context). De tweede afbeelding is het deel "
            "dat de student heeft gemarkeerd — daar gaat de vraag over.\n\n"
            f"Vraag van de student: {question}"
        ),
    ]
    contents = [Message(role="user", parts=parts)]

    system_instruction = f"""You are an outstanding university tutor. The student selected a specific region of a slide and asks about exactly that part.

RULES
- Answer ONLY about the marked region; use the full slide just as context.
- Be direct and didactic: answer first, then a short explanation.
- Math in LaTeX ($...$ / $$...$$). Be precise about what is visually there; do not guess.
- Keep it compact (usually 2-8 sentences, or short steps for a derivation).
- {language_rule_for(req.language)}

Return pure markdown only."""

    if req.stream:
        return StreamingResponse(
            stream_markdown(contents, system_instruction, None),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    markdown, model_name = generate_markdown(contents, system_instruction)
    return {"ok": True, "markdown": markdown, "model": model_name}


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


@app.post("/tts")
def tts_speak(req: TTSRequest):
    try:
        import edge_tts
    except ImportError:
        raise_api_error(501, "TTS_UNAVAILABLE",
                        "edge-tts is niet geïnstalleerd (pip install edge-tts).")

    code = (req.language or "nl").strip().lower()[:2]
    voice = TTS_VOICES.get(code) or TTS_VOICES["en"]
    text = req.text.strip()

    # Gecachet per (stem, tekst) via cache_store: L1 lokaal + L2 gedeeld, dus
    # dezelfde uitleg voorlezen kost hooguit één keer wat, daarna gratis en
    # instant voor iedereen.
    blob_key = f"{sha256_text(voice + '|' + text)}.mp3"
    cache_file = cache_store.blob_local_path("tts_cache", blob_key)
    if cache_file is None:
        import asyncio
        import tempfile
        tmp = tempfile.NamedTemporaryFile(dir=TTS_DIR, suffix=".part", delete=False)
        tmp.close()
        try:
            asyncio.run(edge_tts.Communicate(text, voice).save(tmp.name))
            data = Path(tmp.name).read_bytes()
            if not data:
                raise RuntimeError("lege audio")
            cache_store.put_blob("tts_cache", blob_key, data, "audio/mpeg")
            cache_file = cache_store.blob_local_path("tts_cache", blob_key)
        except Exception as e:
            logger.warning("TTS mislukt (%s): %s", voice, str(e)[:200])
            raise_api_error(502, "TTS_FAILED",
                            "Voorlezen is momenteel niet beschikbaar.",
                            {"reason": str(e)[:200]})
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    return FileResponse(cache_file, media_type="audio/mpeg",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


# =========================================================
# ZOEKEN OVER ALLE DOCUMENTEN
# =========================================================

@app.get("/search")
def search(q: str = Query(min_length=2), file_hash: Optional[str] = None, limit: int = Query(default=20, le=50)):
    terms = [t for t in re.split(r"\W+", q.lower()) if len(t) >= 2]
    if not terms:
        return {"ok": True, "results": []}

    hashes: list[str]
    if file_hash:
        ensure_document_exists(file_hash)
        hashes = [file_hash]
    else:
        hashes = [p.stem for p in META_DIR.glob("*.json")]

    results = []
    for h in hashes:
        meta = load_meta(h)
        if not meta:
            continue
        try:
            file_type, texts = get_document_texts(h)
        except Exception:
            continue
        label = page_label_for(file_type)
        for i, text in enumerate(texts):
            lower = (text or "").lower()
            if not lower:
                continue
            score = sum(lower.count(t) for t in terms)
            if all(t in lower for t in terms):
                score += 5
            if score <= 0:
                continue
            pos = min((lower.find(t) for t in terms if t in lower), default=0)
            start = max(0, pos - 60)
            snippet = (text[start:start + 200]).replace("\n", " ").strip()
            results.append({
                "file_hash": h,
                "file_name": meta.get("file_name"),
                "page_index": i,
                "label": f"{label} {i + 1}",
                "snippet": ("…" if start > 0 else "") + snippet + "…",
                "score": score,
                "image_url": slide_image_url(h, i),
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return {"ok": True, "results": results[:limit]}


# =========================================================
# VOORTGANG ONTHOUDEN
# =========================================================

class ProgressRequest(BaseModel):
    page_index: int


@app.post("/document/{file_hash}/progress")
def save_progress(file_hash: str, req: ProgressRequest):
    meta = ensure_document_exists(file_hash)
    meta["last_page_index"] = max(0, req.page_index)
    meta["last_opened_at"] = time.time()
    save_meta(file_hash, meta)
    return {"ok": True, "last_page_index": meta["last_page_index"]}


# =========================================================
# MAPPEN (VAKKEN)
# =========================================================
# Een map bundelt documenten van één vak. De map zelf is een klein JSON-bestand;
# het lidmaatschap staat als folder_id in de document-metadata, zodat een
# document verwijderen of verplaatsen geen tweede administratie hoeft bij te
# werken.

FOLDERS_FILE = BASE_DIR / "folders.json"  # oude locatie; alleen nog gelezen voor de eenmalige migratie
_folders_lock = threading.Lock()


def load_folders() -> list[dict[str, Any]]:
    # Via cache_store (namespace "folders", key "index") zodat mappen ook
    # overleven als de server opnieuw wordt uitgerold — voorheen stond dit in
    # één kaal JSON-bestand buiten cache_store om. Eenmalige, zelf-herstellende
    # migratie: bestaat er nog geen cache_store-record, maar wél het oude
    # bestand, migreer die inhoud er dan meteen in.
    data = cache_store.get_json("folders", "index")
    if data is None:
        legacy = load_json(FOLDERS_FILE)
        data = legacy if legacy is not None else {"folders": []}
        cache_store.put_json("folders", "index", data)
    return data.get("folders", [])


def save_folders(folders: list[dict[str, Any]]) -> None:
    cache_store.put_json("folders", "index", {"folders": folders})


def find_folder(folder_id: str) -> Optional[dict[str, Any]]:
    return next((f for f in load_folders() if f["id"] == folder_id), None)


def folder_document_hashes(folder_id: str) -> list[str]:
    """Documenten in een map, oudste upload eerst (colleges in volgorde)."""
    docs = []
    for path in META_DIR.glob("*.json"):
        meta = load_json(path)
        if meta and meta.get("folder_id") == folder_id and meta.get("file_hash"):
            docs.append(meta)
    docs.sort(key=lambda m: m.get("uploaded_at") or 0)
    return [m["file_hash"] for m in docs]


class FolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class DocumentFolderRequest(BaseModel):
    folder_id: Optional[str] = None  # None = uit de map halen


@app.get("/folders")
def folders_list():
    counts: dict[str, int] = {}
    for path in META_DIR.glob("*.json"):
        meta = load_json(path)
        fid = (meta or {}).get("folder_id")
        if fid:
            counts[fid] = counts.get(fid, 0) + 1
    folders = [{**f, "document_count": counts.get(f["id"], 0)} for f in load_folders()]
    folders.sort(key=lambda f: f.get("created_at") or 0)
    return {"ok": True, "folders": folders}


@app.post("/folders")
def folders_create(req: FolderRequest, request: Request = None):
    with _folders_lock:
        folders = load_folders()
        folder = {
            "id": sha256_text(f"{req.name}|{time.time()}")[:12],
            "name": req.name.strip(),
            "created_at": time.time(),
            "owner_id": request_user_id(request),
        }
        folders.append(folder)
        save_folders(folders)
    return {"ok": True, "folder": folder}


@app.patch("/folders/{folder_id}")
def folders_rename(folder_id: str, req: FolderRequest, request: Request = None):
    with _folders_lock:
        folders = load_folders()
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if not folder:
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        check_owner(folder, request)
        folder["name"] = req.name.strip()
        save_folders(folders)
    return {"ok": True, "folder": folder}


@app.delete("/folders/{folder_id}")
def folders_delete(folder_id: str, request: Request = None):
    """Verwijdert alleen de map; de documenten blijven bestaan (zonder map)."""
    with _folders_lock:
        folders = load_folders()
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if not folder:
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        check_owner(folder, request)
        save_folders([f for f in folders if f["id"] != folder_id])
    for path in META_DIR.glob("*.json"):
        meta = load_json(path)
        if meta and meta.get("folder_id") == folder_id:
            meta.pop("folder_id", None)
            save_meta(meta["file_hash"], meta)
    return {"ok": True, "folder_id": folder_id}


@app.post("/document/{file_hash}/folder")
def document_set_folder(file_hash: str, req: DocumentFolderRequest, request: Request = None):
    meta = ensure_document_exists(file_hash)
    check_owner(meta, request)
    if req.folder_id:
        if not find_folder(req.folder_id):
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        meta["folder_id"] = req.folder_id
    else:
        meta.pop("folder_id", None)
    save_meta(file_hash, meta)
    return {"ok": True, "file_hash": file_hash, "folder_id": req.folder_id}


@app.get("/folders/{folder_id}/progress")
def folder_progress(folder_id: str):
    """Voortgangsdashboard voor een heel vak: leunt op de al-cumulatieve
    tentamen-conceptmastery per folder-scope (zie exam_attempt/build_review_plan
    verderop) en telt daarnaast de flashcard-SRS van alle documenten in de map
    op. NB: als iemand een tentamen op los-documentniveau draait binnen deze map
    (i.p.v. met folder_id), valt die data hier buiten — dat gebruikt een eigen
    scope-sleutel (`doc:{hash}`) die niet wordt samengevoegd met de map-scope."""
    folder = find_folder(folder_id)
    if not folder:
        raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")

    hashes = folder_document_hashes(folder_id)

    exam_data = load_exam_data(f"folder:{folder_id}")
    plan = build_review_plan(exam_data)
    concepts_summary = {bucket: len(items) for bucket, items in plan.items()}
    weak_pool = plan["due_now"] + plan["tomorrow"] + plan["this_week"]
    weak_pool.sort(key=lambda c: c["mastery"])
    weak_concepts = weak_pool[:8]

    # mastery per document, afgeleid uit dezelfde cumulatieve concepten (elk
    # concept draagt zijn eigen file_hash mee sinds exam_attempt).
    per_doc_stats: dict[str, dict[str, int]] = {}
    for c in exam_data["concepts"].values():
        h = c.get("file_hash")
        if not h:
            continue
        s = per_doc_stats.setdefault(h, {"right": 0, "wrong": 0})
        s["right"] += c.get("right", 0)
        s["wrong"] += c.get("wrong", 0)

    now = time.time()
    fc_total = fc_due = fc_mastered = 0
    per_document = []
    for h in hashes:
        meta = load_meta(h) or {}
        study_data = load_study_data(h)
        for fset in study_data.get("sets", {}).values():
            for card in fset.get("flashcards", []):
                fc_total += 1
                state = fset.get("srs", {}).get(str(card["id"]), {})
                if state.get("due_at", now) <= now:
                    fc_due += 1
                if state.get("interval", 0) >= 14:
                    fc_mastered += 1

        stats = per_doc_stats.get(h)
        mastery_pct = None
        if stats and (stats["right"] + stats["wrong"]) > 0:
            mastery_pct = round(100 * stats["right"] / (stats["right"] + stats["wrong"]))
        per_document.append({
            "file_hash": h,
            "file_name": meta.get("file_name", "document"),
            "mastery_pct": mastery_pct,
        })

    return {
        "ok": True,
        "folder": folder,
        "concepts": concepts_summary,
        "weak_concepts": weak_concepts,
        "flashcards": {"total": fc_total, "due_now": fc_due, "mastered": fc_mastered},
        "per_document": per_document,
        "attempts": exam_data["attempts"][-10:],
    }


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


def exam_scope(req_hash: Optional[str], req_folder: Optional[str]) -> tuple[str, list[str], str]:
    """Geeft (scope_id, document-hashes, weergavenaam) voor een tentamen-scope."""
    if req_folder:
        folder = find_folder(req_folder)
        if not folder:
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        hashes = folder_document_hashes(req_folder)
        if not hashes:
            raise_api_error(400, "FOLDER_EMPTY", "Deze map bevat nog geen documenten.")
        return f"folder:{req_folder}", hashes, folder["name"]
    if req_hash:
        meta = ensure_document_exists(req_hash)
        return f"doc:{req_hash}", [req_hash], str(meta.get("file_name", "document"))
    raise_api_error(400, "MISSING_SCOPE", "Geef een file_hash of folder_id op.")


def build_exam_system(language: str, count: int) -> str:
    return f"""You are a strict but fair university examiner. You create a realistic PRACTICE EXAM from lecture material — the same level, style and depth as a real university exam on this material.

RULES
- Create exactly {count} questions at genuine exam level. Distribution: roughly 30% understanding, 50% APPLICATION (solve, calculate, predict, interpret a scenario) and 20% analysis/combining multiple concepts. Avoid pure recall of definitions unless the definition itself is exam-critical.
- If the material is quantitative, include calculation questions with concrete numbers; if conceptual, use realistic scenarios and case-based questions ("what happens if...", "which conclusion follows...").
- Mix: about 40% multiple-choice (exactly 4 options, distractors are plausible misconceptions or typical calculation errors) and 60% open questions.
- For open questions: model_answer is a complete worked answer (steps included, LaTeX for math). For MC: set correct_option (0-based).
- explanation: 1-3 sentences on why the answer is correct (and for MC why the tempting distractor is wrong).
- concept: a short label (2-5 words) naming the concept/skill being tested, e.g. "Nyquist-criterium toepassen". Reuse the exact same label when two questions test the same concept.
- page_index: the 0-based page the question is mainly about (input page numbers are 1-based: subtract 1). If the material contains multiple documents, also set doc_index to the 1-based document number.
- All math in LaTeX ($...$ / $$...$$), also inside options and answers.
- Base every question strictly on the material; never invent content that is not there.
- {language_rule_for(language)}

Return only JSON matching the schema."""


@app.post("/exam/generate")
def exam_generate(req: ExamGenerateRequest, request: Request = None):
    scope_id, hashes, scope_name = exam_scope(req.file_hash, req.folder_id)

    cache_key = sha256_text("|".join([
        "exam", PROMPT_VERSION, *hashes, str(req.count), req.language.strip().lower(),
    ]))

    def cached_response() -> Optional[dict[str, Any]]:
        cached = cache_store.get_json("ai_cache", cache_key)
        if cached and cached.get("questions"):
            return {"ok": True, "questions": cached["questions"], "scope": scope_id,
                    "scope_name": scope_name, "cached": True}
        return None

    if not req.force_refresh:
        hit = cached_response()
        if hit:
            return hit

    if request is not None:
        uid, plan = quota_gate(request)
        usage.record(uid, plan)

    claim_key = f"exam|{cache_key}"
    claimed = False
    try:
        event, claimed = claim_generation(claim_key)
        if not claimed:
            event.wait(timeout=300)
            hit = cached_response()
            if hit:
                return hit
            event, claimed = claim_generation(claim_key)

        # Materiaal: één digest per document, met een totaalbudget zodat een map
        # met 10 colleges niet 10x zoveel tokens kost als één document.
        total_budget = 30000
        per_doc = max(4000, total_budget // len(hashes))
        parts: list[Any] = []
        blocks = []
        for i, h in enumerate(hashes, start=1):
            digest, _, total_pages = build_document_digest(h, max_total=per_doc)
            meta = load_meta(h) or {}
            name = meta.get("file_name", f"document {i}")
            if len(hashes) > 1:
                blocks.append(f"=== Document {i}: {name} ({total_pages} pagina's) ===\n\n{digest}")
            else:
                blocks.append(digest)
                if len(digest) < 400:
                    parts.extend(document_image_parts(h, total_pages))
        material = "\n\n".join(blocks)[:total_budget]

        parts.append(text_part(
            f"Tentamenstof: {scope_name}"
            + (f" ({len(hashes)} documenten)" if len(hashes) > 1 else "")
            + f".\n\n{material if material.strip() else '(geen tekstlaag; gebruik de afbeeldingen)'}\n\n"
            f"Stel hier nu een oefententamen van {req.count} vragen over samen."
        ))

        result: ExamSet = generate_structured(
            [Message(role="user", parts=parts)],
            build_exam_system(req.language, req.count),
            ExamSet,
        )

        questions = []
        for i, q in enumerate(result.questions[:req.count]):
            item = q.model_dump()
            item["id"] = i
            # doc_index (1-gebaseerd) terugvertalen naar de echte file_hash,
            # zodat de frontend direct naar de juiste dia kan springen.
            di = item.pop("doc_index", None)
            if len(hashes) == 1:
                item["file_hash"] = hashes[0]
            elif di and 1 <= di <= len(hashes):
                item["file_hash"] = hashes[di - 1]
            else:
                item["file_hash"] = None
                item["page_index"] = None  # zonder document is een pagina-index betekenisloos
            questions.append(item)

        cache_store.put_json("ai_cache", cache_key, {"questions": questions, "created_at": time.time()})
        return {"ok": True, "questions": questions, "scope": scope_id,
                "scope_name": scope_name, "cached": False}
    finally:
        if claimed:
            release_generation(claim_key)


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
        item = {
            "concept": c.get("label") or key,
            "file_hash": c.get("file_hash"),
            "page_index": c.get("page_index"),
            "right": c.get("right", 0),
            "wrong": c.get("wrong", 0),
            "mastery": round(mastery, 2),
            "due_at": c.get("due_at", now),
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


class ExamAttemptRequest(BaseModel):
    file_hash: Optional[str] = None
    folder_id: Optional[str] = None
    results: list[ExamResultItem]


@app.post("/exam/attempt")
def exam_attempt(req: ExamAttemptRequest):
    scope_id, _, _ = exam_scope(req.file_hash, req.folder_id)
    if not req.results:
        raise_api_error(400, "EMPTY_RESULTS", "Geen resultaten ontvangen.")

    data = load_exam_data(scope_id)
    now = time.time()
    day = 86400.0

    for r in req.results:
        key = (r.concept or "").strip().lower() or (
            f"p{r.page_index}" if r.page_index is not None else "algemeen")
        c = data["concepts"].get(key, {
            "label": r.concept.strip() or None, "file_hash": r.file_hash,
            "page_index": r.page_index, "right": 0, "wrong": 0,
            "interval": 0.0, "due_at": now,
        })
        if r.concept.strip():
            c["label"] = r.concept.strip()
        if r.file_hash:
            c["file_hash"] = r.file_hash
        if r.page_index is not None:
            c["page_index"] = r.page_index

        # Licht SRS-schema per concept: fout => morgen opnieuw, goed => interval
        # groeit. "Half goed" (score 40-70) telt als fout maar iets milder.
        interval = float(c.get("interval", 0.0))
        if r.correct:
            c["right"] = c.get("right", 0) + 1
            interval = 3.0 if interval <= 0 else min(30.0, interval * 2.2)
        else:
            c["wrong"] = c.get("wrong", 0) + 1
            interval = 1.5 if r.score >= 40 else 1.0
        c["interval"] = interval
        c["due_at"] = now + interval * day
        data["concepts"][key] = c

    avg = round(sum(r.score for r in req.results) / len(req.results))
    data["attempts"].append({"at": now, "score": avg, "count": len(req.results)})
    data["attempts"] = data["attempts"][-50:]
    save_exam_data(scope_id, data)

    return {"ok": True, "score": avg, "attempts": data["attempts"],
            "plan": build_review_plan(data)}


@app.get("/exam/plan")
def exam_plan(file_hash: Optional[str] = None, folder_id: Optional[str] = None):
    scope_id, _, scope_name = exam_scope(file_hash, folder_id)
    data = load_exam_data(scope_id)
    return {"ok": True, "scope": scope_id, "scope_name": scope_name,
            "attempts": data["attempts"], "plan": build_review_plan(data)}


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
