"""Strategy hypotheses as causal decision rules.

Each `decide(ctx)` sees only information with receipt time <= ctx.t. It
returns ("up"|"down", limit_price, reason) to enter, or None to skip. At most
one entry per market (fixed size); positions are held to settlement (these
contracts settle in minutes, so an explicit exit rule is 'hold to resolution').

See research/HYPOTHESES.md for the economic rationale and falsification
criteria of each."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .execution import taker_fee


@dataclass
class Ctx:
    asset: str
    minutes: int
    start_epoch: int
    end_epoch: int
    t: float
    up_ask: float | None
    down_ask: float | None
    up_bid: float | None
    down_bid: float | None
    spot: float | None            # external proxy, last observed before t
    spot0: float | None           # reference: priceToBeat if published, else proxy at start
    spot0_is_official: bool
    sigma_per_s: float | None     # trailing realized vol of log spot, per sqrt(second)
    prev_dir_proxy: int | None    # +1/-1 sign of previous window's proxy move
    other_asset_ret: float | None # log return of the other asset since this window's start
    spot_avg_so_far: float | None = None  # proxy average price from window start to t

    @property
    def tau(self) -> float:
        return self.end_epoch - self.t

    @property
    def elapsed_frac(self) -> float:
        return (self.t - self.start_epoch) / (self.end_epoch - self.start_epoch)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def p_up_model(ctx: Ctx) -> float | None:
    """P(Up) under the verified rule: Up iff the window's TWAP >= start price.

    Final average = f*A_elapsed + (1-f)*A_remaining. With driftless motion the
    remaining average is ~ S_t with sd S_t*sigma*sqrt(tau/3). Falls back to the
    end-price form only when no elapsed average is available. Uses the external
    proxy, so it carries basis risk to the Chainlink stream that settles."""
    if None in (ctx.spot, ctx.spot0, ctx.sigma_per_s) or ctx.sigma_per_s <= 0 or ctx.tau <= 0:
        return None
    if ctx.spot_avg_so_far is None:
        z = math.log(ctx.spot / ctx.spot0) / (ctx.sigma_per_s * math.sqrt(ctx.tau))
        return norm_cdf(z)
    f = min(max(ctx.elapsed_frac, 0.0), 1.0)
    mean_final = f * ctx.spot_avg_so_far + (1 - f) * ctx.spot
    sd = (1 - f) * ctx.spot * ctx.sigma_per_s * math.sqrt(ctx.tau / 3.0)
    if sd <= 0:
        return 1.0 if mean_final >= ctx.spot0 else 0.0
    return norm_cdf((mean_final - ctx.spot0) / sd)


@dataclass
class Strategy:
    name: str
    params: dict = field(default_factory=dict)

    def decide(self, ctx: Ctx):  # pragma: no cover - interface
        raise NotImplementedError

    @property
    def variant_id(self) -> str:
        return self.name + "|" + ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))


class FairValue(Strategy):
    """H1: settlement probability implied by the reference move vs executable ask."""

    def decide(self, ctx):
        lo, hi = self.params.get("window", (0.2, 0.9))
        if not (lo <= ctx.elapsed_frac <= hi):
            return None
        p = p_up_model(ctx)
        if p is None:
            return None
        edge_min = self.params.get("edge_min", 0.04)
        best = None
        for side, prob, ask in (("up", p, ctx.up_ask), ("down", 1 - p, ctx.down_ask)):
            if ask is None or not (0.02 <= ask <= 0.98):
                continue
            fee_ps = taker_fee(1.0, ask)
            edge = prob - ask - fee_ps
            if edge > edge_min and (best is None or edge > best[2]):
                # limit leaves room to walk the book but never past the edge
                best = (side, round(prob - fee_ps - edge_min / 2, 4), edge)
        return (best[0], best[1], f"edge={best[2]:.3f}") if best else None


class LateFavorite(Strategy):
    """H2: favourite-longshot bias near expiry — favourites underpriced late."""

    def decide(self, ctx):
        if ctx.tau > self.params.get("tau_max", 60) or ctx.tau < 3:
            return None
        lo, hi = self.params.get("band", (0.88, 0.97))
        for side, ask in (("up", ctx.up_ask), ("down", ctx.down_ask)):
            if ask is not None and lo <= ask <= hi:
                return side, hi, f"ask={ask}"
        return None


class PrevMomentum(Strategy):
    """H3: short-horizon continuation (or reversal if mode='revert') of the
    previous window's direction, entered early at near-even prices."""

    def decide(self, ctx):
        if ctx.prev_dir_proxy is None or ctx.elapsed_frac > self.params.get("max_elapsed", 0.1):
            return None
        d = ctx.prev_dir_proxy * (-1 if self.params.get("mode") == "revert" else 1)
        side = "up" if d > 0 else "down"
        ask = ctx.up_ask if side == "up" else ctx.down_ask
        cap = self.params.get("max_ask", 0.55)
        return (side, cap, "prev_dir") if ask is not None and ask <= cap else None


class CrossAssetLead(Strategy):
    """H4: BTC moves first; ETH market lags BTC's move since ETH window start."""

    def decide(self, ctx):
        if ctx.asset != "eth" or ctx.other_asset_ret is None:
            return None
        if not (0.15 <= ctx.elapsed_frac <= 0.8) or ctx.spot is None or ctx.spot0 is None:
            return None
        own = math.log(ctx.spot / ctx.spot0)
        gap = ctx.other_asset_ret - own
        thr = self.params.get("gap", 0.0008)
        cap = self.params.get("max_ask", 0.6)
        if gap > thr and ctx.up_ask is not None and ctx.up_ask <= cap:
            return "up", cap, f"gap={gap:.5f}"
        if gap < -thr and ctx.down_ask is not None and ctx.down_ask <= cap:
            return "down", cap, f"gap={gap:.5f}"
        return None


class EarlyBaseline(Strategy):
    """Baseline: buy a fixed or random side shortly after open if near even."""

    def decide(self, ctx):
        if ctx.elapsed_frac > 0.1:
            return None
        mode = self.params.get("side", "up")
        if mode == "random":
            rnd = random.Random(f"{ctx.asset}{ctx.minutes}{ctx.start_epoch}")
            side = rnd.choice(["up", "down"])
        else:
            side = mode
        ask = ctx.up_ask if side == "up" else ctx.down_ask
        return (side, 0.55, "baseline") if ask is not None and ask <= 0.55 else None


def default_grid() -> list[Strategy]:
    """Small, pre-declared variant grid. Every variant run is logged to the
    research register and counted in the multiple-comparison correction."""
    g: list[Strategy] = [EarlyBaseline("baseline_up", {"side": "up"}),
                         EarlyBaseline("baseline_random", {"side": "random"})]
    for e in (0.03, 0.06):
        for w in ((0.2, 0.6), (0.5, 0.9)):
            g.append(FairValue("fair_value", {"edge_min": e, "window": w}))
    for tm in (30, 90):
        for band in ((0.85, 0.93), (0.93, 0.98)):
            g.append(LateFavorite("late_favorite", {"tau_max": tm, "band": band}))
    for mode in ("follow", "revert"):
        g.append(PrevMomentum("prev_momentum", {"mode": mode}))
    for gap in (0.0005, 0.0010):
        g.append(CrossAssetLead("cross_asset_lead", {"gap": gap}))
    return g
