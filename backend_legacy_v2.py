import os
import re
import io
import json
import time
import shutil
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Optional, Literal, Any

import fitz  # PyMuPDF
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pptx import Presentation

from google import genai
from google.genai import types


# =========================================================
# CONFIG
# =========================================================

BASE_PATH = Path(__file__).resolve().parent
ENV_PATH = BASE_PATH / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY ontbreekt.")

BASE_DIR = Path(os.getenv("BACKEND_CACHE_DIR", "backend_cache_v2"))
UPLOAD_DIR = BASE_DIR / "uploads"
IMAGE_DIR = BASE_DIR / "images"
PDF_DIR = BASE_DIR / "converted_pdf"
TEXT_CACHE_DIR = BASE_DIR / "text_cache"
META_DIR = BASE_DIR / "meta"
AI_CACHE_DIR = BASE_DIR / "ai_cache"

ENABLE_RENDER_PPTX = os.getenv("ENABLE_RENDER_PPTX", "true").lower() == "true"
ENABLE_RESPONSE_CACHE = os.getenv("ENABLE_RESPONSE_CACHE", "true").lower() == "true"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

PDF_RENDER_SCALE_NORMAL = float(os.getenv("PDF_RENDER_SCALE_NORMAL", "1.35"))
PDF_RENDER_SCALE_HIGH = float(os.getenv("PDF_RENDER_SCALE_HIGH", "2.0"))
MAX_TEXT_LEN = int(os.getenv("MAX_TEXT_LEN", "2200"))

GEMINI_INSPECT_MODEL = os.getenv("GEMINI_INSPECT_MODEL", "gemini-2.5-flash-lite")
GEMINI_EXPLAIN_MODEL = os.getenv("GEMINI_EXPLAIN_MODEL", "gemini-2.5-flash-lite")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")

for folder in [UPLOAD_DIR, IMAGE_DIR, PDF_DIR, TEXT_CACHE_DIR, META_DIR, AI_CACHE_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("studycopilot-backend-v2")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# =========================================================
# APP
# =========================================================

app = FastAPI(title="StudyCopilot Backend v2", version="2.0.0")

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["*"],
    allow_credentials=os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true",
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# TYPES
# =========================================================

ModeType = Literal["explain", "simple", "study", "questions", "ask", "derive"]
AudienceType = Literal["beginner", "intermediate", "advanced"]
DomainType = Literal["general", "control_systems", "math", "physics", "electronics"]
ConfidenceType = Literal["high", "medium", "low"]
SlideType = Literal[
    "theory",
    "definition",
    "formula",
    "derivation",
    "graph",
    "diagram",
    "summary",
    "exercise",
    "table",
    "multiple_choice",
    "mixed",
]
DocStatus = Literal["uploaded", "processing", "ready", "partial", "failed"]


# =========================================================
# REQUEST / RESPONSE MODELS
# =========================================================

class ApiError(BaseModel):
    ok: bool = False
    error_code: str
    message: str
    details: Optional[dict[str, Any]] = None


class UploadPageInfo(BaseModel):
    index: int
    label: str
    image_ready: bool = False
    local_image_url: Optional[str] = None
    text_preview: str = ""


class UploadResponse(BaseModel):
    ok: bool = True
    file_hash: str
    file_name: str
    file_type: Literal["pdf", "pptx"]
    total_pages: int
    status: DocStatus
    pages: list[UploadPageInfo]


class ExplainRequest(BaseModel):
    file_hash: str
    page_index: int
    language: str = "Nederlands"
    mode: ModeType = "explain"
    question: Optional[str] = None
    audience_level: AudienceType = "intermediate"
    domain: DomainType = "general"
    detail_level: Literal["short", "normal", "long"] = "normal"
    force_refresh: bool = False


class AnalyzeRequest(BaseModel):
    file_hash: str
    page_index: int
    language: str = "Nederlands"
    domain: DomainType = "general"
    force_refresh: bool = False


class SlideAnalysis(BaseModel):
    slide_type: SlideType = "mixed"
    topic: str = ""
    learning_goal: str = ""
    has_formula: bool = False
    has_graph: bool = False
    has_multiple_choice: bool = False
    has_handwriting: bool = False
    graph_reading_required: bool = False
    confidence: ConfidenceType = "medium"
    visible_items: list[str] = Field(default_factory=list)
    important_concepts: list[str] = Field(default_factory=list)
    unclear_areas: list[str] = Field(default_factory=list)


class ExplainStep(BaseModel):
    title: str
    content: str


class ExplainFastOutput(BaseModel):
    mode: ModeType
    short_answer: str = ""
    key_point: str = ""
    steps: list[ExplainStep] = Field(default_factory=list)
    why: str = ""
    calculation: str = ""
    graph_takeaway: str = ""
    exam_tip: str = ""
    correct_answer: str = ""
    warnings: list[str] = Field(default_factory=list)
    content_confidence: ConfidenceType = "medium"
    math_confidence: ConfidenceType = "medium"


class DeriveStep(BaseModel):
    title: str
    expression: str
    why: str


class DeriveFastOutput(BaseModel):
    mode: Literal["derive"] = "derive"
    short_answer: str = ""
    steps: list[DeriveStep] = Field(default_factory=list)
    conclusion: str = ""
    warnings: list[str] = Field(default_factory=list)
    content_confidence: ConfidenceType = "medium"
    math_confidence: ConfidenceType = "medium"

class MathExplainStep(BaseModel):
    title: str
    expression: str
    explanation: str


class MathExplainOutput(BaseModel):
    mode: ModeType
    short_answer: str = ""
    key_point: str = ""
    steps: list[MathExplainStep] = Field(default_factory=list)
    graph_takeaway: str = ""
    exam_tip: str = ""
    correct_answer: str = ""
    warnings: list[str] = Field(default_factory=list)
    content_confidence: ConfidenceType = "medium"
    math_confidence: ConfidenceType = "medium"


# =========================================================
# HASH / CACHE / META HELPERS
# =========================================================

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def natural_sort_key(filename: str):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", filename)]


def truncate_text(text: str, max_len: int = MAX_TEXT_LEN) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].strip()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def meta_path(file_hash: str) -> Path:
    return META_DIR / f"{file_hash}.json"


def text_cache_path(file_hash: str) -> Path:
    return TEXT_CACHE_DIR / f"{file_hash}.json"


def ai_cache_path(key: str) -> Path:
    return AI_CACHE_DIR / f"{key}.json"


def save_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_meta(file_hash: str) -> Optional[dict[str, Any]]:
    return load_json(meta_path(file_hash))


def save_meta(file_hash: str, payload: dict[str, Any]) -> None:
    save_json(meta_path(file_hash), payload)


def load_text_cache(file_hash: str) -> Optional[dict[str, Any]]:
    return load_json(text_cache_path(file_hash))


def save_text_cache(file_hash: str, file_type: str, texts: list[str]) -> None:
    save_json(
        text_cache_path(file_hash),
        {
            "file_type": file_type,
            "texts": texts,
        },
    )


def load_ai_cache(key: str) -> Optional[dict[str, Any]]:
    if not ENABLE_RESPONSE_CACHE:
        return None
    return load_json(ai_cache_path(key))


def save_ai_cache(key: str, payload: dict[str, Any]) -> None:
    if not ENABLE_RESPONSE_CACHE:
        return
    save_json(ai_cache_path(key), payload)


def make_ai_cache_key(
    *,
    kind: str,
    file_hash: str,
    page_index: int,
    language: str,
    mode: str,
    audience_level: str,
    domain: str,
    detail_level: str,
    question: Optional[str],
    text: str,
    image_signature: str,
) -> str:
    raw = "|".join([
        kind,
        file_hash,
        str(page_index),
        language,
        mode,
        audience_level,
        domain,
        detail_level,
        question or "",
        sha256_text(text),
        image_signature,
    ])
    return sha256_text(raw)


# =========================================================
# FILE HELPERS
# =========================================================

def save_file_once(file_bytes: bytes, suffix: str) -> tuple[str, Path]:
    file_hash = sha256_bytes(file_bytes)
    path = UPLOAD_DIR / f"{file_hash}{suffix}"
    if not path.exists():
        path.write_bytes(file_bytes)
    return file_hash, path.resolve()


def find_libreoffice_executable() -> Optional[str]:
    candidates = [
        shutil.which("libreoffice"),
        shutil.which("soffice"),
        "/usr/bin/libreoffice",
        "/usr/bin/soffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def get_document_info(file_hash: str) -> tuple[str, Path]:
    pdf_path = UPLOAD_DIR / f"{file_hash}.pdf"
    pptx_path = UPLOAD_DIR / f"{file_hash}.pptx"

    if pdf_path.exists():
        return "pdf", pdf_path
    if pptx_path.exists():
        return "pptx", pptx_path

    raise HTTPException(status_code=404, detail="Bestand niet gevonden.")


def extract_pdf_texts(path: Path) -> list[str]:
    try:
        doc = fitz.open(str(path))
        texts: list[str] = []
        for i in range(len(doc)):
            page = doc.load_page(i)
            texts.append((page.get_text("text") or "").strip())
        doc.close()
        return texts
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"PDF tekst uitlezen mislukt: {str(e)}")


def extract_shape_text(shape) -> str:
    try:
        if hasattr(shape, "text"):
            return (getattr(shape, "text", "") or "").strip()
    except Exception:
        pass

    parts: list[str] = []

    try:
        if hasattr(shape, "has_table") and shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    cell_text = (cell.text or "").strip()
                    if cell_text:
                        parts.append(cell_text)
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

    return "\n".join([p for p in parts if p]).strip()


def extract_pptx_texts(path: Path) -> list[str]:
    try:
        prs = Presentation(str(path))
        texts: list[str] = []

        for slide in prs.slides:
            parts: list[str] = []

            for shape in slide.shapes:
                try:
                    text = extract_shape_text(shape)
                    if text:
                        parts.append(text)
                except Exception:
                    continue

            texts.append("\n".join(parts).strip())

        return texts
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"PPTX tekst uitlezen mislukt: {str(e)}")


def convert_pptx_to_pdf(path: Path, file_hash: str) -> Optional[Path]:
    libreoffice = find_libreoffice_executable()
    if not libreoffice:
        logger.error("LibreOffice niet gevonden.")
        return None

    out_dir = PDF_DIR / file_hash
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{file_hash}.pdf"

    if out_pdf.exists():
        logger.info("Bestaande PPTX->PDF gevonden: %s", out_pdf)
        return out_pdf

    try:
        logger.info("Start PPTX->PDF conversie | input=%s | output_dir=%s", path, out_dir)

        result = subprocess.run(
            [
                libreoffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(path),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )

        stdout_text = result.stdout.decode("utf-8", errors="ignore")
        stderr_text = result.stderr.decode("utf-8", errors="ignore")

        logger.info("LibreOffice stdout: %s", stdout_text)
        logger.info("LibreOffice stderr: %s", stderr_text)

        candidates = list(out_dir.glob("*.pdf"))
        if not candidates:
            logger.error("Geen PDF output gevonden na conversie. out_dir=%s", out_dir)
            return None

        candidate = candidates[0]
        if candidate != out_pdf:
            candidate.replace(out_pdf)

        logger.info("PPTX->PDF conversie gelukt: %s", out_pdf)
        return out_pdf

    except subprocess.TimeoutExpired:
        logger.exception("PPTX->PDF conversie timeout voor %s", path)
        return None
    except subprocess.CalledProcessError as e:
        logger.exception("LibreOffice gaf foutcode terug voor %s", path)
        try:
            logger.error("stdout: %s", e.stdout.decode("utf-8", errors="ignore") if e.stdout else "")
            logger.error("stderr: %s", e.stderr.decode("utf-8", errors="ignore") if e.stderr else "")
        except Exception:
            pass
        return None
    except Exception as e:
        logger.exception("PPTX->PDF conversie mislukt: %s", e)
        return None


def render_pdf_page(
    pdf_path: Path,
    out_path: Path,
    page_index: int,
    scale: float,
) -> bool:
    try:
        ensure_parent(out_path)
        doc = fitz.open(str(pdf_path))
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pix.save(str(out_path))
        doc.close()
        return True
    except Exception:
        logger.exception("Render page mislukt: %s page=%s", pdf_path.name, page_index)
        return False


def get_pdf_for_document(file_hash: str) -> tuple[str, Path]:
    file_type, original_path = get_document_info(file_hash)

    if file_type == "pdf":
        return file_type, original_path

    pdf_path = convert_pptx_to_pdf(original_path, file_hash)
    if not pdf_path:
        raise HTTPException(
            status_code=500,
            detail="PPTX upload gelukt, maar conversie naar PDF is mislukt.",
        )
    return file_type, pdf_path


def get_document_texts(file_hash: str) -> tuple[str, list[str]]:
    cached = load_text_cache(file_hash)
    if cached and isinstance(cached, dict):
        file_type = str(cached.get("file_type", "")).strip()
        texts = cached.get("texts", [])
        if file_type in ["pdf", "pptx"] and isinstance(texts, list):
            return file_type, [str(x) for x in texts]

    file_type, path = get_document_info(file_hash)
    texts = extract_pdf_texts(path) if file_type == "pdf" else extract_pptx_texts(path)
    save_text_cache(file_hash, file_type, texts)
    return file_type, texts


def get_image_dir(file_hash: str, resolution: Literal["normal", "high"]) -> Path:
    return IMAGE_DIR / file_hash / resolution


def get_page_image_path(
    file_hash: str,
    page_index: int,
    resolution: Literal["normal", "high"] = "normal",
) -> Path:
    return get_image_dir(file_hash, resolution) / f"page_{page_index}.png"


def ensure_slide_image(
    file_hash: str,
    page_index: int,
    resolution: Literal["normal", "high"] = "normal",
) -> Optional[Path]:
    target = get_page_image_path(file_hash, page_index, resolution)
    if target.exists():
        return target

    _, pdf_path = get_pdf_for_document(file_hash)
    scale = PDF_RENDER_SCALE_HIGH if resolution == "high" else PDF_RENDER_SCALE_NORMAL

    ok = render_pdf_page(
        pdf_path=pdf_path,
        out_path=target,
        page_index=page_index,
        scale=scale,
    )
    return target if ok and target.exists() else None


def render_preview_pages_in_background(file_hash: str, max_pages: int = 3) -> None:
    try:
        meta = load_meta(file_hash) or {}
        total_pages = int(meta.get("total_pages", 0))
        count = min(max_pages, total_pages)

        for i in range(count):
            ensure_slide_image(file_hash, i, resolution="normal")

        meta["status"] = "ready"
        save_meta(file_hash, meta)
    except Exception:
        logger.exception("Preview rendering mislukt voor %s", file_hash)
        meta = load_meta(file_hash) or {}
        meta["status"] = "partial"
        save_meta(file_hash, meta)


def make_image_signature(image_path: Optional[Path]) -> str:
    if not image_path or not image_path.exists():
        return "no-image"
    try:
        return sha256_file(image_path)
    except Exception:
        return "image-unavailable"


def local_slide_image_url(file_hash: str, page_index: int, resolution: str = "normal") -> str:
    return f"/slide-image/{file_hash}/{page_index}?resolution={resolution}"


# =========================================================
# DOCUMENT STATE HELPERS
# =========================================================

def init_document_meta(
    *,
    file_hash: str,
    file_name: str,
    file_type: str,
    total_pages: int,
) -> dict[str, Any]:
    payload = {
        "file_hash": file_hash,
        "file_name": file_name,
        "file_type": file_type,
        "total_pages": total_pages,
        "status": "uploaded",
        "uploaded_at": time.time(),
    }
    save_meta(file_hash, payload)
    return payload


def set_document_status(file_hash: str, status: DocStatus) -> None:
    meta = load_meta(file_hash) or {}
    meta["status"] = status
    save_meta(file_hash, meta)


# =========================================================
# BASIC ROUTES
# =========================================================

@app.get("/")
def root():
    return {
        "ok": True,
        "service": "StudyCopilot Backend v2",
        "version": "2.0.0",
        "features": {
            "lazy_rendering": True,
            "pptx_upload": True,
            "pdf_upload": True,
            "fast_ai_output": True,
            "single_pass_explain": True,
            "response_cache": ENABLE_RESPONSE_CACHE,
        },
    }


@app.get("/health/deep")
def deep_health():
    return {
        "ok": True,
        "service": "StudyCopilot Backend v2",
        "env": {
            "gemini_configured": bool(GEMINI_API_KEY),
            "libreoffice_found": bool(find_libreoffice_executable()),
            "render_pptx_enabled": ENABLE_RENDER_PPTX,
        },
        "paths": {
            "base_dir": str(BASE_DIR.resolve()),
            "upload_dir": str(UPLOAD_DIR.resolve()),
            "image_dir": str(IMAGE_DIR.resolve()),
            "pdf_dir": str(PDF_DIR.resolve()),
        },
    }


@app.get("/document/{file_hash}")
def get_document(file_hash: str):
    meta = load_meta(file_hash)
    if not meta:
        raise HTTPException(status_code=404, detail="Document niet gevonden.")

    file_type, texts = get_document_texts(file_hash)
    total_pages = len(texts)

    pages = []
    label = "pagina" if file_type == "pdf" else "dia"

    for i in range(total_pages):
        normal_img = get_page_image_path(file_hash, i, "normal")
        pages.append(
            UploadPageInfo(
                index=i,
                label=f"{label} {i + 1}",
                image_ready=normal_img.exists(),
                local_image_url=local_slide_image_url(file_hash, i),
                text_preview=(texts[i] or "")[:180],
            ).model_dump()
        )

    return {
        "ok": True,
        "file_hash": file_hash,
        "file_name": meta.get("file_name"),
        "file_type": file_type,
        "total_pages": total_pages,
        "status": meta.get("status", "uploaded"),
        "pages": pages,
    }


# =========================================================
# IMAGE ROUTE
# =========================================================

@app.get("/slide-image/{file_hash}/{page_index}")
def slide_image(
    file_hash: str,
    page_index: int,
    resolution: Literal["normal", "high"] = Query(default="normal"),
):
    _, texts = get_document_texts(file_hash)

    if page_index < 0 or page_index >= len(texts):
        raise HTTPException(status_code=400, detail="Ongeldige page_index.")

    image_path = ensure_slide_image(file_hash, page_index, resolution=resolution)
    if not image_path or not image_path.exists():
        raise HTTPException(status_code=404, detail="Slide-afbeelding niet gevonden.")

    return FileResponse(image_path, media_type="image/png")


# =========================================================
# UPLOAD ROUTE
# =========================================================

@app.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    start = time.perf_counter()

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in [".pdf", ".pptx"]:
        raise HTTPException(status_code=400, detail="Alleen .pdf en .pptx worden ondersteund.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Leeg bestand ontvangen.")

    file_hash, saved_path = save_file_once(file_bytes, suffix)

    if suffix == ".pdf":
        file_type = "pdf"
        try:
            texts = extract_pdf_texts(saved_path)
        except Exception as e:
            raise_api_error(
                500,
                "PDF_TEXT_EXTRACTION_FAILED",
                "De tekst uit de PDF kon niet worden gelezen.",
                {"reason": str(e)},
            )

    else:
        file_type = "pptx"

        try:
            texts = extract_pptx_texts(saved_path)
        except Exception as e:
            raise_api_error(
                500,
                "PPTX_TEXT_EXTRACTION_FAILED",
                "De tekst uit de PowerPoint kon niet worden gelezen.",
                {"reason": str(e)},
            )

        if ENABLE_RENDER_PPTX:
            set_document_status(file_hash, "processing")

            try:
                detected_type, pdf_path = get_pdf_for_document(file_hash)
                if detected_type != "pptx":
                    raise_api_error(
                        500,
                        "PPTX_TYPE_MISMATCH",
                        "Intern typeprobleem tijdens PPTX-verwerking.",
                        {"detected_type": detected_type},
                    )
            except HTTPException:
                raise
            except Exception as e:
                raise_api_error(
                    500,
                    "PPTX_TO_PDF_FAILED",
                    "De PowerPoint kon niet naar PDF worden omgezet.",
                    {"reason": str(e)},
                )

            if not pdf_path or not pdf_path.exists():
                raise_api_error(
                    500,
                    "PPTX_TO_PDF_FAILED",
                    "De PowerPoint kon niet naar PDF worden omgezet.",
                    {"file_hash": file_hash},
                )

            first_image = ensure_slide_image(file_hash, 0, resolution="normal")
            if not first_image or not first_image.exists():
                raise_api_error(
                    500,
                    "PPTX_RENDER_FAILED",
                    "De slides van deze PowerPoint konden niet als afbeeldingen worden gerenderd.",
                    {"file_hash": file_hash},
                )

    total_pages = len(texts)
    init_document_meta(
        file_hash=file_hash,
        file_name=file.filename or f"{file_hash}{suffix}",
        file_type=file_type,
        total_pages=total_pages,
    )
    save_text_cache(file_hash, file_type, texts)

    pages = []
    label = "pagina" if file_type == "pdf" else "dia"
    for i in range(total_pages):
        image_exists = get_page_image_path(file_hash, i, "normal").exists()
        pages.append(
            UploadPageInfo(
                index=i,
                label=f"{label} {i + 1}",
                image_ready=image_exists,
                local_image_url=local_slide_image_url(file_hash, i),
                text_preview=(texts[i] or "")[:180],
            )
        )

    if file_type == "pdf":
        set_document_status(file_hash, "processing")
        if background_tasks:
            background_tasks.add_task(render_preview_pages_in_background, file_hash, 3)
        status_value: DocStatus = "processing"
    else:
        set_document_status(file_hash, "ready")
        status_value = "ready"

    elapsed = round(time.perf_counter() - start, 2)
    logger.info("Upload klaar %s in %ss", file_hash, elapsed)

    return UploadResponse(
        file_hash=file_hash,
        file_name=file.filename or f"{file_hash}{suffix}",
        file_type=file_type,
        total_pages=total_pages,
        status=status_value,
        pages=pages,
    )

# =========================================================
# AI HELPERS
# =========================================================

def image_part_from_path(image_path: Optional[Path]) -> Optional[types.Part]:
    if not image_path or not image_path.exists():
        return None
    return types.Part.from_bytes(
        data=image_path.read_bytes(),
        mime_type="image/png",
    )


def gemini_generate_structured(
    *,
    model_name: str,
    prompt: str,
    response_schema: type[BaseModel],
    image_path: Optional[Path] = None,
    temperature: float = 0.15,
) -> BaseModel:
    try:
        parts: list[Any] = []
        image_part = image_part_from_path(image_path)
        if image_part:
            parts.append(image_part)
        parts.append(prompt)

        response = gemini_client.models.generate_content(
            model=model_name,
            contents=parts,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": response_schema.model_json_schema(),
                "temperature": temperature,
            },
        )

        text = response.text or "{}"
        return response_schema.model_validate_json(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini fout: {str(e)}")


def choose_model_for_fast_explain(
    *,
    mode: ModeType,
    analysis: Optional[SlideAnalysis],
    audience_level: AudienceType,
) -> str:
    if mode == "derive":
        return GEMINI_FALLBACK_MODEL

    if analysis:
        if analysis.confidence == "low":
            return GEMINI_FALLBACK_MODEL
        if analysis.has_handwriting:
            return GEMINI_FALLBACK_MODEL
        if analysis.has_graph and analysis.graph_reading_required:
            return GEMINI_FALLBACK_MODEL
        if analysis.has_multiple_choice:
            return GEMINI_FALLBACK_MODEL

    if audience_level == "advanced":
        return GEMINI_EXPLAIN_MODEL

    return GEMINI_EXPLAIN_MODEL


# =========================================================
# ANALYSIS CONTEXT
# =========================================================

def clean_text(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def remove_latex_markers(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\\(", "").replace("\\)", "")
    text = text.replace("\\[", "").replace("\\]", "")
    text = text.replace("$$", "").replace("$", "")
    return text.strip()

def clean_math_expression(expr: str) -> str:
    expr = (expr or "").strip()
    if not expr:
        return ""

    # Basis normalisatie
    expr = expr.replace("−", "-")
    expr = expr.replace("–", "-")
    expr = expr.replace("×", r"\cdot ")
    expr = expr.replace("·", r"\cdot ")
    expr = expr.replace("≤", r"\le ")
    expr = expr.replace("≥", r"\ge ")
    expr = expr.replace("≠", r"\neq ")
    expr = expr.replace("→", r"\rightarrow ")
    expr = expr.replace("∞", r"\infty ")

    # Bekende symbolen / subscripts
    expr = expr.replace("H_CL", r"H_{CL}")
    expr = expr.replace("Hcl", r"H_{CL}")
    expr = expr.replace("HCL", r"H_{CL}")
    expr = expr.replace("Y_ref", r"Y_{ref}")
    expr = expr.replace("s_p", r"s_p")
    expr = expr.replace("omega_n", r"\omega_n")
    expr = expr.replace("ω_n", r"\omega_n")
    expr = expr.replace("zeta", r"\zeta")
    expr = expr.replace("ζ", r"\zeta")

    # Veelgebruikte breuken expliciet omzetten
    expr = re.sub(
        r"\(\s*2\s*-\s*K\s*\)\s*/\s*\(\s*1\s*\+\s*K\s*\)",
        r"\\frac{2-K}{1+K}",
        expr,
    )
    expr = re.sub(
        r"\(\s*s\s*\+\s*1\s*\)\s*/\s*\(\s*s\s*-\s*2\s*\)",
        r"\\frac{s+1}{s-2}",
        expr,
    )
    expr = re.sub(
        r"R\(s\)P\(s\)\s*/\s*\(\s*1\s*\+\s*R\(s\)P\(s\)S\(s\)\s*\)",
        r"\\frac{R(s)P(s)}{1 + R(s)P(s)S(s)}",
        expr,
    )
    expr = re.sub(
        r"K\s*\(\s*s\s*\+\s*1\s*\)\s*/\s*\(\s*\(\s*1\s*\+\s*K\s*\)\s*s\s*\+\s*\(\s*K\s*-\s*2\s*\)\s*\)",
        r"\\frac{K(s+1)}{(1+K)s + (K-2)}",
        expr,
    )

    # Als het nog een simpele a/b-vorm heeft zonder \frac, probeer voorzichtig te vervangen
    if r"\frac" not in expr:
        simple_fraction_pattern = r"(?<!\\frac\{)([A-Za-z0-9\+\-\(\)]+)\s*/\s*([A-Za-z0-9\+\-\(\)]+)"
        if re.fullmatch(simple_fraction_pattern, expr):
            expr = re.sub(simple_fraction_pattern, r"\\frac{\1}{\2}", expr)

    # Nettere spatiëring
    expr = re.sub(r"\s{2,}", " ", expr).strip()

    return expr


import re

def split_math_expression(expr: str) -> list[str]:
    """
    Split chained math expressions like:
    a = b => c = d => e = f

    into separate steps.
    """
    if not expr:
        return []

    # Splits op ⇒, => of ->
    parts = re.split(r"⇒|=>|->", expr)

    # Strip en verwijder lege delen
    parts = [p.strip() for p in parts if p.strip()]

    return parts


def looks_like_math_expression(expr: str) -> bool:
    expr = (expr or "").strip()
    if not expr:
        return False

    math_markers = [
        "=",
        r"\frac",
        "+",
        "-",
        "<",
        ">",
        r"\le",
        r"\ge",
        "(",
        ")",
        "s",
        "K",
    ]
    return any(marker in expr for marker in math_markers)

def dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        item = str(item or "").strip()
        if not item:
            continue
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def build_confidence(
    *,
    has_formula: bool,
    has_graph: bool,
    has_handwriting: bool,
    unclear_areas: list[str],
) -> tuple[ConfidenceType, ConfidenceType]:
    content_conf: ConfidenceType = "high"
    math_conf: ConfidenceType = "high"

    if has_handwriting or unclear_areas:
        content_conf = "medium"
        math_conf = "medium"

    if has_handwriting and unclear_areas:
        content_conf = "low"
        math_conf = "low"

    if has_graph and not has_formula and content_conf == "high":
        math_conf = "medium"

    return content_conf, math_conf


def build_domain_instruction(domain: DomainType, language: str) -> str:
    base = f"""
Antwoord ALTIJD in de gevraagde taal: {language}.

DIDACTISCHE STIJLREGELS (ChatGPT-Tutor Stijl):
- Geef ALTIJD eerst het directe kernantwoord of de hoofdconclusie in één krachtige zin.
- Leg daarna de essentie uit in 1-3 korte, behapbare blokken.
- Gebruik actieve, heldere taal die begrijpelijk is voor een student. Vermijd academische jargon-muren.
- Geen lange inleidingen of beleefdheidsfluff (Vermeid zinnen als "Op deze dia zien we..." of "Laten we beginnen met...").
- Wees exact, compact en didactisch. Complexe concepten breek je op in simpele analogieën of logische componenten.
- Als iets visueel niet 100% met zekerheid te interpreteren is op de afbeelding, wees dan expliciet voorzichtig en ga NIET gokken.
"""

    general_graph_reasoning = """
UNIVERSELE GRAFIEK-, TABEL- EN FIGUURREDENEERREGELS:
- Kijk kritisch naar de visuele elementen: wat staat er op de assen (X-as, Y-as), wat zijn de eenheden, en wat is de trend van de curve?
- Maak een strikt onderscheid tussen:
  1. Wat er feitelijk en exact zichtbaar is (data, lijnen, vormen).
  2. Wat er logisch en wetenschappelijk uit volgt.
  3. Het uiteindelijke antwoord op de leervraag.
- Een curve die in de buurt van een specifiek assenkruis of meetpunt komt, gaat er niet automatisch exact doorheen. Wees accuraat in je observatie.
- Als de slide vraagt om een lokale observatie of een simpele telling, geef dan GEEN brede, afleidende theoretische verhandeling. Houd het lokaal en relevant.
"""

    nyquist_reasoning = """
SPECIFIEK VOOR NYQUIST-DIAGRAMMEN:
- Beantwoord exact wat gevraagd wordt.
- Als de slide vraagt om het aantal omcirkelingen N, geef alleen N met een korte reden.
- Ga niet automatisch door naar stabiliteit of Z = N + P tenzij de slide dat expliciet vraagt.
TELREGELS VOOR N:
- N is het netto aantal omcirkelingen van het kritieke punt (-1,0).
- Tel niet het aantal zichtbare takken, lijnstukken of losse passages.
- Bepaal of de volledige curve het punt (-1,0) netto omsluit. Rothermalen (met de klok mee) = negatief, tegen de klok in = positief.
ZEER BELANGRIJKE EXACTHEIDSREGEL:
- Zeg ALLEEN dat de Nyquist-curve "door het kritieke punt (-1,0) gaat" als dat exact en duidelijk zichtbaar is.
- "In de buurt van (-1,0)" of "lijkt erlangs te gaan" is NIET hetzelfde als "gaat erdoorheen".
- Concludeer dus NIET N = 0 alleen omdat de curve dicht bij (-1,0) komt of de reële as in de buurt van -1 snijdt.
"""

    control_systems_instruction = """
DOMEIN: Regeltechniek / Control Systems
- Leg de nadruk op systeemgedrag, stabiliteit en het effect van parameters.
- Benoem extra begrippen zoals polen, nulpunten, root-locus of stabiliteitsparameters ($\\zeta, \\omega_n$) alleen als ze direct bijdragen aan het beantwoorden van de specifieke vraag op de slide.
"""


    math_instruction = """
DOMEIN: Wiskunde & Calculaties
- Werk strikt exact, stap-voor-stap en compact.
- Trek geen definitieve conclusies uit een visuele schatting, tenzij de figuur of de tekst expliciet mathematische ondersteuning biedt.
"""

    physics_instruction = """
DOMEIN: Natuurkunde / Physics
- Beschrijf eerst de fundamentele zichtbare fysieke relatie (bijv. krachten, velden, energie-overgangen), en trek pas daarna de conclusie.
- Verwar een benaderde grafische ligging nooit met een exact evenwichtspunt.
"""

    electronics_instruction = """
DOMEIN: Elektronica & Circuits
- Analyseer componenten en hun onderlinge visuele verbindingen op het schema.
- Trek geen conclusies op basis van de globale vorm van een signaal alleen; relateer gedrag aan standaardeigenschappen (spanning, stroom, frequentierespons).
"""

    # Dynamische domein-koppeling
    if domain == "control_systems":
        return f"{base}\n{general_graph_reasoning}\n{nyquist_reasoning}\n{control_systems_instruction}"
    elif domain == "math":
        return f"{base}\n{general_graph_reasoning}\n{math_instruction}"
    elif domain == "physics":
        return f"{base}\n{general_graph_reasoning}\n{physics_instruction}"
    elif domain == "electronics":
        return f"{base}\n{general_graph_reasoning}\n{electronics_instruction}"
    
    # "general" of onbekend domein krijgt de krachtige, brede ChatGPT-stijl didactiek mee
    return f"{base}\n{general_graph_reasoning}"


def build_language_style_instruction(language: str) -> str:
    lang = (language or "").strip().lower()

    if lang in ["nederlands", "nl", "dutch"]:
        return """
TAALSTIJL:
- Schrijf in natuurlijk, helder Nederlands.
- Wees rustig, direct en docentachtig.
- Gebruik korte didactische zinnen.
- Vermijd overdreven formele formuleringen.
- Laat de uitleg klinken alsof een goede docent het rustig uitlegt.
""".strip()

    if lang in ["english", "en", "engels"]:
        return """
LANGUAGE STYLE:
- Write in natural, clear English.
- Use a tutor-like tone.
- Be concise, explicit, and well structured.
- Prefer short, clear explanations over long academic wording.
- Make the explanation sound natural, not translated.
""".strip()

    if lang in ["deutsch", "de", "duits", "german"]:
        return """
SPRACHSTIL:
- Schreibe in klarem, natürlichem Deutsch.
- Erkläre systematisch und präzise.
- Nutze eine geordnete, logische Struktur.
- Vermeide unnötig komplizierte oder steife Formulierungen.
- Die Erklärung soll wie von einer guten Lehrkraft klingen.
""".strip()

    if lang in ["français", "fr", "frans", "french"]:
        return """
STYLE DE LANGUE :
- Écris dans un français clair et naturel.
- Garde un ton pédagogique et fluide.
- Explique avec précision, sans phrases inutilement longues.
- Mets l'accent sur la clarté logique.
- Le texte doit sembler rédigé naturellement en français, pas traduit.
""".strip()

    if lang in ["español", "es", "spaans", "spanish"]:
        return """
ESTILO DEL IDIOMA:
- Escribe en español natural, claro y fluido.
- Usa un tono docente y cercano.
- Explica paso a paso cuando haga falta.
- Evita frases rígidas o que suenen traducidas.
- La explicación debe sentirse natural para un estudiante hispanohablante.
""".strip()

    return """
LANGUAGE STYLE:
- Write in a clear, natural, student-friendly way.
- Make the explanation sound native, not translated.
""".strip()


def build_audience_instruction(audience_level: AudienceType, language: str) -> str:
    if audience_level == "beginner":
        return f"""
NIVEAU: beginner
- Gebruik eenvoudige woorden.
- Leg impliciete stappen kort uit.
- Maximaal weinig vaktaal zonder uitleg.
- Houd de uitleg extra kort.
- Schrijf in eenvoudig {language}.
""".strip()

    if audience_level == "advanced":
        return f"""
NIVEAU: advanced
- Wees compacter.
- Gebruik vaktaal waar nuttig.
- Focus op redenering en interpretatie.
- Schrijf in precies {language}.
""".strip()

    return f"""
NIVEAU: intermediate
- Leg kort maar duidelijk uit.
- Gebruik normale vaktaal.
- Geen overbodige zinnen.
- Schrijf in helder {language}.
""".strip()


def build_length_instruction(
    mode: ModeType,
    audience_level: AudienceType,
    detail_level: Literal["short", "normal", "long"],
) -> str:
    if mode == "derive":
        if detail_level == "short":
            return """
LENGTE:
- Maximaal 2 stappen
- Elke stap maximaal 1 korte zin
- Geen lange uitleg
- Alleen de kernredenering
""".strip()

        if detail_level == "long":
            return """
LENGTE:
- Maximaal 5 stappen
- Elke stap maximaal 1-2 korte zinnen
- Alleen noodzakelijke stappen
- Geen uitweidingen
""".strip()

        return """
LENGTE:
- Maximaal 4 stappen
- Elke stap kort
- Geen lange alinea's
- Alleen noodzakelijke redenering
""".strip()

    if detail_level == "short":
        return """
LENGTE:
- short_answer: max 1 zin
- key_point: max 1 zin
- why: max 1 korte zin
- steps: max 2 korte stappen
- calculation: alleen als echt nodig, max 1 regel
- graph_takeaway: max 1 zin
- exam_tip: alleen als echt nuttig, max 1 zin
- Geen lange alinea's
""".strip()

    if detail_level == "long":
        return """
LENGTE:
- short_answer: max 1 zin
- key_point: max 1 zin
- why: max 3 korte zinnen
- steps: max 4 compacte stappen
- calculation: compact maar volledig genoeg
- graph_takeaway: max 2 zinnen
- exam_tip: max 1 zin
- Geen lange alinea's
- Geen herhaling
""".strip()

    return """
LENGTE:
- short_answer: max 1 zin
- key_point: max 1 zin
- why: max 2 korte zinnen
- steps: max 3 compacte stappen
- calculation: alleen als nodig
- graph_takeaway: max 1 zin
- exam_tip: max 1 zin
- Geen lange alinea's
""".strip()


# =========================================================
# FAST ANALYSIS PROMPT
# =========================================================

def build_fast_analysis_prompt(
    *,
    text: str,
    language: str,
    domain: DomainType,
) -> str:
    return f"""
You are a highly precise, rapid-scanning Academic Slide Inspector. Your job is to extract the structural blueprint of the provided slide image.

TASK:
Analyze the uploaded image or page and return ONLY a compact structural analysis in valid JSON format.
- Rely on the IMAGE as your primary source of truth for layout, visual markers, diagrams, and figures.
- Use the provided context text only to resolve unreadable text or specific vocabulary.

{build_domain_instruction(domain, language)}

CRITICAL SCANNING RULES:
1. `slide_type`: Classify the slide into its single most dominant category (e.g., theory, definition, formula, derivation, graph, diagram, summary, exercise, table, multiple_choice, mixed).
2. `visible_items`: List only the 5-7 most important visual components actually present on the image (e.g., "X-axis labeled time", "Flowchart diagram", "Table of constants", "Handwritten equation").
3. `important_concepts`: Extract the core academic terms or pillars being taught.
4. `confidence`: If the slide contains heavily blurred text, complex unreadable handwriting, or ambiguous overlapping plots, lower your confidence level to 'medium' or 'low'.
5. Keep descriptions extremely clean, objective, and short. Do not guess or extrapolate beyond what is visible.

REQUIRED OUTPUT FORMAT (Return ONLY raw valid JSON matching the schema, no markdown blocks, no text outside JSON):
{{
 "slide_type": "mixed",
 "topic": "The main topic heading of the slide",
 "learning_goal": "The apparent learning objective or core question of the slide",
 "has_formula": false,
 "has_graph": false,
 "has_multiple_choice": false,
 "has_handwriting": false,
 "graph_reading_required": false,
 "confidence": "high",
 "visible_items": [],
 "important_concepts": [],
 "unclear_areas": []
}}

CONTEXT SLIDE TEXT:
{text}
""".strip()



def analyze_slide_fast(
    *,
    text: str,
    image_path: Optional[Path],
    language: str,
    domain: DomainType,
) -> SlideAnalysis:
    return gemini_generate_structured(
        model_name=GEMINI_INSPECT_MODEL,
        prompt=build_fast_analysis_prompt(
            text=text,
            language=language,
            domain=domain,
        ),
        response_schema=SlideAnalysis,
        image_path=image_path,
        temperature=0.1,
    )


# =========================================================
# GET PAGE INPUTS
# =========================================================

def get_page_payload(
    *,
    file_hash: str,
    page_index: int,
) -> tuple[str, str, Optional[Path], str]:
    file_type, texts = get_document_texts(file_hash)

    if page_index < 0 or page_index >= len(texts):
        raise HTTPException(status_code=400, detail="Ongeldige page_index.")

    text = truncate_text(clean_text(texts[page_index]))
    image_path = ensure_slide_image(file_hash, page_index, resolution="normal")

    image_signature = make_image_signature(image_path)
    return file_type, text, image_path, image_signature


def get_cached_or_fresh_analysis(
    *,
    file_hash: str,
    page_index: int,
    language: str,
    domain: DomainType,
    force_refresh: bool,
) -> tuple[str, str, Optional[Path], str, SlideAnalysis]:
    file_type, text, image_path, image_signature = get_page_payload(
        file_hash=file_hash,
        page_index=page_index,
    )
    
    cache_key = make_ai_cache_key(
        kind="analysis",
        file_hash=file_hash,
        page_index=page_index,
        language=language,
        mode="analysis",
        audience_level="intermediate",
        domain=domain,
        detail_level="normal",
        question=None,
        text=text,
        image_signature=image_signature,
    )
    
    if not force_refresh:
        cached = load_ai_cache(cache_key)
        if cached and cached.get("analysis"):
            try:
                return file_type, text, image_path, image_signature, SlideAnalysis(**cached["analysis"])
            except Exception:
                pass
                
    # HIER GING HET MIS: we geven nu correct de taal en het domein mee aan de snelle inspector
    analysis = analyze_slide_fast(
        text=text,
        image_path=image_path,
        language=language,
        domain=domain,
    )
    
    save_ai_cache(
        cache_key,
        {
            "analysis": analysis.model_dump(),
        },
    )
    return file_type, text, image_path, image_signature, analysis



# =========================================================
# FAST EXPLAIN PROMPTS
# =========================================================

def build_mode_instruction(mode: ModeType, question: Optional[str]) -> str:
    mapping = {
        "explain": "Leg de kern van de dia kort en docentachtig uit.",
        "simple": "Leg de dia extra simpel uit in korte taal.",
        "study": "Geef een compacte studie-uitleg met alleen wat je moet onthouden.",
        "questions": "Vat de dia samen op een manier die helpt bij leren en ophalen uit het geheugen.",
        "ask": f"Beantwoord alleen deze vraag over de dia: {question or ''}",
        "derive": "Werk de berekening of afleiding kort stap voor stap uit.",
    }
    return mapping.get(mode, "Leg de dia kort en docentachtig uit.")


def build_analysis_context(analysis: Optional[SlideAnalysis]) -> str:
    if not analysis:
        return ""

    return f"""
ANALYSE:
- slide_type: {analysis.slide_type}
- topic: {analysis.topic or 'onbekend'}
- learning_goal: {analysis.learning_goal or 'onbekend'}
- has_formula: {'ja' if analysis.has_formula else 'nee'}
- has_graph: {'ja' if analysis.has_graph else 'nee'}
- has_multiple_choice: {'ja' if analysis.has_multiple_choice else 'nee'}
- has_handwriting: {'ja' if analysis.has_handwriting else 'nee'}
- graph_reading_required: {'ja' if analysis.graph_reading_required else 'nee'}
- confidence: {analysis.confidence}
- visible_items: {', '.join(analysis.visible_items) if analysis.visible_items else 'geen'}
- important_concepts: {', '.join(analysis.important_concepts) if analysis.important_concepts else 'geen'}
- unclear_areas: {', '.join(analysis.unclear_areas) if analysis.unclear_areas else 'geen'}
""".strip()


def build_fast_explain_prompt(
    *,
    text: str,
    language: str,
    mode: ModeType,
    audience_level: AudienceType,
    domain: DomainType,
    detail_level: Literal["short", "normal", "long"],
    analysis: Optional[SlideAnalysis],
    question: Optional[str],
) -> str:
    return f"""
You are a highly skilled academic tutor capable of explaining concepts from any discipline with crystal-clear clarity.

IMPORTANT WORKFLOW
- Think and reason internally in English to build precise structural insights.
- Write the final output completely in {language}.

{build_domain_instruction(domain, language)}
{build_language_style_instruction(language)}
{build_audience_instruction(audience_level, language)}
{build_length_instruction(mode, audience_level, detail_level)}

CRITICAL LOVEABLE / MARKDOWN FORMATTING RULES (MANDATORY):
- IN ALL JSON TEXT FIELDS (`short_answer`, `key_point`, `why`, `exam_tip`, and step contents): You MUST wrap every mathematical variable, equation, or Greek letter in SINGLE dollar signs so Lovable can render it as mathematical symbols. Example: write "De dempingsratio $\\zeta$ en de polen $s$." Never leave raw backslashes without dollar signs.
- FOR GRAPHS/DIAGRAMS: Reference visual markers, zones, and colors (e.g., the green and red zones) explicitly.

REQUIRED OUTPUT FORMAT (Return ONLY valid JSON):
{{
 "mode": "{mode}",
 "short_answer": "Directe conclusie in één zin met desgewenst $\\zeta$.",
 "key_point": "Belangrijkste inzicht.",
 "steps": [
   {{
     "title": "Titel",
     "content": "Inhoud met desgewenst $\\omega_n$ tussen dollars."
   }}
 ],
 "why": "De onderliggende logica.",
 "calculation": "",
 "graph_takeaway": "",
 "exam_tip": "",
 "correct_answer": "",
 "warnings": [],
 "content_confidence": "high",
 "math_confidence": "high"
}}

SLIDE TEXT FOR CONTEXT:
{text}
""".strip()




def build_fast_derive_prompt(
    *,
    text: str,
    language: str,
    audience_level: AudienceType,
    domain: DomainType,
    analysis: Optional[SlideAnalysis],
) -> str:
    return f"""
Je bent een snelle en precieze docent voor berekeningen.

{build_domain_instruction(domain, language)}

{build_language_style_instruction(language)}

{build_audience_instruction(audience_level, language)}

{build_length_instruction('derive', audience_level)}

DOEL:
Geef een KORTE afleiding of berekening.
Niet te veel tekst.
Alleen de noodzakelijke stappen.

{build_analysis_context(analysis)}

REGELS:
- Geef maximaal 4 stappen
- Elke stap moet kort zijn
- Geen dubbele redenering
- Geen alternatieve methode
- short_answer geeft de uitkomst of hoofdconclusie
- conclusion geeft de betekenis van de uitkomst
- Gebruik warnings alleen als iets onduidelijk is
- Verzin niets
- Gebruik de afbeelding als hoofdbron
- Gebruik de tekst als extra context
- Laat de uitleg natuurlijk klinken in de gekozen taal
- Vermijd letterlijk vertaalde formuleringen

Geef ALLEEN geldige JSON terug in exact dit formaat:
{{
  "mode": "derive",
  "short_answer": "",
  "steps": [
    {{
      "title": "",
      "expression": "",
      "why": ""
    }}
  ],
  "conclusion": "",
  "warnings": [],
  "content_confidence": "medium",
  "math_confidence": "medium"
}}

Tekst van de dia:
{text}
""".strip()

def build_ask_prompt(
    *,
    text: str,
    analysis: SlideAnalysis,
    language: str,
    domain: DomainType,
    question: str,
) -> str:
    return f"""
Je bent een uitzonderlijk nauwkeurige en didactische AI-assistent die een gerichte vraag van een student beantwoordt over één specifieke studie-slide.

HOOFDREGEL:
De vraag van de gebruiker is absoluut leidend. De slide en de bijbehorende afbeelding dienen als bronmateriaal en context. Beantwoord de vraag direct, to-the-point en zonder algemene of irrelevante uitweidingen over de rest van de slide.

GEBRUIKERSVRAAG:
{question}

SLIDE CONTEXT (Tekst):
{text[:1800]}

{build_domain_instruction(domain, language)}

VERPLICHTE INTERNE STRATEGIE:
1. Bepaal de aard van de vraag (Bijv. Definities, Processen, Grafiek aflezen, Structuur/Lay-out, of een specifieke Berekening).
2. Scan de afbeelding en de tekst van de slide om exact het antwoord te isoleren.
3. Formuleer een antwoord dat direct bruikbaar is voor de student.

DIDACTISCHE RESPONS-RICHTLIJNEN:
- GEEN INLEIDING: Begin meteen met het antwoord. Zinnen zoals "Op basis van de slide..." of "Als antwoord op jouw vraag..." zijn verboden.
- DIRECT EN HELDER: Geef bij een ja/nee-vraag of een teltelling/aflezing direct de conclusie of de waarde in de allereerste zin. Leg daarna pas kort uit waarom.
- CAUSAAL VERBAND: Als de vraag vraagt naar een effect, invloed of relatie ("wat gebeurt er als...", "hoe hangt X samen met Y..."), leg dan de exacte causale brug uit. Benoem oorzaak, gevolg en wat dit betekent voor het gedrag of het concept.
- FOCUS: Lever geen algemene samenvatting van de slide als de vraag specifiek is. Lever geen losse theorieën die niet bijdragen aan het antwoord.

GEWENSTE UITVOERSTIJL:
- Antwoord volledig in het {language}.
- Schrijf compact, gestructureerd en krachtig (meestal 1 tot 4 korte zinnen).
- Gebruik indien nodig korte, duidelijke opsommingstekens voor de scanbaarheid.
- Geef ALLEEN de platte tekst van je antwoord terug. Geen JSON, geen Markdown-headers, geen meta-tekst.

Geef nu het gerichte antwoord:
"""



def build_detailed_math_explain_prompt(
    *,
    text: str,
    language: str,
    audience_level: AudienceType,
    domain: DomainType,
    analysis: Optional[SlideAnalysis],
    question: Optional[str],
    mode: ModeType,
) -> str:
    return f"""
You are the ultimate academic ChatGPT-Tutor. Your goal is NOT to give a dry lecture, but to make the student instantly 'get' the slide through visual intuition, colors, and simple concepts.

CRITICAL INSTRUCTION FOR FIELDS (MANDATORY):
- `short_answer`: Give a 1-sentence "Kathedraal-inzicht" (the absolute core takeaway of what this slide teaches).
- `key_point`: Explain the intuition behind the slide in plain language. If there are colors (like green/red zones), shapes, or curves, explain EXACTLY what they mean conceptually (e.g., "Groen betekent stabiel, rood betekent dat het systeem explodeert").
- `why`: Explain the underlying physical or practical mechanism. Why does this matter in real life? Keep it short and deeply didactic.
- `graph_takeaway`: Provide a crystal-clear guide on how to read the visual plot/chart. What should the student's eyes focus on first?

MATHEMATICAL STEP LAWS:
- Keep the `steps` strictly limited to breaking down the core terms or sub-components.
- Every `expression` MUST be a single line of pure LaTeX wrapped in DOUBLE dollar signs (e.g., "$$s = -\\zeta\\omega_n \\pm j\\omega_d$$").
- In all text fields, wrap every loose variable or Greek letter in SINGLE dollar signs (e.g., $\\zeta$).

{build_domain_instruction(domain, language)}
{build_language_style_instruction(language)}

EXACT STYLISTIC EXAMPLE (Target this level of intuition):
{{
 "mode": "{mode}",
 "short_answer": "Dit diagram laat zien hoe de positie van polen direct bepaalt of een systeem stabiel blijft of explodeert.",
 "key_point": "Kijk naar de kleuren: De GROENE zone ($0 < \\zeta < 1$) betekent dat het systeem stabiel naar zijn eindpunt toe trilt. De RODE zone ($\\zeta < 0$) is de gevarenzone: hier is het systeem volledig instabiel.",
 "steps": [
   {{
     "title": "De poolcoördinaten",
     "expression": "$$s = -\\zeta\\omega_n \\pm j\\omega_d$$",
     "explanation": "Dit is de positie van de pool. Het reële deel $-\\zeta\\omega_n$ trekt de pool naar links (stabiliteit), het imaginaire deel zorgt voor de trilling."
   }}
 ],
 "why": "Zonder deze analyse storten bruggen in of raken raketten buiten controle. Polen aan de rechterkant (rood) betekenen onbeheerste groei.",
 "graph_takeaway": "Focus op de assen: de verticale as is de grens. Alles links daarvan is veilig (groen), alles rechts is instabiel (rood). De straal van de cirkel is de snelheid $\\omega_n$.",
 "exam_tip": "Op het examen vragen ze vaak wat er gebeurt als $\\zeta = 0$. Onthoud: dan ligt de pool exact op de grensas en blijft het systeem eeuwig trillen.",
 "correct_answer": "",
 "warnings": [],
 "content_confidence": "high",
 "math_confidence": "high"
}}

Generate the intuitive JSON response for this slide text now:
{text}
""".strip()

# =========================================================
# OUTPUT ENHANCERS
# =========================================================

def enhance_explain_output(
    *,
    output: ExplainFastOutput,
    analysis: Optional[SlideAnalysis],
) -> ExplainFastOutput:
    output.short_answer = remove_latex_markers(output.short_answer).strip()
    output.key_point = remove_latex_markers(output.key_point).strip()
    output.why = remove_latex_markers(output.why).strip()
    output.calculation = remove_latex_markers(output.calculation).strip()
    output.graph_takeaway = remove_latex_markers(output.graph_takeaway).strip()
    output.exam_tip = remove_latex_markers(output.exam_tip).strip()
    output.correct_answer = remove_latex_markers(output.correct_answer).strip()

    cleaned_steps: list[ExplainStep] = []
    for step in output.steps[:5]:
        title = remove_latex_markers(step.title).strip()
        content = remove_latex_markers(step.content).strip()

        if not title and not content:
            continue

        cleaned_steps.append(
            ExplainStep(
                title=title or "Stap",
                content=content,
            )
        )

    output.steps = cleaned_steps
    output.warnings = dedupe_keep_order(output.warnings)[:6]

    if output.content_confidence not in {"low", "medium", "high"}:
        output.content_confidence = "medium"

    if output.math_confidence not in {"low", "medium", "high"}:
        output.math_confidence = "medium"

    if analysis:
        if analysis.has_graph and not output.graph_takeaway and output.short_answer:
            output.graph_takeaway = output.short_answer

    return output


def enhance_derive_output(
    *,
    output: DeriveFastOutput,
    analysis: Optional[SlideAnalysis],
) -> DeriveFastOutput:
    output.short_answer = remove_latex_markers(output.short_answer)
    output.conclusion = remove_latex_markers(output.conclusion)
    output.warnings = dedupe_keep_order(output.warnings)[:6]

    cleaned_steps: list[DeriveStep] = []
    for step in output.steps[:4]:
        expr = (step.expression or "").strip()
        if not expr:
            continue
        cleaned_steps.append(
            DeriveStep(
                title=remove_latex_markers(step.title) or "Stap",
                expression=expr,
                why=remove_latex_markers(step.why),
            )
        )
    output.steps = cleaned_steps

    if analysis:
        content_conf, math_conf = build_confidence(
            has_formula=analysis.has_formula,
            has_graph=analysis.has_graph,
            has_handwriting=analysis.has_handwriting,
            unclear_areas=analysis.unclear_areas,
        )
        if output.content_confidence == "medium":
            output.content_confidence = content_conf
        if output.math_confidence == "medium":
            output.math_confidence = math_conf

        if analysis.has_handwriting:
            output.warnings = dedupe_keep_order(
                output.warnings + ["Handgeschreven delen kunnen minder betrouwbaar zijn."]
            )[:6]

    return output


# =========================================================
# FAST EXPLAIN / DERIVE GENERATION
# =========================================================

def generate_fast_explain(
    *,
    text: str,
    image_path: Optional[Path],
    language: str,
    mode: ModeType,
    audience_level: AudienceType,
    domain: DomainType,
    detail_level: Literal["short", "normal", "long"],
    analysis: Optional[SlideAnalysis],
    question: Optional[str],
) -> ExplainFastOutput:
    prompt = build_fast_explain_prompt(
        text=text,
        language=language,
        mode=mode,
        audience_level=audience_level,
        domain=domain,
        detail_level=detail_level,
        analysis=analysis,
        question=question,
    )

    model_name = choose_model_for_fast_explain(
        mode=mode,
        analysis=analysis,
        audience_level=audience_level,
    )

    output = gemini_generate_structured(
        model_name=model_name,
        prompt=prompt,
        response_schema=ExplainFastOutput,
        image_path=image_path,
        temperature=0.1,
    )

    return enhance_explain_output(
        output=output,
        analysis=analysis,
    )


def generate_fast_derive(
    *,
    text: str,
    image_path: Optional[Path],
    language: str,
    audience_level: AudienceType,
    domain: DomainType,
    analysis: Optional[SlideAnalysis],
) -> DeriveFastOutput:
    prompt = build_fast_derive_prompt(
        text=text,
        language=language,
        audience_level=audience_level,
        domain=domain,
        analysis=analysis,
    )

    output = gemini_generate_structured(
        model_name=GEMINI_FALLBACK_MODEL,
        prompt=prompt,
        response_schema=DeriveFastOutput,
        image_path=image_path,
        temperature=0.1,
    )

    return enhance_derive_output(
        output=output,
        analysis=analysis,
    )

def generate_detailed_math_explain(
    *,
    text: str,
    image_path: Optional[Path],
    language: str,
    audience_level: AudienceType,
    domain: DomainType,
    analysis: Optional[SlideAnalysis],
    question: Optional[str],
    mode: ModeType,
) -> MathExplainOutput:
    prompt = build_detailed_math_explain_prompt(
        text=text,
        language=language,
        audience_level=audience_level,
        domain=domain,
        analysis=analysis,
        question=question,
        mode=mode,
    )

    output = gemini_generate_structured(
        model_name=GEMINI_FALLBACK_MODEL,
        prompt=prompt,
        response_schema=MathExplainOutput,
        image_path=image_path,
        temperature=0.1,
    )

    output.short_answer = remove_latex_markers(output.short_answer)
    output.key_point = remove_latex_markers(output.key_point)
    output.graph_takeaway = remove_latex_markers(output.graph_takeaway)
    output.exam_tip = remove_latex_markers(output.exam_tip)
    output.correct_answer = remove_latex_markers(output.correct_answer)
    output.warnings = dedupe_keep_order(output.warnings)[:6]

    cleaned_steps: list[MathExplainStep] = []

    for step in output.steps:
        expr = clean_math_expression(step.expression)
        explanation = remove_latex_markers(step.explanation).strip()
        title = remove_latex_markers(step.title).strip() or "Stap"

        if not expr:
            if explanation:
                cleaned_steps.append(
                    MathExplainStep(
                        title=title,
                        expression="",
                        explanation=explanation,
                    )
                )
            continue

        split_exprs = split_math_expression(expr)

        if len(split_exprs) <= 1:
            cleaned_steps.append(
                MathExplainStep(
                    title=title,
                    expression=expr,
                    explanation=explanation,
                )
            )
        else:
            for idx, sub_expr in enumerate(split_exprs):
                cleaned_steps.append(
                    MathExplainStep(
                        title=title if idx == 0 else "Tussenstap",
                        expression=clean_math_expression(sub_expr),
                        explanation=explanation if idx == 0 else "",
                    )
                )

    # extra safeguard: voorkom te veel stappen
    output.steps = cleaned_steps[:8]

    if analysis:
        content_conf, math_conf = build_confidence(
            has_formula=analysis.has_formula,
            has_graph=analysis.has_graph,
            has_handwriting=analysis.has_handwriting,
            unclear_areas=analysis.unclear_areas,
        )
        if output.content_confidence == "medium":
            output.content_confidence = content_conf
        if output.math_confidence == "medium":
            output.math_confidence = math_conf

        if analysis.has_handwriting:
            output.warnings = dedupe_keep_order(
                output.warnings + ["Handgeschreven delen kunnen minder betrouwbaar zijn."]
            )[:6]

    # fallback reparatie voor expression-velden
    repaired_steps: list[MathExplainStep] = []
    for step in output.steps:
        expr = step.expression.strip()
        if looks_like_math_expression(expr):
            repaired_steps.append(step)
        else:
            repaired_steps.append(
                MathExplainStep(
                    title=step.title,
                    expression=clean_math_expression(expr),
                    explanation=step.explanation,
                )
            )

    output.steps = repaired_steps[:8]

    return output


# =========================================================
# ANALYZE ROUTE
# =========================================================

@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    start = time.perf_counter()

    file_type, text, image_path, _, analysis = get_cached_or_fresh_analysis(
        file_hash=req.file_hash,
        page_index=req.page_index,
        language=req.language,
        domain=req.domain,
        force_refresh=req.force_refresh,
    )

    return {
        "ok": True,
        "file_hash": req.file_hash,
        "page_index": req.page_index,
        "file_type": file_type,
        "used_vision": image_path is not None,
        "analysis": analysis.model_dump(),
        "duration_seconds": round(time.perf_counter() - start, 2),
    }


# =========================================================
# EXPLAIN ROUTE
# =========================================================

@app.post("/explain")
def explain(req: ExplainRequest):
    start = time.perf_counter()
    try:
        file_type, text, image_path, image_signature, analysis = get_cached_or_fresh_analysis(
            file_hash=req.file_hash,
            page_index=req.page_index,
            language=req.language,
            domain=req.domain,
            force_refresh=req.force_refresh,
        )
        
        detail_level = getattr(req, "detail_level", "normal")
        cache_key = make_ai_cache_key(
            kind="explain",
            file_hash=req.file_hash,
            page_index=req.page_index,
            language=req.language,
            mode=req.mode,
            audience_level=req.audience_level,
            domain=req.domain,
            detail_level=detail_level,
            question=req.question,
            text=text,
            image_signature=image_signature,
        )
        
        if not req.force_refresh:
            cached = load_ai_cache(cache_key)
            if cached and cached.get("output"):
                return {
                    "ok": True,
                    "file_hash": req.file_hash,
                    "page_index": req.page_index,
                    "file_type": file_type,
                    "analysis": analysis.model_dump(),
                    "output": cached["output"],
                    "cached": True,
                    "duration_seconds": round(time.perf_counter() - start, 2),
                }
                
        is_math_heavy = (
            analysis.has_formula
            or analysis.slide_type in ["formula", "derivation", "exercise"]
        )
        
        if req.mode == "derive":
            output = generate_fast_derive(
                text=text,
                image_path=image_path,
                language=req.language,
                audience_level=req.audience_level,
                domain=req.domain,
                analysis=analysis,
            )
        elif req.mode == "ask":
            prompt = build_ask_prompt(
                text=text,
                analysis=analysis,
                language=req.language,
                domain=req.domain,
                question=req.question or "",
            )
            raw_output = gemini_generate_structured(
                model_name=GEMINI_EXPLAIN_MODEL,
                prompt=prompt,
                response_schema=ExplainFastOutput,
                image_path=image_path,
                temperature=0.1,
            )
            output = enhance_explain_output(
                output=raw_output,
                analysis=analysis,
            )
        elif req.mode == "explain" and is_math_heavy:
            output = generate_detailed_math_explain(
                text=text,
                image_path=image_path,
                language=req.language,
                audience_level=req.audience_level,
                domain=req.domain,
                analysis=analysis,
                question=req.question,
                mode=req.mode,
            )
        else:
            # HIER IS DETAIL_LEVEL NU CORRECT TOEGEVOEGD:
            output = generate_fast_explain(
                text=text,
                image_path=image_path,
                language=req.language,
                mode=req.mode,
                audience_level=req.audience_level,
                domain=req.domain,
                detail_level=detail_level,
                analysis=analysis,
                question=req.question,
            )
            
        payload = output.model_dump()
        save_ai_cache(
            cache_key,
            {
                "output": payload,
            },
        )
        
        return {
            "ok": True,
            "file_hash": req.file_hash,
            "page_index": req.page_index,
            "file_type": file_type,
            "analysis": analysis.model_dump(),
            "output": payload,
            "cached": False,
            "duration_seconds": round(time.perf_counter() - start, 2),
        }
    except Exception as e:
        logger.exception("EXPLAIN failed")
        raise_api_error(
            status_code=500,
            error_code="EXPLAIN_PROCESS_FAILED",
            message="Er is een fout opgetreden tijdens het genereren van de AI uitleg.",
            details={"reason": str(e), "type": type(e).__name__}
        )



  # =========================================================
# ERROR HELPERS
# =========================================================

def raise_api_error(status_code: int, error_code: str, message: str, details: Optional[dict[str, Any]] = None):
    raise HTTPException(
        status_code=status_code,
        detail={
            "ok": False,
            "error_code": error_code,
            "message": message,
            "details": details or {},
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    detail = exc.detail

    if isinstance(detail, dict) and "error_code" in detail:
        return fastapi_json_response(exc.status_code, detail)

    return fastapi_json_response(
        exc.status_code,
        {
            "ok": False,
            "error_code": "HTTP_ERROR",
            "message": str(detail),
            "details": {},
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_, exc: Exception):
    logger.exception("Onverwachte backendfout: %s", exc)
    return fastapi_json_response(
        500,
        {
            "ok": False,
            "error_code": "INTERNAL_SERVER_ERROR",
            "message": "Er ging iets mis in de backend.",
            "details": {},
        },
    )


def fastapi_json_response(status_code: int, payload: dict[str, Any]):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status_code, content=payload)


# =========================================================
# SMALL SAFETY OVERRIDES
# =========================================================

def validate_page_index(file_hash: str, page_index: int) -> int:
    _, texts = get_document_texts(file_hash)
    if page_index < 0 or page_index >= len(texts):
        raise_api_error(
            400,
            "INVALID_PAGE_INDEX",
            "Ongeldige page_index.",
            {"page_index": page_index, "total_pages": len(texts)},
        )
    return len(texts)


def ensure_document_exists(file_hash: str) -> dict[str, Any]:
    meta = load_meta(file_hash)
    if not meta:
        raise_api_error(404, "DOCUMENT_NOT_FOUND", "Document niet gevonden.")
    return meta


def ensure_page_image_with_fallback(
    *,
    file_hash: str,
    page_index: int,
    prefer_high: bool = False,
) -> Optional[Path]:
    resolution_order = ["high", "normal"] if prefer_high else ["normal", "high"]

    for resolution in resolution_order:
        image_path = ensure_slide_image(file_hash, page_index, resolution=resolution)
        if image_path and image_path.exists():
            return image_path
    return None


# =========================================================
# REFRESH / PRELOAD HELPERS
# =========================================================

def preload_page_range(
    *,
    file_hash: str,
    start_index: int,
    end_index: int,
    resolution: Literal["normal", "high"] = "normal",
) -> dict[str, Any]:
    total_pages = validate_page_index(file_hash, start_index)
    validate_page_index(file_hash, min(end_index, total_pages - 1))

    rendered = 0
    failed = []

    for i in range(start_index, end_index + 1):
        try:
            image_path = ensure_slide_image(file_hash, i, resolution=resolution)
            if image_path and image_path.exists():
                rendered += 1
            else:
                failed.append(i)
        except Exception:
            failed.append(i)

    return {
        "ok": True,
        "file_hash": file_hash,
        "start_index": start_index,
        "end_index": end_index,
        "resolution": resolution,
        "rendered": rendered,
        "failed_pages": failed,
    }


def refresh_analysis_cache_for_page(
    *,
    file_hash: str,
    page_index: int,
    language: str,
    domain: DomainType,
) -> dict[str, Any]:
    file_type, text, image_path, image_signature, analysis = get_cached_or_fresh_analysis(
        file_hash=file_hash,
        page_index=page_index,
        language=language,
        domain=domain,
        force_refresh=True,
    )

    return {
        "ok": True,
        "file_hash": file_hash,
        "page_index": page_index,
        "file_type": file_type,
        "analysis": analysis.model_dump(),
        "image_signature": image_signature,
        "used_vision": image_path is not None,
    }


# =========================================================
# CACHE / FILE CLEANUP HELPERS
# =========================================================

def safe_delete_file(path: Path) -> bool:
    try:
        if path.exists():
            path.unlink()
        return True
    except Exception:
        return False


def safe_delete_dir(path: Path) -> bool:
    try:
        if path.exists():
            shutil.rmtree(path)
        return True
    except Exception:
        return False


def clear_document_ai_cache(file_hash: str) -> int:
    removed = 0
    for path in AI_CACHE_DIR.glob("*.json"):
        try:
            payload = load_json(path)
            if not payload:
                continue
            payload_str = json.dumps(payload, ensure_ascii=False)
            if file_hash in payload_str:
                path.unlink()
                removed += 1
        except Exception:
            continue
    return removed


def delete_document_assets(file_hash: str) -> dict[str, Any]:
    info = {
        "meta_deleted": safe_delete_file(meta_path(file_hash)),
        "text_cache_deleted": safe_delete_file(text_cache_path(file_hash)),
        "image_dir_deleted": safe_delete_dir(IMAGE_DIR / file_hash),
        "pdf_dir_deleted": safe_delete_dir(PDF_DIR / file_hash),
    }

    for suffix in [".pdf", ".pptx"]:
        path = UPLOAD_DIR / f"{file_hash}{suffix}"
        if path.exists():
            info[f"upload_{suffix[1:]}_deleted"] = safe_delete_file(path)

    info["ai_cache_deleted_count"] = clear_document_ai_cache(file_hash)
    info["ok"] = True
    info["file_hash"] = file_hash
    return info


# =========================================================
# DEBUG / PERFORMANCE HELPERS
# =========================================================

@app.get("/debug/document/{file_hash}")
def debug_document(file_hash: str):
    meta = ensure_document_exists(file_hash)
    file_type, texts = get_document_texts(file_hash)

    pages = []
    for i in range(len(texts)):
        normal_path = get_page_image_path(file_hash, i, "normal")
        high_path = get_page_image_path(file_hash, i, "high")
        pages.append(
            {
                "page_index": i,
                "normal_exists": normal_path.exists(),
                "high_exists": high_path.exists(),
                "text_len": len(texts[i] or ""),
            }
        )

    return {
        "ok": True,
        "meta": meta,
        "file_type": file_type,
        "total_pages": len(texts),
        "pages": pages,
    }

@app.get("/debug/pptx/{file_hash}")
def debug_pptx(file_hash: str):
    meta = load_meta(file_hash)
    if not meta:
        raise HTTPException(status_code=404, detail="Document niet gevonden.")

    file_type, original_path = get_document_info(file_hash)

    if file_type != "pptx":
        return {
            "ok": True,
            "file_hash": file_hash,
            "file_type": file_type,
            "message": "Dit bestand is geen PPTX."
        }

    converted_pdf_dir = PDF_DIR / file_hash
    converted_pdf = converted_pdf_dir / f"{file_hash}.pdf"

    normal_dir = IMAGE_DIR / file_hash / "normal"
    high_dir = IMAGE_DIR / file_hash / "high"

    return {
        "ok": True,
        "file_hash": file_hash,
        "file_type": file_type,
        "original_exists": original_path.exists(),
        "original_path": str(original_path),
        "converted_pdf_exists": converted_pdf.exists(),
        "converted_pdf_path": str(converted_pdf),
        "normal_dir_exists": normal_dir.exists(),
        "high_dir_exists": high_dir.exists(),
        "normal_images": sorted([p.name for p in normal_dir.glob("*.png")]) if normal_dir.exists() else [],
        "high_images": sorted([p.name for p in high_dir.glob("*.png")]) if high_dir.exists() else [],
    }


@app.get("/debug/cache")
def debug_cache():
    ai_files = list(AI_CACHE_DIR.glob("*.json"))
    text_files = list(TEXT_CACHE_DIR.glob("*.json"))
    meta_files = list(META_DIR.glob("*.json"))

    return {
        "ok": True,
        "ai_cache_files": len(ai_files),
        "text_cache_files": len(text_files),
        "meta_files": len(meta_files),
    }


@app.get("/debug/timings/{file_hash}/{page_index}")
def debug_timings(
    file_hash: str,
    page_index: int,
    language: str = "Nederlands",
    domain: DomainType = "general",
    audience_level: AudienceType = "intermediate",
):
    total_pages = validate_page_index(file_hash, page_index)

    t0 = time.perf_counter()
    file_type, text, image_path, image_signature, analysis = get_cached_or_fresh_analysis(
        file_hash=file_hash,
        page_index=page_index,
        language=language,
        domain=domain,
        force_refresh=False,
    )
    t1 = time.perf_counter()

    output = generate_fast_explain(
        text=text,
        image_path=image_path,
        language=language,
        mode="explain",
        audience_level=audience_level,
        domain=domain,
        analysis=analysis,
        question=None,
    )
    t2 = time.perf_counter()

    return {
        "ok": True,
        "file_hash": file_hash,
        "page_index": page_index,
        "total_pages": total_pages,
        "file_type": file_type,
        "analysis_seconds": round(t1 - t0, 3),
        "explain_seconds": round(t2 - t1, 3),
        "total_seconds": round(t2 - t0, 3),
        "image_used": image_path is not None,
        "image_signature": image_signature,
        "analysis": analysis.model_dump(),
        "output": output.model_dump(),
    }


# =========================================================
# PRELOAD / REFRESH ROUTES
# =========================================================

@app.post("/preload/{file_hash}")
def preload(file_hash: str):
    meta = load_meta(file_hash)
    if not meta:
        raise HTTPException(status_code=404, detail="Document niet gevonden.")

    total = meta.get("total_pages", 0)

    # 🔥 preload eerste 3 slides
    for i in range(min(3, total)):
        try:
            ensure_slide_image(file_hash, i, "normal")
            get_cached_or_fresh_analysis(file_hash=file_hash, page_index=i)
        except Exception:
            pass

    return {"ok": True}



@app.post("/refresh-analysis")
def refresh_analysis(req: AnalyzeRequest):
    ensure_document_exists(req.file_hash)
    return refresh_analysis_cache_for_page(
        file_hash=req.file_hash,
        page_index=req.page_index,
        language=req.language,
        domain=req.domain,
    )


@app.post("/refresh-explain")
def refresh_explain(req: ExplainRequest):
    ensure_document_exists(req.file_hash)

    file_type, text, image_path, image_signature, analysis = get_cached_or_fresh_analysis(
        file_hash=req.file_hash,
        page_index=req.page_index,
        language=req.language,
        domain=req.domain,
        force_refresh=True,
    )

    if req.mode == "derive":
        output = generate_fast_derive(
            text=text,
            image_path=image_path,
            language=req.language,
            audience_level=req.audience_level,
            domain=req.domain,
            analysis=analysis,
        )
    else:
        output = generate_fast_explain(
            text=text,
            image_path=image_path,
            language=req.language,
            mode=req.mode,
            audience_level=req.audience_level,
            domain=req.domain,
            analysis=analysis,
            question=req.question,
        )

    cache_key = make_ai_cache_key(
        kind="explain",
        file_hash=req.file_hash,
        page_index=req.page_index,
        language=req.language,
        mode=req.mode,
        audience_level=req.audience_level,
        domain=req.domain,
        question=req.question,
        text=text,
        image_signature=image_signature,
    )

    save_ai_cache(cache_key, {"output": output.model_dump()})

    return {
        "ok": True,
        "file_hash": req.file_hash,
        "page_index": req.page_index,
        "file_type": file_type,
        "analysis": analysis.model_dump(),
        "output": output.model_dump(),
        "cached": False,
        "refreshed": True,
    }


# =========================================================
# DELETE ROUTE
# =========================================================

@app.delete("/document/{file_hash}")
def delete_document(file_hash: str):
    ensure_document_exists(file_hash)
    return delete_document_assets(file_hash)


# =========================================================
# OPTIONAL: NEXT / PREV PAGE HELPERS
# =========================================================

@app.get("/document/{file_hash}/neighbors/{page_index}")
def get_neighbors(file_hash: str, page_index: int):
    _, texts = get_document_texts(file_hash)
    total_pages = len(texts)

    if page_index < 0 or page_index >= total_pages:
        raise_api_error(
            400,
            "INVALID_PAGE_INDEX",
            "Ongeldige page_index.",
            {"page_index": page_index, "total_pages": total_pages},
        )

    prev_index = page_index - 1 if page_index > 0 else None
    next_index = page_index + 1 if page_index < total_pages - 1 else None

    return {
        "ok": True,
        "file_hash": file_hash,
        "page_index": page_index,
        "prev_page_index": prev_index,
        "next_page_index": next_index,
        "total_pages": total_pages,
    }


# =========================================================
# OPTIONAL: WARM NEXT PAGE IN BACKGROUND
# =========================================================

@app.post("/warm-next/{file_hash}/{page_index}")
def warm_next(file_hash: str, page_index: int):
    try:
        _, _, _, _, analysis = get_cached_or_fresh_analysis(
            file_hash=file_hash,
            page_index=page_index,
        )

        # Warm volgende slide
        next_index = page_index + 1

        meta = load_meta(file_hash)
        if not meta:
            return {"ok": True, "warmed": False}

        total = meta.get("total_pages", 0)
        if next_index >= total:
            return {"ok": True, "warmed": False}

        # 🔥 dit is belangrijk
        ensure_slide_image(file_hash, next_index, "normal")

        # 🔥 en dit maakt AI sneller
        get_cached_or_fresh_analysis(
            file_hash=file_hash,
            page_index=next_index,
        )

        return {"ok": True, "warmed": True}

    except Exception:
        return {"ok": True, "warmed": False}


# =========================================================
# FRONTEND-FRIENDLY SUMMARY ENDPOINT
# =========================================================

@app.get("/document/{file_hash}/summary")
def document_summary(file_hash: str):
    meta = ensure_document_exists(file_hash)
    file_type, texts = get_document_texts(file_hash)
    total_pages = len(texts)

    ready_images = 0
    for i in range(total_pages):
        if get_page_image_path(file_hash, i, "normal").exists():
            ready_images += 1

    return {
        "ok": True,
        "file_hash": file_hash,
        "file_name": meta.get("file_name"),
        "file_type": file_type,
        "status": meta.get("status", "uploaded"),
        "total_pages": total_pages,
        "ready_images": ready_images,
        "all_images_ready": ready_images == total_pages,
    }


# =========================================================
# SMALL RECOMMENDED PATCHES FOR EXISTING ROUTES
# =========================================================
# 1. In slide_image(...) kun je prefer_high logic gebruiken als je wilt:
#
# image_path = ensure_page_image_with_fallback(
#     file_hash=file_hash,
#     page_index=page_index,
#     prefer_high=(resolution == "high"),
# )
#
# 2. In explain(...) kun je optioneel next page warmen vanuit frontend
#    door na een succesvolle response /warm-next aan te roepen.
#
# 3. Voor frontend:
#    - gebruik output.short_answer als default zichtbare tekst
#    - toon key_point/why pas eronder
#    - maak calculation collapsible
#    - laat exam_tip alleen zien als niet leeg