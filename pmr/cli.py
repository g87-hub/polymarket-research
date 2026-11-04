"""Command line entry point: python -m pmr <command>."""
from __future__ import annotations

import argparse
import json
import time

from . import collect, history, normalize, outcomes, quality, research
from .config import (ASSETS, CLOB, COINBASE, DATA, DURATIONS_MIN, LIMITS, RAW, REPORTS,
                     window_start)
from .discovery import lookup
from .http import fetch


def probe() -> dict:
    """Verify live schema/mechanics before trusting the parser. Saves raw payloads."""
    out, now = {"t": time.time()}, time.time()
    d = REPORTS / "probe"
    d.mkdir(parents=True, exist_ok=True)
    for a in ASSETS:
        for m in DURATIONS_MIN:
            mm, rec = lookup(a, m, window_start(now, m))
            (d / f"event_{a}_{m}m.json").write_text(json.dumps(rec["data"], indent=2))
            item = {"parsed": mm.to_dict(), "status": rec["status"]}
            if mm.found:
                b = fetch(f"{CLOB}/book", params={"token_id": mm.token_up})
                fr = fetch(f"{CLOB}/fee-rate", params={"token_id": mm.token_up})
                item["book_keys"] = list((b["data"] or {}).keys())
                item["book_top"] = {k: (b["data"] or {}).get(k, [])[:3] for k in ("bids", "asks")}
                item["fee_rate_endpoint"] = {"status": fr["status"], "data": fr["data"]}
            out[f"{a}_{m}m"] = item
    out["coinbase"] = fetch(f"{COINBASE}/products/BTC-USD/ticker")["data"]
    g = fetch("https://polymarket.com/api/geoblock")
    out["runner_geoblock_status"] = {"status": g["status"], "data": g["data"],
                                     "note": "describes the GitHub runner IP, not you; "
                                             "this project never trades regardless"}
    (d / "probe_summary.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str)[:6000])
    return out


def data_size_mb() -> float:
    return sum(p.stat().st_size for p in DATA.rglob("*") if p.is_file()) / 1e6


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pmr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    c = sub.add_parser("collect")
    c.add_argument("--minutes", type=float, default=60)
    c.add_argument("--max-mb", type=float, default=None)
    c.add_argument("--poll", type=float, default=None)
    b = sub.add_parser("backfill")
    b.add_argument("--days", type=float, default=7)
    b.add_argument("--max-minutes", type=float, default=120)
    sub.add_parser("audit-history")
    sub.add_parser("history-report")
    sub.add_parser("normalize-all")
    sub.add_parser("research")
    f = sub.add_parser("freeze")
    f.add_argument("variant")
    sub.add_parser("evaluate-test")
    sub.add_parser("paper-eval")
    sub.add_parser("size")
    a = ap.parse_args(argv)

    if a.cmd == "probe":
        probe()
    elif a.cmd == "collect":
        if data_size_mb() > LIMITS.max_repo_data_mb:
            raise SystemExit(f"data/ exceeds {LIMITS.max_repo_data_mb} MB cap; prune or archive first")
        run_dir = collect.run(a.minutes, max_mb=a.max_mb, poll_s=a.poll)
        normalize.normalize_run(run_dir)
        quality.write(run_dir.name, REPORTS / "quality")
    elif a.cmd == "backfill":
        outcomes.backfill(a.days, max_minutes=a.max_minutes)
    elif a.cmd == "audit-history":
        r = outcomes.audit_history()
        (REPORTS / "history_audit.json").write_text(json.dumps(r, indent=2, default=str))
    elif a.cmd == "history-report":
        history.report()
        print((REPORTS / "history_latest.md").read_text())
    elif a.cmd == "normalize-all":
        for rd in sorted(p for p in RAW.glob("*") if p.is_dir()):
            normalize.normalize_run(rd)
    elif a.cmd == "research":
        r = research.research()
        print((REPORTS / "research_latest.md").read_text())
    elif a.cmd == "freeze":
        print(json.dumps(research.freeze(a.variant), indent=2, default=str))
    elif a.cmd == "evaluate-test":
        print(json.dumps(research.evaluate_test(), indent=2, default=str))
    elif a.cmd == "paper-eval":
        print(json.dumps(research.paper_evaluate(), indent=2, default=str))
    elif a.cmd == "size":
        print(f"data/: {data_size_mb():.1f} MB (cap {LIMITS.max_repo_data_mb} MB)")


if __name__ == "__main__":
    main()
