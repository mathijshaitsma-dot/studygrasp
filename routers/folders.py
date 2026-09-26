"""Router: folders. Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth
from core import _folders_lock

router = APIRouter()




@router.get("/folders")
def folders_list(request: Request = None):
    uid = auth.require_user_id(request)
    counts: dict[str, int] = {}
    for file_hash in user_document_hashes(uid):
        fid = (load_meta(uid, file_hash) or {}).get("folder_id")
        if fid:
            counts[fid] = counts.get(fid, 0) + 1
    folders = [{**f, "document_count": counts.get(f["id"], 0)} for f in load_folders(uid)]
    folders.sort(key=lambda f: f.get("created_at") or 0)
    return {"ok": True, "folders": folders}




@router.post("/folders")
def folders_create(req: FolderRequest, request: Request = None):
    uid = auth.require_user_id(request)

    with _folders_lock:
        folders = load_folders(uid)
        folder = {
            "id": sha256_text(f"{req.name}|{time.time()}")[:12],
            "name": req.name.strip(),
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




@router.delete("/folders/{folder_id}")
def folders_delete(folder_id: str, request: Request = None):
    """Verwijdert alleen de map; de documenten blijven bestaan (zonder map)."""
    uid = auth.require_user_id(request)

    with _folders_lock:
        folders = load_folders(uid)
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if not folder:
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        save_folders(uid, [f for f in folders if f["id"] != folder_id])
    for file_hash in user_document_hashes(uid):
        meta = load_meta(uid, file_hash)
        if meta and meta.get("folder_id") == folder_id:
            meta.pop("folder_id", None)
            save_meta(uid, file_hash, meta)
    return {"ok": True, "folder_id": folder_id}




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
