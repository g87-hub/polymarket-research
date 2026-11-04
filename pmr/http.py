"""Rate-limited, timed HTTP with provenance. Read-only GETs/POSTs to public
market-data endpoints only; this module has no notion of auth or orders."""
from __future__ import annotations

import threading
import time
from typing import Any

import requests

from .config import LIMITS

_session = requests.Session()
_session.headers["User-Agent"] = "pm-research-readonly/0.1"
_lock = threading.Lock()
_last = 0.0

# Endpoints that place, cancel or sign anything. Refused unconditionally.
FORBIDDEN_PATH_PARTS = ("/order", "/orders", "/auth", "/api-key", "/cancel")


def _throttle() -> None:
    global _last
    with _lock:
        gap = 1.0 / LIMITS.max_requests_per_s
        wait = _last + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()


def fetch(url: str, params: dict | None = None, json_body: Any = None,
          retries: int = 2) -> dict:
    """Return {'ok', 'status', 'data', 't_send', 't_recv', 'latency_ms', 'url', 'error'}.
    Times are local wall-clock epoch seconds (receipt time), separate from any
    source-event timestamp contained in the payload."""
    lowered = url.lower()
    if any(p in lowered.split("?")[0] for p in FORBIDDEN_PATH_PARTS) and "/books" not in lowered:
        raise PermissionError(f"refusing non-market-data endpoint: {url}")
    err = None
    for attempt in range(retries + 1):
        _throttle()
        t_send = time.time()
        try:
            if json_body is None:
                r = _session.get(url, params=params, timeout=LIMITS.http_timeout_s)
            else:
                r = _session.post(url, json=json_body, timeout=LIMITS.http_timeout_s)
            t_recv = time.time()
            ok = r.status_code == 200
            data = r.json() if ok else None
            if ok or r.status_code not in (429, 500, 502, 503, 504):
                return {"ok": ok, "status": r.status_code, "data": data, "t_send": t_send,
                        "t_recv": t_recv, "latency_ms": (t_recv - t_send) * 1000,
                        "url": r.url, "error": None if ok else r.text[:300]}
            err = f"http {r.status_code}"
        except (requests.RequestException, ValueError) as e:  # ValueError: bad JSON
            err = repr(e)[:300]
        time.sleep(0.5 * (2 ** attempt))
    return {"ok": False, "status": None, "data": None, "t_send": t_send,
            "t_recv": time.time(), "latency_ms": None, "url": url, "error": err}
