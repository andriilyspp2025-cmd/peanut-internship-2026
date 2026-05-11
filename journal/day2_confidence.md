# Day 2 — 08/05/2026 (Live Scan, no trades)

## Figures
- Starting capital: $99.87
- Ending capital: $99.87
- Profit/Loss (PnL): $0.00 (no trades)
- Number of live trades: 0
- Win rate: N/A
- Best trade: N/A
- Worst trade: N/A
- Commissions paid: $0

## What happened

**The bot was launched in live mode with settings from Day 1.**

During the session, the bot scanned the market and identified signals. Most were rejected due to `inventory_ok=False` or a circuit breaker.

**18:16** — CHIP/USDT, 58.6 bps, expected PnL +$0.007. `FAILED: inventory_ok=False` — the DEX wallet did not have enough CHIP for BUY_CEX_SELL_DEX. Skipped.

**Later during the session** — several signals were rejected due to risk limits or inventory checks; there were no active orders.

## Issues encountered

- `inventory_ok=False` blocked the signal at 18:16 — CHIP was not available on both sides simultaneously
- Some signals were rejected by risk limits — thresholds need reviewing
- QuickNode WSS went down in the evening → HTTP polling → 429 errors → TIMEOUT entries with dex_price=999999

## Changes made

Following an analysis of signal quality and rejections:
```
MIN_SPREAD_BPS:          15  →  50    (only significant spreads)
MAX_CONSECUTIVE_LOSSES:  revised  (circuit breaker too aggressive)
```

It was decided to investigate the frequency of signals in observation mode before the next live trades.

## Conclusions

- `inventory_ok=False` — a system issue. After BUY_CEX_SELL_DEX, tokens move to the DEX side. Without rebalancing, the bot becomes one-sided.
- With a $10 position, even a 1-tick slippage on CHIP (~0.00001 USDT) can wipe out the advantage — a wider spread or larger trade size is required.
- The circuit breaker must take signal quality into account, not just the counter.

## Plan for tomorrow

- 24-hour monitoring mode — collect data on the frequency and size of spreads
- Resolve the inventory issue: pre-fund both sides before the session
- Explore alternative pairs if CHIP/USDT shows few opportunities


Translated with DeepL.com (free version)