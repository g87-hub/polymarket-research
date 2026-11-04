"""Bounded prospective collector (REST polling).

Every poll records: local send/receipt times, the source timestamp the venue
puts in the payload, the request latency, and the raw payload. Hard runtime
and data-volume caps stop the run. No auth, no orders, no wallets."""
from __future__ import annotations

import gzip
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import (ASSETS, CLOB, COINBASE, DURATIONS_MIN, LIMITS, RAW, SPOT_PRODUCT,
                     window_start)
from .discovery import MarketMeta, lookup
from .http import fetch


class RawWriter:
    def __init__(self, run_dir: Path):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.dir = run_dir
        self.files = {}

    def write(self, stream: str, obj: dict) -> None:
        f = self.files.get(stream)
        if f is None:
            f = self.files[stream] = gzip.open(self.dir / f"{stream}.jsonl.gz", "at")
        f.write(json.dumps(obj, separators=(",", ":")) + "\n")

    def flush(self) -> None:
        for f in self.files.values():
            f.flush()

    def mb(self) -> float:
        self.flush()
        return sum(p.stat().st_size for p in self.dir.glob("*.gz")) / 1e6

    def close(self) -> None:
        for f in self.files.values():
            f.close()


def _active_windows(now: float) -> list[tuple[str, int, int]]:
    """Current window and the next one for each asset x duration (the next
    one is pre-registered so rollover never leaves a gap)."""
    out = []
    for a in ASSETS:
        for m in DURATIONS_MIN:
            s = window_start(now, m)
            out += [(a, m, s), (a, m, s + m * 60)]
    return out


def run(minutes: float, run_id: str | None = None, max_mb: float | None = None,
        poll_s: float | None = None) -> Path:
    minutes = min(minutes, LIMITS.max_collect_minutes)
    max_mb = min(max_mb or LIMITS.max_raw_mb_per_run, LIMITS.max_raw_mb_per_run)
    poll_s = max(poll_s or LIMITS.poll_interval_s, 0.5)
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    w = RawWriter(RAW / run_id)
    meta: dict[tuple, MarketMeta] = {}
    deadline = time.time() + minutes * 60
    stats = {"polls": 0, "book_fail": 0, "spot_fail": 0, "meta_missing": 0, "stop": "time"}
    w.write("run", {"run_id": run_id, "started": time.time(), "minutes": minutes,
                    "max_mb": max_mb, "poll_s": poll_s, "git_sha": os.environ.get("GITHUB_SHA")})
    last_meta_retry: dict[tuple, float] = {}

    while time.time() < deadline:
        tick = time.time()
        # --- market discovery / rollover ------------------------------------
        wanted = _active_windows(tick)
        for key in wanted:
            mm = meta.get(key)
            if mm is not None and mm.found:
                continue
            if tick - last_meta_retry.get(key, 0) < 20:
                continue
            last_meta_retry[key] = tick
            mm, rec = lookup(*key)
            meta[key] = mm
            w.write("meta", {"t_recv": rec["t_recv"], "latency_ms": rec["latency_ms"],
                             "status": rec["status"], "meta": mm.to_dict(),
                             "raw": rec["data"] if mm.found else None})
            if not mm.found:
                stats["meta_missing"] += 1
        for key in list(meta):  # drop windows that ended > 2 min ago
            if meta[key].end_epoch < tick - 120:
                del meta[key]

        # --- books for both outcomes of every live market -------------------
        tokens = []
        for key, mm in meta.items():
            if mm.found and mm.start_epoch - 60 <= tick <= mm.end_epoch + 5:
                tokens += [mm.token_up, mm.token_down]
        if tokens:
            rec = fetch(f"{CLOB}/books", json_body=[{"token_id": t} for t in tokens])
            w.write("books", {"t_send": rec["t_send"], "t_recv": rec["t_recv"],
                              "latency_ms": rec["latency_ms"], "status": rec["status"],
                              "error": rec["error"], "data": rec["data"]})
            stats["book_fail"] += 0 if rec["ok"] else 1

        # --- external spot proxy -------------------------------------------
        for a in ASSETS:
            rec = fetch(f"{COINBASE}/products/{SPOT_PRODUCT[a]}/ticker")
            w.write("spot", {"asset": a, "src": "coinbase", "t_send": rec["t_send"],
                             "t_recv": rec["t_recv"], "latency_ms": rec["latency_ms"],
                             "status": rec["status"], "data": rec["data"]})
            stats["spot_fail"] += 0 if rec["ok"] else 1

        stats["polls"] += 1
        if stats["polls"] % 60 == 0 and w.mb() > max_mb:
            stats["stop"] = "data_cap"
            break
        time.sleep(max(0.0, poll_s - (time.time() - tick)))

    stats["ended"] = time.time()
    stats["raw_mb"] = round(w.mb(), 2)
    w.write("run", {"run_id": run_id, "summary": stats})
    w.close()
    print(json.dumps({"run_id": run_id, **stats}))
    return RAW / run_id
