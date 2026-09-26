"""Router: media. Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth

router = APIRouter()




def _tts_voice(language: str) -> str:
    code = (language or "nl").strip().lower()[:2]
    return TTS_VOICES.get(code) or TTS_VOICES["en"]


def _tts_synth(text: str, voice: str):
    """Genereer met edge-tts in ÉÉN run zowel de MP3 als de woord-tijdmarkeringen,
    en cache beide (L1 lokaal + L2 gedeeld). Geeft (audio_path, marks) terug, met
    marks = lijst van {"t": start-ms, "w": woord} in leesvolgorde — voor de
    meeleesindicator. Zo kost voorlezen hooguit één keer wat; daarna gratis en
    instant voor iedereen, inclusief de tijdstempels."""
    key = sha256_text(voice + "|" + text)
    blob_key = f"{key}.mp3"

    def from_cache():
        path = cache_store.blob_local_path("tts_cache", blob_key)
        marks = cache_store.get_json("tts_marks", key)
        if path is not None and marks is not None:
            return path, marks.get("marks", [])
        return None

    hit = from_cache()
    if hit:
        return hit

    # Dedup, net als bij /explain: het voorwarmen van een dia en het klikken op
    # voorlezen vragen exact dezelfde audio. Zonder claim genereerde de tweede
    # aanvraag alles nóg een keer — dubbel werk, en de gebruiker wachtte alsnog
    # de volle synthesetijd. Nu lift de tweede mee op de eerste.
    claim_key = f"tts|{key}"
    event, claimed = claim_generation(claim_key)
    if not claimed:
        event.wait(timeout=60)
        hit = from_cache()
        if hit:
            return hit
        event, claimed = claim_generation(claim_key)   # de ander is mislukt: zelf doen
    try:
        return _tts_synth_inner(text, voice, key, blob_key)
    finally:
        if claimed:
            release_generation(claim_key)


def _tts_synth_inner(text: str, voice: str, key: str, blob_key: str):
    import asyncio
    import edge_tts

    async def run():
        audio = bytearray()
        marks = []
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                # edge-tts geeft standaard SentenceBoundary (zin + tijd); offset in
                # 100ns-eenheden => /10.000 = ms. Zin-niveau is precies de goede
                # korrel voor een meeleesindicator (rustiger dan per woord).
                marks.append({"t": int(chunk["offset"] // 10000), "w": chunk.get("text", "")})
        return bytes(audio), marks

    data, marks = asyncio.run(run())
    if not data:
        raise RuntimeError("lege audio")
    cache_store.put_blob("tts_cache", blob_key, data, "audio/mpeg")
    cache_store.put_json("tts_marks", key, {"marks": marks})
    audio_path = cache_store.blob_local_path("tts_cache", blob_key)
    return audio_path, marks


@router.post("/tts")
def tts_speak(req: TTSRequest, request: Request = None):
    auth.require_user(request)
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        raise_api_error(501, "TTS_UNAVAILABLE",
                        "edge-tts is niet geïnstalleerd (pip install edge-tts).")
    text = req.text.strip()
    if not text:
        raise_api_error(400, "TTS_EMPTY", "Geen tekst om voor te lezen.")
    try:
        audio_path, _ = _tts_synth(text, _tts_voice(req.language))
    except Exception as e:
        logger.warning("TTS mislukt: %s", str(e)[:200])
        raise_api_error(502, "TTS_FAILED", "Voorlezen is momenteel niet beschikbaar.", debug_reason(e))
    return FileResponse(audio_path, media_type="audio/mpeg",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.post("/tts-marks")
def tts_marks(req: TTSRequest, request: Request = None):
    auth.require_user(request)
    """Woord-tijdmarkeringen voor de meeleesindicator. Deelt de cache met /tts,
    dus dit genereert de audio hooguit één keer. Faalt zacht (lege lijst) zodat
    voorlezen altijd blijft werken, ook zonder highlight."""
    text = (req.text or "").strip()
    if not text:
        return {"ok": True, "marks": []}
    try:
        import edge_tts  # noqa: F401
        _, marks = _tts_synth(text, _tts_voice(req.language))
        return {"ok": True, "marks": marks}
    except Exception as e:
        logger.warning("TTS-marks mislukt: %s", str(e)[:200])
        return {"ok": True, "marks": []}




# =========================================================
# ZOEKEN OVER ALLE DOCUMENTEN
# =========================================================

@router.get("/search")
def search(q: str = Query(min_length=2), file_hash: Optional[str] = None, limit: int = Query(default=20, le=50),
           request: Request = None):
    terms = [t for t in re.split(r"\W+", q.lower()) if len(t) >= 2]
    if not terms:
        return {"ok": True, "results": []}

    uid = auth.require_user_id(request)
    hashes: list[str]
    if file_hash:
        ensure_document_exists(uid, file_hash)
        hashes = [file_hash]
    else:
        # Alleen lesmateriaal doorzoeken: opgaven en losse snel-foto's horen niet
        # tussen de dia-resultaten (een expliciete file_hash blijft wél werken).
        hashes = [h for h in user_document_hashes(uid) if is_material(load_meta(uid, h))]

    results = []
    for h in hashes:
        meta = load_meta(uid, h)
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
