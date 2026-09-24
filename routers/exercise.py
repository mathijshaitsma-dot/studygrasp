"""Router: exercise. Opgaven koppelen aan een college en de juiste dia's vinden.
Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)

router = APIRouter()




@router.get("/exercises/{source_file_hash}")
def list_exercises(source_file_hash: str):
    """De opgaven die aan dit college gekoppeld zijn, nieuwste eerst."""
    ensure_document_exists(source_file_hash)
    items = []
    for path in META_DIR.glob("*.json"):
        meta = load_json(path)
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
def list_folder_exercises(folder_id: str):
    """Vak-brede opgaven (gekoppeld aan de map, niet aan één college), nieuwste
    eerst. College-specifieke opgaven staan onder hun eigen college."""
    items = []
    for path in META_DIR.glob("*.json"):
        meta = load_json(path)
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
    meta = ensure_document_exists(req.exercise_hash)
    _, texts = get_document_texts(req.exercise_hash)
    total = int(meta.get("total_pages") or 0) or len(texts)

    cache_key = sha256_text("|".join([
        "exq", PROMPT_VERSION, req.exercise_hash, req.language.strip().lower(),
    ]))
    cached = cache_store.get_json("ai_cache", cache_key)
    if cached is not None:
        return {"ok": True, "questions": cached.get("questions", []), "cached": True}

    uid, plan = quota_gate(request)
    usage.record(uid, plan)

    parts: list[Any] = document_image_parts(req.exercise_hash, total)
    joined = clean_text("\n".join(texts))
    if joined:
        parts.append(text_part(f"Exercise text layer (if useful):\n{joined[:8000]}"))
    parts.append(text_part("Extract the individual questions from this exercise."))

    result: ExerciseQuestionSet = generate_structured(
        [Message(role="user", parts=parts)],
        build_exercise_parse_system(req.language),
        ExerciseQuestionSet,
    )
    questions = [{"id": i, "number": q.number, "text": q.text}
                 for i, q in enumerate(result.questions[:40]) if q.text.strip()]
    cache_store.put_json("ai_cache", cache_key, {"questions": questions})
    return {"ok": True, "questions": questions, "cached": False}




@router.post("/exercise/locate")
def exercise_locate(req: ExerciseLocateRequest, request: Request):
    """Vind de 1-3 dia's die uitleggen wat je nodig hebt voor deze opgave (of één
    specifieke deelvraag). Gecacht per vraag/pagina + zoekgebied. Faalt zacht
    (lege lijst) als er geen passend materiaal is — nooit een verzonnen pagina."""
    ensure_document_exists(req.exercise_hash)
    hashes = exercise_material_hashes(req.source_file_hash, req.folder_id, req.widen)
    if not hashes:
        return {"ok": True, "slides": [], "reason": "no-material"}

    # Zoekvraag: een specifieke (deel)vraag is het scherpst; anders de hele pagina.
    query = (req.question_text or "").strip()
    attach_image = False
    if query:
        ex_text = query
        key_part = "q:" + sha256_text(query)[:16]
    else:
        _, ex_texts = get_document_texts(req.exercise_hash)
        ex_text = clean_text(ex_texts[req.page_index]) if 0 <= req.page_index < len(ex_texts) else ""
        attach_image = len(ex_text) < 120  # weinig tekst (foto/handschrift) → afbeelding erbij
        key_part = "p:" + str(req.page_index)

    cache_key = sha256_text("|".join([
        "locate", PROMPT_VERSION, req.exercise_hash, key_part,
        "widen" if req.widen else "narrow", *hashes, req.language.strip().lower(),
    ]))
    cached = cache_store.get_json("ai_cache", cache_key)
    if cached is not None:
        return {"ok": True, "slides": cached.get("slides", []), "cached": True}

    uid, plan = quota_gate(request)
    usage.record(uid, plan)

    parts: list[Any] = []
    if attach_image:
        img = ensure_slide_image(req.exercise_hash, req.page_index, "ai")
        if img:
            parts.append(image_part(img))
    # De opgavetekst stuurt de voorselectie bij een dikke bron (boek).
    material = build_material_blocks(hashes, query=ex_text)
    parts.append(text_part(
        f"EXERCISE the student is stuck on:\n{ex_text or '(see the attached image)'}\n\n"
        f"LECTURE MATERIAL (search only here):\n{material}\n\n"
        "Which slides/pages explain what is needed to solve this exercise?"
    ))

    result: ExerciseLocateResult = generate_structured(
        [Message(role="user", parts=parts)],
        build_locate_system(req.language, multi=len(hashes) > 1),
        ExerciseLocateResult,
    )

    slides = []
    for s in result.slides[:3]:
        di = s.doc_index if 1 <= s.doc_index <= len(hashes) else 1
        fh = hashes[di - 1]
        pi = max(0, s.page - 1)
        m = load_meta(fh) or {}
        total = int(m.get("total_pages") or 0)
        if total and pi >= total:
            continue
        slides.append({
            "file_hash": fh,
            "page_index": pi,
            "file_name": m.get("file_name"),
            "why": s.why,
            "image_url": slide_image_url(fh, pi),
        })

    cache_store.put_json("ai_cache", cache_key, {"slides": slides})
    return {"ok": True, "slides": slides, "cached": False}




@router.post("/exercise/help")
def exercise_help(req: ExerciseHelpRequest, request: Request):
    """Begeleidende hulp bij een opgave: hints en deelstappen, niet meteen het
    hele antwoord — tenzij de student er expliciet om vraagt."""
    ensure_document_exists(req.exercise_hash)
    uid, plan = quota_gate(request)
    usage.record(uid, plan)

    query = (req.question_text or "").strip()
    attach_image = False
    if query:
        ex_text = query
    else:
        _, ex_texts = get_document_texts(req.exercise_hash)
        ex_text = clean_text(ex_texts[req.page_index]) if 0 <= req.page_index < len(ex_texts) else ""
        attach_image = len(ex_text) < 120

    parts: list[Any] = []
    if attach_image:
        img = ensure_slide_image(req.exercise_hash, req.page_index, "ai")
        if img:
            parts.append(image_part(img))

    hashes = exercise_material_hashes(req.source_file_hash, req.folder_id, req.widen)
    context = f"EXERCISE:\n{ex_text or '(see the attached image)'}\n"
    if req.question and req.question.strip():
        context += f"\nThe student asks: {req.question.strip()}\n"
    if hashes:
        context += f"\nLECTURE MATERIAL (for reference):\n{build_material_blocks(hashes, total_budget=9000, query=ex_text)}\n"
    parts.append(text_part(context + "\nHelp the student with the next step."))

    contents = [Message(role="user", parts=parts)]
    system_instruction = build_exercise_help_system(req.language)

    if req.stream:
        return StreamingResponse(
            stream_markdown(contents, system_instruction, None),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    markdown, model_name = generate_markdown(contents, system_instruction)
    return {"ok": True, "markdown": markdown, "model": model_name}
