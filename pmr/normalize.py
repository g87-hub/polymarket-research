"""Raw JSONL -> normalized tables. Raw files are never modified."""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import NORM

DEPTH_LEVELS = 10


def _iter(path: Path):
    if not path.exists():
        return
    with gzip.open(path, "rt") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue  # truncated final line after a killed job


def _levels(side, reverse):
    lv = []
    for x in side or []:
        try:
            lv.append((float(x["price"]), float(x["size"])))
        except (KeyError, TypeError, ValueError):
            continue
    lv.sort(key=lambda t: t[0], reverse=reverse)
    return lv[:DEPTH_LEVELS]


def _iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def normalize_run(run_dir: Path) -> Path:
    out = NORM / run_dir.name
    out.mkdir(parents=True, exist_ok=True)

    markets, token_map = {}, {}
    for r in _iter(run_dir / "meta.jsonl.gz"):
        m = r["meta"]
        if not m.get("found"):
            continue
        key = (m["asset"], m["minutes"], m["start_epoch"])
        markets[key] = {k: m.get(k) for k in (
            "asset", "minutes", "start_epoch", "end_epoch", "slug", "condition_id",
            "token_up", "token_down", "min_order_size", "tick_size", "price_to_beat",
            "resolution_source")} | {"fee_fields": json.dumps(m.get("fee_fields"))}
        token_map[m["token_up"]] = key + ("up",)
        token_map[m["token_down"]] = key + ("down",)
    pd.DataFrame(markets.values()).to_csv(out / "markets.csv", index=False)

    rows = []
    for r in _iter(run_dir / "books.jsonl.gz"):
        if not isinstance(r.get("data"), list):
            continue
        for b in r["data"]:
            tid = str(b.get("asset_id"))
            if tid not in token_map:
                continue
            a, mins, start, side = token_map[tid]
            bids, asks = _levels(b.get("bids"), True), _levels(b.get("asks"), False)
            try:
                src_ts = float(b.get("timestamp")) / 1000.0
            except (TypeError, ValueError):
                src_ts = None
            rows.append({
                "t_send": r["t_send"], "t_recv": r["t_recv"], "src_ts": src_ts,
                "asset": a, "minutes": mins, "start_epoch": start, "side": side,
                "best_bid": bids[0][0] if bids else None, "bid_sz": bids[0][1] if bids else None,
                "best_ask": asks[0][0] if asks else None, "ask_sz": asks[0][1] if asks else None,
                "asks": json.dumps(asks), "bids": json.dumps(bids), "hash": b.get("hash")})
    pd.DataFrame(rows).to_csv(out / "books.csv.gz", index=False)

    spot = []
    for r in _iter(run_dir / "spot.jsonl.gz"):
        d = r.get("data") or {}
        try:
            price = float(d["price"])
        except (KeyError, TypeError, ValueError):
            continue
        spot.append({"t_send": r["t_send"], "t_recv": r["t_recv"], "src_ts": _iso(d.get("time")),
                     "asset": r["asset"], "src": r["src"], "price": price,
                     "bid": float(d.get("bid") or "nan"), "ask": float(d.get("ask") or "nan")})
    pd.DataFrame(spot).to_csv(out / "spot.csv.gz", index=False)
    return out


def load_normalized(run_ids: list[str] | None = None) -> dict[str, pd.DataFrame]:
    dirs = sorted(p for p in NORM.glob("*") if p.is_dir())
    if run_ids:
        dirs = [d for d in dirs if d.name in run_ids]
    parts = {"markets": [], "books": [], "spot": []}
    for d in dirs:
        for k, fn in (("markets", "markets.csv"), ("books", "books.csv.gz"), ("spot", "spot.csv.gz")):
            p = d / fn
            if p.exists() and p.stat().st_size > 30:
                try:
                    df = pd.read_csv(p)
                except pd.errors.EmptyDataError:
                    continue
                df["run_id"] = d.name
                parts[k].append(df)
    res = {k: (pd.concat(v, ignore_index=True) if v else pd.DataFrame()) for k, v in parts.items()}
    if not res["markets"].empty:
        res["markets"] = res["markets"].drop_duplicates(["asset", "minutes", "start_epoch"], keep="last")
    return res
