"""
Freemium-metering: hoeveel VERSE AI-generaties (cache-misses) een gebruiker per
dag mag. Cache-hits tellen nooit mee — die kosten geen tokens, dus populaire
(gecachte) vakken voelen onbeperkt, terwijl jouw kost per gratis gebruiker
begrensd blijft.

Standaard staat metering UIT (ENABLE_QUOTA=false): geen limiet, handig tijdens
ontwikkeling en testen. Zet 'm aan zodra je wilt gaan verdienen — dan geldt per
plan een dagbudget:

  ENABLE_QUOTA=true
  FREE_DAILY_LIMIT=30       # gratis: 30 verse generaties/dag
  PLUS_DAILY_LIMIT=300      # Plus
  PREMIUM_DAILY_LIMIT=0     # Premium: 0 = onbeperkt

De teller loopt via cache_store, dus met Supabase is hij net zo permanent en
gedeeld als de cache zelf (anders per server, per dag, in het geheugen van de
schijf-cache). De limiet is bewust "zacht": de teller is niet strikt atomair over
meerdere servers, dus in het uiterste geval krijgt iemand een paar generaties
extra — nooit een probleem voor een gratis tier.
"""

import os
import time
from datetime import datetime, timezone
from typing import Optional

import cache_store


def enabled() -> bool:
    return os.getenv("ENABLE_QUOTA", "false").strip().lower() == "true"


def plan_limit(plan: str) -> Optional[int]:
    """Dagbudget voor een plan. None = onbeperkt."""
    p = (plan or "free").strip().lower()
    if p == "plus":
        return int(os.getenv("PLUS_DAILY_LIMIT", "300"))
    if p in ("premium", "pro", "unlimited"):
        raw = int(os.getenv("PREMIUM_DAILY_LIMIT", "0"))
        return raw or None  # 0 => onbeperkt
    return int(os.getenv("FREE_DAILY_LIMIT", "30"))


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _key(user_id: str, day: str) -> str:
    return f"{user_id}:{day}"


def identify(request) -> tuple[str, str]:
    """(user_id, plan) uit de request-headers. Onbekend => anon/free."""
    user_id = (request.headers.get("x-user-id") or "").strip() or "anon"
    plan = (request.headers.get("x-user-plan") or "free").strip().lower()
    return user_id, plan


def status(user_id: str, plan: str) -> tuple[int, Optional[int]]:
    """(gebruikt_vandaag, limiet). limiet None = onbeperkt."""
    limit = plan_limit(plan)
    if limit is None:
        return 0, None
    rec = cache_store.get_json("usage", _key(user_id, _today())) or {}
    return int(rec.get("count", 0)), limit


def allowed(user_id: str, plan: str) -> bool:
    if not enabled():
        return True
    used, limit = status(user_id, plan)
    return limit is None or used < limit


def record(user_id: str, plan: str) -> None:
    """Eén verse generatie bijschrijven. No-op als metering uit staat."""
    if not enabled():
        return
    if plan_limit(plan) is None:
        return
    day = _today()
    key = _key(user_id, day)
    rec = cache_store.get_json("usage", key) or {"count": 0, "day": day}
    rec["count"] = int(rec.get("count", 0)) + 1
    rec["updated_at"] = time.time()
    cache_store.put_json("usage", key, rec)
