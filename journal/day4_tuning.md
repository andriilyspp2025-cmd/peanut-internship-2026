# Day 4 — 2026-05-09 (Observation + Pair Research)

## Numbers

- Starting capital: $99.52
- Ending capital: $99.52
- PnL: $0.00 (no live trades — observation mode)
- Trades: 0
- Fees paid: $0

## What Happened

After Day 3 losses and the missed 320 bps opportunity, made a deliberate decision to run in observation mode and collect data before making any more trades.

**Full day data — CHIP/USDT, 36,082 ticks scanned:**


| Metric                       | Value     |
| ---------------------------- | --------- |
| Total ticks scanned          | 36,082    |
| SKIPPED (below threshold)    | 36,069    |
| TIMEOUT (RPC errors)         | 12        |
| Signals with positive spread | 14,972    |
| Spreads > 100 bps            | 1         |
| Spreads > 50 bps             | 3         |
| Mean spread (all SKIPPED)    | -4.0 bps  |
| Max spread seen              | 206.7 bps |

The one notable signal: **05:28** — CHIP/USDT at 206.7 bps, expected PnL +$0.237. Status: `REJECTED_RISK` (consecutive loss limit still active from previous session). Another painful block.

**Conclusion from data:** In 36,000+ ticks over 24 hours, only **1 spread exceeded 100 bps**. For a bot with ~2.4s execution time that needs >100 bps just to break even at $10 size, this means the expected number of profitable trades per day on CHIP/USDT is approximately 1 — and only if the circuit breaker doesn't block it.

**Pair research:**

Began evaluating alternatives. Criteria applied from Week 5 analysis:

- Must be on Binance Spot
- Uniswap V3 pool on Arbitrum, TVL > $200K
- Fee tier ≤ 0.05%
- 24h volume > $50K in the pool
- Spread > 100 bps occurring 5+ times per day (estimated)

Evaluated **ESP/USDC** (Espresso Price, Arbitrum):

- Pool: `0x15eb51a325cbce6c1cc8202a6f8a76224c5b7540`
- Fee tier: 0.01% (100 bps tier — cheapest available) ✅
- TVL: $535,200 ✅
- 24h pool volume: $178,300 ✅
- 24h Binance volume: ~$166K USDC ✅
- Transactions: every ~30 seconds (much more active than CHIP) ✅
- Binance warning: "Seed Tag — higher volatility" — means wider spreads are more likely

Also evaluated ARB/USDT, GMX/USDT (already in PAIRS_CONFIG as disabled) — lower pool activity than ESP currently.

## Problems Encountered

- 12 TIMEOUT entries from QuickNode 429 errors — RPC stability issue persists
- 206.7 bps signal at 05:28 blocked again by REJECTED_RISK — recurring pattern: every time a large spread appears, the circuit breaker is active from a previous session
- CHIP/USDT is losing volume and volatility (compare Day 2: active signals every few minutes vs Day 4: 1 signal above 100 bps in 24h)

## Changes Made

No live parameter changes — observation day. Decisions made for next session:

```
Pair:             CHIP/USDT → ESP/USDC 
MIN_SPREAD_BPS:   50  →  300  (account for full cost stack at $50 trade size)
MAX_TRADE_USD:    10  →  50   (reduce gas from ~80 bps to ~10 bps of trade)
MAX_CONSECUTIVE_LOSSES: 3 → 1 (stop after first loss, investigate before resuming)
```

## Lessons Learned

- 36,000 ticks with 1 opportunity above breakeven threshold is not a viable trading environment for my execution speed. The pair needs to change, not the parameters.
- Watching the 206 bps signal get blocked at 05:28 reinforced: the circuit breaker needs to be smarter about signal quality, not just raw count of losses.
- Observation days cost nothing and teach a lot. Should have done this for CHIP before running live trades.

## Tomorrow's Plan

- Complete ESP/USDC code integration (PAIRS_CONFIG, token map, approve script)
- Run dry-run on ESP/USDC for first few hours
- Prepare demo materials
