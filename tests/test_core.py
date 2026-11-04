import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from pmr import strategies as S
from pmr.backtest import SpotIndex, chronological_splits, run_backtest
from pmr.config import market_slug, window_start
from pmr.discovery import parse_event
from pmr.execution import BookIndex, ExecParams, simulate_buy, taker_fee
from pmr.http import fetch
from pmr.stats import holm, summarize

from .synthetic import make

EP5 = ExecParams(stale_s=6.0)  # synthetic books are 5s apart


def test_fee_matches_documented_example():
    # docs.polymarket.com/trading/fees: 100 shares at 50c, crypto rate 0.07 -> $1.75
    assert taker_fee(100, 0.5) == pytest.approx(1.75)
    assert taker_fee(100, 0.3) == pytest.approx(taker_fee(100, 0.7))  # symmetric


def test_slug_and_window():
    assert market_slug("btc", 5, 1779909000) == "btc-updown-5m-1779909000"
    assert window_start(1779909123, 5) == 1779909000
    assert window_start(1779909123, 15) % 900 == 0


def test_parse_event_orders_outcomes_and_resolution():
    ev = {"markets": [{"outcomes": '["Down","Up"]', "clobTokenIds": '["D","U"]',
                       "outcomePrices": '["0","1"]', "closed": True, "conditionId": "c",
                       "orderMinSize": 5, "orderPriceMinTickSize": 0.01}],
          "eventMetadata": {"priceToBeat": 61234.5}}
    m = parse_event(ev, "btc", 5, 1779909000)
    assert (m.token_up, m.token_down) == ("U", "D")
    assert m.resolved_up() is True and m.price_to_beat == 61234.5


def test_refuses_order_endpoints():
    with pytest.raises(PermissionError):
        fetch("https://clob.polymarket.com/order", json_body={})


def _one_book(asks, t=100.3):
    return pd.DataFrame([{"t_recv": t, "asset": "btc", "minutes": 5, "start_epoch": 0, "side": "up",
                          "asks": json.dumps(asks), "best_ask": asks[0][0] if asks else None,
                          "best_bid": 0.4}])


def test_execution_walks_book_partial_and_rejects():
    bi = BookIndex(_one_book([[0.50, 20], [0.52, 20], [0.60, 100]]))
    ep = ExecParams(delay_s=0.2, depth_share=1.0)
    f = simulate_buy(bi, ("btc", 5, 0, "up"), 100.0, 30, 0.55, ep, end_epoch=300)
    assert f.status == "filled" and f.shares == 30
    assert f.cost == pytest.approx(20 * 0.50 + 10 * 0.52)
    assert f.fee == pytest.approx(taker_fee(20, 0.5) + taker_fee(10, 0.52), abs=1e-5)
    f = simulate_buy(bi, ("btc", 5, 0, "up"), 100.0, 60, 0.55, ep, end_epoch=300)
    assert f.status == "partial" and f.shares == 40
    # depth haircut halves what we can take
    f = simulate_buy(bi, ("btc", 5, 0, "up"), 100.0, 60, 0.55, replace(ep, depth_share=0.5), 300)
    assert f.shares == 20
    assert simulate_buy(bi, ("btc", 5, 0, "up"), 90.0, 10, 0.55, ep, 300).reason == "no_fresh_book"
    assert simulate_buy(bi, ("btc", 5, 0, "up"), 299.9, 10, 0.55, ep, 300).reason == "arrives_after_close"
    assert simulate_buy(bi, ("btc", 5, 0, "up"), 100.0, 10, 0.45, ep, 300).status == "rejected"


def test_splits_are_chronological_with_embargo():
    starts = np.arange(0, 300 * 300, 300)
    sp = chronological_splits(starts)
    assert sp["train"][1] < sp["validation"][0] < sp["validation"][1] < sp["test"][0]
    assert sp["validation"][0] - sp["train"][1] >= 900


def test_holm_monotone():
    adj = holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)


def test_no_lookahead_in_spot_index():
    spot = pd.DataFrame({"t_recv": [0.0, 1.0, 2.0], "asset": "btc", "price": [1.0, 2.0, 3.0]})
    si = SpotIndex(spot)
    assert si.at("btc", 1.5)[0] == 2.0  # never the future 3.0
    assert si.at("btc", -1)[0] is None


def test_null_market_yields_no_edge():
    """Efficient synthetic market: fair-value strategy must not show a
    significant positive edge after spread and fees."""
    data, outc = make(hours=24, step_s=5)
    st = S.FairValue("fair_value", {"edge_min": 0.03, "window": (0.2, 0.9)})
    led = run_backtest(data, outc, st, EP5, (0, 1e12))
    s = summarize(led)
    assert s.get("n_settled", 0) == 0 or s["net_pnl"] < 0 or s["bootstrap"]["p_le_0"] > 0.05
    # forcing trades with no edge threshold must not manufacture profit
    st0 = S.FairValue("fair_value", {"edge_min": -0.05, "window": (0.2, 0.9)})
    s0 = summarize(run_backtest(data, outc, st0, EP5, (0, 1e12)))
    assert s0["n_settled"] > 50
    assert s0["net_pnl"] < 0 or s0["bootstrap"]["p_le_0"] > 0.05


def test_injected_mispricing_is_detected():
    """Sanity check of power: if Down asks are systematically too cheap, buying
    on the model should profit. (Synthetic; proves the harness can see an edge.)"""
    data, outc = make(hours=24, step_s=5, mispricing=0.12)  # Up overpriced => Down underpriced
    st = S.FairValue("fair_value", {"edge_min": 0.03, "window": (0.2, 0.9)})
    s = summarize(run_backtest(data, outc, st, EP5, (0, 1e12)))
    assert s["net_pnl"] > 0


def test_ledger_reconciles():
    data, outc = make(hours=12, step_s=5, mispricing=0.05)
    st = S.LateFavorite("late_favorite", {"tau_max": 90, "band": (0.85, 0.97)})
    led = run_backtest(data, outc, st, EP5, (0, 1e12))
    f = led.dropna(subset=["pnl_net"])
    assert np.allclose(f.pnl_net, f.payout - f.cost - f.fee)
    assert summarize(led)["net_pnl"] == pytest.approx(round(f.pnl_net.sum(), 2))
    assert led.groupby(["asset", "minutes", "start_epoch"]).size().max() == 1  # one entry per market


def test_twap_rule_model():
    """Late in the settlement window, a high average so far keeps Up likely even
    if spot has just dipped below the start price; before the window opens the
    same spot dip makes Up less likely than not."""
    base = dict(asset="btc", minutes=5, start_epoch=0, end_epoch=300, up_ask=None, down_ask=None,
                up_bid=None, down_bid=None, spot0=100.0, spot0_is_official=True, sigma_per_s=0.0003,
                prev_dir_proxy=None, other_asset_ret=None)
    late = S.Ctx(t=270.0, spot=99.95, spot_avg_so_far=100.2, **base)
    assert S.p_up_model(late) > 0.9
    early = S.Ctx(t=100.0, spot=99.95, spot_avg_so_far=None, **base)
    assert S.p_up_model(early) < 0.5
