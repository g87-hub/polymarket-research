"""Research pipeline: variant grid on train/validation, pre-declared selection,
freeze, one-shot untouched test, forward paper evaluation, verdicts.

Every variant evaluated is appended to research/register.jsonl. The test
split is guarded by research/test_lock.json."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, replace

import pandas as pd

from . import strategies as S
from .backtest import SpotIndex, chronological_splits, run_backtest
from .config import OUTCOMES, REPORTS, STATE as RESEARCH
from .execution import BookIndex, ExecParams
from .normalize import load_normalized
from .stats import calibration, holm, outcome_base_rates, summarize

REGISTER = RESEARCH / "register.jsonl"
FROZEN = RESEARCH / "frozen.json"
TEST_LOCK = RESEARCH / "test_lock.json"

MIN_MARKETS_PER_SPLIT = 150      # below this, verdict = insufficient evidence
SELECT_ALPHA = 0.10              # Holm-adjusted, on validation
PAPER_RULE = {"min_settled_trades": 300, "min_distinct_days": 14, "max_p_le_0": 0.05,
              "exec": "delay 1s, fee x1.0, depth_share 0.5 — same as frozen"}

STRATEGY_CLASSES = {c.__name__: c for c in (S.FairValue, S.LateFavorite, S.PrevMomentum,
                                            S.CrossAssetLead, S.EarlyBaseline)}


def load_outcomes() -> pd.DataFrame:
    parts = [pd.read_csv(p) for p in OUTCOMES.glob("*.csv")]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fingerprint(data: dict) -> str:
    b = data["books"]
    s = f"{len(b)}|{b['t_recv'].min() if len(b) else 0}|{b['t_recv'].max() if len(b) else 0}"
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def log_register(entry: dict) -> None:
    RESEARCH.mkdir(parents=True, exist_ok=True)
    with REGISTER.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _strip(summary: dict) -> dict:
    return {k: v for k, v in summary.items() if k not in ("by_day",)}


def research(ep: ExecParams = ExecParams(), shares: float = 20.0, bankroll: float = 500.0) -> dict:
    data, outc = load_normalized(), load_outcomes()
    rep = {"generated": time.time(), "base_rates_from_outcomes": outcome_base_rates(outc) if len(outc) else None}
    if data["markets"].empty:
        rep["status"] = "no collected book data yet: run the collect workflow"
        return _write(rep)
    starts = data["markets"]["start_epoch"]
    try:
        sp = chronological_splits(starts)
    except ValueError as e:
        rep["status"] = f"insufficient data: {e}"
        return _write(rep)
    n_by = {k: int(((starts >= a) & (starts < b)).sum()) for k, (a, b) in sp.items()}
    # calibration uses train+validation only, never the untouched test split
    pre_test = data["books"][data["books"].start_epoch < sp["test"][0]]
    rep["calibration_train_val"] = calibration(pre_test, outc)
    rep["splits"] = {k: [int(a), int(b)] for k, (a, b) in sp.items()} | {"markets": n_by}
    fp = fingerprint(data)
    bi, si = BookIndex(data["books"]), SpotIndex(data["spot"])
    results, pv = {}, {}
    for st in S.default_grid():
        r = {}
        for split in ("train", "validation"):
            led = run_backtest(data, outc, st, ep, sp[split], shares, bankroll, bi, si)
            r[split] = summarize(led, bankroll)
            log_register({"t": time.time(), "variant": st.variant_id, "split": split,
                          "exec": asdict(ep), "data_fp": fp, "metrics": _strip(r[split])})
        results[st.variant_id] = r
        pv[st.variant_id] = (r["validation"].get("bootstrap") or {}).get("p_le_0")
    adj = holm({k: v for k, v in pv.items() if v is not None})
    base = max((results[v]["validation"].get("net_pnl", 0) for v in results if v.startswith("baseline")),
               default=0)
    eligible = []
    for v, r in results.items():
        if v.startswith("baseline"):
            continue
        tr, va = r["train"], r["validation"]
        ok = (tr.get("net_pnl", 0) > 0 and va.get("net_pnl", 0) > max(0, base)
              and adj.get(v, 1) < SELECT_ALPHA and va.get("n_settled", 0) >= 30)
        r["holm_p_validation"] = adj.get(v)
        r["eligible"] = ok
        if ok:
            eligible.append((va["net_pnl"] / va["n_settled"], v))
    rep["variants"] = {v: {"train": _strip(r["train"]), "validation": _strip(r["validation"]),
                           "holm_p_validation": r.get("holm_p_validation"), "eligible": r.get("eligible")}
                       for v, r in results.items()}
    rep["n_variants_tested"] = len(results)
    rep["enough_data"] = min(n_by["train"], n_by["validation"]) >= MIN_MARKETS_PER_SPLIT
    if eligible:
        best = max(eligible)[1]
        rep["candidate"] = best
        st = next(s for s in S.default_grid() if s.variant_id == best)
        rep["sensitivity_validation"] = sensitivity(data, outc, st, ep, sp["validation"], shares, bankroll, bi, si)
    else:
        rep["candidate"] = None
    rep["verdicts"] = verdicts(rep)
    return _write(rep)


def sensitivity(data, outc, st, ep, period, shares, bankroll, bi, si) -> list[dict]:
    rows = []
    grid = [("delay_s", [0.5, 1.0, 2.0, 4.0]), ("fee_mult", [1.0, 1.5, 2.0]),
            ("extra_slip_ticks", [0, 1, 2]), ("depth_share", [0.25, 0.5, 1.0])]
    for field, vals in grid:
        for v in vals:
            e2 = replace(ep, **{field: v})
            s = summarize(run_backtest(data, outc, st, e2, period, shares, bankroll, bi, si), bankroll)
            rows.append({"param": field, "value": v, "net": s.get("net_pnl"), "n": s.get("n_settled"),
                         "rejected": sum((s.get("rejections") or {}).values())})
    return rows


def verdicts(rep: dict) -> dict:
    out = {}
    lock = json.loads(TEST_LOCK.read_text()) if TEST_LOCK.exists() else {}
    paper = json.loads((REPORTS / "paper" / "paper_eval.json").read_text()) \
        if (REPORTS / "paper" / "paper_eval.json").exists() else {}
    by_name: dict[str, list] = {}
    for v, r in rep.get("variants", {}).items():
        by_name.setdefault(v.split("|")[0], []).append(r)
    for name, rs in by_name.items():
        if name.startswith("baseline"):
            continue
        if not rep.get("enough_data"):
            out[name] = "insufficient evidence"
        elif any(r.get("eligible") for r in rs):
            frozen_name = (lock.get("variant") or "").split("|")[0]
            if frozen_name == name and lock.get("test_net", 0) > 0 and lock.get("test_p_le_0", 1) < 0.05:
                out[name] = ("supported by forward paper observations"
                             if paper.get("passes_rule") else "historically promising")
            else:
                out[name] = "historically promising (validation only; untouched test not yet run)" \
                    if not lock else "rejected (failed untouched test)"
        elif all(r["validation"].get("n_settled", 0) < 30 for r in rs):
            out[name] = "insufficient evidence"
        else:
            out[name] = "rejected"
    return out


def freeze(variant_id: str, ep: ExecParams = ExecParams(), shares: float = 20.0) -> dict:
    if FROZEN.exists():
        raise SystemExit("research/frozen.json exists; delete it deliberately (and note why in "
                         "research/STATUS.md) before freezing another strategy")
    st = next((s for s in S.default_grid() if s.variant_id == variant_id), None)
    if st is None:
        raise SystemExit(f"unknown variant {variant_id}")
    fz = {"variant": variant_id, "class": type(st).__name__, "name": st.name, "params": st.params,
          "exec": asdict(ep), "shares": shares, "frozen_at": time.time(), "paper_rule": PAPER_RULE}
    fz["hash"] = hashlib.sha1(json.dumps(fz, sort_keys=True, default=str).encode()).hexdigest()[:12]
    FROZEN.write_text(json.dumps(fz, indent=2, default=str))
    log_register({"t": time.time(), "event": "freeze", **fz})
    return fz


def _frozen_strategy() -> tuple[dict, S.Strategy, ExecParams]:
    fz = json.loads(FROZEN.read_text())
    params = {k: tuple(v) if isinstance(v, list) else v for k, v in fz["params"].items()}
    return fz, STRATEGY_CLASSES[fz["class"]](fz["name"], params), ExecParams(**fz["exec"])


def evaluate_test(bankroll: float = 500.0) -> dict:
    if not FROZEN.exists():
        raise SystemExit("freeze a strategy first")
    fz, st, ep = _frozen_strategy()
    data, outc = load_normalized(), load_outcomes()
    sp = chronological_splits(data["markets"]["start_epoch"])
    if TEST_LOCK.exists():
        lock = json.loads(TEST_LOCK.read_text())
        if lock["test_period"][0] <= sp["test"][0] < lock["test_period"][1] or lock["frozen_hash"] != fz["hash"]:
            raise SystemExit("untouched test already used. Collect NEW data and evaluate only on "
                             "markets after the previous test period, or use paper mode.")
    led = run_backtest(data, outc, st, ep, sp["test"], fz["shares"], bankroll)
    s = summarize(led, bankroll)
    REPORTS.mkdir(parents=True, exist_ok=True)
    led.to_csv(REPORTS / "test_ledger.csv", index=False)
    lock = {"variant": fz["variant"], "frozen_hash": fz["hash"], "test_period": list(sp["test"]),
            "evaluated_at": time.time(), "test_net": s.get("net_pnl"),
            "test_p_le_0": (s.get("bootstrap") or {}).get("p_le_0"), "summary": _strip(s)}
    TEST_LOCK.write_text(json.dumps(lock, indent=2, default=str))
    log_register({"t": time.time(), "event": "test_evaluation", **lock})
    return lock


def paper_evaluate(bankroll: float = 500.0) -> dict:
    """Deterministically re-run the frozen strategy on every market that
    STARTED after the freeze time. Fills are simulated, never real."""
    fz, st, ep = _frozen_strategy()
    data, outc = load_normalized(), load_outcomes()
    led = run_backtest(data, outc, st, ep, (fz["frozen_at"], 1e12), fz["shares"], bankroll)
    d = REPORTS / "paper"
    d.mkdir(parents=True, exist_ok=True)
    if not led.empty:
        led.insert(0, "fill_type", "SIMULATED_PAPER")
    led.to_csv(d / "paper_ledger.csv", index=False)
    s = summarize(led, bankroll)
    days = len(s.get("by_day") or [])
    rule = fz["paper_rule"]
    passes = (s.get("n_settled", 0) >= rule["min_settled_trades"] and days >= rule["min_distinct_days"]
              and s.get("net_pnl", 0) > 0 and (s.get("bootstrap") or {}).get("p_le_0", 1) <= rule["max_p_le_0"])
    ev = {"variant": fz["variant"], "rule": rule, "distinct_days": days, "passes_rule": bool(passes),
          "summary": _strip(s), "note": "all fills are simulated from recorded books"}
    (d / "paper_eval.json").write_text(json.dumps(ev, indent=2, default=str))
    return ev


def _write(rep: dict) -> dict:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "research_latest.json").write_text(json.dumps(rep, indent=2, default=str))
    (REPORTS / "research_latest.md").write_text(to_markdown(rep))
    return rep


def to_markdown(rep: dict) -> str:
    L = ["# Research report", "",
         f"Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(rep['generated']))}.", ""]
    if rep.get("status"):
        L += [f"**Status:** {rep['status']}", ""]
    br = rep.get("base_rates_from_outcomes")
    if br:
        L += ["## Outcome base rates (signal only, no prices)", "",
              "| combo | n | P(Up) | p vs 0.5 | P(Up|prev Up) | P(Up|prev Down) | z | slot hours sig. (Holm) | days |",
              "|---|---|---|---|---|---|---|---|---|"]
        for k, v in br.items():
            if isinstance(v, dict):
                L.append(f"| {k} | {v['n']} | {v['p_up']} | {v['p_value_vs_half']} | {v['p_up_after_up']} | "
                         f"{v['p_up_after_down']} | {v['autocorr_z']} | {v['slot_hours_significant_after_holm'] or '-'} | {v['span_days']} |")
        L.append("")
    if rep.get("splits"):
        L += ["## Splits (chronological, embargoed)", "", f"`{json.dumps(rep['splits'])}`", ""]
    if rep.get("calibration_train_val"):
        L += ["## Calibration: executable ask vs realized win rate (train+validation)", "",
              "Positive `edge_after_fee` beyond ~2 se is a lead, not a result (many cells are inspected).", "",
              "| combo | elapsed | ask bucket | n | mean ask | win rate | edge after fee | se |",
              "|---|---|---|---|---|---|---|---|"]
        for c in rep["calibration_train_val"]:
            L.append(f"| {c['asset']} {c['minutes']}m | {c['frac']} | {c['bucket']} | {c['n']} | {c['mean_ask']} | "
                     f"{c['win_rate']} | {c['edge_after_fee']} | {c['se']} |")
        L.append("")
    if rep.get("variants"):
        L += ["## Variants (train / validation, net USDC after fees)", "",
              "| variant | train n | train net | val n | val net | val win | val PF | Holm p | eligible |",
              "|---|---|---|---|---|---|---|---|---|"]
        for v, r in rep["variants"].items():
            t, va = r["train"], r["validation"]
            L.append(f"| `{v}` | {t.get('n_settled', 0)} | {t.get('net_pnl', '-')} | {va.get('n_settled', 0)} | "
                     f"{va.get('net_pnl', '-')} | {va.get('win_rate', '-')} | {va.get('profit_factor', '-')} | "
                     f"{r.get('holm_p_validation', '-')} | {r.get('eligible')} |")
        L += ["", f"Variants tested: {rep['n_variants_tested']}. Candidate: `{rep.get('candidate')}`.", ""]
    if rep.get("verdicts"):
        L += ["## Verdicts", ""] + [f"- **{k}**: {v}" for k, v in rep["verdicts"].items()] + [""]
    L += ["All fills are simulated from recorded order books; none are real trades."]
    return "\n".join(L)
