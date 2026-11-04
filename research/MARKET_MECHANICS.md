# Market mechanics (verified 2026-11-04; re-verify with workflow 0)

Status legend: **V** = confirmed from official docs or live market text; **S** = secondary source only; **?** = unverified, checked by `pmr probe` on first run.

## Contracts
- **V/S** Slugs: `{asset}-updown-{5m|15m}-{window start, UTC epoch s}`, e.g. `btc-updown-5m-1779909000`. Windows are aligned to multiples of 5/15 minutes.
- **S** Rules text on sampled international markets: resolves **Up if the end-of-window price is greater than or equal to the start price**, using the Chainlink BTC/USD (or ETH/USD) *data stream* (`data.chain.link/streams/...`), explicitly not exchange spot. Ties go to Up.
- **S** Some descriptions (Polymarket US, KuCoin's relay of the same markets) describe a **Chainlink 60-second TWAP**. The rule may differ by venue and date. The collector stores each market's full rules text so the exact rule is known per market; do not pool periods with different rules.
- **S** History: BTC 5-minute launched Feb 2026; 15-minute earlier (late 2025); settlement mechanics refined during 2026. ETH 5-minute availability on the international venue: **?** (probe checks all four combos).
- **?** Whether the start price ("price to beat") is published in market metadata. The parser looks for it; otherwise the external proxy at window start is used and flagged `ref_official = False`.

## Fees
- **V** `fee = shares × 0.07 × p × (1 − p)` for crypto takers, makers 0, rounded to 5 dp, charged in USDC at match time. 100 shares at 50¢ → $1.75 (docs example, used as a unit test). Peak cost ~1.75¢/share at 50¢; ~0.33¢/share at 95¢.
- **S** Taker fees on these markets began **2026-01-07** (previously zero) and the schedule has changed since; a share of fees is rebated to makers. Never apply the current formula to older data without checking the per-market fee fields the collector records.

## Data access (what this project can and cannot see)
| Need | Source | Status |
|---|---|---|
| Market metadata, rules, outcomes | Gamma `GET /events?slug=` | public, no auth |
| Live order book, both outcomes | CLOB `POST /books` | public; **live only — no historical books** |
| Historical prices | CLOB `/prices-history` | price series without depth → **cannot establish fills** |
| Settlement reference (Chainlink stream) | Polymarket live-data channel | **requires CLOB API credentials → not used** |
| Reference proxy | Coinbase public ticker | public; **basis risk** vs Chainlink |

Consequence: any executable-price test needs prospectively collected books. Outcome-only tests (base rates, autocorrelation, slot effects) can use backfilled history.

## Competitive context (secondary sources)
- Bitcoin is ~77% of fast-market turnover, ETH ~13%.
- Automated addresses were an estimated 55–62% of 5m/15m volume in Feb–Mar 2026.
- The dynamic taker fee was introduced specifically to neutralise latency arbitrage against exchange spot. Prior for latency-based edges from a 1-second REST poller on a shared GitHub runner: **low**.

## Access and eligibility
- Polymarket has been barred from operating in **Ontario** since the 2025 OSC settlement, and its terms list Ontario (plus Alberta, BC and Quebec since July 2026) as restricted. Polymarket says using a VPN to get around this is prohibited.
- This project only reads public market data from GitHub's servers. It never creates an account, connects a wallet, signs, or places orders. Results are research findings, not a route to trading from a restricted jurisdiction.

## Sources
- [Polymarket fees docs](https://docs.polymarket.com/trading/fees), [Maker rebates](https://docs.polymarket.com/developers/market-makers/maker-rebates-program), [Live data websocket docs](https://docs.polymarket.com/market-data/websocket/rtds.md)
- [Cointelegraph on taker fees](https://cointelegraph.com/news/polymarket-quietly-adds-taker-fees-15-minute-crypto-markets), [Finance Magnates on latency arbitrage](https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees-to-curb-latency-arbitrage-in-short-term-crypto-markets/), [Dune on fast markets](https://dune.com/blog/polymarket-fast-markets)
- [Crypto Briefing on Polymarket US 5-minute markets](https://cryptobriefing.com/polymarket-us-five-minute-crypto-markets/), [sample market rules](https://explorer.struct.to/markets/btc-updown-5m-1779909000)
- [Covers on the OSC settlement](https://www.covers.com/politics/polymarket-ontario-securities-settlement-canada-election-betting-april-2025), [CP24, Oct 2026](https://www.cp24.com/local/toronto/2026/10/20/polymarket-is-banned-in-ontario-but-theres-more-than-us400k-wagered-on-torontos-mayoral-election/), [Canadian Gaming Business, July 2026](https://www.canadiangamingbusiness.com/2026/07/13/polymarket-blocks-provinces/)
