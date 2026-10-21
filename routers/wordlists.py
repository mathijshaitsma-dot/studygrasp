"""Router: wordlists. Endpoints; gedeelde logica komt uit core."""
import re

from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth
from core import _seed_cards, _wordlist_public, _wordlists_lock

router = APIRouter()


_WORDLIST_PREFIX = re.compile(r"^\s*(?:[-*•]\s+|\d{1,3}[.)]\s*)")
_WORDLIST_SEPARATORS = (
    re.compile(r"\s*\t+\s*"),
    re.compile(r"\s+[—–·]\s+"),
    re.compile(r"\s+-\s+"),
    re.compile(r"\s*:\s+"),
)


def _explicit_wordlist_pairs(texts: list[str], limit: int) -> list[VocabPair]:
    """Lees een bestaande term–definitielijst zonder verlies via een AI-call.

    PDF-, Word- en PowerPoint-extractie bewaart rijen meestal als losse regels.
    We accepteren de gangbare kolomscheidingstekens, maar eisen minstens drie
    geldige paren zodat gewone lopende tekst niet per ongeluk een woordenlijst
    wordt. Dubbelen worden hoofdletterongevoelig samengevoegd.
    """
    pairs: list[VocabPair] = []
    seen: set[str] = set()
    for raw in "\n".join(texts).splitlines():
        line = _WORDLIST_PREFIX.sub("", raw.strip())
        if not line or len(line) > 700:
            continue
        pieces = None
        for separator in _WORDLIST_SEPARATORS:
            candidate = separator.split(line, maxsplit=1)
            if len(candidate) == 2:
                pieces = candidate
                break
        if not pieces:
            continue
        term, definition = (part.strip(" \t-–—:;,.|") for part in pieces)
        if not term or not definition or len(term) > 120 or len(definition) > 560:
            continue
        if len(term.split()) > 14 or term.casefold() == definition.casefold():
            continue
        if term.casefold() in {"term", "begrip", "woord", "begrippenlijst"} and definition.casefold().startswith(
            ("definitie", "betekenis", "omschrijving", "vertaling")
        ):
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        pairs.append(VocabPair(term=term, definition=definition))
        if len(pairs) >= limit:
            break
    return pairs if len(pairs) >= 3 else []




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
    source_texts: list[str] = []
    if req.folder_id:
        folder = find_folder(uid, req.folder_id)
        if not folder:
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        hashes = folder_document_hashes(uid, req.folder_id)
        if not hashes:
            raise_api_error(400, "FOLDER_EMPTY", "Deze map bevat nog geen documenten.")
        material, parts = build_folder_material(uid, hashes)
        source_name = folder["name"]
        source_label = f"het hele vak {source_name} ({len(hashes)} documenten)"
    elif req.file_hash:
        meta = ensure_document_exists(uid, req.file_hash)
        hashes = [req.file_hash]
        _, source_texts = get_document_texts(req.file_hash)
        material, _, total_pages = build_document_digest(req.file_hash)
        parts: list[Any] = []
        if len(material) < 400:
            parts.extend(document_image_parts(req.file_hash, total_pages))
        source_name = meta.get("file_name", "document")
        source_label = f"{total_pages} pagina's"
    else:
        raise_api_error(400, "MISSING_SCOPE", "Geef een file_hash of folder_id op.")
    scope_context = study_scope_context(uid, hashes, source_name)
    if req.selection == "exam_essential":
        term_limit = 200
        amount_rule = (
            "Produce the COMPLETE set of exam-essential terms. Decide the number from the material: "
            "do not aim for or stop at an arbitrary target count. Include every concept the student "
            "truly needs to recognize, explain or apply, while excluding merely supportive wording "
            "and incidental details. Completeness applies to essential knowledge, not every noun."
        )
    elif req.amount_mode == "auto":
        term_limit = 200
        amount_rule = (
            "Create a BROAD learning glossary and decide the appropriate number of terms from the "
            "material itself; do not aim for or stop at an arbitrary target count. Include the "
            "exam-essential concepts plus all useful supporting terms that materially improve "
            "understanding, while still excluding trivia and redundant wording."
        )
    else:
        term_limit = req.max_terms
        amount_rule = (
            f"Produce at most {term_limit} pairs. Create a broad learning glossary: include "
            "exam-essential concepts plus useful supporting terms that make the material easier "
            "to understand, while still excluding trivia."
        )

    direct_pairs = _explicit_wordlist_pairs(source_texts, term_limit)
    if direct_pairs:
        result = VocabSet(pairs=direct_pairs)
    else:
        if request is not None:
            quota_gate(request, cost=2)
        parts.append(text_part(
            f"Materiaal ({source_label}):\n\n"
            f"{material if material else '(geen tekstlaag; gebruik de afbeeldingen)'}\n\n"
            "Haal hier nu de te leren begrippen uit als term/definitie-paren."
        ))

        system_instruction = f"""You extract a VOCABULARY / TERM LIST from study material for memorization.

{academic_relevance_rules(scope_context)}

RULES
- {amount_rule}
- Each pair: term = the word/concept to learn, definition = its short meaning or translation.
- If the material is a bilingual word list (e.g. a language course), keep both columns: term = the foreign word, definition = the translation. Read EVERY row of the list.
- If the material is subject matter (biology, law, ...), pick the key terms and give a crisp definition each.
- Keep definitions short (one line). No cards about layout, agenda or metadata.
- Math in LaTeX ($...$) where needed.
- {language_rule_for(req.language)}

Return only JSON matching the schema."""

        result = generate_structured(
            [Message(role="user", parts=parts)], system_instruction, VocabSet,
        )

    now = time.time()
    srs: dict[str, Any] = {}
    cards, next_id = _seed_cards(
        [{"term": p.term, "definition": p.definition} for p in result.pairs[:term_limit]], 0, srs, now)
    if not cards:
        raise_api_error(422, "NO_TERMS_FOUND", "Geen begrippen gevonden in dit materiaal.")

    name = (req.name or "").strip() or f"Begrippenlijst — {source_name}"
    list_id = sha256_text(f"{name}|{now}")[:12]
    wl = {"id": list_id, "name": name, "owner_id": request_user_id(request),
          "language": req.language, "created_at": now, "next_id": next_id,
          "cards": cards, "srs": srs}
    with _wordlists_lock:
        save_wordlist(uid, wl)
    return {"ok": True, "wordlist": _wordlist_public(wl)}
