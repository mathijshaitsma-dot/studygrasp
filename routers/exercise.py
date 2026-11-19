"""Router: exercise. Opgaven koppelen aan een college en de juiste dia's vinden.
Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth

router = APIRouter()


def _exercise_page_context(exercise_hash: str, page_index: int, question_text: Optional[str]) -> tuple[str, list[Any]]:
    """De gekozen vraag mét de echte opdrachtpagina en omliggende casustekst."""
    _, texts = get_document_texts(exercise_hash)
    page_text = clean_text(texts[page_index]) if 0 <= page_index < len(texts) else ""
    header_text = clean_text(texts[0]) if texts and page_index > 0 else ""
    previous_text = clean_text(texts[page_index - 1]) if 0 < page_index < len(texts) else ""
    question = clean_text(question_text or "")
    if question:
        text = f"SPECIFIC QUESTION:\n{question}\n\nFULL CONTEXT ON EXERCISE PAGE {page_index + 1}:\n{page_text}"
    else:
        text = f"FULL EXERCISE PAGE {page_index + 1}:\n{page_text}"
    if previous_text:
        text += f"\n\nPREVIOUS EXERCISE PAGE (case context only):\n{truncate(previous_text, 3000)}"
    if header_text and header_text != previous_text:
        text += f"\n\nDOCUMENT HEADER / COURSE REFERENCES FROM PAGE 1:\n{truncate(header_text, 2500)}"
    parts: list[Any] = [text_part(f"[EXERCISE PAGE {page_index + 1}; page_index={page_index}]\n{text}")]
    image = ensure_slide_image(exercise_hash, page_index, "ai")
    if image:
        parts.append(image_part(image))
    if page_index > 0:
        previous_image = ensure_slide_image(exercise_hash, page_index - 1, "ai")
        if previous_image:
            parts.append(text_part(f"[PREVIOUS EXERCISE PAGE {page_index}; case context only]"))
            parts.append(image_part(previous_image))
    search_text = exercise_search_query(question, page_text + "\n" + previous_text, texts[0] if texts else "")
    return search_text, parts




@router.get("/exercises/{source_file_hash}")
def list_exercises(source_file_hash: str, request: Request = None):
    """De opgaven die aan dit college gekoppeld zijn, nieuwste eerst."""
    uid = auth.require_user_id(request)
    ensure_document_exists(uid, source_file_hash)
    items = []
    for file_hash in user_document_hashes(uid):
        meta = load_meta(uid, file_hash)
        if not meta or meta.get("kind") != "exercise":
            continue
        if meta.get("source_file_hash") != source_file_hash:
            continue
        h = meta["file_hash"]
        items.append({
            "file_hash": h,
            "file_name": meta.get("file_name"),
            "file_type": meta.get("file_type"),
            "total_pages": meta.get("total_pages", 0),
            "uploaded_at": meta.get("uploaded_at"),
            "thumbnail_url": slide_image_url(h, 0),
        })
    items.sort(key=lambda d: d.get("uploaded_at") or 0, reverse=True)
    return {"ok": True, "exercises": items}




@router.get("/exercises-in-folder/{folder_id}")
def list_folder_exercises(folder_id: str, request: Request = None):
    """Vak-brede opgaven (gekoppeld aan de map, niet aan één college), nieuwste
    eerst. College-specifieke opgaven staan onder hun eigen college."""
    uid = auth.require_user_id(request)
    items = []
    for file_hash in user_document_hashes(uid):
        meta = load_meta(uid, file_hash)
        if not meta or meta.get("kind") != "exercise":
            continue
        if meta.get("folder_id") != folder_id or meta.get("source_file_hash"):
            continue
        h = meta["file_hash"]
        items.append({
            "file_hash": h,
            "file_name": meta.get("file_name"),
            "file_type": meta.get("file_type"),
            "total_pages": meta.get("total_pages", 0),
            "uploaded_at": meta.get("uploaded_at"),
            "thumbnail_url": slide_image_url(h, 0),
        })
    items.sort(key=lambda d: d.get("uploaded_at") or 0, reverse=True)
    return {"ok": True, "exercises": items}




@router.post("/exercise/questions")
def exercise_questions(req: ExerciseQuestionsRequest, request: Request):
    """Splits een opgave via vision in losse (deel)vragen, zodat 'waar staat dit?'
    en de hulp per vraag kunnen werken. Gecacht per opgave."""
    uid = auth.require_user_id(request)
    meta = ensure_document_exists(uid, req.exercise_hash)
    _, texts = get_document_texts(req.exercise_hash)
    total = int(meta.get("total_pages") or 0) or len(texts)

    cache_key = sha256_text("|".join([
        "exq", EXERCISE_PROMPT_VERSION, req.exercise_hash, req.language.strip().lower(),
    ]))
    quota_gate(request, cost=2, unlock_key=f"exercise-questions:{cache_key}")
    cached = cache_store.get_json("ai_cache", cache_key)
    if cached is not None:
        return {"ok": True, "questions": cached.get("questions", []), "cached": True}

    parts: list[Any] = []
    for page_index in range(total):
        page_text = clean_text(texts[page_index]) if page_index < len(texts) else ""
        parts.append(text_part(
            f"[EXERCISE PAGE {page_index + 1}; page_index={page_index}]\n"
            f"Text layer for this page:\n{exercise_readable_text_layer(page_text) or '(no reliable text layer; read the image)'}"
        ))
        image = ensure_slide_image(req.exercise_hash, page_index, "ai")
        if image:
            parts.append(image_part(image))
    parts.append(text_part("Extract the individual questions from this exercise."))

    result: ExerciseQuestionSet = generate_structured(
        [Message(role="user", parts=parts)],
        build_exercise_parse_system(req.language),
        ExerciseQuestionSet,
        candidate_mode="responsive",
    )
    questions = []
    seen: set[tuple[int, str, str]] = set()
    for q in result.questions[:120]:
        text = clean_text(q.text)
        page_index = min(max(0, q.page_index), max(0, total - 1))
        key = (page_index, normalize_exercise_text(q.number), normalize_exercise_text(text))
        if not text or key in seen or is_external_exercise_instruction(text):
            continue
        seen.add(key)
        questions.append({
            "id": len(questions), "number": q.number.strip(), "text": text,
            "page_index": page_index,
        })
    cache_store.put_json("ai_cache", cache_key, {"questions": questions})
    return {"ok": True, "questions": questions, "cached": False}




@router.post("/exercise/locate")
def exercise_locate(req: ExerciseLocateRequest, request: Request, background_tasks: BackgroundTasks = None):
    """Vind de 1-3 dia's die uitleggen wat je nodig hebt voor deze opgave (of één
    specifieke deelvraag). Gecacht per vraag/pagina + zoekgebied. Faalt zacht
    (lege lijst) als er geen passend materiaal is — nooit een verzonnen pagina."""
    started = time.perf_counter()
    uid = auth.require_user_id(request)
    ensure_document_exists(uid, req.exercise_hash)
    hashes = exercise_material_hashes(uid, req.source_file_hash, req.folder_id, req.widen)
    if not hashes:
        return {"ok": True, "slides": [], "reason": "no-material"}

    # De vraag stuurt de zoekrangschikking; de volledige juiste opdrachtpagina
    # blijft altijd als tekst én beeld aanwezig voor casus- en figuurcontext.
    key_part = "p:" + str(req.page_index) + ":q:" + sha256_text((req.question_text or "").strip())[:16]

    cache_key = sha256_text("|".join([
        "locate", EXERCISE_PROMPT_VERSION, req.exercise_hash, key_part,
        "widen" if req.widen else "narrow", *hashes, req.language.strip().lower(),
    ]))
    quota_gate(request, cost=1, unlock_key=f"exercise-locate:{cache_key}")
    cached = cache_store.get_json("ai_cache", cache_key)
    if cached is not None:
        return {"ok": True, "slides": cached.get("slides", []), "cached": True,
                "timings_ms": {"total": round((time.perf_counter() - started) * 1000)}}

    ex_text, exercise_parts = _exercise_page_context(req.exercise_hash, req.page_index, req.question_text)
    parts: list[Any] = list(exercise_parts)
    picked_pages = shortlist_pages(hashes, ex_text, uid)
    visual_question = bool(re.search(r"afbeeld|figuur|foto|echo|\bct\b|schets|diagram|image|figure", req.question_text or "", re.I))
    visual_parts, visual_pages = build_exercise_source_visual_parts(
        uid, hashes, ex_text, max_images=6, picked_pages=picked_pages,
        sparse_only=bool(req.question_text) and not visual_question,
    )
    parts.extend(visual_parts)
    # De opgavetekst stuurt de voorselectie bij een dikke bron (boek).
    material = build_material_blocks(uid, hashes, query=ex_text, picked_pages=picked_pages)
    parts.append(text_part(
        f"EXERCISE the student is stuck on:\n{ex_text or '(see the attached image)'}\n\n"
        f"LECTURE MATERIAL (search only here):\n{material}\n\n"
        "Which slides/pages explain what is needed to solve this exercise?"
    ))

    prepared_at = time.perf_counter()
    result: ExerciseLocateResult = generate_structured(
        [Message(role="user", parts=parts)],
        build_locate_system(req.language, multi=len(hashes) > 1),
        ExerciseLocateResult,
        candidate_mode="fast", attempt_timeout_s=8, total_timeout_s=22, max_attempts=6,
    )
    slides = validate_located_slides(uid, hashes, result, visual_pages)

    # Het resultaat is direct lokaal bruikbaar. Permanente opslag mag de
    # zichtbare reactie niet ophouden als Supabase tijdelijk traag is.
    payload = {"slides": slides}
    if background_tasks is not None:
        save_json(AI_CACHE_DIR / f"{cache_key}.json", payload)
        background_tasks.add_task(cache_store.put_json, "ai_cache", cache_key, payload)
    else:
        cache_store.put_json("ai_cache", cache_key, payload)
    ended = time.perf_counter()
    timings = {"prepare": round((prepared_at - started) * 1000),
               "ai_and_validation": round((ended - prepared_at) * 1000),
               "total": round((ended - started) * 1000)}
    logger.info("Exercise locate completed: %s ms, %s sources, %s slides", timings, len(hashes), len(slides))
    return {"ok": True, "slides": slides, "cached": False, "timings_ms": timings}




@router.post("/exercise/help")
def exercise_help(req: ExerciseHelpRequest, request: Request):
    """Begeleidende hulp bij een opgave: hints en deelstappen, niet meteen het
    hele antwoord — tenzij de student er expliciet om vraagt."""
    uid = auth.require_user_id(request)
    ensure_document_exists(uid, req.exercise_hash)
    quota_gate(request, cost=1)

    ex_text, parts = _exercise_page_context(req.exercise_hash, req.page_index, req.question_text)

    hashes = exercise_material_hashes(uid, req.source_file_hash, req.folder_id, req.widen)
    context = "Use the attached, explicitly labelled exercise page as the authoritative exercise context.\n"
    if req.question and req.question.strip():
        context += f"\nThe student asks: {req.question.strip()}\n"
    if hashes:
        context += f"\nLECTURE MATERIAL (for reference):\n{build_material_blocks(uid, hashes, total_budget=9000, query=ex_text)}\n"
    parts.append(text_part(context + "\nHelp the student with the next step."))

    contents = [Message(role="user", parts=parts)]
    system_instruction = build_exercise_help_system(req.language, req.mode)

    if req.stream:
        return StreamingResponse(
            stream_markdown(contents, system_instruction, None),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    markdown, model_name = generate_markdown(contents, system_instruction)
    return {"ok": True, "markdown": markdown, "model": model_name}
