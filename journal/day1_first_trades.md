# Day 1 — 2026-05-07 (Dry Run Testing + Live Debugging + First Successful Trade)

## Numbers
- Starting capital: $100.00
- Ending capital: $99.87
- PnL: -$0.13
- Real executed trades: 1 (0 wins, 1 loss)
- Dry-run simulated trades: 7 (zero real capital)
- Failed DEX attempts (unwind): 5 (PnL = $0.00 each)
- Fees paid: ~$0.01 (CEX) + ~$0.07 (DEX gas) — final trade only

## What Happened

**Phase 1 — Dry run: config calibration (14:41–14:58)**

Ran 5 dry-run sessions back to back. `DRY RUN enabled: execution will be skipped` — no real transactions, zero gas, zero CEX orders.

- First 3 sessions: zero signals — score threshold too strict
- 4th session (14:56): signals appeared but all `REJECTED - SCORE: 30.0`
- Conclusion: lower score threshold, raise MIN_SPREAD_BPS

**Phase 2 — Dry run: execution flow test (15:08–15:14)**

Two dry-run sessions with signals that passed through to `ACCEPTED`. The trades journal CSV shows `EXECUTED` and `state=DONE` — but this is a dry-run simulation artifact:

```
DRY RUN: signal accepted but execution skipped
pair=CHIP/USDT notional_usd=9.99...
```

7 dry-run "trades" (15:08:33, 15:08:39, 15:08:42, 15:13:28, 15:13:32, 15:13:35 + one more) — all simulated, no real Binance orders, no on-chain transactions. PnL figures in CSV (-$0.05 each) are calculated estimates, not real.

After the 3rd dry-run trade: `Consecutive loss limit reached` — circuit breaker triggered even in simulation. Confirmed the limit was configured too aggressively.

`bot_20260507_120820.log` (15:09): `Current Capital: $87.74` — dry-run PnL accumulator artifact, not a real balance.

**Phase 3 — Live: debugging DEX execution (15:24–17:53)**

Switched to live mode. Bot found signals and attempted DEX leg execution — but failed with various errors. In all cases **unwind executed correctly**:

| Time | DEX Error | Result |
|------|-----------|--------|
| 15:30 | `leg1_success=False` — CHIP not approved | Both legs False — no position opened |
| 15:45 (×3) | Same | Same |
| 17:02 (×2) | `'Contract' object has no attribute 'encodeABI'` | CEX order opened → **unwind executed** ✅ |
| 17:25 | `encode_abi() got unexpected keyword argument 'fn_name'` | CEX → **unwind** ✅ |
| 17:42 | `chain ID 1, but connected node is on 42161` | CEX → **unwind** ✅ |
| 17:45 | `execution reverted: STF` (not approved) | CEX → **unwind** ✅ |
| 17:50 | `execution reverted: STF` | CEX → **unwind** ✅ |

Each one logged: `TRADE_RECEIPT | actual_net_pnl=$0.00 | state=FAILED | leg1_success=True | leg2_success=False` + `Unwind successful. Emergency flat position taken.`

Between attempts: fixed ABI method → fixed chain ID → ran approve_chip_router.py for CHIP token.

**Phase 4 — First successful live trade (17:54–17:55)**

17:54 — `REJECTED - RISK: Trade notional exceeds max_trade_usd: 8.0` — approve worked but MAX_TRADE_USD too low. Raised it.

17:55 — ✅ **First real round-trip:**
- Direction: BUY_DEX_SELL_CEX, spread 0.34 bps (intentionally low — goal: verify the full pipeline)
- DEX: TX `a277aa801ad9457969c4ee72df9a7a9f03cbc6528b9b946788f75424dba82c91` ✅
- CEX: order `118003552` filled at 0.05465 ✅
- Actual PnL: **-$0.13** (expected — gas ~$0.07 + CEX fee at 0.34 bps = guaranteed loss)
- Execution time: ~2s

## Problems Encountered

- CHIP not approved for Uniswap V3 Router — first live attempts failed with `STF`
- web3.py ABI encoding API: `encodeABI` → `encode_abi` with correct arguments — found through iterative debugging across 3 sessions
- Chain ID hardcoded as 1 (Ethereum) instead of 42161 (Arbitrum) — only caught in live mode
- Score threshold in dry-run too strict — first 3 sessions produced zero signals
- Dry-run capital tracker showed $87.74 — simulation artifact, not a real loss

## Changes Made

```
score_threshold:  strict → lowered      (after dry-run phase)
chain_id:         1      → 42161        (Ethereum → Arbitrum)
ABI method:       encodeABI → encode_abi (web3.py fix)
CHIP approval:    ❌ → ✅               (approve_chip_router.py)
MAX_TRADE_USD:    ~8  → 10              (before final trade)
```

## Lessons Learned

- Dry-run before live was the right call. 7 simulated trades at zero cost to calibrate config.
- **Unwind mechanism was the most important safety feature today.** CEX leg opened 5 times, DEX failed 5 times, unwind closed every position automatically. Zero open positions left.
- Token approval is a mandatory preflight step. Spent ~1.5 hours debugging something solved by a single approve script.
- Never hardcode chain ID. Always read from config or the connected provider.
- Paying -$0.13 to confirm the full pipeline works (signal → CEX order → DEX swap → PnL tracking) was a justified cost.

## Tomorrow's Plan

- Raise MIN_SPREAD_BPS to 15+ (only positive spreads)
- Keep MAX_TRADE_USD at $10
- Monitor signal frequency and quality throughout the day