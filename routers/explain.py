"""Router: explain. Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth
from core import _prefetch_pool

router = APIRouter()

# Wacht een verzoek op een generatie die al loopt (meestal de prefetch van
# dezelfde dia), dan wachtten we vroeger tot 180 seconden zonder één byte te
# sturen. De client zag al die tijd alleen "AI is een uitleg aan het maken".
# Nu wachten we in korte slices met een heartbeat ertussen: de verbinding blijft
# aantoonbaar levend voor de stall-waakhond van de client, en na DEDUP_WAIT
# geven we het wachten op en genereren we het gewoon zelf.
DEDUP_WAIT_SECONDS = 45
DEDUP_HEARTBEAT_SECONDS = 5




@router.post("/explain")
def explain(req: ExplainRequest, background_tasks: BackgroundTasks, request: Request):
    uid = auth.require_user_id(request)
    meta = ensure_document_exists(uid, req.file_hash)
    total_pages = int(meta.get("total_pages") or 0)
    if not total_pages:
        total_pages = len(get_document_texts(req.file_hash)[1])
    if req.page_index < 0 or req.page_index >= total_pages:
        raise_api_error(400, "INVALID_PAGE_INDEX", "Ongeldige page_index.",
                        {"page_index": req.page_index, "total_pages": total_pages})

    cache_key = explanation_cache_key_for(req)

    # Volgende dia's alvast genereren zodat doorklikken (bijna) instant voelt.
    # Alleen bij een normale uitleg, niet bij vervolgvragen in de chat.
    # (cache_only is slechts een polsing van de frontend — die mag geen nieuwe
    # generaties in gang zetten.)
    if not req.question and not req.history and not req.cache_only:
        background_tasks.add_task(prefetch_ahead, uid, req, total_pages)

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

    # Alleen-uit-cache: hierboven was er geen hit, dus stoppen vóór quota_gate.
    # Zo kan de frontend gratis polsen of een dia al klaarstaat (voor het
    # voorwarmen van de voorleesaudio) zonder per ongeluk een generatie te
    # starten of tegoed te verbruiken.
    if req.cache_only:
        return {"ok": True, "markdown": None, "cached": False}

    # Cache-miss → verse generatie: tegoed controleren en afschrijven.
    uid, plan = quota_gate(request)
    usage.record(uid, plan)

    prepared = prepare_explain_inputs(uid, req)

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
                        deadline = time.monotonic() + DEDUP_WAIT_SECONDS
                        while not event.wait(timeout=DEDUP_HEARTBEAT_SECONDS):
                            if time.monotonic() >= deadline:
                                break
                            yield sse_event({"type": "waiting"})
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
                event.wait(timeout=DEDUP_WAIT_SECONDS)
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




@router.post("/prefetch/{file_hash}/{page_index}")
def prefetch(
    file_hash: str,
    page_index: int,
    background_tasks: BackgroundTasks,
    request: Request,
    language: str = Query(default="auto"),
    detail_level: Literal["short", "normal", "long"] = Query(default="normal"),
    mode: Literal["explain", "simple", "study"] = Query(default="explain"),
    audience_level: Literal["beginner", "intermediate", "advanced"] = Query(default="intermediate"),
):
    """Genereer de uitleg van een dia alvast op de achtergrond, in een specifieke
    modus/niveau. Zo kan de frontend bv. de Kernpunten-versie (mode=study) van de
    huidige dia vast warmen zodra de gebruiker die modus gebruikt — dan is
    omschakelen instant i.p.v. seconden wachten."""
    # Eigen, ruime IP-noodrem in een aparte bucket: begrenst speculatief warmen
    # zonder van het quotum af te schrijven en zonder de échte /explain-aanvragen
    # van dezelfde gebruiker te verdringen. Zacht falen (geen 429): prefetch is
    # best-effort, dus we laten de klik gewoon zelf genereren als het te druk is.
    client_ip = request.client.host if request is not None and request.client else "unknown"
    if not rate_limit.check(f"prefetch:{client_ip}", max_per_window=PREFETCH_RATE_MAX_PER_MIN):
        return {"ok": True, "prefetched": False, "reason": "rate-limited"}

    ensure_document_exists(auth.require_user_id(request), file_hash)
    _, texts = get_document_texts(file_hash)
    if page_index < 0 or page_index >= len(texts):
        return {"ok": True, "prefetched": False, "reason": "buiten bereik"}

    base_req = ExplainRequest(
        file_hash=file_hash, page_index=page_index,
        language=language, detail_level=detail_level,
        mode=mode, audience_level=audience_level, stream=False,
    )
    background_tasks.add_task(prefetch_one_page, auth.require_user_id(request), base_req, page_index)
    return {"ok": True, "prefetched": True, "page_index": page_index}




@router.post("/summary")
def summarize(req: SummaryRequest, request: Request):
    uid = auth.require_user_id(request)
    ensure_document_exists(uid, req.file_hash)
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
    meta = load_meta(uid, req.file_hash) or {}
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




@router.post("/ask-region")
def ask_region(req: RegionAskRequest, request: Request):
    uid = auth.require_user_id(request)
    ensure_document_exists(uid, req.file_hash)
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
- If the marked region is a QUESTION, EXERCISE or CALCULATION: solve it COMPLETELY and end with the final answer. This is the most important rule:
  * Actually carry out every step to the end — do the substitutions, the algebra and the arithmetic. NEVER stop at "now substitute and compute" or "by symmetry you can double it": perform that computation and reach the concrete final result.
  * Lay it out as numbered steps. Each step shows the real math (the expressions and how they transform), with a short reason why. Show the intermediate algebra so it is easy to follow.
  * State the final answer on its own line, in bold.
  * Be efficient — the math itself does the teaching: no filler, no restating the question, no long meta-commentary.
- Otherwise (a concept, term, formula or figure to explain): be compact and didactic — answer first, then a short explanation (usually 2-8 sentences).
- Math in LaTeX ($...$ / $$...$$). Be precise about what is visually there; do not guess. If something needed is missing, state the assumption briefly and continue to the final answer.
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
