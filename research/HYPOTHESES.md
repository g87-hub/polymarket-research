# Strategy hypotheses — ranked shortlist

Ranking = (plausibility of a real, fee-surviving edge) × (testability with data we can actually get). All entries are taker-only, fixed size (20 shares), hold to settlement, at most one entry per market. Exact rules live in `pmr/strategies.py`; variant grids in `default_grid()`. Every variant run is logged to `state/register.jsonl` and counted in the Holm correction.

Diagnostic run before any strategy: **calibration** (ask vs realized win rate by price bucket × time elapsed, per combo). If no cell shows positive edge after fee, strategies built on those cells are unlikely to work.

---

### 1. H2 Late favourite (favourite–longshot bias near expiry) — `late_favorite`
- **Why an edge might exist:** Retail flow tends to overpay for longshots and under-hold favourites in binary markets. Near expiry the fee is small at extreme prices (p(1−p) ≈ 0.05–0.10), so a small mispricing can survive costs. Latency matters less than for H1.
- **Data:** live books both sides (collected), outcomes (backfill). Available prospectively only.
- **Rules:** enter when time left ≤ `tau_max` (30 or 90 s) and ≥ 3 s, if a side's ask is in `band` ((0.85, 0.93) or (0.93, 0.98)); limit = band top; skip otherwise.
- **Costs:** spread from walking book; fee ≈ 0.4–0.9¢/share; risk of rare large losses (lose ~0.9 per share).
- **Falsified if:** calibration shows win rate ≤ ask + fee in those buckets, or validation net ≤ 0, or result vanishes at 2 s delay.

### 2. H1 Fair value vs executable price — `fair_value`
- **Why:** The settlement probability given the reference move so far is roughly Φ(ln(S_t/S_0)/(σ√τ)). If books update slower than the reference, asks lag.
- **Data:** books + external spot proxy (Coinbase) + realized vol; official start price if published.
- **Rules:** within `window` of elapsed fraction, buy the side whose model prob − ask − fee/share > `edge_min` (0.03, 0.06); limit = prob − fee − edge_min/2.
- **Costs:** fee highest near 50¢ (1.75¢/share); basis risk vs Chainlink; competition.
- **Prior: low.** Fees were introduced to kill exactly this, and bots dominate. We do **not** assume a latency advantage; delay sensitivity (0.5–4 s) is part of the test.
- **Falsified if:** edge disappears at ≥ 1 s delay or under 1.5× fees, or calibration shows no lag.

### 3. H3 Previous-window continuation / reversal — `prev_momentum`
- **Why:** Short-horizon crypto returns can show weak autocorrelation around flows/liquidations.
- **Data:** first a signal test on backfilled outcomes (cheap, large sample); then executable entry at open (needs books).
- **Rules:** in first 10% of window, buy previous window's direction (or opposite for `revert`) if ask ≤ 0.55.
- **Prior: low** (5-minute returns are close to a random walk). Must beat 1.75¢ fee + spread at 50¢.
- **Falsified if:** |P(Up|prev Up) − P(Up|prev Down)| is within noise on backfilled outcomes.

### 4. H4 BTC → ETH lead — `cross_asset_lead`
- **Why:** BTC price discovery often leads ETH by seconds; the ETH market might price ETH's own move but not BTC's.
- **Rules:** ETH only, elapsed 15–80%; if BTC log-return since ETH window start exceeds ETH's by `gap` (5 or 10 bp), buy the lagging direction if ask ≤ 0.6.
- **Prior: low–moderate.** Needs books; same latency caveats as H1.

### 5. H5 Order-book imbalance — deferred
Needs sub-second book updates (websocket) to be meaningful; REST polling at 1 s is too coarse. Revisit only if H1/H4 show book-lag evidence.

### Control: exact-time-slot effects (exploratory only)
Per-UTC-hour Up frequency on backfilled outcomes, Holm-corrected across 24 hours. Expect nothing. Never traded on; reported only to catch data artefacts (e.g. rule changes or tie effects at specific times).

### Baselines
`baseline_up` (always Up near open if ask ≤ 0.55) and `baseline_random`. A candidate must beat the best baseline and no-trade (0).

## Selection and validation protocol (pre-declared)
1. Chronological splits by market start: 50% train / 25% validation / 25% untouched test, 15-minute embargo between splits.
2. Eligible on validation only if: train net > 0, validation net > max(0, best baseline), Holm-adjusted cluster-bootstrap p < 0.10 across all variants, ≥ 30 settled trades.
3. Need ≥ 150 markets per split before any verdict other than "insufficient evidence".
4. Freeze one variant (`state/frozen.json`), then evaluate the untouched test **once** (`state/test_lock.json` blocks re-runs).
5. Forward paper: frozen rules on markets starting after freeze; passes only with ≥ 300 settled trades over ≥ 14 distinct days, net > 0, bootstrap p ≤ 0.05, same execution assumptions.
