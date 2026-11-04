# Polymarket Up/Down research (read-only, paper only)

Cloud-only research into whether BTC/ETH 5- and 15-minute Up/Down markets on Polymarket offer any edge **after realistic costs**. "No reliable edge found" is an acceptable and likely result.

**This software never trades.** No accounts, wallets, keys, signing or order endpoints (the HTTP layer refuses order paths). It makes no LLM API calls. All fills are simulated from recorded order books and labelled as such. Polymarket is barred in Ontario; nothing here is a way around that.

## How to run (all in GitHub, nothing on your laptop)
Create a GitHub repo, upload these files, then go to **Actions** and run workflows manually, in order:

| Workflow | What it does | Typical runtime |
|---|---|---|
| **0 - Probe market schema** | Verifies live field names, fees, book format | ~2 min |
| **2 - Look back at past markets** | How past markets closed + their price history; writes `history_latest.md` (base rates, streaks, hour effects, calibration, approximate strategy screen) | up to the minutes you set (default 150) |
| **1 - Collect** | Polls books (both outcomes, all 4 combos) + Coinbase spot at 1 s; normalizes; quality report | input minutes (≤ 330) |
| **3 - Research** | Rebuilds tables, calibration, variant grid on train/validation, verdicts. Optional: freeze a variant; run the one-shot untouched test | ~5–20 min |
| **4 - Paper** | Collects, then re-runs the frozen strategy on post-freeze markets only | input minutes |
| **Tests** | Unit + pipeline tests (also on pull requests) | ~1 min |

Results land on the `research-data` branch (`reports/research_latest.md`, `reports/quality/`, `state/register.jsonl`) and in each run's summary page.

## Costs and limits
- **GitHub Actions:** free for public repos on standard runners. Private repos use your plan's included minutes (Free plan: 2,000/month at the time of writing — check *Settings → Billing*). A 330-minute collect run uses 330 of them. No paid hosting, no schedules: everything is manual.
- **Storage:** raw data is gzipped JSONL on `research-data`; normalized tables are rebuilt, not stored. Hard caps in code: 400 MB raw per run, 900 MB total (commit refused above that). Actual MB/hour is printed by each collect run; record it in `research/STATUS.md`.
- **Request rate:** ≤ 8 requests/s, well under published limits.
- Public repo means the collected (public) market data and your research are public too.

## Layout
```
pmr/            collector, normalizer, quality, outcomes, execution model, strategies, backtest, stats, research
tests/          synthetic fixtures (tests only — never evidence)
research/       MARKET_MECHANICS.md, HYPOTHESES.md (pre-declared protocol), STATUS.md (session checkpoint)
.github/        manual workflows
scripts/store.sh  persistence to the research-data branch
```

## Method in one paragraph
Chronological 50/25/25 train/validation/test split by market start with a 15-minute embargo; every variant is logged and Holm-corrected; cluster bootstrap (by day, or hour for short samples) for uncertainty; effective sample counts trades closing in the same 15 minutes as one; costs counted once (book walking for spread/slippage, documented fee formula); observation-to-order delay, stale-book rejection, depth haircut for competing takers, partial fills, minimum size and capital lock-up; no maker fills assumed; fixed share size. The test split is used once per frozen strategy (`state/test_lock.json`).
