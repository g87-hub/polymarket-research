"""Paper execution model for taker buys against recorded order books.

Costs are counted exactly once:
  * spread and slippage come only from walking recorded ask levels
    (`extra_slip_ticks` is a sensitivity knob, default 0, NOT a second spread charge);
  * the taker fee is the documented formula on the filled shares at each level.
Maker (resting) orders are deliberately not simulated: a quote being visible
says nothing about whether our order would have been filled."""
from __future__ import annotations

import bisect
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import CRYPTO_TAKER_FEE_RATE


def taker_fee(shares: float, price: float, rate: float = CRYPTO_TAKER_FEE_RATE) -> float:
    return round(shares * rate * price * (1.0 - price), 5)


@dataclass(frozen=True)
class ExecParams:
    delay_s: float = 1.0          # observation -> order arrival
    stale_s: float = 3.0          # reject if no fresh book this soon after arrival
    depth_share: float = 0.5      # fraction of displayed size we assume we win vs other takers
    extra_slip_ticks: int = 0     # sensitivity only
    tick: float = 0.01
    fee_rate: float = CRYPTO_TAKER_FEE_RATE
    fee_mult: float = 1.0
    min_shares: float = 5.0       # replaced by market orderMinSize when known


@dataclass
class Fill:
    status: str                   # filled | partial | rejected
    reason: str
    t_arrive: float
    t_book: float | None
    shares: float = 0.0
    cost: float = 0.0             # USDC paid for shares (ex fee)
    fee: float = 0.0
    vwap: float | None = None
    top_ask: float | None = None


class BookIndex:
    """Per (asset, minutes, start_epoch, side) time-sorted snapshots."""

    def __init__(self, books: pd.DataFrame):
        self.idx: dict[tuple, tuple[np.ndarray, list]] = {}
        if books.empty:
            return
        books = books.sort_values("t_recv")
        for key, g in books.groupby(["asset", "minutes", "start_epoch", "side"], sort=False):
            self.idx[key] = (g["t_recv"].to_numpy(float), g[["asks", "best_ask", "best_bid"]].to_dict("records"))

    def at_or_after(self, key, t: float):
        ts, recs = self.idx.get(key, (None, None))
        if ts is None:
            return None, None
        i = bisect.bisect_left(ts, t)
        return (ts[i], recs[i]) if i < len(ts) else (None, None)

    def last_before(self, key, t: float):
        ts, recs = self.idx.get(key, (None, None))
        if ts is None:
            return None, None
        i = bisect.bisect_right(ts, t) - 1
        return (ts[i], recs[i]) if i >= 0 else (None, None)


def simulate_buy(bi: BookIndex, key: tuple, t_decision: float, shares: float,
                 limit_price: float, ep: ExecParams, end_epoch: float,
                 min_shares: float | None = None) -> Fill:
    t_arr = t_decision + ep.delay_s
    if t_arr >= end_epoch:
        return Fill("rejected", "arrives_after_close", t_arr, None)
    t_book, rec = bi.at_or_after(key, t_arr)
    if rec is None or t_book - t_arr > ep.stale_s or t_book >= end_epoch:
        return Fill("rejected", "no_fresh_book", t_arr, t_book)
    asks = json.loads(rec["asks"]) if isinstance(rec["asks"], str) else rec["asks"]
    if not asks:
        return Fill("rejected", "empty_book", t_arr, t_book)
    left, cost, fee, got = shares, 0.0, 0.0, 0.0
    for px, sz in asks:
        px = round(px + ep.extra_slip_ticks * ep.tick, 6)
        if px > limit_price or px >= 1.0:
            break
        take = min(left, sz * ep.depth_share)
        if take <= 0:
            continue
        cost += take * px
        fee += taker_fee(take, px, ep.fee_rate) * ep.fee_mult
        got += take
        left -= take
        if left <= 1e-9:
            break
    mins = min_shares if min_shares is not None and not np.isnan(min_shares) else ep.min_shares
    if got < mins:
        return Fill("rejected", "below_min_size_or_limit", t_arr, t_book, top_ask=asks[0][0])
    status = "filled" if left <= 1e-9 else "partial"
    return Fill(status, "ok", t_arr, t_book, got, round(cost, 6), round(fee, 6), cost / got, asks[0][0])
