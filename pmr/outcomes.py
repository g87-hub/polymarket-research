"""Historical outcome backfill + historical-data availability audit.

Outcomes alone support base-rate tests (slot effects, outcome autocorrelation)
but NOT fill-realistic backtests: they say nothing about what price was
executable. Resumable: already-fetched windows are skipped."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from .config import ASSETS, CLOB, DURATIONS_MIN, OUTCOMES, window_start
from .discovery import lookup
from .http import fetch

COLS = ["asset", "minutes", "start_epoch", "slug", "found", "closed", "resolved_up",
        "price_to_beat", "condition_id", "token_up", "token_down", "fee_fields", "fetched_at", "error"]


def _path(asset: str, minutes: int) -> Path:
    return OUTCOMES / f"{asset}_{minutes}m.csv"


def _prices_path(asset: str, minutes: int) -> Path:
    return OUTCOMES / f"{asset}_{minutes}m_prices.csv"


def fetch_price_path(token: str, start: int, minutes: int) -> list[dict]:
    """Up-token price series for one past window (CLOB /prices-history,
    1-minute fidelity). A trade/mid style series: NOT executable prices."""
    rec = fetch(f"{CLOB}/prices-history", params={"market": token, "startTs": start - 60,
                                                   "endTs": start + minutes * 60 + 60, "fidelity": 1})
    hist = (rec["data"] or {}).get("history") if rec["ok"] and isinstance(rec["data"], dict) else None
    out = []
    for h in hist or []:
        try:
            out.append({"t": int(h["t"]), "p": float(h["p"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def backfill(days: float, assets=ASSETS, durations=DURATIONS_MIN, max_minutes: float = 300,
             settle_lag_s: int = 1800, with_prices: bool = True) -> dict:
    OUTCOMES.mkdir(parents=True, exist_ok=True)
    now = time.time() - settle_lag_s  # only windows that should be resolved
    summary = {}
    combos = [(a, m) for a in assets for m in durations]
    for a, m in combos:
            # equal time budget per combo so one slow combo cannot starve the rest
            deadline = time.time() + max_minutes * 60 / len(combos)
            p = _path(a, m)
            pp = _prices_path(a, m)
            have_px = set(pd.read_csv(pp)["start_epoch"].astype(int)) if pp.exists() and pp.stat().st_size > 30 else set()
            px_rows = []
            old = pd.read_csv(p) if p.exists() else pd.DataFrame(columns=COLS)
            done = set(old.loc[old["resolved_up"].notna(), "start_epoch"].astype(int)) if len(old) else set()
            last = window_start(now, m) - m * 60
            first = last - int(days * 86400)
            new = []
            for s in range(last, first, -m * 60):  # newest first: useful even if cut short
                if s in done:
                    continue
                if time.time() > deadline:
                    break
                mm, _ = lookup(a, m, s)
                new.append({"asset": a, "minutes": m, "start_epoch": s, "slug": mm.slug,
                            "found": mm.found, "closed": mm.closed, "resolved_up": mm.resolved_up(),
                            "price_to_beat": mm.price_to_beat, "condition_id": mm.condition_id,
                            "token_up": mm.token_up, "token_down": mm.token_down,
                            "fee_fields": json.dumps(mm.fee_fields), "fetched_at": time.time(),
                            "error": mm.error})
                if with_prices and mm.found and mm.token_up and s not in have_px:
                    for pt in fetch_price_path(mm.token_up, s, m):
                        px_rows.append({"start_epoch": s, "t": pt["t"], "p_up": pt["p"]})
            if px_rows:
                pd.DataFrame(px_rows).to_csv(pp, mode="a", header=not pp.exists(), index=False)
            df = pd.concat([old, pd.DataFrame(new, columns=COLS)], ignore_index=True)
            df = df.sort_values("fetched_at").drop_duplicates("start_epoch", keep="last")
            df.sort_values("start_epoch").to_csv(p, index=False)
            summary[f"{a}_{m}m"] = {"rows": int(len(df)), "new": len(new),
                                    "resolved": int(df["resolved_up"].notna().sum()),
                                    "not_found": int((~df["found"].astype(bool)).sum()),
                                    "price_points_added": len(px_rows)}
    print(json.dumps(summary, indent=2))
    return summary


def audit_history(sample_per_combo: int = 3) -> dict:
    """What historical data exists for already-closed markets? Checks the CLOB
    price-history endpoint on a few known tokens. A price series is midpoint/
    last-trade style data, not an order book, so it cannot establish fills."""
    rep = {}
    for a in ASSETS:
        for m in DURATIONS_MIN:
            p = _path(a, m)
            if not p.exists():
                rep[f"{a}_{m}m"] = "no outcomes yet: run backfill first"
                continue
            df = pd.read_csv(p).dropna(subset=["token_up"]).tail(sample_per_combo)
            res = []
            for _, r in df.iterrows():
                rec = fetch(f"{CLOB}/prices-history",
                            params={"market": str(r.token_up), "startTs": int(r.start_epoch) - 600,
                                    "endTs": int(r.start_epoch) + m * 60 + 60, "fidelity": 1})
                hist = (rec["data"] or {}).get("history") if rec["ok"] else None
                res.append({"start_epoch": int(r.start_epoch), "status": rec["status"],
                            "points": len(hist) if hist else 0,
                            "median_step_s": (pd.Series([h["t"] for h in hist]).diff().median()
                                              if hist and len(hist) > 2 else None)})
            rep[f"{a}_{m}m"] = res
    rep["conclusion_template"] = ("price history (if any) is a midpoint/trade series with no depth; "
                                  "executable-price tests require prospective book collection")
    print(json.dumps(rep, indent=2, default=str))
    return rep
