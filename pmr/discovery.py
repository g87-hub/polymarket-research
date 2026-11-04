"""Find Up/Down markets by slug and parse metadata defensively.

Field names are taken from Gamma responses but every one is optional: the
probe (`pmr probe`) saves raw payloads so the parser can be checked against
reality before any research relies on it."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .config import GAMMA, market_slug
from .http import fetch


def _maybe_json(v):
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


@dataclass
class MarketMeta:
    asset: str
    minutes: int
    start_epoch: int
    slug: str
    found: bool = False
    condition_id: str | None = None
    token_up: str | None = None
    token_down: str | None = None
    end_date: str | None = None
    closed: bool | None = None
    resolution_source: str | None = None
    rules: str | None = None
    min_order_size: float | None = None
    tick_size: float | None = None
    price_to_beat: float | None = None
    outcome_prices: list | None = None
    fee_fields: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def end_epoch(self) -> int:
        return self.start_epoch + self.minutes * 60

    def resolved_up(self) -> bool | None:
        """True/False once outcomePrices settle to 1/0; None otherwise."""
        if not self.closed or not self.outcome_prices:
            return None
        try:
            p = [float(x) for x in self.outcome_prices]
        except (TypeError, ValueError):
            return None
        if sorted(p) != [0.0, 1.0]:
            return None
        return p[0] == 1.0  # outcome order normalized to [Up, Down] in parse

    def to_dict(self) -> dict:
        d = asdict(self)
        d["end_epoch"] = self.end_epoch
        return d


def parse_event(event: dict, asset: str, minutes: int, start_epoch: int) -> MarketMeta:
    m = MarketMeta(asset, minutes, start_epoch, market_slug(asset, minutes, start_epoch))
    markets = event.get("markets") or []
    if not markets:
        m.error = "event has no markets"
        return m
    mk = markets[0]
    outcomes = [str(o).lower() for o in (_maybe_json(mk.get("outcomes")) or [])]
    tokens = _maybe_json(mk.get("clobTokenIds")) or []
    prices = _maybe_json(mk.get("outcomePrices"))
    if len(outcomes) != 2 or len(tokens) != 2 or set(outcomes) != {"up", "down"}:
        m.error = f"unexpected outcomes/tokens: {outcomes} {tokens}"
        return m
    iu, idn = outcomes.index("up"), outcomes.index("down")
    m.found = True
    m.condition_id = mk.get("conditionId")
    m.token_up, m.token_down = str(tokens[iu]), str(tokens[idn])
    if isinstance(prices, list) and len(prices) == 2:
        m.outcome_prices = [prices[iu], prices[idn]]
    m.end_date = mk.get("endDate") or event.get("endDate")
    m.closed = bool(mk.get("closed", event.get("closed")))
    m.resolution_source = mk.get("resolutionSource") or event.get("resolutionSource")
    m.rules = mk.get("description") or event.get("description")
    for k, dst in (("orderMinSize", "min_order_size"), ("orderPriceMinTickSize", "tick_size")):
        try:
            setattr(m, dst, float(mk[k]))
        except (KeyError, TypeError, ValueError):
            pass
    meta = _maybe_json(event.get("eventMetadata")) or _maybe_json(mk.get("eventMetadata")) or {}
    if isinstance(meta, dict):
        for k in ("priceToBeat", "price_to_beat", "startPrice"):
            if meta.get(k) is not None:
                try:
                    m.price_to_beat = float(meta[k])
                    break
                except (TypeError, ValueError):
                    pass
    m.fee_fields = {k: v for src in (event, mk) for k, v in src.items() if "fee" in k.lower()}
    return m


def lookup(asset: str, minutes: int, start_epoch: int) -> tuple[MarketMeta, dict]:
    """Return parsed metadata plus the raw fetch record (for provenance)."""
    slug = market_slug(asset, minutes, start_epoch)
    rec = fetch(f"{GAMMA}/events", params={"slug": slug})
    if not rec["ok"]:
        m = MarketMeta(asset, minutes, start_epoch, slug, error=rec["error"])
        return m, rec
    data = rec["data"]
    events = data if isinstance(data, list) else [data]
    if not events or not events[0]:
        return MarketMeta(asset, minutes, start_epoch, slug, error="not found"), rec
    return parse_event(events[0], asset, minutes, start_epoch), rec
