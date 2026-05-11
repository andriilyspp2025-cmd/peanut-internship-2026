# Day 3 — 2026-05-08 (Tightened Settings + Big Missed Opportunity)

## Numbers
- Starting capital: $99.87
- Ending capital: $99.835
- PnL: -$0.035 (one live trade)
- Trades: 1 executed (0 wins, 1 loss)
- Win rate: 0%
- Best trade: -$0.035 (CHIP/USDT, BUY_CEX_SELL_DEX, 104.3 bps)
- Worst trade: same
- Fees paid: ~$0.01 (CEX) + ~$0.08 (DEX gas)

## What Happened

**Session 1 (09:38–10:30) — tightened settings:**

Ran bot with significantly tighter parameters after Day 2 analysis. Scanned ~15,575 ticks. 

Key finding: with stricter settings, 11 signals reached `REJECTED_RISK` — all in the 58-71 bps range with positive expected PnL ($0.002–$0.013). These were blocked by the consecutive loss counter from Day 2.

One FAILED signal at 55.7 bps at 10:30 — would have been the first profitable trade attempt but fell to `inventory_ok=False` again. Also: CHIP/USDT#500 signal at 79.7 bps FAILED (inventory).

Most concerning: the 11 REJECTED_RISK signals between 09:38–10:07 all had positive expected PnL. The circuit breaker was being too aggressive — blocking potentially profitable signals.

**Session 2 (18:16–19:34) — reset and resumed:**

Reset consecutive loss counter. Bot ran again. Three notable events:

1. **18:16** — CHIP/USDT at 58.6 bps, net_pnl +$0.007. Status: `FAILED` (inventory_ok=False — no CHIP on DEX wallet). Missed.

2. **18:53** — CHIP/USDT at 104.3 bps. Bot executed BUY_CEX_SELL_DEX:
   - CEX fill: 0.06667 (1 tick worse than quoted 0.06668)
   - DEX fill: 0.06737 (as quoted)
   - Expected PnL: +$0.052 → Actual PnL: **-$0.035**
   - Execution: 2,376ms
   - Loss cause: 1-tick CEX slippage on a thin-liquidity token flipped the sign entirely

3. **19:00** — CHIP/USDT#500 at 320.5 bps, expected PnL **+$0.268**. Status: `REJECTED_RISK` (consecutive loss after 18:53 trade). This was the biggest missed opportunity of the entire week.

4. **19:34** — CHIP/USDT#500 at 91.3 bps. Also REJECTED_RISK.

## Problems Encountered

- QuickNode WSS node fell during Session 2, bot switched to HTTP polling → 429 errors → some TIMEOUT entries with dex_price=999999
- 1-tick CEX slippage on the one executed trade flipped +$0.052 to -$0.035. On a thin token at $10 size, even minimum tick slip is decisive.
- `consecutive_loss` circuit breaker blocked the 320.5 bps signal immediately after the -$0.035 trade. Correctly protective but cost a significant opportunity.
- `inventory_ok=False` blocked two more signals — CHIP inventory on DEX side depleted again.

## Changes Made

After missing the 320.5 bps signal:
```
# Reconsidered circuit breaker aggressiveness
MAX_CONSECUTIVE_LOSSES: reduced sensitivity  
# Decided to investigate why DEX inventory keeps depleting  
# Root cause: BUY_CEX_SELL_DEX trades consume DEX-side CHIP tokens without replenishment
```

## Lessons Learned

- The 320 bps signal at 19:00 blocked by circuit breaker is a painful lesson: after a loss, the correct response is human review, not automatic blocking of the next opportunity. A 320 bps spread is qualitatively different from a 1 bps spread — the risk system can't distinguish.
- `inventory_ok=False` is recurring. After each BUY_CEX_SELL_DEX trade, DEX-side CHIP is consumed. Need a rebalancing mechanism or pre-funded inventory on both sides before session.
- At 2,376ms execution, even a spread of 104 bps can flip negative. Need either faster execution or much higher spread threshold (~200 bps minimum).

## Tomorrow's Plan

- Observe CHIP/USDT spread frequency without trading — is there consistent opportunity?
- Analyze if the pair is becoming less active (volume/volatility declining)
- If frequency too low, begin research on alternative pairs
