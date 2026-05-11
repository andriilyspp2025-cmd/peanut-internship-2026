# Day 5 — 2026-05-10/11 (Final Session + Liberal Strictness Batch)

## Numbers
- Starting capital: $100.00 (beginning of week)
- Ending capital: ~$98.78
- Total week PnL: **-$1.221**
- Real executed trades this week: **12**
- Capital preserved: **98.78%** ✅

## What Happened

**Sessions summary — May 10-11, 42,045 ticks (CHIP/USDT, CHIP/USDT#500, ESP/USDC):**

Bot scanned the market throughout the day. Results:
- 41,984 SKIPPED — vast majority with no actionable signal
- 45 REJECTED_RISK — filtered by risk rules
- 10 EXECUTED — 1 early trade + 9 liberal batch fills
- 4 TIMEOUT — QuickNode 429 errors
- 2 FAILED — inventory/execution issues
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

**13:45-13:59 - liberal strictness batch (9 real trades):**

For the demo I relaxed risk strictness to increase the trade count. This produced 9 real fills at ~$5.10 size, but the economics stayed negative because fixed costs dominate at this size.

- Total net PnL: **-$1.0195** (avg **-$0.1133** per trade)
- Avg spread: **12.9248 bps**, avg size: **$5.0995**
- Direction mix: 6 BUY_DEX_SELL_CEX, 3 BUY_CEX_SELL_DEX

| # | Time | Pair | Direction | Spread (bps) | Net PnL | TX |
| - | ---- | ---- | --------- | ------------ | ------- | -- |
| 1 | 13:45:18 | CHIP/USDT | BUY_DEX_SELL_CEX | 12.9578 | -$0.1146 | https://arbiscan.io/tx/b3bb331928bd11056450022e91837793bfd7c22775d4dc68b014ce56763f643d |
| 2 | 13:45:27 | CHIP/USDT | BUY_DEX_SELL_CEX | 12.1386 | -$0.1142 | https://arbiscan.io/tx/bdbf85bbc927fa75ec68de5f477fa7ac0c40b671ab26a0257e7c5b6ab30d07bd |
| 3 | 13:49:27 | CHIP/USDT | BUY_DEX_SELL_CEX | 8.1580  | -$0.1161 | https://arbiscan.io/tx/f01566ac240e54fa9225c3eb25bddefff8bd387661f20c7cb6832d1bf2a09eb6 |
| 4 | 13:49:34 | CHIP/USDT#500 | BUY_DEX_SELL_CEX | 12.7516 | -$0.1114 | https://arbiscan.io/tx/445391e160771702f3a1282d0f3d6eac616d4f22fe8eba8447eb004147293541 |
| 5 | 13:49:40 | CHIP/USDT | BUY_DEX_SELL_CEX | 17.2136 | -$0.1115 | https://arbiscan.io/tx/998f6af8c0d7b55cb495d78ba324e28ce5bf330c40449dc6fbe66962a51d7823 |
| 6 | 13:53:04 | CHIP/USDT#500 | BUY_DEX_SELL_CEX | 8.2563  | -$0.1168 | https://arbiscan.io/tx/67b9832b48386366b15ec30c3adf486d5031ee184f3c5baae8cef5fbb6d4b28a |
| 7 | 13:57:30 | CHIP/USDT#500 | BUY_CEX_SELL_DEX | 16.7980 | -$0.1110 | https://arbiscan.io/tx/bd1836e3ea8fcc5e8d1d6d1c30a7eb7d439439e06f0d87cadb863d37ff452272 |
| 8 | 13:59:04 | CHIP/USDT#500 | BUY_CEX_SELL_DEX | 10.2314 | -$0.1135 | https://arbiscan.io/tx/b96df77b739f5931f2c76d044de355d89f8bb5f4587db2bc846ee1b006ff93b8 |
| 9 | 13:59:11 | CHIP/USDT#500 | BUY_CEX_SELL_DEX | 17.8176 | -$0.1105 | https://arbiscan.io/tx/3f121ae28b39956f0930bdee9a5b6ad0387baae479c7adb79c0528511c28b8a1 |

**Week summary — 12 real trades:**

| # | Date | Pair | Direction | Spread | PnL |
|---|------|------|-----------|--------|-----|
| 1 | May 7, 17:55 | CHIP/USDT | BUY_DEX_SELL_CEX | 0.34 bps | -$0.130 |
| 2 | May 8, 18:53 | CHIP/USDT | BUY_CEX_SELL_DEX | 104.3 bps | -$0.035 |
| 3 | May 11, 04:18 | CHIP/USDT#500 | BUY_DEX_SELL_CEX | 82.7 bps | -$0.037 |
| 4 | May 11, 13:45:18 | CHIP/USDT | BUY_DEX_SELL_CEX | 12.9578 bps | -$0.1146 |
| 5 | May 11, 13:45:27 | CHIP/USDT | BUY_DEX_SELL_CEX | 12.1386 bps | -$0.1142 |
| 6 | May 11, 13:49:27 | CHIP/USDT | BUY_DEX_SELL_CEX | 8.1580 bps | -$0.1161 |
| 7 | May 11, 13:49:34 | CHIP/USDT#500 | BUY_DEX_SELL_CEX | 12.7516 bps | -$0.1114 |
| 8 | May 11, 13:49:40 | CHIP/USDT | BUY_DEX_SELL_CEX | 17.2136 bps | -$0.1115 |
| 9 | May 11, 13:53:04 | CHIP/USDT#500 | BUY_DEX_SELL_CEX | 8.2563 bps | -$0.1168 |
| 10 | May 11, 13:57:30 | CHIP/USDT#500 | BUY_CEX_SELL_DEX | 16.7980 bps | -$0.1110 |
| 11 | May 11, 13:59:04 | CHIP/USDT#500 | BUY_CEX_SELL_DEX | 10.2314 bps | -$0.1135 |
| 12 | May 11, 13:59:11 | CHIP/USDT#500 | BUY_CEX_SELL_DEX | 17.8176 bps | -$0.1105 |

All 3 trades: both legs confirmed, on-chain TX verified, both directions executed.

## Problems Encountered

- Same economics problem: $15 size still too small for CHIP with gas ~$0.08
- QuickNode 429 errors — 4 TIMEOUT entries during the session
- Telegram alerts never worked throughout the week (Windows environment)

## Changes Made

Risk strictness was relaxed to increase trade count for the demo.

## Lessons Learned — Week Summary

**Main lesson:** economics dominates strategy at small size. All 12 trades had correct logic, both legs executed — but fixed costs ($0.08 gas + $0.01 CEX fee) = 60–90 bps overhead at $5–15 size. No spread below ~100 bps can be profitable at this scale.

**Second lesson:** inventory management is the most critical operational concern. The biggest missed opportunities of the week (338 bps, 320 bps, 206 bps) were blocked by `inventory_ok=False` or the circuit breaker — not by the absence of market opportunity.

**Third lesson:** observation days are invaluable. Day 4 with 36,000 ticks and zero trades produced more insight than any live day, at zero cost.

## If Continuing with $1,000

1. Start with `MAX_TRADE_USD=$100` immediately — gas drops from ~80 bps to ~8 bps
2. Pre-fund both sides with the token: equal amounts on CEX and wallet before starting
3. Run 24h observation on a new pair before any live execution
4. Upgrade QuickNode to a paid plan — free tier is incompatible with continuous scanning
5. Deploy on a Linux server to reduce latency and fix Telegram alerts