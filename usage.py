"""Accountgebonden maandcredits voor AI-functies.

Een credit is een producteenheid, geen provider-token. Dezelfde exacte output
wordt per account maar eenmaal betaald: de algemene AI-cache blijft gedeeld,
maar een accountgebonden unlock bepaalt of deze gebruiker de inhoud al heeft.

Standaard: Gratis 200 credits/maand, Premium 1000, Ultra 2000 en het
eigenaarsaccount onbeperkt. Legacy-plan ``plus`` valt onder
Premium; ``pro`` valt onder Ultra.
"""

import hashlib
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import cache_store

_consume_lock = threading.RLock()


def enabled() -> bool:
    return os.getenv("ENABLE_QUOTA", "true").strip().lower() == "true"


def canonical_plan(plan: str) -> str:
    value = (plan or "free").strip().lower()
    return {"plus": "premium", "pro": "ultra"}.get(value, value)


def plan_limit(plan: str) -> Optional[int]:
    """Maandbudget in StudyGrasp-credits; None betekent onbeperkt."""
    value = canonical_plan(plan)
    if value in ("owner", "unlimited"):
        return None
    if value == "ultra":
        return int(os.getenv("ULTRA_MONTHLY_CREDITS", "2000"))
    if value == "premium":
        return int(os.getenv("PREMIUM_MONTHLY_CREDITS", "1000"))
    return int(os.getenv("FREE_MONTHLY_CREDITS", "200"))


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _period_key(user_id: str, period: str) -> str:
    return f"{user_id}:{period}"


def _unlock_storage_key(user_id: str, content_key: str) -> str:
    return hashlib.sha256(f"{user_id}|{content_key}".encode("utf-8")).hexdigest()


def is_unlocked(user_id: str, content_key: Optional[str]) -> bool:
    return bool(content_key and cache_store.get_json(
        "usage_unlocks", _unlock_storage_key(user_id, content_key)))


def mark_unlocked(user_id: str, content_key: Optional[str]) -> None:
    if not content_key:
        return
    cache_store.put_json("usage_unlocks", _unlock_storage_key(user_id, content_key), {
        "user_id": user_id,
        "content_hash": hashlib.sha256(content_key.encode("utf-8")).hexdigest(),
        "unlocked_at": time.time(),
    })


def status(user_id: str, plan: str) -> tuple[int, Optional[int]]:
    """(verbruikt_deze_maand, maandlimiet)."""
    limit = plan_limit(plan)
    rec = cache_store.get_json("usage_monthly", _period_key(user_id, _month())) or {}
    return int(rec.get("credits", 0)), limit


def allowed(user_id: str, plan: str, cost: int = 1, unlock_key: Optional[str] = None) -> bool:
    cost = max(0, int(cost))
    if unlock_key and is_unlocked(user_id, unlock_key):
        return True
    used, limit = status(user_id, plan)
    return not enabled() or cost == 0 or limit is None or used + cost <= limit


def _add(namespace: str, key: str, period_field: str, period: str, cost: int) -> None:
    rec = cache_store.get_json(namespace, key) or {period_field: period, "credits": 0}
    rec["credits"] = int(rec.get("credits", 0)) + cost
    rec["updated_at"] = time.time()
    cache_store.put_json(namespace, key, rec)


def consume(user_id: str, plan: str, cost: int = 1, unlock_key: Optional[str] = None) -> bool:
    """Controleer en schrijf credits af; een bestaande unlock is gratis."""
    cost = max(0, int(cost))
    with _consume_lock:
        if unlock_key and is_unlocked(user_id, unlock_key):
            return True
        if not allowed(user_id, plan, cost):
            return False
        if enabled() and cost:
            month = _month()
            _add("usage_monthly", _period_key(user_id, month), "month", month, cost)
        # Ook met quota tijdelijk uit onthouden we eerder bekeken inhoud.
        mark_unlocked(user_id, unlock_key)
        return True


def record(user_id: str, plan: str, cost: int = 1) -> None:
    """Compatibiliteit; nieuwe request-code gebruikt consume via quota_gate."""
    consume(user_id, plan, cost)
