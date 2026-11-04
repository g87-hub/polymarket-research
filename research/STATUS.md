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

## Session 1 result — look-back at past markets (run 41926868713, 2026-11-04)
Data: 10,684 resolved markets over 14 days (BTC/ETH × 5m/15m), 97k one-minute price points. Costs assumed: price + 1¢ half-spread + 0.07·p·(1−p) fee. Prices are trade/mid history, not order books.

**Outcomes:** Up won 46.6–49.4% depending on combo. Only BTC 15m differs from 50% at p = 0.013 uncorrected (≈0.05 after correcting for 4 combos); this most likely reflects a down-trending fortnight, not a repeatable effect. No streak effect (|z| ≤ 2.0, none significant after correction). No UTC hour stood out after Holm correction.

**Strategies (20 tests):** all rejected or insufficient evidence. No variant was positive in both discovery (older 10 days) and holdout (newest 5 days) by more than noise. Buying at open loses ≈ 1–7¢/share; late favourites ≈ −2¢ to +0.5¢.

**Calibration:** prices are close to fair. Best cells show +2–4¢ after costs at 1.5–2.7 standard errors, consistent with chance given ~200 cells inspected. Weakest-possible lead: 15m markets, minute 6, sides priced 10–30¢ won ~6–7 points more often than priced (BTC n=329, ETH n=340). Not tested on holdout; would need order-book confirmation.

**Verdict:** no reliable edge found in simple rules on past markets. H2 (late favourite) and H3 (momentum/reversal) rejected on history. H1 and H4 untested (need real order books and are low-prior).

## Session 1 (cont.) — edge research, 2026-11-04 evening
- External research summarized in chat; mechanisms pre-registered as H6–H8 in HYPOTHESES.md.
- Settlement model corrected: average over the final 60 s (`SETTLE_WINDOW_S`), not the whole window. 16 tests pass.
- Key external evidence: in one measured week ~31% of wallets were profitable after fees; the crowd roughly broke even before fees; the biggest loser was a passive maker (adverse selection). Last-tick manipulation was closed by the TWAP change on 2026-08-07.
- **Next cheapest tests:** (1) one 120-min collect run (workflow 1) → read `sum_best_asks` for H7 and gather book data for H8; (2) after ≥ 5 new days, rerun workflow 2 and evaluate H6 only on markets starting after 2026-11-05 00:00 UTC.
