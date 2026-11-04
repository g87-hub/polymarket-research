"""Dependence-aware performance statistics and multiple-comparison control."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def holm(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted((p, k) for k, p in pvals.items() if p is not None and not math.isnan(p))
    m, out, running = len(items), {}, 0.0
    for i, (p, k) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


def cluster_bootstrap(values: pd.Series, clusters: pd.Series, n: int = 2000, seed: int = 7):
    """Resample whole clusters (days, or hours for short samples) so trades
    that share a time bucket — correlated across assets/durations — are not
    treated as independent."""
    df = pd.DataFrame({"v": values.to_numpy(float), "c": clusters.to_numpy()})
    sums = df.groupby("c")["v"].sum().to_numpy()
    counts = df.groupby("c")["v"].size().to_numpy()
    k = len(sums)
    if k < 3:
        return {"clusters": k, "ci95_total": None, "p_le_0": None}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, size=(n, k))
    tot = sums[idx].sum(axis=1)
    mean_per_trade = tot / counts[idx].sum(axis=1)
    return {"clusters": k, "ci95_total": [round(float(np.percentile(tot, 2.5)), 2),
                                         round(float(np.percentile(tot, 97.5)), 2)],
            "ci95_mean_per_trade": [round(float(np.percentile(mean_per_trade, 2.5)), 4),
                                    round(float(np.percentile(mean_per_trade, 97.5)), 4)],
            "p_le_0": round(float((tot <= 0).mean()), 4)}


def _streak(won: pd.Series) -> int:
    best = cur = 0
    for w in won:
        cur = cur + 1 if not w else 0
        best = max(best, cur)
    return best


def summarize(ledger: pd.DataFrame, bankroll: float = 500.0) -> dict:
    if ledger.empty:
        return {"n_signals": 0}
    out = {"n_signals": int(len(ledger)),
           "rejections": ledger.loc[ledger.status == "rejected", "fill_reason"].value_counts().to_dict()}
    f = ledger[ledger.status.isin(["filled", "partial"])].copy()
    out["n_filled"] = int(len(f))
    out["n_partial"] = int((f.status == "partial").sum())
    s = f.dropna(subset=["pnl_net"]).copy() if "pnl_net" in f else f.iloc[0:0]
    out["n_unsettled"] = int(len(f) - len(s))
    if s.empty:
        return out
    s["end"] = s["start_epoch"] + s["minutes"] * 60
    s = s.sort_values("end")
    span_days = (s["end"].max() - s["end"].min()) / 86400
    bucket = 86400 if span_days >= 5 else 3600
    s["cluster"] = (s["end"] // bucket).astype(int)
    eff = s["end"].floordiv(900).nunique()  # trades closing in the same 15 min ~ one observation
    wins, losses = s.loc[s.pnl_net > 0, "pnl_net"], s.loc[s.pnl_net <= 0, "pnl_net"]
    cum = s["pnl_net"].cumsum()
    deployed = float((s["cost"] + s["fee"]).sum())
    out |= {
        "n_settled": int(len(s)),
        "effective_independent_n": int(eff),
        "gross_pnl": round(float(s.pnl_gross.sum()), 2),
        "fees": round(float(s.fee.sum()), 2),
        "net_pnl": round(float(s.pnl_net.sum()), 2),
        "capital_assumption": f"fixed shares per trade, bankroll ${bankroll:.0f}, capital locked to settlement",
        "return_on_bankroll": round(float(s.pnl_net.sum()) / bankroll, 4),
        "return_on_deployed": round(float(s.pnl_net.sum()) / deployed, 4) if deployed else None,
        "win_rate": round(float((s.pnl_net > 0).mean()), 4),
        "avg_win": round(float(wins.mean()), 3) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 3) if len(losses) else None,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if len(losses) and losses.sum() < 0 else None,
        "max_drawdown": round(float((cum.cummax() - cum).max()), 2),
        "longest_losing_streak": _streak(s.pnl_net > 0),
        "avg_entry_price": round(float(s.vwap.mean()), 4),
        "bootstrap": cluster_bootstrap(s.pnl_net, s.cluster) | {"cluster_unit": "day" if bucket == 86400 else "hour"},
        "by_combo": (s.groupby(["asset", "minutes"]).agg(n=("pnl_net", "size"), net=("pnl_net", "sum"),
                                                         win=("won", "mean"))
                     .round(3).reset_index().to_dict("records")),
        "by_day": (s.assign(day=pd.to_datetime(s.end, unit="s").dt.strftime("%Y-%m-%d"))
                   .groupby("day")["pnl_net"].agg(["size", "sum"]).round(2).reset_index().to_dict("records")),
    }
    return out


# ---------------------------------------------------------------- outcomes --
def _binom_p_two_sided(k: int, n: int, p: float = 0.5) -> float:
    if n == 0:
        return float("nan")
    z = (k - n * p) / math.sqrt(n * p * (1 - p))
    return math.erfc(abs(z) / math.sqrt(2))


def outcome_base_rates(out: pd.DataFrame) -> dict:
    """Signal-only tests on resolved outcomes (no prices => no profit claim).
    Up wins ties (rule: end >= start), so a tiny Up bias is mechanical."""
    res = {}
    o = out.dropna(subset=["resolved_up"]).copy()
    if o.empty:
        return {"note": "no resolved outcomes"}
    o["up"] = o["resolved_up"].astype(str).isin(["True", "1", "1.0"])
    for (a, m), g in o.groupby(["asset", "minutes"]):
        g = g.sort_values("start_epoch")
        k, n = int(g.up.sum()), int(len(g))
        contiguous = g.start_epoch.diff() == m * 60
        prev = g.up.shift(1)
        uu = g[contiguous & (prev == True)].up  # noqa: E712
        du = g[contiguous & (prev == False)].up  # noqa: E712
        diff = uu.mean() - du.mean() if len(uu) and len(du) else float("nan")
        se = math.sqrt(uu.var(ddof=1) / max(len(uu), 1) + du.var(ddof=1) / max(len(du), 1)) if len(uu) > 1 and len(du) > 1 else float("nan")
        g["hour"] = pd.to_datetime(g.start_epoch, unit="s").dt.hour
        hours = {int(h): _binom_p_two_sided(int(x.up.sum()), len(x)) for h, x in g.groupby("hour")}
        hp = holm({str(h): p for h, p in hours.items()})
        res[f"{a}_{m}m"] = {
            "n": n, "p_up": round(k / n, 4), "p_value_vs_half": round(_binom_p_two_sided(k, n), 4),
            "p_up_after_up": round(float(uu.mean()), 4) if len(uu) else None,
            "p_up_after_down": round(float(du.mean()), 4) if len(du) else None,
            "autocorr_diff": None if math.isnan(diff) else round(float(diff), 4),
            "autocorr_z": None if math.isnan(se) or se == 0 else round(float(diff / se), 2),
            "slot_hours_significant_after_holm": [h for h, p in hp.items() if p < 0.05],
            "span_days": round((g.start_epoch.max() - g.start_epoch.min()) / 86400, 1)}
    return res


def calibration(books: pd.DataFrame, outcomes: pd.DataFrame, at_fracs=(0.25, 0.5, 0.75, 0.9)) -> list[dict]:
    """Executable ask vs realized win frequency. For each market and checkpoint
    (fraction of window elapsed) take the last snapshot at or before it, per
    side. A side priced at ask a that wins more than a + fee/share is the raw
    material of any taker edge; less means takers lose there."""
    if books.empty or outcomes.empty:
        return []
    o = outcomes.dropna(subset=["resolved_up"]).copy()
    o["up"] = o["resolved_up"].astype(str).isin(["True", "1", "1.0"])
    lab = o.set_index(["asset", "minutes", "start_epoch"])["up"].to_dict()
    b = books.dropna(subset=["best_ask"]).copy()
    b["frac"] = (b.t_recv - b.start_epoch) / (b.minutes * 60)
    rows = []
    for f in at_fracs:
        snap = b[b.frac <= f].sort_values("t_recv").groupby(["asset", "minutes", "start_epoch", "side"]).tail(1)
        snap = snap[snap.frac >= f - 0.05]
        for r in snap.itertuples():
            u = lab.get((r.asset, int(r.minutes), int(r.start_epoch)))
            if u is None:
                continue
            rows.append({"asset": r.asset, "minutes": int(r.minutes), "frac": f, "ask": r.best_ask,
                         "won": u if r.side == "up" else (not u)})
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df["bucket"] = pd.cut(df.ask, [0, 0.1, 0.3, 0.5, 0.7, 0.9, 0.97, 1.0])
    g = df.groupby(["asset", "minutes", "frac", "bucket"], observed=True).agg(
        n=("won", "size"), mean_ask=("ask", "mean"), win_rate=("won", "mean")).reset_index()
    g["fee_ps"] = 0.07 * g.mean_ask * (1 - g.mean_ask)
    g["edge_after_fee"] = g.win_rate - g.mean_ask - g.fee_ps
    g["se"] = (g.win_rate * (1 - g.win_rate) / g.n).pow(0.5)
    g["bucket"] = g["bucket"].astype(str)
    return g.round(4).to_dict("records")
