"""Router: wordlists. Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth
from core import _seed_cards, _wordlist_public, _wordlists_lock

router = APIRouter()




@router.get("/wordlists")
def wordlists_list(request: Request = None):
    uid = auth.require_user_id(request)
    lists = sorted(load_wordlist_index(uid), key=lambda e: e.get("created_at") or 0, reverse=True)
    return {"ok": True, "wordlists": lists}




@router.post("/wordlists")
def wordlists_create(req: WordlistCreateRequest, request: Request = None):
    uid = auth.require_user_id(request)
    now = time.time()
    list_id = sha256_text(f"{req.name}|{now}")[:12]
    srs: dict[str, Any] = {}
    cards, next_id = _seed_cards([c.model_dump() for c in req.cards], 0, srs, now)
    wl = {"id": list_id, "name": req.name.strip(), "owner_id": request_user_id(request),
          "language": req.language, "created_at": now, "next_id": next_id,
          "cards": cards, "srs": srs}
    with _wordlists_lock:
        save_wordlist(uid, wl)
    return {"ok": True, "wordlist": _wordlist_public(wl)}




@router.get("/wordlists/{list_id}")
def wordlists_get(list_id: str, request: Request = None):
    uid = auth.require_user_id(request)
    wl = load_wordlist(uid, list_id)
    if not wl:
        raise_api_error(404, "WORDLIST_NOT_FOUND", "Woordenlijst niet gevonden.")
    return {"ok": True, "wordlist": _wordlist_public(wl)}




@router.patch("/wordlists/{list_id}")
def wordlists_update(list_id: str, req: WordlistUpdateRequest, request: Request = None):
    uid = auth.require_user_id(request)
    with _wordlists_lock:
        wl = load_wordlist(uid, list_id)
        if not wl:
            raise_api_error(404, "WORDLIST_NOT_FOUND", "Woordenlijst niet gevonden.")
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
        save_wordlist(uid, wl)
    return {"ok": True, "wordlist": _wordlist_public(wl)}




@router.delete("/wordlists/{list_id}")
def wordlists_delete(list_id: str, request: Request = None):
    uid = auth.require_user_id(request)
    with _wordlists_lock:
        wl = load_wordlist(uid, list_id)
        if not wl:
            raise_api_error(404, "WORDLIST_NOT_FOUND", "Woordenlijst niet gevonden.")
        cache_store.delete_json("wordlists", user_key(uid, list_id))
        save_wordlist_index(uid, [e for e in load_wordlist_index(uid) if e["id"] != list_id])
    return {"ok": True, "id": list_id}




@router.post("/wordlists/{list_id}/review")
def wordlists_review(list_id: str, req: WordlistReviewRequest, request: Request = None):
    uid = auth.require_user_id(request)

    with _wordlists_lock:
        wl = load_wordlist(uid, list_id)
        if not wl:
            raise_api_error(404, "WORDLIST_NOT_FOUND", "Woordenlijst niet gevonden.")
        if not any(c["id"] == req.card_id for c in wl.get("cards", [])):
            raise_api_error(404, "CARD_NOT_FOUND", "Kaart niet gevonden.")
        new_state = apply_sm2(wl.get("srs", {}).get(str(req.card_id)), req.rating, time.time())
        wl.setdefault("srs", {})[str(req.card_id)] = new_state
        save_wordlist(uid, wl)
    return {"ok": True, "card_id": req.card_id, "next_due_at": new_state["due_at"],
            "interval_days": round(new_state["interval"], 2)}




@router.post("/wordlists/generate")
def wordlists_generate(req: WordlistGenerateRequest, request: Request = None):
    """AI haalt term/definitie-paren uit een geüpload document (werkt ook op een
    foto van een woordenlijst, want die is ook een 1-pagina-document)."""
    uid = auth.require_user_id(request)
    meta = ensure_document_exists(uid, req.file_hash)
    if request is not None:
        _, plan = quota_gate(request)
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
        save_wordlist(uid, wl)
    return {"ok": True, "wordlist": _wordlist_public(wl)}
