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

---

## Additions after the look-back (2026-11-04, written before testing)

Context: the 14-day look-back rejected H2 and H3 and found prices close to fair. These are the remaining mechanisms worth checking, from external research plus our own data.

### H6 Mid-window longshots in 15m markets (pre-registered retest of a look-back lead)
- **Lead:** in discovery days, sides priced 10–30¢ at minute 6 of 15m markets won ~6–7 points more often than priced (BTC n=329, ETH n=340, ~1.5–1.8 se). With ~200 cells inspected, this is likely chance.
- **Exact rule, frozen now:** 15m markets only, at elapsed minute 6, buy the side priced in (0.10, 0.30]. Cost = price + 1¢ + fee. One trade per market.
- **Test:** only on markets that START after 2026-11-05 00:00 UTC (data nobody has looked at). Re-run workflow 2 after ≥ 5 new days.
- **Falsified if:** net per share ≤ 0 on the new days, or p ≥ 0.05 (one test per asset, Holm over 2).

### H7 Both sides below $1 ("pair cost")
- **Why:** if best ask Up + best ask Down < 1 − fees, buying both locks a profit. Fees are ~1.75¢ per leg at 50¢, so the pair must cost ≤ ~96.5¢.
- **Data:** the collector records both books every second; the quality report already prints `sum_best_asks`.
- **Rule:** count snapshots and executable size where ask_up + ask_down + fees < 1 at the same poll; simulate both legs with the same delay/depth model.
- **Falsified if:** such moments are absent, or too small/short to fill both legs after a 1 s delay. Note: "legging in" at different times is directional risk, not arbitrage.

### H8 Book lag inside the settlement window
- **Why:** settlement averages the final 60 s (Chainlink `twap-60s` stream; some sources say 30 s for 5m). Once inside that window, part of the average is known, so the fair price moves predictably. A book that reprices slower than the partial average would lag.
- **Data:** books + Coinbase proxy (basis risk to Chainlink). This is H1's `fair_value` restricted to the final 60 s; the model now uses the final-window average.
- **Falsified if:** at ≥ 1 s delay the ask already reflects the partial average (no positive edge after fees), or the edge exists only at 0.5 s delay, which a GitHub runner cannot achieve.

### Not testable here, recorded for completeness
- **Market making for maker rebates:** makers pay no fee and share 20% of taker fees, but on-chain evidence shows a large passive maker lost heavily to adverse selection. Simulating maker fills honestly requires queue data we do not have, and doing it live needs order placement (prohibited here).
- **Cross-venue (Kalshi, Polymarket US, options):** different settlement sources and times, so not true arbitrage; acting on it requires trading accounts.
- **Opening-price manipulation:** the start price is a single snapshot. Deliberately moving it is market manipulation; excluded on principle.
- **Copying top wallets:** survivorship bias; top wallets' edge is infrastructure and speed, which copying after the fact cannot reproduce.
