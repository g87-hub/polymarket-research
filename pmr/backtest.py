"""Event-driven paper backtest over recorded snapshots, producing a
trade-by-trade ledger. Fixed share size, capital locked until settlement."""
from __future__ import annotations

import bisect
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .execution import BookIndex, ExecParams, simulate_buy
from .strategies import Ctx, Strategy


class SpotIndex:
    def __init__(self, spot: pd.DataFrame, vol_lookback_s: int = 900):
        self.d = {}
        if spot.empty:
            return
        for a, g in spot.sort_values("t_recv").groupby("asset"):
            t = g["t_recv"].to_numpy(float)
            lp = np.log(g["price"].to_numpy(float))
            r2 = np.r_[0.0, np.diff(lp) ** 2]
            dt = np.r_[0.0, np.diff(t)]
            # time-window realized variance per second, causal (uses <= t)
            cs_r2, cs_dt = np.cumsum(r2), np.cumsum(dt)
            j = np.searchsorted(t, t - vol_lookback_s, side="left")
            var_s = (cs_r2 - cs_r2[j]) / np.maximum(cs_dt - cs_dt[j], 1e-9)
            enough = (cs_dt - cs_dt[j]) > vol_lookback_s * 0.5
            sig = np.where(enough, np.sqrt(var_s), np.nan)
            self.d[a] = (t, g["price"].to_numpy(float), sig)

    def at(self, asset: str, t: float):
        if asset not in self.d:
            return None, None
        ts, px, sig = self.d[asset]
        i = bisect.bisect_right(ts, t) - 1
        if i < 0 or t - ts[i] > 5:  # stale proxy -> unusable
            return None, None
        s = sig[i]
        return float(px[i]), (None if math.isnan(s) else float(s))


def chronological_splits(starts, fracs=(0.5, 0.25, 0.25), embargo_s: int = 900) -> dict:
    """Split by market start time. An embargo gap removes markets whose
    windows (up to 15 min) could straddle or inform the neighbouring split."""
    u = np.sort(np.unique(np.asarray(starts, dtype=float)))
    if len(u) < 8:
        raise ValueError(f"too few markets to split ({len(u)})")
    c1 = u[int(len(u) * fracs[0])]
    c2 = u[int(len(u) * (fracs[0] + fracs[1]))]
    return {"train": (u[0], c1 - embargo_s), "validation": (c1, c2 - embargo_s),
            "test": (c2, u[-1] + 1)}


def run_backtest(data: dict, outcomes: pd.DataFrame, strat: Strategy, ep: ExecParams,
                 period: tuple[float, float], shares: float = 20.0,
                 bankroll: float = 500.0, bi: BookIndex | None = None,
                 si: SpotIndex | None = None) -> pd.DataFrame:
    books, markets = data["books"], data["markets"]
    if books.empty or markets.empty:
        return pd.DataFrame()
    bi = bi or BookIndex(books)
    si = si or SpotIndex(data["spot"])
    lab = {}
    if outcomes is not None and not outcomes.empty:
        for r in outcomes.dropna(subset=["resolved_up"]).itertuples():
            lab[(r.asset, int(r.minutes), int(r.start_epoch))] = bool(r.resolved_up in (True, "True", 1, 1.0))

    mk = markets[(markets.start_epoch >= period[0]) & (markets.start_epoch < period[1])]
    mk = mk.sort_values("start_epoch")
    open_pos: list[tuple[float, float]] = []  # (end_epoch, capital)
    rows = []
    for m in mk.itertuples():
        key_up = (m.asset, int(m.minutes), int(m.start_epoch), "up")
        key_dn = key_up[:3] + ("down",)
        ts = bi.idx.get(key_up, (np.array([]), None))[0]
        ts = ts[(ts >= m.start_epoch) & (ts < m.end_epoch)]
        if len(ts) == 0:
            continue
        ptb = getattr(m, "price_to_beat", None)
        official = ptb is not None and not (isinstance(ptb, float) and math.isnan(ptb))
        other = "btc" if m.asset == "eth" else "eth"
        pm = int(m.minutes) * 60
        for t in ts:
            spot, sig = si.at(m.asset, t)
            s0 = float(ptb) if official else si.at(m.asset, m.start_epoch)[0]
            prev_a, prev_b = si.at(m.asset, m.start_epoch - pm)[0], si.at(m.asset, m.start_epoch)[0]
            o_now, o0 = si.at(other, t)[0], si.at(other, m.start_epoch)[0]
            _, ru = bi.last_before(key_up, t)
            _, rd = bi.last_before(key_dn, t)
            ctx = Ctx(m.asset, int(m.minutes), int(m.start_epoch), int(m.end_epoch), float(t),
                      ru and _f(ru["best_ask"]), rd and _f(rd["best_ask"]),
                      ru and _f(ru["best_bid"]), rd and _f(rd["best_bid"]),
                      spot, s0, official, sig,
                      None if None in (prev_a, prev_b) or prev_a == prev_b else (1 if prev_b > prev_a else -1),
                      None if None in (o_now, o0) else math.log(o_now / o0))
            dec = strat.decide(ctx)
            if dec is None:
                continue
            side, limit, reason = dec
            open_pos = [p for p in open_pos if p[0] > t]
            free = bankroll - sum(p[1] for p in open_pos)
            k = key_up if side == "up" else key_dn
            if free < shares * min(limit, 1.0):
                f = None
                status, freason = "rejected", "insufficient_capital"
            else:
                f = simulate_buy(bi, k, t, shares, limit, ep, m.end_epoch, getattr(m, "min_order_size", None))
                status, freason = f.status, f.reason
            lbl = lab.get(key_up[:3])
            row = {"variant": strat.variant_id, "strategy": strat.name, "asset": m.asset,
                   "minutes": int(m.minutes), "start_epoch": int(m.start_epoch),
                   "slot_utc": datetime.fromtimestamp(m.start_epoch, timezone.utc).strftime("%H:%M"),
                   "t_decision": float(t), "tau_s": round(m.end_epoch - t, 1), "side": side,
                   "limit": limit, "reason": reason, "status": status, "fill_reason": freason,
                   "ref_official": official, "outcome_up": lbl}
            if f is not None and f.status in ("filled", "partial"):
                won = None if lbl is None else (lbl if side == "up" else not lbl)
                payout = None if won is None else (f.shares if won else 0.0)
                row |= {"shares": f.shares, "vwap": f.vwap, "cost": f.cost, "fee": f.fee,
                        "top_ask": f.top_ask, "t_arrive": f.t_arrive, "t_book": f.t_book,
                        "won": won, "payout": payout,
                        "pnl_gross": None if payout is None else payout - f.cost,
                        "pnl_net": None if payout is None else payout - f.cost - f.fee}
                open_pos.append((m.end_epoch, f.cost + f.fee))
            rows.append(row)
            break  # one decision per market, whether or not it filled
    return pd.DataFrame(rows)


def _f(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def exec_params_dict(ep: ExecParams) -> dict:
    return json.loads(json.dumps(asdict(ep)))
