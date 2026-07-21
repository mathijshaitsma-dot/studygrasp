"""Router: media. Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)

router = APIRouter()




@router.post("/tts")
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
                            debug_reason(e))
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    return FileResponse(cache_file, media_type="audio/mpeg",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})




# =========================================================
# ZOEKEN OVER ALLE DOCUMENTEN
# =========================================================

@router.get("/search")
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
