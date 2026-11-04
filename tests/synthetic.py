"""Synthetic fixtures for TESTS ONLY. Never used as research evidence.

Settles like the real markets (window TWAP >= start price). Builds an efficiently priced market: asks = true settlement probability +
half-spread, so a correct pipeline should find no edge after fees."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from pmr.strategies import norm_cdf


def make(hours: float = 24, minutes: int = 5, step_s: int = 5, sigma: float = 0.0002,
         half_spread: float = 0.01, seed: int = 1, t0: int = 1_800_000_000, mispricing: float = 0.0):
    rng = np.random.default_rng(seed)
    n = int(hours * 3600)
    t = t0 + np.arange(n, dtype=float)
    spot = 60000 * np.exp(np.cumsum(rng.normal(0, sigma, n)))
    spot_df = pd.DataFrame({"t_send": t - 0.05, "t_recv": t, "src_ts": t - 0.1, "asset": "btc",
                            "src": "synthetic", "price": spot, "bid": spot - 1, "ask": spot + 1})
    w = minutes * 60
    mk, books, outs = [], [], []
    for s in range(t0 + w * 3, t0 + n - w, w):  # leave warm-up for vol estimate
        e = s + w
        s0 = spot[s - t0]
        win = spot[s - t0:e - t0]
        up = bool(win.mean() >= s0)  # verified rule: window TWAP >= start price
        csum = np.cumsum(win)
        mk.append({"asset": "btc", "minutes": minutes, "start_epoch": s, "end_epoch": e,
                   "slug": f"btc-updown-{minutes}m-{s}", "min_order_size": 5.0, "tick_size": 0.01,
                   "price_to_beat": s0})
        outs.append({"asset": "btc", "minutes": minutes, "start_epoch": s, "resolved_up": up})
        for ts in range(s, e, step_s):
            k = ts - s + 1                      # seconds observed so far
            f, tau, st = k / w, w - k, spot[ts - t0]
            mean_final = f * (csum[k - 1] / k) + (1 - f) * st
            sd = (1 - f) * st * sigma * math.sqrt(tau / 3.0)
            p = (1.0 if mean_final >= s0 else 0.0) if sd <= 0 else norm_cdf((mean_final - s0) / sd)
            p = min(max(p + mispricing, 0.0), 1.0)
            for side, q in (("up", p), ("down", 1 - p)):
                ask = min(0.99, math.ceil((q + half_spread) * 100) / 100)
                bid = max(0.01, math.floor((q - half_spread) * 100) / 100)
                asks = [[round(ask + i * 0.01, 2), 100.0] for i in range(5) if ask + i * 0.01 < 1]
                bids = [[round(bid - i * 0.01, 2), 100.0] for i in range(5) if bid - i * 0.01 > 0]
                books.append({"t_send": ts + 0.2, "t_recv": float(ts) + 0.3, "src_ts": float(ts),
                              "asset": "btc", "minutes": minutes, "start_epoch": s, "side": side,
                              "best_bid": bids[0][0] if bids else None, "bid_sz": 100.0,
                              "best_ask": asks[0][0] if asks else None, "ask_sz": 100.0,
                              "asks": json.dumps(asks), "bids": json.dumps(bids), "hash": None})
    return ({"markets": pd.DataFrame(mk), "books": pd.DataFrame(books), "spot": spot_df},
            pd.DataFrame(outs))
