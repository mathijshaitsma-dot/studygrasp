"""
Zicht op de multi-provider AI-fallback (ai_engine.py): hoe vaak wordt elke laag
(Gemini/Groq/OpenRouter/Mistral/GitHub, per model) geraakt, hoe vaak faalt hij
en waarom, en hoe snel antwoordt hij. ai_engine.py had hiervoor alleen een
in-memory cooldown-dict die bij een herstart verdwijnt — dit schrijft
dag-gebufferde tellers weg via cache_store (zelfde aanpak als usage.py), dus
overleeft het een herstart en is met Supabase gedeeld over meerdere servers.
"""

import time
from datetime import datetime, timedelta, timezone

import cache_store

NAMESPACE = "ai_stats"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record(candidate_label: str, success: bool, error_class: str = None, latency_ms: float = None) -> None:
    day = _today()
    rec = cache_store.get_json(NAMESPACE, day) or {"day": day, "providers": {}}
    p = rec["providers"].setdefault(candidate_label, {
        "success": 0, "failure": 0, "errors": {}, "latency_ms_sum": 0.0, "latency_count": 0,
    })
    if success:
        p["success"] += 1
        if latency_ms is not None:
            p["latency_ms_sum"] += latency_ms
            p["latency_count"] += 1
    else:
        p["failure"] += 1
        if error_class:
            p["errors"][error_class] = p["errors"].get(error_class, 0) + 1
    rec["updated_at"] = time.time()
    cache_store.put_json(NAMESPACE, day, rec)


def aggregate(days: int = 7) -> dict:
    """Som van de laatste N dagen, per provider/model-label."""
    totals: dict[str, dict] = {}
    today = datetime.now(timezone.utc)
    for i in range(days):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        rec = cache_store.get_json(NAMESPACE, day)
        if not rec:
            continue
        for label, p in rec.get("providers", {}).items():
            t = totals.setdefault(label, {
                "success": 0, "failure": 0, "errors": {}, "latency_ms_sum": 0.0, "latency_count": 0,
            })
            t["success"] += p.get("success", 0)
            t["failure"] += p.get("failure", 0)
            t["latency_ms_sum"] += p.get("latency_ms_sum", 0.0)
            t["latency_count"] += p.get("latency_count", 0)
            for err, n in p.get("errors", {}).items():
                t["errors"][err] = t["errors"].get(err, 0) + n

    providers = {}
    for label, t in totals.items():
        attempts = t["success"] + t["failure"]
        providers[label] = {
            "success": t["success"],
            "failure": t["failure"],
            "attempts": attempts,
            "error_rate": round(t["failure"] / attempts, 3) if attempts else 0.0,
            "avg_latency_ms": round(t["latency_ms_sum"] / t["latency_count"], 1) if t["latency_count"] else None,
            "errors": t["errors"],
        }
    return {"days": days, "providers": providers}
