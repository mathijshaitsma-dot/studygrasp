"""Maak lokaal een privacyveilige StudyGrasp-migratieback-up.

Voorbeeld:
    python tools/export_legacy_migration.py --output "%USERPROFILE%\\Desktop\\StudyGrasp-migratie.zip"

Accounts, sessies, wachtwoordhashes, API-sleutels en verbruikstellers worden
nooit opgenomen. De zip bevat alleen het materiaal van het lokale owneraccount.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def find_owner_id(base: Path, requested_email: str | None) -> str:
    candidates = []
    for path in (base / "users").glob("*.json"):
        user = read_json(path)
        if not isinstance(user, dict):
            continue
        if requested_email and str(user.get("email", "")).lower() == requested_email.lower():
            return str(user["id"])
        if user.get("plan") == "owner":
            candidates.append(str(user["id"]))
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit("Kon het lokale owneraccount niet eenduidig vinden; gebruik --owner-email.")


def account_json(base: Path, namespace: str, owner_id: str, key: str):
    for name in (f"{owner_id}__{key}.json", f"{key}.json"):
        path = base / namespace / name
        if path.is_file():
            return read_json(path)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "backend_cache_v3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--owner-email")
    args = parser.parse_args()

    base = args.source.resolve()
    if not base.is_dir():
        raise SystemExit(f"Lokale opslag niet gevonden: {base}")
    owner_id = find_owner_id(base, args.owner_email)
    manifest = {
        "format": "studygrasp-legacy-migration",
        "version": 1,
        "documents": [],
        "folders": account_json(base, "folders", owner_id, owner_id) or account_json(base, "folders", owner_id, "index"),
        "wordlists": [],
        "ai_cache_members": [],
    }

    meta_files = sorted((base / "meta").glob(f"{owner_id}__*.json"))
    if not meta_files:
        raise SystemExit("Geen colleges gevonden voor het lokale owneraccount.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for meta_path in meta_files:
            meta = read_json(meta_path)
            if not isinstance(meta, dict):
                continue
            file_hash = str(meta.get("file_hash") or "")
            uploads = list((base / "uploads").glob(f"{file_hash}.*"))
            if len(uploads) != 1:
                print(f"Overslaan: bronbestand voor {meta.get('file_name', file_hash)} niet eenduidig gevonden.", file=sys.stderr)
                continue
            upload = uploads[0]
            upload_member = f"uploads/{upload.name}"
            archive.write(upload, upload_member)
            item = {
                "file_hash": file_hash,
                "suffix": upload.suffix.lower(),
                "upload_member": upload_member,
                "meta": meta,
            }
            text_path = base / "text_cache" / f"{file_hash}.json"
            if text_path.is_file():
                item["text_cache_member"] = f"text_cache/{file_hash}.json"
                archive.write(text_path, item["text_cache_member"])
            notes = account_json(base, "notes", owner_id, file_hash)
            study = account_json(base, "study", owner_id, file_hash)
            if isinstance(notes, dict):
                item["notes"] = notes
            if isinstance(study, dict):
                item["study"] = study
            manifest["documents"].append(item)

        wordlist_index = account_json(base, "wordlists", owner_id, "index")
        if isinstance(wordlist_index, dict):
            for entry in wordlist_index.get("lists") or []:
                if isinstance(entry, dict) and entry.get("id"):
                    wordlist = account_json(base, "wordlists", owner_id, str(entry["id"]))
                    if isinstance(wordlist, dict):
                        manifest["wordlists"].append(wordlist)

        for path in sorted((base / "ai_cache").glob("*.json")):
            member = f"ai_cache/{path.name}"
            archive.write(path, member)
            manifest["ai_cache_members"].append(member)

        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))

    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"Gemaakt: {args.output}")
    print(f"Documenten: {len(manifest['documents'])}; grootte: {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
