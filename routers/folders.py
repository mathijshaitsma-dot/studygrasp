"""Router: folders. Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth
from core import _folders_lock

router = APIRouter()




@router.get("/folders")
def folders_list(request: Request = None):
    """Alle mappen plat, met hun plek in de boom erbij.

    `document_count` is wat er rechtstreeks in zit; `total_document_count` telt
    de submappen mee. De frontend heeft beide nodig: de kaartjes tonen de
    directe inhoud, de studeerknoppen gaan over alles eronder.
    """
    uid = auth.require_user_id(request)
    counts: dict[str, int] = {}
    for file_hash in user_document_hashes(uid):
        meta = load_meta(uid, file_hash) or {}
        fid = meta.get("folder_id")
        if fid and is_material(meta):
            counts[fid] = counts.get(fid, 0) + 1

    raw = load_folders(uid)
    by_id = {f["id"]: f for f in raw}
    folders = []
    for f in raw:
        # Een parent_id die niet (meer) bestaat zou de map onzichtbaar maken,
        # want hij hangt dan onder niets. Zulke mappen horen weer bovenin.
        parent_id = f.get("parent_id")
        if parent_id and parent_id not in by_id:
            parent_id = None
        subtree = folder_descendant_ids(uid, f["id"], raw)
        folders.append({
            **f,
            "parent_id": parent_id,
            "depth": folder_depth(uid, f["id"], raw),
            "document_count": counts.get(f["id"], 0),
            "total_document_count": sum(counts.get(x, 0) for x in subtree),
            "subfolder_count": sum(1 for x in raw if x.get("parent_id") == f["id"]),
        })
    folders.sort(key=lambda f: f.get("created_at") or 0)
    return {"ok": True, "folders": folders}




@router.post("/folders")
def folders_create(req: FolderRequest, request: Request = None):
    uid = auth.require_user_id(request)

    with _folders_lock:
        folders = load_folders(uid)
        parent_id = req.parent_id or None
        if parent_id:
            if not any(f["id"] == parent_id for f in folders):
                raise_api_error(404, "FOLDER_NOT_FOUND", "Bovenliggende map niet gevonden.")
            if folder_depth(uid, parent_id, folders) + 1 >= MAX_FOLDER_DEPTH:
                raise_api_error(400, "FOLDER_TOO_DEEP",
                                f"Mappen kunnen maximaal {MAX_FOLDER_DEPTH} niveaus diep.")
        folder = {
            "id": sha256_text(f"{req.name}|{time.time()}")[:12],
            "name": req.name.strip(),
            "parent_id": parent_id,
            "created_at": time.time(),
            "owner_id": request_user_id(request),
        }
        folders.append(folder)
        save_folders(uid, folders)
    return {"ok": True, "folder": folder}




@router.patch("/folders/{folder_id}")
def folders_rename(folder_id: str, req: FolderRequest, request: Request = None):
    uid = auth.require_user_id(request)

    with _folders_lock:
        folders = load_folders(uid)
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if not folder:
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        folder["name"] = req.name.strip()
        save_folders(uid, folders)
    return {"ok": True, "folder": folder}




@router.post("/folders/{folder_id}/parent")
def folders_move(folder_id: str, req: FolderParentRequest, request: Request = None):
    """Verplaatst een map naar een andere map (of naar bovenin met parent_id=null)."""
    uid = auth.require_user_id(request)

    with _folders_lock:
        folders = load_folders(uid)
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if not folder:
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")

        parent_id = req.parent_id or None
        if parent_id:
            if parent_id == folder_id:
                raise_api_error(400, "FOLDER_CYCLE", "Een map kan niet in zichzelf.")
            if not any(f["id"] == parent_id for f in folders):
                raise_api_error(404, "FOLDER_NOT_FOUND", "Bovenliggende map niet gevonden.")
            # In je eigen submap schuiven zou de map (en alles eronder) van de
            # boom losknippen: nergens meer bereikbaar, want de keten naar boven
            # is dan een kringetje.
            subtree = folder_descendant_ids(uid, folder_id, folders)
            if parent_id in subtree:
                raise_api_error(400, "FOLDER_CYCLE", "Een map kan niet in zijn eigen submap.")
            own_depth = folder_depth(uid, folder_id, folders)
            subtree_height = max(folder_depth(uid, x, folders) for x in subtree) - own_depth
            if folder_depth(uid, parent_id, folders) + 1 + subtree_height >= MAX_FOLDER_DEPTH:
                raise_api_error(400, "FOLDER_TOO_DEEP",
                                f"Mappen kunnen maximaal {MAX_FOLDER_DEPTH} niveaus diep.")

        folder["parent_id"] = parent_id
        save_folders(uid, folders)
    return {"ok": True, "folder": folder}




@router.delete("/folders/{folder_id}")
def folders_delete(folder_id: str, request: Request = None):
    """Verwijdert de map en zijn submappen; de documenten blijven bestaan
    (zonder map). Submappen laten staan zou ze bovenin laten opduiken alsof je
    ze daar zelf had neergezet — een vak weggooien hoort het hele vak te zijn,
    maar nooit je studiemateriaal."""
    uid = auth.require_user_id(request)

    with _folders_lock:
        folders = load_folders(uid)
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if not folder:
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        removed = set(folder_descendant_ids(uid, folder_id, folders))
        save_folders(uid, [f for f in folders if f["id"] not in removed])
    for file_hash in user_document_hashes(uid):
        meta = load_meta(uid, file_hash)
        if meta and meta.get("folder_id") in removed:
            meta.pop("folder_id", None)
            save_meta(uid, file_hash, meta)
    return {"ok": True, "folder_id": folder_id, "removed_folder_ids": sorted(removed)}




@router.get("/folders/{folder_id}/progress")
def folder_progress(folder_id: str, request: Request = None):
    """Voortgangsdashboard voor een heel vak: leunt op de al-cumulatieve
    tentamen-conceptmastery per folder-scope (zie exam_attempt/build_review_plan
    verderop) en telt daarnaast de flashcard-SRS van alle documenten in de map
    op. NB: als iemand een tentamen op los-documentniveau draait binnen deze map
    (i.p.v. met folder_id), valt die data hier buiten — dat gebruikt een eigen
    scope-sleutel (`doc:{hash}`) die niet wordt samengevoegd met de map-scope."""
    uid = auth.require_user_id(request)
    folder = find_folder(uid, folder_id)
    if not folder:
        raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")

    hashes = folder_document_hashes(uid, folder_id)

    exam_data = load_exam_data(f"folder:{folder_id}")
    plan = build_review_plan(exam_data)
    concepts_summary = {bucket: len(items) for bucket, items in plan.items()}
    weak_pool = plan["due_now"] + plan["tomorrow"] + plan["this_week"]
    weak_pool.sort(key=lambda c: c["mastery"])
    weak_concepts = weak_pool[:8]

    # mastery per document, afgeleid uit dezelfde cumulatieve concepten (elk
    # concept draagt zijn eigen file_hash mee sinds exam_attempt).
    per_doc_stats: dict[str, dict[str, int]] = {}
    for c in exam_data["concepts"].values():
        h = c.get("file_hash")
        if not h:
            continue
        s = per_doc_stats.setdefault(h, {"right": 0, "wrong": 0})
        s["right"] += c.get("right", 0)
        s["wrong"] += c.get("wrong", 0)

    now = time.time()
    fc_total = fc_due = fc_mastered = 0
    per_document = []
    for h in hashes:
        meta = load_meta(uid, h) or {}
        study_data = load_study_data(uid, h)
        for fset in study_data.get("sets", {}).values():
            context = study_scope_context(uid, [h], meta.get("file_name", "document"))
            if (fset.get("prompt_version") != FLASHCARD_PROMPT_VERSION
                    or fset.get("context_key") != sha256_text(context)):
                continue
            for card in fset.get("flashcards", []):
                fc_total += 1
                state = fset.get("srs", {}).get(str(card["id"]), {})
                if state.get("due_at", now) <= now:
                    fc_due += 1
                if state.get("interval", 0) >= 14:
                    fc_mastered += 1

        stats = per_doc_stats.get(h)
        mastery_pct = None
        if stats and (stats["right"] + stats["wrong"]) > 0:
            mastery_pct = round(100 * stats["right"] / (stats["right"] + stats["wrong"]))
        per_document.append({
            "file_hash": h,
            "file_name": meta.get("file_name", "document"),
            "mastery_pct": mastery_pct,
        })

    return {
        "ok": True,
        "folder": folder,
        "concepts": concepts_summary,
        "weak_concepts": weak_concepts,
        "flashcards": {"total": fc_total, "due_now": fc_due, "mastered": fc_mastered},
        "per_document": per_document,
        "attempts": exam_data["attempts"][-10:],
    }


@router.get("/folders/{folder_id}/flashcards")
def folder_flashcards(folder_id: str, language: str = Query(default="auto"), request: Request = None):
    """Alle flashcards van een heel vak bij elkaar, submappen meegeteld.

    Bewust alleen samenvoegen, geen eigen opslag: de kaarten en hun
    herhaalplanning blijven van het document waar ze bij horen. Zo telt een
    beurt hier gewoon mee in de kaartjes van dat ene college, en andersom.
    Elke kaart draagt daarom zijn `file_hash` mee, zodat de frontend de beurt
    naar het juiste document terugstuurt.
    """
    uid = auth.require_user_id(request)
    folder = find_folder(uid, folder_id)
    if not folder:
        raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")

    now = time.time()
    cards, due_count = [], 0
    documents = []
    for h in folder_document_hashes(uid, folder_id):
        meta = load_meta(uid, h) or {}
        fset = flashcard_set(load_study_data(uid, h), language)
        context = study_scope_context(uid, [h], meta.get("file_name", "document"))
        current_cards = (fset["flashcards"]
                         if (fset.get("prompt_version") == FLASHCARD_PROMPT_VERSION
                             and fset.get("context_key") == sha256_text(context)) else [])
        documents.append({
            "file_hash": h,
            "file_name": meta.get("file_name", "document"),
            "card_count": len(current_cards),
        })
        for card in current_cards:
            state = fset["srs"].get(str(card["id"]), {})
            due_at = state.get("due_at", now)
            is_due = due_at <= now
            if is_due:
                due_count += 1
            cards.append({**card, "file_hash": h, "file_name": meta.get("file_name", "document"),
                          "due_at": due_at, "is_due": is_due,
                          "interval_days": round(state.get("interval", 0.0), 2),
                          "reps": state.get("reps", 0)})

    # Oudste eerst binnen een document, documenten in collegevolgorde: zo loop
    # je het vak door zoals je het hebt gevolgd in plaats van kriskras.
    return {"ok": True, "folder": {"id": folder["id"], "name": folder["name"]},
            "cards": cards, "due_count": due_count, "total": len(cards),
            "documents": documents}




@router.post("/folder-summary")
def folder_summary(req: FolderSummaryRequest, request: Request):
    """Samenvatting over een heel vak: alle colleges in de map en zijn
    submappen samen, niet per document. Dezelfde stof als het oefententamen
    (build_folder_material), zodat de twee nooit uit elkaar lopen."""
    uid = auth.require_user_id(request)
    folder = find_folder(uid, req.folder_id)
    if not folder:
        raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
    hashes = folder_document_hashes(uid, req.folder_id)
    if not hashes:
        raise_api_error(400, "FOLDER_EMPTY", "Deze map bevat nog geen documenten.")

    # De cachesleutel gaat over de documenten, niet over het map-id: hernoem je
    # de map of verplaats je hem, dan blijft dezelfde samenvatting geldig. Komt
    # er een college bij, dan verandert de sleutel vanzelf.
    cache_key = sha256_text("|".join([
        "folder-summary", PROMPT_VERSION, *hashes, req.language.strip().lower(),
    ]))

    cached = None if req.force_refresh else load_explanation_cache(cache_key)
    quota_gate(request, cost=3, unlock_key=f"folder-summary:{cache_key}",
               force=req.force_refresh)
    if cached and cached.get("markdown"):
        if req.stream:
            def cached_stream():
                yield sse_event({"type": "delta", "text": cached["markdown"]})
                yield sse_event({"type": "done", "model": cached.get("model"), "cached": True})
            return StreamingResponse(cached_stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        return {"ok": True, "markdown": cached["markdown"], "model": cached.get("model"), "cached": True}

    material, parts = build_folder_material(uid, hashes)
    parts.append(text_part(
        f"Vak: {folder['name']} ({len(hashes)} documenten).\n\n"
        f"{material if material.strip() else '(geen tekstlaag; gebruik de afbeeldingen)'}\n\n"
        "Schrijf nu één samenhangende studiesamenvatting over dit hele vak. "
        "Behandel de documenten als onderdelen van hetzelfde vak: leg de rode draad "
        "tussen de colleges, benoem waar begrippen terugkomen, en herhaal niets dubbel."
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
