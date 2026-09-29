"""Eenmalige, eigenaar-only migratie van de oude lokale StudyGrasp-opslag.

De back-up bevat bewust geen accounts, sessies, wachtwoorden of API-sleutels.
Alle accountgebonden records worden tijdens import opnieuw gekoppeld aan het
ingelogde eigenaaraccount. Zipleden worden nooit rechtstreeks uitgepakt: ieder
pad, iedere grootte en iedere documenthash wordt eerst gevalideerd.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile

import auth
import cache_store
from core import (
    BASE_DIR,
    SUPPORTED_SUFFIXES,
    UPLOAD_DIR,
    _document_texts_cached,
    raise_api_error,
    save_meta,
    user_key,
)

router = APIRouter()

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_MAX_ARCHIVE_BYTES = int(os.getenv("MIGRATION_MAX_ARCHIVE_MB", "180")) * 1024 * 1024
_MAX_UNCOMPRESSED_BYTES = int(os.getenv("MIGRATION_MAX_UNCOMPRESSED_MB", "260")) * 1024 * 1024
_MAX_ENTRIES = 2000
_MAX_JSON_BYTES = 5 * 1024 * 1024


def _require_owner(request: Request) -> dict:
    user = auth.require_user(request)
    if user.get("plan") != "owner":
        raise_api_error(403, "OWNER_REQUIRED", "Alleen de eigenaar kan een oude back-up importeren.")
    return user


def _read_json_member(zf: zipfile.ZipFile, name: str, *, max_bytes: int = _MAX_JSON_BYTES) -> dict:
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise_api_error(400, "INVALID_MIGRATION", f"Ontbrekend back-upbestand: {name}.")
    if info.file_size > max_bytes or info.flag_bits & 0x1:
        raise_api_error(400, "INVALID_MIGRATION", f"Ongeldig back-upbestand: {name}.")
    try:
        payload = json.loads(zf.read(info).decode("utf-8"))
    except Exception:
        raise_api_error(400, "INVALID_MIGRATION", f"Back-upbestand {name} bevat geen geldige JSON.")
    if not isinstance(payload, dict):
        raise_api_error(400, "INVALID_MIGRATION", f"Back-upbestand {name} heeft een ongeldig formaat.")
    return payload


def _safe_meta(meta: dict, uid: str, file_hash: str, suffix: str) -> dict:
    allowed = {
        "file_name", "file_type", "total_pages", "status", "note",
        "uploaded_at", "folder_id", "last_page_index", "last_opened_at",
        "kind", "source_file_hash",
    }
    clean = {key: meta.get(key) for key in allowed if key in meta}
    name = Path(str(clean.get("file_name") or f"{file_hash}{suffix}")).name[:255]
    clean.update({
        "file_hash": file_hash,
        "file_name": name,
        "file_type": SUPPORTED_SUFFIXES[suffix],
        "owner_id": uid,
        "status": clean.get("status") if clean.get("status") in {"uploaded", "processing", "ready", "partial", "failed"} else "ready",
    })
    try:
        clean["total_pages"] = max(1, int(clean.get("total_pages") or 1))
        clean["last_page_index"] = max(0, min(int(clean.get("last_page_index") or 0), clean["total_pages"] - 1))
    except (TypeError, ValueError):
        raise_api_error(400, "INVALID_MIGRATION", f"Ongeldige diametadata voor {name}.")
    return clean


def _import_upload(zf: zipfile.ZipFile, member: str, file_hash: str, suffix: str) -> None:
    expected = f"uploads/{file_hash}{suffix}"
    if member != expected:
        raise_api_error(400, "INVALID_MIGRATION", "Een documentpad in de back-up is ongeldig.")
    try:
        info = zf.getinfo(member)
    except KeyError:
        raise_api_error(400, "INVALID_MIGRATION", f"Document ontbreekt in de back-up: {member}.")
    if info.flag_bits & 0x1 or info.file_size > _MAX_UNCOMPRESSED_BYTES:
        raise_api_error(400, "INVALID_MIGRATION", f"Document in de back-up is ongeldig: {member}.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination = UPLOAD_DIR / f"{file_hash}{suffix}"
    temporary = destination.with_name(destination.name + ".migrating")
    digest = hashlib.sha256()
    written = 0
    try:
        with zf.open(info, "r") as source, temporary.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > _MAX_UNCOMPRESSED_BYTES:
                    raise_api_error(413, "MIGRATION_TOO_LARGE", "Een document in de back-up is te groot.")
                digest.update(chunk)
                target.write(chunk)
        if digest.hexdigest() != file_hash:
            raise_api_error(400, "INVALID_MIGRATION", f"De inhoudscontrole van {member} is mislukt.")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


@router.post("/owner/migration/import")
async def import_legacy_backup(request: Request, backup: UploadFile = File(...)):
    """Importeer een door ``tools/export_legacy_migration.py`` gemaakte zip.

    Herhaald importeren is veilig: documenten zijn content-addressed en de
    accountrecords worden met dezelfde sleutels bijgewerkt.
    """
    user = _require_owner(request)
    uid = user["id"]
    temp_dir = Path(BASE_DIR) / "migration_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{uid}.zip.uploading"

    received = 0
    try:
        with temp_path.open("wb") as target:
            while chunk := await backup.read(1024 * 1024):
                received += len(chunk)
                if received > _MAX_ARCHIVE_BYTES:
                    raise_api_error(413, "MIGRATION_TOO_LARGE", "De migratieback-up is te groot.")
                target.write(chunk)

        try:
            zf = zipfile.ZipFile(temp_path, "r")
        except (zipfile.BadZipFile, OSError):
            raise_api_error(400, "INVALID_MIGRATION", "Dit is geen geldige StudyGrasp-migratieback-up.")

        with zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ENTRIES or sum(item.file_size for item in infos) > _MAX_UNCOMPRESSED_BYTES:
                raise_api_error(413, "MIGRATION_TOO_LARGE", "De uitgepakte migratieback-up is te groot.")
            if len({item.filename for item in infos}) != len(infos):
                raise_api_error(400, "INVALID_MIGRATION", "De back-up bevat dubbele bestanden.")

            manifest = _read_json_member(zf, "manifest.json")
            if manifest.get("format") != "studygrasp-legacy-migration" or manifest.get("version") != 1:
                raise_api_error(400, "INVALID_MIGRATION", "Deze back-upversie wordt niet ondersteund.")

            documents = manifest.get("documents")
            if not isinstance(documents, list) or not documents or len(documents) > 200:
                raise_api_error(400, "INVALID_MIGRATION", "De documentenlijst in de back-up is ongeldig.")

            imported_docs = imported_notes = imported_study = imported_text = 0
            for item in documents:
                if not isinstance(item, dict):
                    raise_api_error(400, "INVALID_MIGRATION", "Een documentrecord is ongeldig.")
                file_hash = str(item.get("file_hash") or "").lower()
                suffix = str(item.get("suffix") or "").lower()
                if not _HASH_RE.fullmatch(file_hash) or suffix not in SUPPORTED_SUFFIXES:
                    raise_api_error(400, "INVALID_MIGRATION", "Een documenthash of bestandstype is ongeldig.")

                _import_upload(zf, str(item.get("upload_member") or ""), file_hash, suffix)
                save_meta(uid, file_hash, _safe_meta(item.get("meta") or {}, uid, file_hash, suffix))
                imported_docs += 1

                text_member = item.get("text_cache_member")
                if text_member:
                    expected = f"text_cache/{file_hash}.json"
                    if text_member != expected:
                        raise_api_error(400, "INVALID_MIGRATION", "Een tekstcachepad is ongeldig.")
                    cache_store.put_json("text_cache", file_hash, _read_json_member(zf, expected))
                    imported_text += 1

                notes = item.get("notes")
                if isinstance(notes, dict):
                    cache_store.put_json("notes", user_key(uid, file_hash), notes)
                    imported_notes += 1
                study = item.get("study")
                if isinstance(study, dict):
                    cache_store.put_json("study", user_key(uid, file_hash), study)
                    imported_study += 1

            folders = manifest.get("folders")
            if isinstance(folders, dict) and isinstance(folders.get("folders"), list):
                safe_folders = []
                for folder in folders["folders"][:200]:
                    if isinstance(folder, dict) and _ID_RE.fullmatch(str(folder.get("id") or "")):
                        safe_folders.append({
                            "id": str(folder["id"]),
                            "name": str(folder.get("name") or "Vak")[:120],
                            "created_at": folder.get("created_at"),
                        })
                cache_store.put_json("folders", uid, {"folders": safe_folders})

            wordlists = manifest.get("wordlists")
            imported_wordlists = 0
            if isinstance(wordlists, list):
                index = []
                for wordlist in wordlists[:200]:
                    if not isinstance(wordlist, dict) or not _ID_RE.fullmatch(str(wordlist.get("id") or "")):
                        continue
                    clean = dict(wordlist)
                    clean["owner_id"] = uid
                    list_id = str(clean["id"])
                    cache_store.put_json("wordlists", user_key(uid, list_id), clean)
                    index.append({
                        "id": list_id,
                        "name": str(clean.get("name") or "Woordenlijst")[:120],
                        "owner_id": uid,
                        "created_at": clean.get("created_at"),
                        "language": clean.get("language", "auto"),
                        "count": len(clean.get("cards") or []),
                    })
                    imported_wordlists += 1
                cache_store.put_json("wordlists", user_key(uid, "index"), {"lists": index})

            imported_ai_cache = 0
            for member in manifest.get("ai_cache_members") or []:
                match = re.fullmatch(r"ai_cache/([0-9a-f]{64})\.json", str(member))
                if not match:
                    raise_api_error(400, "INVALID_MIGRATION", "Een uitlegcachepad is ongeldig.")
                cache_store.put_json("ai_cache", match.group(1), _read_json_member(zf, str(member)))
                imported_ai_cache += 1

            _document_texts_cached.cache_clear()
            return {
                "ok": True,
                "documents": imported_docs,
                "notes": imported_notes,
                "study_records": imported_study,
                "text_caches": imported_text,
                "wordlists": imported_wordlists,
                "ai_cache_records": imported_ai_cache,
            }
    finally:
        await backup.close()
        temp_path.unlink(missing_ok=True)
