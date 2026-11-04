"""Data-quality checks on a normalized run. Output is a JSON dict plus a short
markdown table; failures are reported, never silently dropped."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .normalize import load_normalized


def _pct(s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {}
    return {f"p{q}": round(float(np.percentile(s, q)), 1) for q in (50, 90, 99)} | {"n": int(len(s))}


def check(run_id: str, poll_s: float = 1.0) -> dict:
    d = load_normalized([run_id])
    b, s, m = d["books"], d["spot"], d["markets"]
    rep: dict = {"run_id": run_id, "markets_found": int(len(m))}
    if b.empty:
        rep["fatal"] = "no book snapshots"
        return rep

    rep["book_request_latency_ms"] = _pct((b["t_recv"] - b["t_send"]) * 1000)
    rep["book_source_age_ms"] = _pct((b["t_recv"] - b["src_ts"]) * 1000)
    polls = np.sort(b["t_recv"].unique())
    gaps = np.diff(polls)
    rep["poll_gaps_over_3x"] = int((gaps > 3 * poll_s).sum())
    rep["max_poll_gap_s"] = round(float(gaps.max()), 2) if len(gaps) else None

    crossed = (b["best_bid"] >= b["best_ask"]).sum()
    rep["crossed_books"] = int(crossed)
    rep["empty_ask_side"] = int(b["best_ask"].isna().sum())

    # Up ask + Down ask below 1 would be a free lock (before fees) — either a
    # real dislocation or a timing artefact of two separate snapshots.
    piv = b.pivot_table(index=["t_recv", "asset", "minutes", "start_epoch"], columns="side",
                        values="best_ask", aggfunc="first").dropna()
    if not piv.empty:
        tot = piv["up"] + piv["down"]
        rep["sum_best_asks"] = {"min": round(float(tot.min()), 4), "median": round(float(tot.median()), 4),
                                "n_below_1": int((tot < 1).sum())}

    cov = []
    for _, mk in m.iterrows():
        sub = b[(b.asset == mk.asset) & (b.minutes == mk.minutes) & (b.start_epoch == mk.start_epoch)
                & (b.t_recv >= mk.start_epoch) & (b.t_recv < mk.end_epoch)]
        secs = sub["t_recv"].astype(int).nunique()
        cov.append({"asset": mk.asset, "minutes": int(mk.minutes), "start_epoch": int(mk.start_epoch),
                    "coverage": round(secs / (mk.minutes * 60), 3)})
    cdf = pd.DataFrame(cov)
    if not cdf.empty:
        rep["coverage_by_combo"] = (cdf.groupby(["asset", "minutes"])["coverage"]
                                    .agg(["count", "median", "min"]).round(3).reset_index()
                                    .to_dict("records"))
    if not s.empty:
        rep["spot_request_latency_ms"] = _pct((s["t_recv"] - s["t_send"]) * 1000)
        rep["spot_source_age_ms"] = _pct((s["t_recv"] - s["src_ts"]) * 1000)
    rep["clock_note"] = ("source ages compare venue timestamps with the runner clock; "
                         "runner clock skew (NTP, usually <50ms) is not corrected")
    return rep


def write(run_id: str, out_dir: Path) -> Path:
    rep = check(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"quality_{run_id}.json"
    p.write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))
    return p
