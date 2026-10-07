"""Router: folders. Endpoints; gedeelde logica komt uit core."""
import re
import secrets

from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth
from core import _folders_lock

router = APIRouter()

_SHARE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,100}$")


def _share_key(token: str) -> str:
    if not _SHARE_TOKEN_RE.fullmatch(token or ""):
        raise_api_error(404, "SHARE_NOT_FOUND", "Deze deel-link is ongeldig of niet meer beschikbaar.")
    return sha256_text(token)


def _active_share(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    record = cache_store.get_json("folder_shares", _share_key(token))
    if not record or record.get("revoked_at"):
        raise_api_error(410, "SHARE_REVOKED", "Deze deel-link is ingetrokken of niet meer beschikbaar.")
    folder = find_folder(record.get("owner_id", ""), record.get("folder_id", ""))
    if not folder:
        raise_api_error(410, "SHARE_UNAVAILABLE", "De gedeelde map bestaat niet meer.")
    return record, folder


def _share_preview(token: str) -> dict[str, Any]:
    record, folder = _active_share(token)
    owner_id, folder_id = record["owner_id"], record["folder_id"]
    hashes = folder_document_hashes(owner_id, folder_id)
    documents = []
    for file_hash in hashes:
        meta = load_meta(owner_id, file_hash) or {}
        documents.append({
            "file_name": meta.get("file_name", "document"),
            "total_pages": meta.get("total_pages", 0),
        })
    subtree = folder_descendant_ids(owner_id, folder_id)
    return {
        "name": folder.get("name", "Gedeelde map"),
        "document_count": len(documents),
        "subfolder_count": max(0, len(subtree) - 1),
        "documents": documents[:50],
        "created_at": record.get("created_at"),
    }




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


@router.post("/folders/{folder_id}/share")
def folder_share_create(folder_id: str, request: Request):
    """Maak één herbruikbare, intrekbare geheime link voor een map."""
    owner = auth.require_account(request)
    folder = find_folder(owner["id"], folder_id)
    if not folder:
        raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")

    index_key = sha256_text(f"{owner['id']}|{folder_id}")
    indexed = cache_store.get_json("folder_share_index", index_key) or {}
    token = indexed.get("token")
    if token:
        existing = cache_store.get_json("folder_shares", _share_key(token))
        if existing and not existing.get("revoked_at"):
            return {"ok": True, "token": token, "share": _share_preview(token), "existing": True}

    token = secrets.token_urlsafe(32)
    record = {
        "owner_id": owner["id"], "folder_id": folder_id,
        "created_at": time.time(), "created_by": owner["id"],
    }
    cache_store.put_json("folder_shares", _share_key(token), record)
    cache_store.put_json("folder_share_index", index_key, {"token": token})
    return {"ok": True, "token": token, "share": _share_preview(token), "existing": False}


@router.delete("/folders/{folder_id}/share")
def folder_share_revoke(folder_id: str, request: Request):
    owner = auth.require_account(request)
    if not find_folder(owner["id"], folder_id):
        raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
    index_key = sha256_text(f"{owner['id']}|{folder_id}")
    indexed = cache_store.get_json("folder_share_index", index_key) or {}
    token = indexed.get("token")
    if token:
        key = _share_key(token)
        record = cache_store.get_json("folder_shares", key)
        if record and record.get("owner_id") == owner["id"] and record.get("folder_id") == folder_id:
            record["revoked_at"] = time.time()
            record["revoked_by"] = owner["id"]
            cache_store.put_json("folder_shares", key, record)
    cache_store.delete_json("folder_share_index", index_key)
    return {"ok": True}


@router.get("/folder-shares/{token}")
def folder_share_get(token: str):
    return {"ok": True, "share": _share_preview(token)}


@router.post("/folder-shares/{token}/accept")
def folder_share_accept(token: str, request: Request):
    """Importeer een gedeeld vak in de eigen werkruimte.

    Alleen bronmetadata wordt gekopieerd. Notities, voortgang, SRS en
    tentamenresultaten blijven daardoor altijd accountgebonden.
    """
    recipient = auth.require_user(request)
    recipient_id = recipient["id"]
    record, root_folder = _active_share(token)
    owner_id, source_root = record["owner_id"], record["folder_id"]
    if recipient_id == owner_id:
        return {"ok": True, "folder_id": source_root, "imported_documents": 0,
                "already_present": 0, "own_folder": True}

    share_key = _share_key(token)
    source_folders = load_folders(owner_id)
    source_ids = folder_descendant_ids(owner_id, source_root, source_folders)
    source_by_id = {folder["id"]: folder for folder in source_folders if folder["id"] in source_ids}

    import_key = user_key(recipient_id, share_key)
    previous = cache_store.get_json("folder_share_imports", import_key) or {}
    folder_map = dict(previous.get("folder_map") or {})
    recipient_folders = load_folders(recipient_id)
    existing_ids = {folder["id"] for folder in recipient_folders}

    # Wanneer de ontvanger de eerder geïmporteerde hoofdmap zelf verwijderde,
    # maakt opnieuw openen een frisse kopie in plaats van naar een dood id te sturen.
    mapped_root = folder_map.get(source_root)
    if mapped_root not in existing_ids:
        folder_map = {}

    def destination_id(source_id: str) -> str:
        current = folder_map.get(source_id)
        if current and current in existing_ids:
            return current
        attempt = sha256_text(f"share|{share_key}|{recipient_id}|{source_id}")[:12]
        counter = 0
        while attempt in existing_ids:
            counter += 1
            attempt = sha256_text(f"share|{share_key}|{recipient_id}|{source_id}|{counter}")[:12]
        folder_map[source_id] = attempt
        existing_ids.add(attempt)
        return attempt

    for source_id in source_ids:
        source = source_by_id.get(source_id)
        if not source:
            continue
        dest_id = destination_id(source_id)
        current = next((folder for folder in recipient_folders if folder["id"] == dest_id), None)
        parent_source = source.get("parent_id") if source_id != source_root else None
        payload = {
            "id": dest_id, "name": source.get("name", "Gedeelde map"),
            "parent_id": folder_map.get(parent_source), "created_at": time.time(),
            "owner_id": recipient_id, "shared_from": share_key,
        }
        if current:
            current.update({"name": payload["name"], "parent_id": payload["parent_id"]})
        else:
            recipient_folders.append(payload)
    save_folders(recipient_id, recipient_folders)

    imported = already_present = 0
    for file_hash in folder_document_hashes(owner_id, source_root):
        source_meta = load_meta(owner_id, file_hash)
        if not source_meta or source_meta.get("folder_id") not in folder_map:
            continue
        existing = load_meta(recipient_id, file_hash)
        if existing and existing.get("shared_from") != share_key:
            already_present += 1
            continue
        copied = dict(source_meta)
        copied.update({
            "owner_id": recipient_id,
            "folder_id": folder_map[source_meta["folder_id"]],
            "shared_from": share_key,
            "shared_at": time.time(),
        })
        for field in ("last_page_index", "last_opened_at"):
            copied.pop(field, None)
        save_meta(recipient_id, file_hash, copied)
        imported += 1

    cache_store.put_json("folder_share_imports", import_key, {
        "share_key": share_key, "folder_map": folder_map,
        "root_folder_id": folder_map[source_root], "updated_at": time.time(),
    })
    return {
        "ok": True, "folder_id": folder_map[source_root],
        "imported_documents": imported, "already_present": already_present,
        "own_folder": False,
    }




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
    # Opgeslagen AI-overzichten blijven net als documenten behouden en gaan
    # terug naar het hoofdoverzicht wanneer hun map wordt verwijderd.
    for entry in list(load_saved_overview_index(uid)):
        if entry.get("folder_id") in removed:
            overview = load_saved_overview(uid, entry["id"])
            if overview:
                overview["folder_id"] = None
                overview["updated_at"] = time.time()
                save_saved_overview(uid, overview)
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
