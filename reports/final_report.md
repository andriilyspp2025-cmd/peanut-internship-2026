# Final Report — Week 6: Go-Live & Beyond Spot Arbitrage

**Chain:** Arbitrum One | **DEX:** Uniswap V3 | **CEX:** Binance | **Pair:** CHIP/USDT (Days 1-5), ESP/USDC (integration in progress)

---

## 1. Configuration & Setup

### Pair Selection Process

Before committing capital, I ran a multi-pair evaluation scan across ETH/USDT, GMX/USDT, ARB/USDT, and CHIP/USDT simultaneously (~7,463 ticks). Results:

- ETH/USDT, GMX/USDT: zero executable spreads — dominated by professional bots with sub-100ms execution
- ARB/USDT: insufficient pool activity (only 242 ticks triggered)
- **CHIP/USDT: highest signal frequency, active Binance pair, Uniswap V3 pool on Arbitrum**

CHIP/USDT was selected based on data, not intuition.

### Parameter Evolution


| Parameter              | Day 1      | Day 2 | Day 3+ | Rationale                                                 |
| ---------------------- | ---------- | ----- | ------ | --------------------------------------------------------- |
| MIN_SPREAD_BPS         | ~0 (test)  | 15    | 50     | Cost analysis: $10 trade needs 90+ bps just to break even |
| MAX_TRADE_USD          | 5          | 10    | 10     | Gas fraction too high below $10                           |
| MAX_CONSECUTIVE_LOSSES | permissive | 3     | 1      | Stop and review after any loss                            |
| COOLDOWN_SECONDS       | 2          | 2     | 5      | Avoid stale DEX quotes                                    |

### Testnet vs Production Surprises

- **Gas cost dominates at small size.** Testnet gas was near-zero. On Arbitrum mainnet a V3 swap costs $0.07-0.10. At $10 trade size this is 70-100 bps of overhead — the largest single cost.
- **1-tick CEX slippage is decisive.** A single tick slip on CHIP (0.00001 USDT) at $10 size was enough to flip a +$0.052 expected PnL to -$0.035 actual.
- **Inventory depletion is a first-class problem.** Each BUY_CEX_SELL_DEX trade consumes DEX-side tokens. Without active rebalancing, the bot becomes one-directional quickly.
- **QuickNode free tier exhausts quickly.** Two of three WSS nodes failed on startup in later sessions, causing HTTP polling fallback and 429 errors.

---

## 2. Trading Results


| Metric                   | Value                                                                |
| ------------------------ | -------------------------------------------------------------------- |
| Starting capital         | $100.00                                                              |
| Ending capital           | ~$99.80                                                              |
| Total week PnL           | **-$0.202**                                                          |
| Capital preserved        | **99.80%**                                                           |
| Real executed trades     | **3**                                                                |
| Both directions executed | **YES** — BUY_DEX_SELL_CEX (Days 1, 5) and BUY_CEX_SELL_DEX (Day 2) |
| Win rate                 | 0%                                                                   |
| Max drawdown             | 0.202%                                                               |
| Total CEX fees           | ~$0.03                                                               |
| Total DEX gas            | ~$0.22                                                               |

### What counts and what doesn't

**Day 1 dry-run trades (15:08–15:13):** The trades journal CSV shows `EXECUTED/DONE` entries from this period, but these were dry-run simulations — logs confirm `DRY RUN: signal accepted but execution skipped`. No real Binance orders, no on-chain transactions. They do not appear in `executed_trades.json` for this reason.

**Day 1 failed DEX attempts (15:24–17:50):** 5 attempts where CEX leg opened but DEX leg failed with errors (`encodeABI`, `chain ID`, `STF`). Unwind executed automatically each time — `Unwind successful. Emergency flat position taken.` — PnL=$0.00 on all, no open positions left. These are not counted as trades.

### Real Trade Log


| # | Day   | Time      | Pair          | Direction            | Spread    | Actual PnL  | Note                                        |
| - | ----- | --------- | ------------- | -------------------- | --------- | ----------- | ------------------------------------------- |
| 1 | **1** | **17:55** | CHIP/USDT     | **BUY_DEX_SELL_CEX** | 0.34 bps  | **-$0.130** | Intentional pipeline test. TX:`a277aa80...` |
| 2 | **2** | **18:53** | CHIP/USDT     | **BUY_CEX_SELL_DEX** | 104.3 bps | **-$0.035** | Fully autonomous. TX:`738cf130...`          |
| 3 | **5** | **04:18** | CHIP/USDT#500 | **BUY_DEX_SELL_CEX** | 82.7 bps  | **-$0.037** | Autonomous, ~$15 size. TX:`9cfc2369...`     |

### Best and Worst Trade

**Best execution:** Trade 2 (Day 2, 18:53) — 104.3 bps spread, fully autonomous, -$0.035. Largest spread caught, smallest loss. Demonstrates the bot finding a real edge and executing both legs correctly without intervention.

**Worst trade:** Trade 1 (Day 1, 17:55) — 0.34 bps was never going to be profitable. Intentional: the goal was pipeline validation. Gas ~$0.07 + CEX fee guaranteed a loss at this spread.

### Biggest Missed Opportunities

Three signals were blocked that had strongly positive expected PnL:


| Time         | Spread    | Expected PnL | Reason blocked                                     |
| ------------ | --------- | ------------ | -------------------------------------------------- |
| Day 2, 19:00 | 320.5 bps | +$0.268      | REJECTED_RISK (consecutive loss after 18:53 trade) |
| Day 2, 18:58 | 338.2 bps | +$0.286      | inventory_ok=False (no CHIP on DEX side)           |
| Day 4, 05:28 | 206.7 bps | +$0.238      | REJECTED_RISK (consecutive loss)                   |

Combined missed profit: ~$0.79. If these three trades had executed, the week would have ended with +$0.63 profit despite the two real losses.

---

## 3. Risk Management in Practice

### Circuit Breaker Performance

The consecutive_loss circuit breaker triggered correctly after each real loss. After the -$0.035 trade on Day 2 at 18:53, it immediately blocked signals at 320 bps (19:00) and 91 bps (19:34). On Day 4, it blocked the 206 bps signal at 05:28.

This created a painful irony: the risk system that protected capital from small losses also blocked the large profitable trades. With hindsight, a spread-quality-aware circuit breaker (e.g., "block if expected PnL < $0.05, allow if expected PnL > $0.20") would have been better than a simple trade count.

### Kill Switch

File-based kill switch implemented and tested. Bot detects `kill_switch.txt` on the next loop iteration, executes graceful shutdown with session summary logged.

### What Saved Money

The `inventory_ok` check prevented one-sided execution — no scenario where only CEX leg executed and I was left with an open position. Every trade that went through had both legs confirmed.

### Closest Call

Day 3, Session 2: QuickNode 429 errors caused DEX quoter to return `dex_price=999999`, generating TIMEOUT entries. If the risk system had treated these as real negative trades, the daily loss limit would have triggered incorrectly. It correctly categorized them as TIMEOUT (not DONE), so no false triggering occurred.

---

## 4. What I Learned

### Biggest Surprise

Gas cost at small trade size. On testnet this was abstract. In production, paying $0.07-0.10 per swap on a $10 trade means 70-100 bps of overhead before considering any other cost. The same spread that would be profitable at $100 trade size is a guaranteed loss at $10. This is the fundamental economic constraint I underestimated.

### What I'd Do Differently with $1,000

1. **Start with $100 per trade** — gas drops from ~80 bps to ~8 bps. Same bot, same code, completely different economics.
2. **Pre-fund both sides before starting** — equal token inventory on CEX and wallet. The three biggest missed trades this week were blocked by inventory problems.
3. **Run 24h observation before any live trade** — Day 4 taught more than Days 1-3 combined, at zero cost.
4. **Upgrade QuickNode immediately** — free tier WSS failures create data quality issues and opportunity gaps.
5. **Run it on the server to reduce latency** - The delay on my PC was about 2 seconds; during that time, 9 new blocks were being mined on Arbitrum blockchain

### Most Confident Module

`src/safety/limits.py` / `RiskManager` — every circuit breaker triggered correctly, zero false positives, zero missed triggers. The risk system did exactly what it was designed to do.

### Least Confident Module

Gas estimation fallback. After WSS disconnect, gas falls back to a hardcoded constant. In production with volatile gas prices, this could be 2-5x wrong. Should maintain a rolling cache of last N successful estimates rather than using a static default.

### One Thing I Wish I'd Built

A pair evaluation script: given a pool address + CEX symbol, automatically calculate breakeven spread at different trade sizes, estimate signal frequency from historical data, and output a go/no-go recommendation. I had all the building blocks (V3 quoter, CEX feed, spread calculator) but never assembled them into a standalone pre-trade analysis tool.

---

## 5. Technical Challenges

### Infrastructure

QuickNode free tier WSS nodes hit daily limits repeatedly. Starting from Day 3, at least one node failed per session. Bot fell back to HTTP polling, which introduced latency and 429 rate-limit errors. This is the highest-priority infrastructure fix before any scaling.

### Gas Estimation

Estimated: 220,000 gas units × current gas price. Actual: within 20% typically. Falls back to hardcoded $0.10 after WSS disconnect. Improvement: cache last valid gas estimate, expire after 5 minutes, use expired value rather than hardcoded constant.

### Latency

CEX order placement to fill: ~50-100ms. DEX quote to on-chain confirmation: ~2,000-2,500ms. Total round-trip: ~2,376ms (observed). This latency is the core limitation on which pairs are viable — any pair where spreads close faster than 3s cannot be traded profitably.

### Bugs Found in Production

1. **web3.py ABI encoding** (Day 1) — `encodeABI` → `encode_abi` with correct arguments. Found through iterative debugging across 3 sessions. Each fix brought a new error until the correct API was reached.
2. **Chain ID hardcoded as 1** (Day 1) — Ethereum mainnet instead of Arbitrum 42161. Caught in production, not testnet.
3. **Token not approved** (Day 1) — CHIP not approved for Uniswap V3 Router, causing `execution reverted: STF`. Fixed by running approve script before live trading.
4. **`'NoneType' object has no attribute 'base_fee'`** — gas cache not re-initialized after WSS disconnect. Non-fatal but should be fixed.
5. **`dex_price=999999` in TIMEOUT entries** — cosmetically confusing in data analysis but correctly handled by risk system.

---

## 6. Beyond Spot Arbitrage

### Strategy Analyzed: Funding Rate Arbitrage

**Mechanism:**

Perpetual futures contracts use a funding rate paid every 8 hours between longs and shorts to keep the perp price anchored to spot. When the market is bullish (positive funding), longs pay shorts. This creates an arbitrage:

1. Buy and hold the token on spot (e.g., $1,000 of ESP)
2. Open an equal short on a perpetual DEX (Hyperliquid, dYdX, GMX)
3. Net directional exposure = 0 (delta neutral)
4. Collect funding payments every 8 hours from the short

**Example calculation:**

```
Capital: $1,000 spot + $100 margin (10x short)
Funding rate: +0.02% per 8h = 0.06%/day = ~22%/year
Daily income: $1,000 × 0.06% = $0.60/day
Monthly: ~$18 on $1,100 total = ~1.6%/month
Annual: ~20% — significantly above lending yields
```

**Key risks:**

- *Funding rate reversal* — if sentiment shifts, you pay instead of receiving. Must monitor hourly and close when funding inverts.
- *Short liquidation* — if spot price spikes sharply, short margin may be liquidated before spot gains can offset. Solution: use 3-5x leverage max, maintain 50%+ margin buffer.
- *Basis risk* — perp price doesn't perfectly track spot during extreme volatility.
- *Smart contract risk* — perp DEX exploits have occurred (GMX 2022).

**Capital requirements:**

- Minimum viable: ~$1,000 total (to cover costs meaningfully)
- Comfortable: $5,000+
- At $100 (my current capital): not viable — one funding payment of ~$0.06 barely covers the cost of opening positions

**Connection to existing codebase:**


| Existing module          | Reuse                                         |
| ------------------------ | --------------------------------------------- |
| `src/exchange/client.py` | Binance spot buy/hold                         |
| `src/safety/limits.py`   | Risk limits, daily loss cap                   |
| `src/safety/alerts.py`   | Telegram alerts for funding rate changes      |
| `src/inventory/pnl.py`   | PnL accounting (add funding payment tracking) |

New components needed:

- `src/exchange/perp_client.py` — Hyperliquid REST API (open/close perp, query funding)
- `src/strategy/funding_monitor.py` — hourly polling loop, alert on rate inversion
- `src/strategy/rebalancer.py` — keep spot and perp sizes matched as price moves

The architecture is identical (signal → risk check → execute → monitor), but the signal is "funding rate > threshold" and execution is once (open) rather than per-opportunity.

**Expected returns:**

Conservative (stable market, 0.01%/8h funding): ~4% annualized on notional
Base case (mild bull, 0.02%/8h): ~20% annualized
Bull market (0.05%/8h, seen in 2021 and early 2024): ~50%+ annualized

After costs (~0.3% round-trip to open/close): net yield subtracts ~3% annually. At moderate funding rates, net 17-20% is realistic — significantly better than lending rates.

**Would I pursue this post-internship?**

Yes, at $5,000+ capital. The implementation reuses most of what I've built, the monitoring is simpler than spot arb (no millisecond timing required), and the yield is more predictable. Funding rate changes slowly enough to monitor hourly and respond manually. The main new component is a perp DEX client — Hyperliquid has a clean, well-documented REST API.

At $100 the economics don't work, for the same reason spot arb at $10 didn't work: fixed costs dominate at small size. The first lesson of this week generalizes: capital efficiency matters more than strategy elegance.
