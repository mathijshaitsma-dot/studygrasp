"""Router: documents. Endpoints; gedeelde logica komt uit core."""
from fastapi import APIRouter, File, Form, UploadFile, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import *  # noqa: F401,F403 (gedeelde helpers/modellen/config)
import auth
from core import _document_texts_cached

router = APIRouter()




# =========================================================
# ROUTES: UPLOAD / DOCUMENT / AFBEELDING
# =========================================================

@router.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...), kind: Optional[str] = Form(default=None),
                 folder_id: Optional[str] = Form(default=None),
                 source_file_hash: Optional[str] = Form(default=None),
                 background_tasks: BackgroundTasks = None, request: Request = None):
    start = time.perf_counter()

    client_ip = request.client.host if request is not None and request.client else "unknown"
    if not rate_limit.check(f"upload:{client_ip}", max_per_window=RATE_LIMIT_UPLOAD_MAX_PER_MIN):
        raise_api_error(429, "RATE_LIMITED", "Te veel uploads kort na elkaar — even wachten.", {})

    identity = auth.require_user(request)
    uid = identity["id"]
    if identity.get("guest"):
        guest_limit = max(1, int(os.getenv("GUEST_MAX_DOCUMENTS", "3")))
        if len(user_document_hashes(uid)) >= guest_limit:
            raise_api_error(
                403, "LOGIN_REQUIRED",
                f"Je kunt als gast maximaal {guest_limit} documenten proberen. Log in om ze te bewaren.",
            )
        if not rate_limit.check(f"guest-upload:{client_ip}", max_per_window=6, window_s=3600):
            raise_api_error(429, "RATE_LIMITED", "Te veel gastuploads — probeer het later opnieuw.")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise_api_error(
            400, "UNSUPPORTED_FILE_TYPE",
            "Dit bestandstype wordt niet ondersteund.",
            {"supported": sorted(SUPPORTED_SUFFIXES.keys())},
        )

    # Lees begrensd in plaats van het hele request ineens in het geheugen. Een
    # aanvaller kan anders een bestand van meerdere GB sturen en pas ná het
    # inlezen onze MAX_UPLOAD_MB-controle raken.
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    chunks = bytearray()
    while True:
        chunk = await file.read(min(1024 * 1024, max_bytes + 1 - len(chunks)))
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > max_bytes:
            raise_api_error(
                413, "FILE_TOO_LARGE", f"Bestand is groter dan de limiet van {MAX_UPLOAD_MB}MB.",
                {"max_mb": MAX_UPLOAD_MB},
            )
    file_bytes = bytes(chunks)
    if not file_bytes:
        raise_api_error(400, "EMPTY_FILE", "Leeg bestand ontvangen.")
    if not file_signature_ok(suffix, file_bytes):
        raise_api_error(
            400, "FILE_CONTENT_MISMATCH",
            "De inhoud van het bestand komt niet overeen met het bestandstype. "
            "Sla het opnieuw op als een echt PDF/PowerPoint/Word-bestand of afbeelding.",
        )

    file_hash = sha256_bytes(file_bytes)
    saved_path = UPLOAD_DIR / f"{file_hash}{suffix}"
    if not saved_path.exists():
        saved_path.write_bytes(file_bytes)
    # Best-effort backup van het brondocument zelf (niet alleen de afgeleide
    # cache) — zonder dit is een geüpload college weg zodra de lokale schijf
    # het is (deploy, schijfcorruptie, ...).
    cache_store.put_blob("uploads", f"{file_hash}{suffix}", file_bytes, file.content_type or "application/octet-stream")

    file_type = SUPPORTED_SUFFIXES[suffix]
    note: Optional[str] = None
    status: DocStatus = "ready"

    if file_type == "pptx":
        # De echte conversie gebeurt ná de response in post_upload_processing,
        # zodat de upload niet op LibreOffice hoeft te wachten (tekst-extractie
        # via python-pptx heeft de PDF niet nodig).
        if not find_libreoffice_executable():
            status = "partial"
            note = (
                "De PowerPoint kon niet naar afbeeldingen worden omgezet (LibreOffice niet beschikbaar). "
                "De AI werkt nu alleen op tekst; grafieken en figuren worden niet gezien."
            )
    elif file_type == "ppt":
        # Het klassieke binaire formaat heeft geen python-pptx-tekstfallback.
        # Converteer daarom meteen; de resulterende PDF levert zowel de tekst
        # als de dia-afbeeldingen voor alle bestaande vervolgfuncties.
        if not convert_office_to_pdf(saved_path, file_hash):
            saved_path.unlink(missing_ok=True)
            raise_api_error(
                500, "POWERPOINT_CONVERSION_FAILED",
                "De oude PowerPoint kon niet worden omgezet (LibreOffice is hiervoor nodig).",
            )
    elif file_type == "docx":
        if not convert_office_to_pdf(saved_path, file_hash):
            saved_path.unlink(missing_ok=True)
            raise_api_error(
                500, "DOCX_CONVERSION_FAILED",
                "Het Word-document kon niet worden omgezet (LibreOffice is hiervoor nodig).",
            )
    elif file_type == "image":
        if not image_to_pdf(saved_path, file_hash):
            saved_path.unlink(missing_ok=True)
            raise_api_error(500, "IMAGE_CONVERSION_FAILED", "De afbeelding kon niet worden verwerkt.")

    try:
        texts = extract_texts_for(file_type, saved_path, file_hash)
    except Exception as e:
        logger.warning("Tekst-extractie mislukt voor %s: %s", file_hash[:12], str(e)[:300])
        raise_api_error(500, "TEXT_EXTRACTION_FAILED", "De tekst kon niet uit het bestand worden gelezen.", debug_reason(e))

    save_json(text_cache_path(file_hash), {"file_type": file_type, "texts": texts})

    total_pages = len(texts)
    # Dezelfde bytes kunnen ook in de bibliotheek van iemand anders zitten; we
    # kijken hier uitsluitend in de eigen bibliotheek, zodat een re-upload je
    # eigen mapindeling en voortgang behoudt zonder iets van een ander te raken.
    existing_meta = load_meta(uid, file_hash)
    now = time.time()
    save_meta(uid, file_hash, {
        "file_hash": file_hash,
        "file_name": file.filename or f"{file_hash}{suffix}",
        "file_type": file_type,
        "total_pages": total_pages,
        "status": status,
        "note": note,
        # Een identieke herupload is geen tweede bibliotheekitem. Behoud de
        # oorspronkelijke uploaddatum en zet hem wel bovenaan bij 'ga verder',
        # omdat de gebruiker dit document zojuist opnieuw heeft gekozen.
        "uploaded_at": (existing_meta.get("uploaded_at") if existing_meta else None) or now,
        "owner_id": uid,
        # Blijft behouden bij een re-upload van identieke bytes. Een expliciete
        # folder_id bij de upload (bv. een opgave in een vakmap) wint.
        "folder_id": folder_id or (existing_meta.get("folder_id") if existing_meta else None),
        "last_page_index": existing_meta.get("last_page_index") if existing_meta else None,
        "last_opened_at": now,
        # "quick" = losse huiswerkfoto (snel-foto-flow); "exercise" = opgave/
        # oefententamen gekoppeld aan een college. Beide worden uit de gewone
        # documentenlijst gefilterd zodat die niet vervuilt.
        "kind": (kind or (existing_meta.get("kind") if existing_meta else None)),
        # Alleen voor opgaven: het college waar ze bij horen (bron voor "waar staat dit?").
        "source_file_hash": source_file_hash or (existing_meta.get("source_file_hash") if existing_meta else None),
    })

    # Op de achtergrond: dia's alvast renderen en de eerste uitleg(gen) alvast
    # genereren, zodat het openen van het document instant voelt.
    if background_tasks:
        background_tasks.add_task(post_upload_processing, uid, file_hash)

    label = page_label_for(file_type)
    pages = [
        PageInfo(
            index=i,
            label=f"{label} {i + 1}",
            image_ready=page_image_path(file_hash, i, "display").exists(),
            image_url=slide_image_url(file_hash, i),
            text_preview=(texts[i] or "")[:180],
        )
        for i in range(total_pages)
    ]

    logger.info("Upload %s klaar in %.2fs (%s pagina's)", file_hash[:12], time.perf_counter() - start, total_pages)

    # Alleen bij een echte foto: is hij scherp/licht genoeg om goed uit te leggen?
    image_quality = assess_image_quality(file_bytes) if file_type == "image" else None

    return UploadResponse(
        file_hash=file_hash,
        file_name=file.filename or f"{file_hash}{suffix}",
        file_type=file_type,
        total_pages=total_pages,
        status=status,
        note=note,
        deduplicated=existing_meta is not None,
        pages=pages,
        image_quality=image_quality,
    )




@router.get("/documents")
def list_documents(request: Request):
    """De documenten van de ingelogde gebruiker, nieuwste eerst."""
    uid = auth.require_user_id(request)
    documents = []
    for file_hash in user_document_hashes(uid):
        meta = load_meta(uid, file_hash)
        if not meta or not meta.get("file_hash"):
            continue
        documents.append({
            "file_hash": file_hash,
            "file_name": meta.get("file_name"),
            "file_type": meta.get("file_type"),
            "total_pages": meta.get("total_pages", 0),
            "status": meta.get("status", "uploaded"),
            "uploaded_at": meta.get("uploaded_at"),
            "last_opened_at": meta.get("last_opened_at", meta.get("uploaded_at")),
            "last_page_index": meta.get("last_page_index", 0),
            "folder_id": meta.get("folder_id"),
            "kind": meta.get("kind"),
            "thumbnail_url": slide_image_url(file_hash, 0),
        })
    documents.sort(key=lambda d: d.get("last_opened_at") or 0, reverse=True)
    return {"ok": True, "documents": documents}




@router.get("/document/{file_hash}")
def get_document(file_hash: str, request: Request):
    uid = auth.require_user_id(request)
    meta = ensure_document_exists(uid, file_hash)
    meta["last_opened_at"] = time.time()
    save_meta(uid, file_hash, meta)
    file_type, texts = get_document_texts(file_hash)
    label = page_label_for(file_type)

    pages = [
        PageInfo(
            index=i,
            label=f"{label} {i + 1}",
            image_ready=page_image_path(file_hash, i, "display").exists(),
            image_url=slide_image_url(file_hash, i),
            text_preview=(texts[i] or "")[:180],
        ).model_dump()
        for i in range(len(texts))
    ]

    return {
        "ok": True,
        "file_hash": file_hash,
        "file_name": meta.get("file_name"),
        "file_type": file_type,
        "total_pages": len(texts),
        "status": meta.get("status", "uploaded"),
        "note": meta.get("note"),
        "last_page_index": meta.get("last_page_index", 0),
        "folder_id": meta.get("folder_id"),
        "pages": pages,
    }




@router.get("/slide-image/{file_hash}/{page_index}")
def slide_image(
    file_hash: str,
    page_index: int,
    resolution: Literal["display", "ai", "normal", "high"] = Query(default="display"),
    request: Request = None,
):
    meta = ensure_document_exists(auth.require_user_id(request), file_hash)
    total_pages = int(meta.get("total_pages") or 0)
    if page_index < 0 or (total_pages and page_index >= total_pages):
        raise_api_error(400, "INVALID_PAGE_INDEX", "Ongeldige page_index.")

    # backwards-compatibel: normal->display, high->ai
    res: Resolution = "ai" if resolution in ("ai", "high") else "display"
    image_path = ensure_slide_image(file_hash, page_index, res)
    if not image_path:
        raise_api_error(404, "IMAGE_NOT_AVAILABLE", "Dia-afbeelding niet beschikbaar.")
    return FileResponse(
        image_path,
        media_type="image/jpeg",
        # Accountgebonden inhoud mag nooit in een gedeelde cache terechtkomen.
        # De URL is content-addressed, dus de privécache mag hem wel lang bewaren.
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "Vary": "Authorization",
        },
    )




@router.delete("/document/{file_hash}")
def delete_document(file_hash: str, request: Request = None):
    uid = auth.require_user_id(request)
    ensure_document_exists(uid, file_hash)

    # Eerst het accountitem zelf verwijderen. De oude code gebruikte hier
    # alleen `file_hash`, terwijl metadata accountgebonden als
    # `user_id__file_hash` wordt opgeslagen; daardoor kwam een verwijderd
    # document na het verversen gewoon terug.
    delete_meta(uid, file_hash)
    cache_store.delete_json("notes", user_key(uid, file_hash))
    cache_store.delete_json("study", user_key(uid, file_hash))

    # De zware bron- en renderbestanden zijn inhoudsgebaseerd en worden tussen
    # accounts gedeeld. Ruim ze alleen op wanneer werkelijk niemand dit bestand
    # nog in de bibliotheek heeft; anders zou verwijderen bij A het document van
    # B kapotmaken.
    removed_shared_data = document_reference_count(file_hash) == 0
    if removed_shared_data:
        for suffix in SUPPORTED_SUFFIXES:
            path = UPLOAD_DIR / f"{file_hash}{suffix}"
            if path.exists():
                path.unlink()
            cache_store.delete_blob("uploads", f"{file_hash}{suffix}")
        for directory in (IMAGE_DIR / file_hash, PDF_DIR / file_hash):
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
        text_cache_path(file_hash).unlink(missing_ok=True)
        # Eenmalige opruiming van eventuele pre-accountrecords uit oudere
        # versies; de huidige accountgebonden sleutels zijn hierboven gewist.
        for namespace in ("meta", "notes", "study"):
            cache_store.delete_json(namespace, file_hash)

    _document_texts_cached.cache_clear()
    # Uitleg-cache-keys zijn hashes zonder document-koppeling; losse cache-bestanden
    # zijn klein en onschadelijk, dus die laten we staan.
    return {"ok": True, "file_hash": file_hash, "removed_shared_data": removed_shared_data}




@router.get("/document/{file_hash}/notes")
def get_notes(file_hash: str, request: Request):
    uid = auth.require_user_id(request)
    ensure_document_exists(uid, file_hash)
    return {"ok": True, **load_notes_data(uid, file_hash)}




@router.post("/document/{file_hash}/notes")
def update_notes(file_hash: str, req: NoteUpdateRequest, request: Request):
    uid = auth.require_user_id(request)
    ensure_document_exists(uid, file_hash)
    data = load_notes_data(uid, file_hash)
    key = str(req.page_index)
    entry = data["pages"].setdefault(key, {})
    if req.note is not None:
        if req.note.strip():
            entry["note"] = req.note
        else:
            entry.pop("note", None)
    if req.star is not None:
        entry["star"] = req.star
    if req.unclear is not None:
        entry["unclear"] = req.unclear
    if not entry:
        data["pages"].pop(key, None)
    save_notes_data(uid, file_hash, data)
    return {"ok": True, "page_index": req.page_index, "entry": data["pages"].get(key, {})}




@router.post("/document/{file_hash}/progress")
def save_progress(file_hash: str, req: ProgressRequest, request: Request):
    uid = auth.require_user_id(request)
    meta = ensure_document_exists(uid, file_hash)
    meta["last_page_index"] = max(0, req.page_index)
    meta["last_opened_at"] = time.time()
    save_meta(uid, file_hash, meta)
    return {"ok": True, "last_page_index": meta["last_page_index"]}




@router.post("/document/{file_hash}/folder")
def document_set_folder(file_hash: str, req: DocumentFolderRequest, request: Request = None):
    uid = auth.require_user_id(request)
    meta = ensure_document_exists(uid, file_hash)
    if req.folder_id:
        if not find_folder(uid, req.folder_id):
            raise_api_error(404, "FOLDER_NOT_FOUND", "Map niet gevonden.")
        meta["folder_id"] = req.folder_id
    else:
        meta.pop("folder_id", None)
    save_meta(uid, file_hash, meta)
    return {"ok": True, "file_hash": file_hash, "folder_id": req.folder_id}
