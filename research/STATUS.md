# Research status (checkpoint)

Update this file at the end of every session. Machine state (register, freeze, test lock, reports) lives on the `research-data` branch.

## Session 1 — 2026-11-04
**What ran**
- Verified mechanics from official docs and sampled market text → `research/MARKET_MECHANICS.md`.
- Built collector, normalizer, quality checks, outcome backfill, history audit, execution model, 5 hypotheses + 2 baselines, research pipeline with freeze/test lock, forward paper mode, manual workflows.
- 13 tests pass, including: documented fee example; book walking, partial fills, stale/after-close rejection; no look-ahead; embargoed splits; Holm; ledger reconciliation; **null test** (efficient synthetic market → no edge; forced trading loses spread + fees); **power test** (injected mispricing is detected).

**What the evidence supports:** nothing yet about real profitability. No real market data has been collected — the build environment cannot reach Polymarket or exchange APIs. All test results are on synthetic data and are not evidence.

**Blockers:** live data must be collected from GitHub Actions; field names (`clobTokenIds`, `eventMetadata.priceToBeat`, fee fields) are unverified until workflow 0 runs.

**Verdicts:** all strategies — *insufficient evidence* (no data).

**Update (same session):** per user request, the first real test is a look-back at past markets only. Workflow 2 now backfills outcomes **and** each market's 1-minute price history, then writes `reports/history_latest.md`: outcome base rates, streak and hour-of-day tests, calibration, and 5 pre-declared approximate strategies (older 2/3 of days = discovery, newest 1/3 = holdout, Holm-corrected). Costs = price + 1¢ assumed half-spread + taker fee. This is a screen, not fill-realistic.

**Next cheapest useful tests (in order)**
1. Workflow 0 (probe, ~2 min). Check `reports/probe/probe_summary.json`: all four combos found, outcomes parse as Up/Down, book has bids/asks, fee fields present. Fix `pmr/discovery.py` if not.
2. Workflow 2 (look back 14 days, ≤ 150 min). Read `history_latest.md` in the run summary. Only strategies marked 'promising on history' justify forward order-book collection.
3. Workflow 1 (collect 120 min) once, then check `reports/quality/` for coverage, gaps and latency. Record actual MB/hour here to set the storage budget.
4. After ~3–5 collection days (≥ 150 markets per split per combo), run workflow 3. Read calibration first.
