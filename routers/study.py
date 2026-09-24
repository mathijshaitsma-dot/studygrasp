"""Router: study. Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
from core import _flashcards_generate_inner

router = APIRouter()




# request is optioneel: de prefetch roept deze functie ook intern aan (zonder
# HTTP-request en dus zonder quota — cache-warming telt niet tegen de gebruiker).
@router.post("/quiz/generate")
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




@router.post("/quiz/grade")
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
- error_type: ONLY when verdict is not "correct", classify the MAIN reason the answer went wrong as exactly one of:
  "concept" (misunderstood the underlying concept), "detail" (knew the idea but forgot an essential detail/condition/exception), "formula" (applied a formula or method incorrectly), "misread" (misread or misinterpreted what the question asked), "connection" (failed to connect/combine the relevant concepts), "other" (none of these fit). When verdict is "correct", set error_type to null.
- If a page image is attached, use it to verify the correct answer.
- {language_rule_for(req.language)}

Return only JSON matching the schema."""

    result: QuizGradeResult = generate_structured(
        [Message(role="user", parts=parts)], system_instruction, QuizGradeResult,
    )
    return {"ok": True, **result.model_dump()}




@router.post("/quiz/recovery")
def quiz_recovery(req: RecoveryRequest, request: Request):
    """Genereer een paar korte herstelvragen die precies het gemaakte fouttype
    aanpakken. Op verzoek (de student klikt 'oefen deze fout') en gecacht per
    concept + fouttype + dia, zodat dezelfde fout maar één keer tokens kost."""
    ensure_document_exists(req.file_hash)

    cache_key = sha256_text("|".join([
        "recovery", PROMPT_VERSION, req.file_hash, str(req.page_index),
        (req.concept or "").strip().lower(), req.error_type or "other",
        req.language.strip().lower(),
    ]))
    cached = cache_store.get_json("ai_cache", cache_key)
    if cached and cached.get("questions"):
        return {"ok": True, "questions": cached["questions"], "cached": True}

    uid, plan = quota_gate(request)
    usage.record(uid, plan)

    context = f"Concept being remediated: {req.concept or '—'}\n"
    if req.question:
        context += f"\nThe exam question the student got wrong:\n{req.question}\n"
    if req.model_answer:
        context += f"\nCorrect / model answer:\n{req.model_answer}\n"
    if req.student_answer:
        context += f"\nThe student's (wrong) answer:\n{req.student_answer}\n"
    # Bronmateriaal van de betreffende dia meegeven voor houvast en juistheid.
    try:
        _, texts = get_document_texts(req.file_hash)
        if req.page_index is not None and 0 <= req.page_index < len(texts):
            page_text = (texts[req.page_index] or "").strip()
            if page_text:
                context += f"\nRelevant source material (page {req.page_index + 1}):\n{page_text[:4000]}\n"
    except Exception:
        pass

    parts = [text_part(context + "\nCreate the recovery questions now.")]
    result: QuizSet = generate_structured(
        [Message(role="user", parts=parts)],
        build_recovery_system(req.language, req.error_type),
        QuizSet,
    )

    questions = []
    for i, q in enumerate(result.questions[:3]):
        item = q.model_dump()
        item["id"] = i
        if item.get("page_index") is None:
            item["page_index"] = req.page_index
        questions.append(item)

    cache_store.put_json("ai_cache", cache_key, {"questions": questions, "created_at": time.time()})
    return {"ok": True, "questions": questions, "cached": False}




# request optioneel: ook intern aangeroepen door de prefetch (geen quota).
@router.post("/flashcards/generate")
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




@router.get("/flashcards/{file_hash}")
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




@router.post("/flashcards/review")
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
