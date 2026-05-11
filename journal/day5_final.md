# Day 5 — 2026-05-10/11 (Final Session + Third Real Trade)

## Numbers
- Starting capital: $100.00 (beginning of week)
- Ending capital: ~$99.80
- Total week PnL: **-$0.202**
- Real executed trades this week: **3**
- Capital preserved: **99.80%** ✅

## What Happened

**Observation session — CHIP/USDT, 41,239 ticks:**

Bot scanned the market throughout the day. Results:
- 41,230 SKIPPED — vast majority with no actionable signal
- 4 TIMEOUT — QuickNode 429 errors
- 1 FAILED — inventory check
- Max spread in SKIPPED: below threshold

**04:18 — third real trade:**

Early morning the bot found a signal on CHIP/USDT#500 and executed autonomously:

```
Pair:       CHIP/USDT#500
Direction:  BUY_DEX_SELL_CEX
Size:       238.36 CHIP (~$15.00)
Spread:     82.65 bps
Expected:   +$0.051
Actual:     -$0.037
CEX order:  133926814 (fill: 0.06292)
DEX TX:     9cfc2369298d1045a9ce950413d5d04a8df65ce562aadb4e49f52cfa36789c03 ✅
Execution:  2,281ms
```

Same structural problem as Trade 2: 82 bps looks positive but after gas (~$0.08) + CEX fee (~$0.01) + slippage the result is negative. At $15 size gas = ~53 bps overhead. Breakeven at this size is ~65–70 bps on fixed costs alone — 82 bps leaves zero buffer.

The decision to return to CHIP was deliberate — I wanted to hit at least 3 real trades before the demo. The trade confirmed the bot runs stably (2,281ms execution, both legs confirmed on-chain) even if the economics haven't changed.

**Week summary — 3 real trades:**

| # | Date | Pair | Direction | Spread | PnL |
|---|------|------|-----------|--------|-----|
| 1 | May 7, 17:55 | CHIP/USDT | BUY_DEX_SELL_CEX | 0.34 bps | -$0.130 |
| 2 | May 8, 18:53 | CHIP/USDT | BUY_CEX_SELL_DEX | 104.3 bps | -$0.035 |
| 3 | May 11, 04:18 | CHIP/USDT#500 | BUY_DEX_SELL_CEX | 82.7 bps | -$0.037 |

All 3 trades: both legs confirmed, on-chain TX verified, both directions executed.

## Problems Encountered

- Same economics problem: $15 size still too small for CHIP with gas ~$0.08
- QuickNode 429 errors — 4 TIMEOUT entries during the session
- Telegram alerts never worked throughout the week (Windows environment)

## Changes Made

No new code changes — day was final observation + one targeted trade for the demo.

## Lessons Learned — Week Summary

**Main lesson:** economics dominates strategy at small size. All 3 trades had correct logic, both legs executed — but fixed costs ($0.08 gas + $0.01 CEX fee) = 60–90 bps overhead at $10–15 size. No spread below ~100 bps can be profitable at this scale.

**Second lesson:** inventory management is the most critical operational concern. The biggest missed opportunities of the week (338 bps, 320 bps, 206 bps) were blocked by `inventory_ok=False` or the circuit breaker — not by the absence of market opportunity.

**Third lesson:** observation days are invaluable. Day 4 with 36,000 ticks and zero trades produced more insight than any live day, at zero cost.

## If Continuing with $1,000

1. Start with `MAX_TRADE_USD=$100` immediately — gas drops from ~80 bps to ~8 bps
2. Pre-fund both sides with the token: equal amounts on CEX and wallet before starting
3. Run 24h observation on a new pair before any live execution
4. Upgrade QuickNode to a paid plan — free tier is incompatible with continuous scanning
5. Deploy on a Linux server to reduce latency and fix Telegram alerts