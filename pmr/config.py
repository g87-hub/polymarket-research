"""Central configuration and hard limits. Nothing here makes LLM calls."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
NORM = DATA / "normalized"
OUTCOMES = DATA / "outcomes"
REPORTS = ROOT / "reports"
RESEARCH = ROOT / "research"   # human-written docs (main branch)
STATE = ROOT / "state"         # machine state: register, freeze, test lock (data branch)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
# External spot proxies (public, no auth). Binance is avoided because GitHub
# runners are US-hosted and Binance.com rejects US IPs.
COINBASE = "https://api.exchange.coinbase.com"
KRAKEN = "https://api.kraken.com"

ASSETS = ("btc", "eth")
DURATIONS_MIN = (5, 15)
SPOT_PRODUCT = {"btc": "BTC-USD", "eth": "ETH-USD"}
KRAKEN_PAIR = {"btc": "XBTUSD", "eth": "ETHUSD"}

# Fee model (docs.polymarket.com/trading/fees, read 2026-11-04):
#   fee_usdc = shares * rate * p * (1 - p), crypto taker rate 0.07, maker 0,
#   rounded to 5 dp. The live per-market value is re-checked by the probe and
#   recorded with every collected market; historical schedules differed
#   (fees began 2026-01-07), so never assume this constant for old data.
CRYPTO_TAKER_FEE_RATE = 0.07
FEE_EFFECTIVE_FROM = "2026-01-07"


@dataclass(frozen=True)
class Limits:
    """Hard caps enforced in code, not just in workflow YAML."""
    max_collect_minutes: int = 330          # under GitHub's 6h job limit
    max_raw_mb_per_run: int = 400           # stop collecting past this
    max_repo_data_mb: int = 900             # refuse to commit data past this
    poll_interval_s: float = 1.0            # book + spot polling cadence
    max_requests_per_s: float = 8.0         # well below published CLOB limits
    http_timeout_s: float = 5.0


LIMITS = Limits()


def market_slug(asset: str, minutes: int, start_epoch: int) -> str:
    """Slug pattern observed on live markets, e.g. btc-updown-5m-1779909000
    where the number is the window start in UTC epoch seconds."""
    return f"{asset}-updown-{minutes}m-{start_epoch}"


def window_start(epoch: float, minutes: int) -> int:
    step = minutes * 60
    return int(epoch // step) * step
