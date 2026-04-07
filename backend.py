import os
import re
import base64
import hashlib
import tempfile
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
import pythoncom
import win32com.client
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pptx import Presentation
from openai import OpenAI

# =========================
# CONFIG
# =========================

OPENAI_API_KEY = "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"
MODEL_NAME = "gpt-4o-mini"


client = OpenAI(api_key=OPENAI_API_KEY)

BASE_DIR = Path("backend_cache")
UPLOAD_DIR = BASE_DIR / "uploads"
IMAGE_DIR = BASE_DIR / "images"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="StudyCopilot Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# HELPERS
# =========================

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def natural_sort_key(filename: str):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", filename)]


def save_file_once(file_bytes: bytes, suffix: str):
    file_hash = sha256_bytes(file_bytes)
    path = UPLOAD_DIR / f"{file_hash}{suffix}"
    if not path.exists():
        path.write_bytes(file_bytes)
    return file_hash, path.resolve()


def image_to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def truncate_text(text: str, max_len: int = 1600) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n\n[tekst ingekort]"


# =========================
# PPTX PROCESSING
# =========================

def extract_pptx_texts(path: Path) -> list[str]:
    prs = Presentation(str(path))
    texts = []

    for slide in prs.slides:
        slide_text = ""
        for shape in slide.shapes:
            if hasattr(shape, "text"):
                slide_text += shape.text + "\n"
        texts.append(slide_text.strip())

    return texts


def render_pptx_to_real_images(path: Path, file_hash: str) -> Path:
    """
    Exporteert echte PowerPoint-slides naar PNG met de desktopversie van PowerPoint.
    Alleen voor Windows + PowerPoint geïnstalleerd.
    """
    out_dir = IMAGE_DIR / file_hash
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(
        [f for f in os.listdir(out_dir) if f.lower().endswith(".png")],
        key=natural_sort_key
    )
    if existing:
        return out_dir

    pythoncom.CoInitialize()
    powerpoint = None
    presentation = None

    try:
        powerpoint = win32com.client.Dispatch("PowerPoint.Application")
        powerpoint.Visible = 1

        pptx_path = str(path.resolve())
        out_dir_str = str(out_dir.resolve())

        # Open(path, ReadOnly, Untitled, WithWindow)
        presentation = powerpoint.Presentations.Open(pptx_path, False, False, False)

        # 18 = ppSaveAsPNG
        presentation.SaveAs(out_dir_str, 18)
        presentation.Close()
        presentation = None

        powerpoint.Quit()
        powerpoint = None

    except Exception as e:
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass

        if powerpoint is not None:
            try:
                powerpoint.Quit()
            except Exception:
                pass

        raise HTTPException(
            status_code=500,
            detail=(
                "PPTX export naar echte slide-afbeeldingen mislukt. "
                "Controleer of Microsoft PowerPoint op deze Windows-machine is geïnstalleerd. "
                f"Technische fout: {str(e)}"
            )
        )

    return out_dir


# =========================
# PDF PROCESSING
# =========================

def extract_pdf_texts(path: Path) -> list[str]:
    doc = fitz.open(str(path))
    texts = []
    for i in range(len(doc)):
        page = doc.load_page(i)
        texts.append(page.get_text("text").strip())
    doc.close()
    return texts


def render_pdf_to_images(path: Path, file_hash: str) -> Path:
    out_dir = IMAGE_DIR / file_hash
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(
        [f for f in os.listdir(out_dir) if f.lower().endswith(".png")],
        key=natural_sort_key
    )
    if existing:
        return out_dir

    doc = fitz.open(str(path))
    for i in range(len(doc)):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
        pix.save(str(out_dir / f"page_{i}.png"))
    doc.close()
    return out_dir


# =========================
# OPENAI PROMPTS
# =========================

def build_explain_prompt(text: str, language: str) -> str:
    return f"""
Je bent een rustige, slimme docent.

Leg deze dia of pagina uit in vloeiend {language}, alsof je het mondeling uitlegt aan een student.
Schrijf natuurlijk en verhalend. Gebruik gewone zinnen.
Gebruik geen bullet points en geen lijstjes, tenzij dat echt noodzakelijk is.

Belangrijk:
- leg uit wat hier bedoeld wordt
- leg uit hoe onderdelen samenhangen
- leg formules, schema's, processen of grafieken uit in gewone taal
- houd het compact, maar wel menselijk en helder
- klink niet als een droge samenvatting

Tekst van de dia/pagina:
{text}
""".strip()


def build_simple_prompt(text: str, language: str) -> str:
    return f"""
Je bent een vriendelijke docent.

Leg deze dia of pagina nog eenvoudiger uit in vloeiend {language}.
Schrijf kort, natuurlijk en helder.
Gebruik geen bullet points.
Alsof je het uitlegt aan iemand die het voor het eerst ziet.

Tekst van de dia/pagina:
{text}
""".strip()


def build_keypoints_prompt(text: str, language: str) -> str:
    return f"""
Je bent een docent.

Haal uit deze dia of pagina alleen de belangrijkste dingen die iemand moet onthouden.
Gebruik maximaal 5 korte punten.
Schrijf in {language}.

Tekst van de dia/pagina:
{text}
""".strip()


def build_questions_prompt(text: str, language: str) -> str:
    return f"""
Je bent een docent.

Maak 3 korte oefenvragen over deze dia of pagina en geef onder elke vraag een kort antwoord.
Schrijf in {language}.

Tekst van de dia/pagina:
{text}
""".strip()


def build_ask_prompt(text: str, language: str, question: str) -> str:
    return f"""
Je bent een rustige studiecoach.

Beantwoord alleen de vraag van de gebruiker over deze dia of pagina.
Schrijf in {language}.
Schrijf helder en natuurlijk.
Gebruik geen bullet points, tenzij echt nodig.

Tekst van de dia/pagina:
{text}

Vraag van de gebruiker:
{question}
""".strip()


def ask_openai(prompt: str) -> str:
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Je bent een rustige, behulpzame docent. "
                        "Je legt natuurlijk, verhalend en helder uit. "
                        "Je vermijdt bullet points tenzij die expliciet gevraagd worden."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )
        return response.choices[0].message.content

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI fout: {str(e)}")


# =========================
# REQUEST MODEL
# =========================

class ExplainRequest(BaseModel):
    file_hash: str
    page_index: int
    language: str = "Nederlands"
    mode: str = "explain"
    question: Optional[str] = None


# =========================
# ROUTES
# =========================

@app.get("/")
def health():
    return {"ok": True, "service": "StudyCopilot Backend"}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    try:
        suffix = Path(file.filename).suffix.lower()

        if suffix not in [".pdf", ".pptx"]:
            raise HTTPException(status_code=400, detail="Alleen .pdf en .pptx worden ondersteund.")

        file_bytes = await file.read()
        file_hash, path = save_file_once(file_bytes, suffix)

        if suffix == ".pdf":
            texts = extract_pdf_texts(path)
            image_dir = render_pdf_to_images(path, file_hash)
            file_type = "pdf"
            label = "pagina"
        else:
            texts = extract_pptx_texts(path)
            image_dir = render_pptx_to_real_images(path, file_hash)
            file_type = "pptx"
            label = "dia"

        image_files = sorted(
            [p for p in image_dir.glob("*.png")],
            key=lambda p: natural_sort_key(p.name)
        )

        pages = []
        for i, img_path in enumerate(image_files):
            pages.append({
                "index": i,
                "label": f"{label} {i + 1}",
                "image_base64": image_to_base64(img_path),
                "text_preview": texts[i][:300] if i < len(texts) else ""
            })

        return {
            "file_hash": file_hash,
            "file_name": file.filename,
            "file_type": file_type,
            "total_pages": len(texts),
            "pages": pages
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload fout: {str(e)}")


@app.post("/explain")
def explain(req: ExplainRequest):
    try:
        possible_pdf = UPLOAD_DIR / f"{req.file_hash}.pdf"
        possible_pptx = UPLOAD_DIR / f"{req.file_hash}.pptx"

        if possible_pdf.exists():
            texts = extract_pdf_texts(possible_pdf)
        elif possible_pptx.exists():
            texts = extract_pptx_texts(possible_pptx)
        else:
            raise HTTPException(status_code=404, detail="Bestand niet gevonden.")

        if req.page_index < 0 or req.page_index >= len(texts):
            raise HTTPException(status_code=400, detail="Ongeldige page_index.")

        text = truncate_text(texts[req.page_index])

        if not text.strip():
            return {"output": "Er is te weinig leesbare tekst gevonden op deze dia of pagina."}

        mode = req.mode.lower().strip()

        if mode == "explain":
            prompt = build_explain_prompt(text, req.language)
        elif mode == "simple":
            prompt = build_simple_prompt(text, req.language)
        elif mode == "keypoints":
            prompt = build_keypoints_prompt(text, req.language)
        elif mode == "questions":
            prompt = build_questions_prompt(text, req.language)
        elif mode == "ask":
            if not req.question or not req.question.strip():
                raise HTTPException(status_code=400, detail="Bij mode='ask' moet je een question meesturen.")
            prompt = build_ask_prompt(text, req.language, req.question.strip())
        else:
            raise HTTPException(
                status_code=400,
                detail="Ongeldige mode. Gebruik explain, simple, keypoints, questions of ask."
            )

        output = ask_openai(prompt)

        return {
            "file_hash": req.file_hash,
            "page_index": req.page_index,
            "mode": mode,
            "language": req.language,
            "output": output
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Explain fout: {str(e)}")