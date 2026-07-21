"""
Zachte IP-gebaseerde noodrem op dure AI-endpoints, los van de freemium-teller
(usage.py telt per X-User-Id — triviaal te omzeilen door steeds een nieuwe
willekeurige id te sturen). In-memory en dus per proces/herstart: er zijn geen
accounts, dus geen persistente per-gebruiker administratie nodig. Doel is
alleen script-misbruik afremmen, niet legitieme gebruikers hinderen — daarom
een royaal standaardplafond.
"""

import os
import threading
import time
from collections import deque

_WINDOW_S = float(os.getenv("RATE_LIMIT_WINDOW_S", "60"))
_MAX_PER_WINDOW = int(os.getenv("RATE_LIMIT_MAX_PER_MIN", "20"))

_lock = threading.Lock()
_hits: dict[str, deque] = {}


def check(key: str, max_per_window: int = None, window_s: float = None) -> bool:
    """True als de aanvraag mag doorgaan; False als de limiet is bereikt.
    max_per_window/window_s overriden de standaard-noodrem — handig voor een
    endpoint met een eigen, zwaarder plafond (bv. uploads)."""
    limit = _MAX_PER_WINDOW if max_per_window is None else max_per_window
    window = _WINDOW_S if window_s is None else window_s
    now = time.time()
    with _lock:
        dq = _hits.setdefault(key, deque())
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True
