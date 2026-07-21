"""Router: exam. Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)

router = APIRouter()




@router.post("/exam/generate")
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




@router.post("/exam/attempt")
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




@router.get("/exam/plan")
def exam_plan(file_hash: Optional[str] = None, folder_id: Optional[str] = None):
    scope_id, _, scope_name = exam_scope(file_hash, folder_id)
    data = load_exam_data(scope_id)
    return {"ok": True, "scope": scope_id, "scope_name": scope_name,
            "attempts": data["attempts"], "plan": build_review_plan(data)}
