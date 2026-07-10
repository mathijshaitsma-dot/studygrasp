"""
Twee-laags cache-opslag: lokale schijf (L1, snel) + optioneel Supabase (L2,
permanent en gedeeld over alle gebruikers en servers).

WAAROM
======
De AI-uitleg wordt gecachet op basis van de inhoud van een dia. Zolang die
cache alleen op de lokale schijf van de server staat, wordt hij bij elke deploy
of herstart gewist — en dan betaal je álle tokens opnieuw. Met L2 (Supabase)
overleeft de cache deploys en wordt hij gedeeld: één keer een dia uitleggen, en
elke gebruiker daarna (op elke server) krijgt hem gratis. Dáár zit de besparing
die je app winstgevend maakt: kosten schalen dan met unieke content, niet met
het aantal gebruikers.

WAT WEL EN NIET NAAR L2
=======================
Alleen dúre, AI-gegenereerde content gaat naar L2: uitleg, samenvattingen,
quizzes, flashcards, voorgelezen audio, en de verbruikstellers. Dia-afbeeldingen
en geëxtraheerde tekst blijven lokaal — die zijn gratis opnieuw af te leiden uit
het bronbestand en zouden L2 alleen maar duur en traag maken.

ZONDER SUPABASE
===============
Zijn SUPABASE_URL en SUPABASE_SERVICE_ROLE_KEY niet gezet, dan draait alles
gewoon op schijf (L1) — exact zoals voorheen, zonder configuratie.

Zie SUPABASE_SETUP.md voor het aanmaken van de tabel en de bucket.
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("studycopilot-cache")

_BASE = Path(os.getenv("BACKEND_CACHE_DIR", "backend_cache_v3"))

CACHE_TABLE = os.getenv("SUPABASE_CACHE_TABLE", "ai_cache")
BLOB_BUCKET = os.getenv("SUPABASE_CACHE_BUCKET", "ai-cache")


# =========================================================
# L2: Supabase (lazy, optioneel, nooit fataal)
# =========================================================

_sb_lock = threading.Lock()
_sb = None
_sb_ready: Optional[bool] = None  # None = nog niet geprobeerd


def _supabase():
    global _sb, _sb_ready
    if _sb_ready is not None:
        return _sb
    with _sb_lock:
        if _sb_ready is not None:
            return _sb
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            _sb_ready = False
            logger.info("Cache-L2 (Supabase) uit: geen SUPABASE_URL/SERVICE_ROLE_KEY. Alleen lokale cache.")
            return None
        try:
            from supabase import create_client
            _sb = create_client(url, key)
            _sb_ready = True
            logger.info("Cache-L2 (Supabase) actief — cache is nu permanent en gedeeld.")
        except Exception as e:
            _sb, _sb_ready = None, False
            logger.warning("Cache-L2 (Supabase) kon niet starten (%s). Alleen lokale cache.", str(e)[:200])
        return _sb


def l2_active() -> bool:
    return _supabase() is not None


# =========================================================
# L1: lokale schijf
# =========================================================

def _dir(namespace: str) -> Path:
    d = _BASE / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d


def _json_path(namespace: str, key: str) -> Path:
    return _dir(namespace) / f"{key}.json"


def _blob_path(namespace: str, key: str) -> Path:
    return _dir(namespace) / key


# =========================================================
# JSON (uitleg, quizzes, flashcards, verbruik, …)
# =========================================================

def get_json(namespace: str, key: str) -> Optional[dict[str, Any]]:
    """L1 eerst (snel). Mis? Dan L2, en vul L1 zodat de volgende hit lokaal is."""
    path = _json_path(namespace, key)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    sb = _supabase()
    if sb is not None:
        try:
            res = (sb.table(CACHE_TABLE).select("data")
                   .eq("namespace", namespace).eq("key", key).limit(1).execute())
            rows = res.data or []
            if rows:
                data = rows[0].get("data")
                try:
                    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
                return data
        except Exception as e:
            logger.debug("L2 get_json faalde (%s/%s): %s", namespace, key[:12], str(e)[:150])
    return None


def put_json(namespace: str, key: str, data: dict[str, Any]) -> None:
    """Naar L1 én (best-effort) L2. L2-fouten zijn nooit fataal."""
    path = _json_path(namespace, key)
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    sb = _supabase()
    if sb is not None:
        try:
            sb.table(CACHE_TABLE).upsert(
                {"namespace": namespace, "key": key, "data": data},
                on_conflict="namespace,key",
            ).execute()
        except Exception as e:
            logger.debug("L2 put_json faalde (%s/%s): %s", namespace, key[:12], str(e)[:150])


# =========================================================
# Binaire blobs (voorgelezen mp3-audio)
# =========================================================

def get_blob(namespace: str, key: str) -> Optional[bytes]:
    path = _blob_path(namespace, key)
    if path.exists():
        try:
            return path.read_bytes()
        except Exception:
            pass

    sb = _supabase()
    if sb is not None:
        try:
            data = sb.storage.from_(BLOB_BUCKET).download(f"{namespace}/{key}")
            if data:
                try:
                    path.write_bytes(data)
                except Exception:
                    pass
                return data
        except Exception as e:
            logger.debug("L2 get_blob faalde (%s/%s): %s", namespace, key[:12], str(e)[:150])
    return None


def put_blob(namespace: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    path = _blob_path(namespace, key)
    try:
        path.write_bytes(data)
    except Exception:
        pass

    sb = _supabase()
    if sb is not None:
        try:
            sb.storage.from_(BLOB_BUCKET).upload(
                path=f"{namespace}/{key}",
                file=data,
                file_options={"content-type": content_type, "upsert": "true"},
            )
        except Exception as e:
            logger.debug("L2 put_blob faalde (%s/%s): %s", namespace, key[:12], str(e)[:150])


def blob_local_path(namespace: str, key: str) -> Optional[Path]:
    """Lokaal pad voor FileResponse. Ontbreekt het lokaal maar staat het in L2,
    dan wordt het eerst opgehaald en lokaal weggeschreven."""
    path = _blob_path(namespace, key)
    if path.exists():
        return path
    if get_blob(namespace, key) is not None and path.exists():
        return path
    return None
