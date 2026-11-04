"""Look-back analysis of PAST markets only (no forward collection needed).

Uses backfilled outcomes plus each market's 1-minute Up-price history. The
price history is a trade/mid series, not an order book: entry costs are
approximated as price + assumed half-spread + documented taker fee. Results
are therefore a screen for where an edge *might* be, not proof of fills.

Discovery = older 2/3 of days, holdout = newest 1/3. Rules are fixed in
advance below; everything run is Holm-corrected together."""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from .config import CRYPTO_TAKER_FEE_RATE, OUTCOMES, REPORTS
from .stats import cluster_bootstrap, holm, outcome_base_rates

HALF_SPREAD = 0.01  # assumed extra cost per share over the recorded price


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    outs, pxs = [], []
    for p in OUTCOMES.glob("*_*m.csv"):
        df = pd.read_csv(p)
        outs.append(df)
        pp = p.with_name(p.stem + "_prices.csv")
        if pp.exists() and pp.stat().st_size > 30:
            x = pd.read_csv(pp)
            x["asset"], x["minutes"] = df["asset"].iloc[0], int(df["minutes"].iloc[0])
            pxs.append(x)
    o = pd.concat(outs, ignore_index=True) if outs else pd.DataFrame()
    if not o.empty:
        o = o.dropna(subset=["resolved_up"]).copy()
        o["up"] = o["resolved_up"].astype(str).isin(["True", "1", "1.0"])
    return o, (pd.concat(pxs, ignore_index=True) if pxs else pd.DataFrame())


def _price_at(px: pd.DataFrame, elapsed_s: int) -> pd.Series:
    """Last Up price at or before start + elapsed_s, per market (causal)."""
    x = px[px.t <= px.start_epoch + elapsed_s]
    x = x[x.t >= x.start_epoch + elapsed_s - 90]  # must be recent
    return x.sort_values("t").groupby(["asset", "minutes", "start_epoch"])["p_up"].last()


def cost_per_share(p):
    p = np.asarray(p, float)
    return p + HALF_SPREAD + CRYPTO_TAKER_FEE_RATE * p * (1 - p)


def calibration(o: pd.DataFrame, px: pd.DataFrame) -> list[dict]:
    rows = []
    for m in sorted(o.minutes.unique()):
        for k in range(1, int(m)):
            pu = _price_at(px[px.minutes == m], k * 60).rename("p_up").reset_index()
            d = pu.merge(o[["asset", "minutes", "start_epoch", "up"]], on=["asset", "minutes", "start_epoch"])
            both = pd.concat([d.assign(price=d.p_up, won=d.up),
                              d.assign(price=1 - d.p_up, won=~d.up)])
            both["bucket"] = pd.cut(both.price, [0, .1, .3, .5, .7, .9, .97, 1.0])
            g = both.groupby(["asset", "bucket"], observed=True).agg(
                n=("won", "size"), price=("price", "mean"), win=("won", "mean")).reset_index()
            g["minutes"], g["elapsed_min"] = int(m), k
            rows.append(g)
    if not rows:
        return []
    g = pd.concat(rows)
    g["edge_after_costs"] = g.win - cost_per_share(g.price)
    g["se"] = np.sqrt(g.win * (1 - g.win) / g.n)
    g["bucket"] = g.bucket.astype(str)
    return g.round(4).to_dict("records")


def _trades(o: pd.DataFrame, px: pd.DataFrame) -> pd.DataFrame:
    """Pre-declared approximate strategies, one entry per market, 1 share."""
    T = []
    for m in sorted(o.minutes.unique()):
        om = o[o.minutes == m].set_index(["asset", "minutes", "start_epoch"])
        pm = px[px.minutes == m]
        open_p = _price_at(pm, 30)                       # ~open
        late_p = _price_at(pm, (int(m) - 1) * 60)        # last minute mark
        prev = om["up"].copy()
        prev.index = pd.MultiIndex.from_tuples([(a, mm, s + int(m) * 60) for a, mm, s in prev.index],
                                               names=prev.index.names)
        for key, won_up in om["up"].items():
            po, pl, pv = open_p.get(key), late_p.get(key), prev.get(key)
            base = {"asset": key[0], "minutes": key[1], "start_epoch": key[2]}
            if po is not None and po <= 0.55:
                T.append(base | {"strategy": "baseline_up_open", "price": po, "won": won_up})
            if pv is not None and po is not None:
                for mode, side_up in (("follow", bool(pv)), ("revert", not bool(pv))):
                    p = po if side_up else 1 - po
                    if p <= 0.55:
                        T.append(base | {"strategy": f"prev_{mode}_open", "price": p,
                                         "won": won_up if side_up else not won_up})
            if pl is not None:
                for lo, hi in ((0.85, 0.93), (0.93, 0.98)):
                    for side_up, p in ((True, pl), (False, 1 - pl)):
                        if lo <= p <= hi:
                            T.append(base | {"strategy": f"late_fav_{lo}-{hi}", "price": p,
                                             "won": won_up if side_up else not won_up})
    t = pd.DataFrame(T)
    if not t.empty:
        t["pnl"] = t.won.astype(float) - cost_per_share(t.price)
        t["day"] = (t.start_epoch // 86400).astype(int)
    return t


def _summ(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"n": 0}
    span = (t.start_epoch.max() - t.start_epoch.min()) / 86400
    clusters = t.day if span >= 5 else t.start_epoch // 3600  # hour clusters for short samples
    bs = cluster_bootstrap(t.pnl, clusters)
    return {"n": int(len(t)), "win_rate": round(float(t.won.mean()), 4),
            "avg_price": round(float(t.price.mean()), 4),
            "net_per_share": round(float(t.pnl.mean()), 4), "net_total": round(float(t.pnl.sum()), 2),
            "days": int(t.day.nunique()), "p_le_0": bs.get("p_le_0"), "ci95_total": bs.get("ci95_total")}


def report() -> dict:
    o, px = load()
    rep = {"generated": time.time(), "half_spread_assumed": HALF_SPREAD,
           "markets": int(len(o)), "price_points": int(len(px))}
    if o.empty:
        rep["status"] = "no backfilled outcomes yet: run workflow 2"
        return _write(rep)
    rep["base_rates"] = outcome_base_rates(o)
    if px.empty:
        rep["status"] = "outcomes only (no price history returned): base rates are the whole result"
        return _write(rep)
    days = np.sort(o.start_epoch.floordiv(86400).unique())
    cut = days[int(len(days) * 2 / 3)] if len(days) >= 3 else days[-1] + 1
    disc_o, hold_o = o[o.start_epoch // 86400 < cut], o[o.start_epoch // 86400 >= cut]
    rep["split"] = {"discovery_days": int((days < cut).sum()), "holdout_days": int((days >= cut).sum())}
    rep["calibration_discovery"] = calibration(disc_o, px)
    td, th = _trades(disc_o, px), _trades(hold_o, px)
    res, pv = {}, {}
    for (strat, a, m), g in td.groupby(["strategy", "asset", "minutes"]) if not td.empty else []:
        key = f"{strat} · {a} {m}m"
        h = th[(th.strategy == strat) & (th.asset == a) & (th.minutes == m)] if not th.empty else th
        res[key] = {"discovery": _summ(g), "holdout": _summ(h)}
        pv[key] = res[key]["discovery"].get("p_le_0")
    adj = holm({k: v for k, v in pv.items() if v is not None})
    for k, r in res.items():
        r["holm_p_discovery"] = adj.get(k)
        d, h = r["discovery"], r["holdout"]
        if d.get("n", 0) < 100 or h.get("n", 0) < 50:
            r["verdict"] = "insufficient evidence"
        elif adj.get(k, 1) < 0.10 and d["net_per_share"] > 0 and h["net_per_share"] > 0:
            r["verdict"] = "promising on history (needs real order-book test)"
        else:
            r["verdict"] = "rejected"
    rep["strategies"], rep["n_tests"] = res, len(res)
    return _write(rep)


def _write(rep: dict) -> dict:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "history_latest.json").write_text(json.dumps(rep, indent=2, default=str))
    (REPORTS / "history_latest.md").write_text(to_md(rep))
    return rep


def to_md(rep: dict) -> str:
    L = ["# How past markets closed", "",
         f"Markets: {rep['markets']}. Price points: {rep['price_points']}. "
         f"Assumed half-spread: {rep['half_spread_assumed']}. Taker fee: 0.07·p·(1−p).", "",
         "> Prices are historical trade/mid prices, not order books. Profit figures are a screen, "
         "not achievable fills.", ""]
    if rep.get("status"):
        L += [f"**Status:** {rep['status']}", ""]
    if rep.get("base_rates"):
        L += ["## Outcomes", "", "| combo | markets | days | Up % | p vs 50% | Up after Up | Up after Down | z | hours flagged (Holm) |",
              "|---|---|---|---|---|---|---|---|---|"]
        for k, v in rep["base_rates"].items():
            if isinstance(v, dict):
                L.append(f"| {k} | {v['n']} | {v['span_days']} | {v['p_up']} | {v['p_value_vs_half']} | "
                         f"{v['p_up_after_up']} | {v['p_up_after_down']} | {v['autocorr_z']} | "
                         f"{v['slot_hours_significant_after_holm'] or '-'} |")
        L.append("")
    if rep.get("strategies"):
        L += [f"## Approximate strategies ({rep['n_tests']} tests, Holm-corrected)",
              f"Discovery = older {rep['split']['discovery_days']} days, holdout = newest {rep['split']['holdout_days']} days. Net is per 1-share trade after assumed costs.", "",
              "| strategy | disc n | disc win | disc net/share | Holm p | hold n | hold net/share | verdict |",
              "|---|---|---|---|---|---|---|---|"]
        for k, r in rep["strategies"].items():
            d, h = r["discovery"], r["holdout"]
            L.append(f"| {k} | {d.get('n')} | {d.get('win_rate')} | {d.get('net_per_share')} | "
                     f"{r.get('holm_p_discovery')} | {h.get('n')} | {h.get('net_per_share')} | {r['verdict']} |")
        L.append("")
    if rep.get("calibration_discovery"):
        cal = [c for c in rep["calibration_discovery"] if c["n"] >= 30]
        best = sorted(cal, key=lambda c: -c["edge_after_costs"])[:10]
        L += ["## Calibration (discovery): top 10 cells by edge after costs", "",
              "Many cells are inspected; treat these as leads only.", "",
              "| combo | minute | price bucket | n | avg price | win rate | edge after costs | se |",
              "|---|---|---|---|---|---|---|---|"]
        for c in best:
            L.append(f"| {c['asset']} {c['minutes']}m | {c['elapsed_min']} | {c['bucket']} | {c['n']} | "
                     f"{c['price']} | {c['win']} | {c['edge_after_costs']} | {c['se']} |")
    return "\n".join(L)
