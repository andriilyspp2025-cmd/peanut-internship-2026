# Peanut Trade Quant Internship - HFT Arbitrage Bot

This repository contains the final project of the Peanut Trade Quant Internship.

The system is a deterministic, high-performance arbitrage engine operating between centralized exchanges (Binance Testnet) and decentralized exchanges on Arbitrum (Uniswap V3). It is designed with production-grade architecture, focusing on execution safety, precision, and resilience under real market conditions.

---

## Architecture & Core Principles

- **No Floats Policy**  
  All financial computations use `decimal.Decimal` to eliminate rounding errors and ensure deterministic results.

- **Resilient RPC Layer**  
  RPC round-robin with automatic node rotation (Merkle, MEVBlocker) to handle rate limits and improve reliability.

- **State Machine Execution**  
  The execution engine is modeled as a finite-state machine (`PENDING → FILLED → DONE`) with built-in unwind logic for failure recovery.

- **Inventory-Aware Decision Making**  
  Opportunities are evaluated based on both spread and portfolio state. Trades are rejected if inventory imbalance introduces risk.

- **Deterministic Strategy Pipeline**  
  Clear separation of responsibilities:
  - Signal detection
  - Opportunity scoring
  - Execution
  - Recovery

- **DEX Pricing via Uniswap V3 Quoter**  
  DEX quotes are sourced from the Arbitrum Uniswap V3 Quoter for accurate output estimates.

- **Mempool-Aware Refresh**  
  Optional V3 swap event monitoring triggers faster price refresh when pools move.

---

## System Overview

The system is divided into two main components:

### Strategy Layer (Brain)
- Detects arbitrage opportunities between CEX and DEX
- Calculates spread in basis points (bps)
- Scores opportunities based on liquidity and inventory constraints

### Execution Layer (Muscles)
- Executes trades asynchronously
- Supports DEX-first (Flashbots) and CEX-first strategies
- Handles partial fills and rollback (unwind)

---

## Development Timeline

### Week 1: Core & Chain
- Wallet management with private key isolation
- Canonical JSON serialization
- Core domain types (`TokenAmount`, `Address`)

### Week 2: Pricing Engine
- Uniswap V3 Quoter pricing on Arbitrum
- V3 swap event monitoring for fast refresh
- Fork-based simulation for diagnostics

### Week 3: Inventory & CEX
- Binance Testnet integration via `ccxt`
- Order book analysis with slippage awareness
- Real-time inventory tracking

### Week 4: Strategy & Execution
- Signal generation (CEX vs DEX spreads)
- Opportunity scoring (0–100)
- Async execution engine
- Circuit breaker and replay protection

### Week 5: Production Hardening
- Dockerized deployment with Compose
- Dead man's switch watchdog + heartbeat
- Telegram alerts, risk limits, and pre-trade validation

---

## Deployment & Infrastructure

The production deployment uses Docker and Docker Compose as the primary entry point.

- `arb-bot`: main arbitrage runner (`scripts.arb_bot`) in production mode
- `watchdog`: dead man's switch process that monitors the heartbeat file
- `shared-tmp`: shared `/tmp` volume used to exchange heartbeat files between services

Build and start:

```bash
docker-compose up -d --build
```

---

## Safety & Monitoring

- Dead man's switch watchdog monitors the heartbeat file and terminates the bot if it stalls
- Graceful shutdown on `SIGTERM` ensures the heartbeat is cleaned up and a stop alert is emitted
- Telegram alerts notify on startup, shutdown, errors, and kill switch activation
- Risk limits (`RiskManager`) and signal validation (`PreTradeValidator`) enforce safety gates

---

## Quick Start (Docker)

### 1. Environment Setup

```bash
git clone https://github.com/your-username/peanut-internship-2026.git
cd peanut-internship-2026
cp .env.example .env
````

Fill in the required values in `.env`:

```env
ENVIRONMENT=prod
ARBITRUM_RPC_URL=https://arb1.arbitrum.io/rpc
RPC_URL=https://your-arbitrum-rpc
SEPOLIA_RPC_URL=https://ethereum-sepolia-rpc.publicnode.com
ETH_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY
WSS_URL=wss://your-arbitrum-wss

PRIVATE_KEY=0x...
ADDRESS=0x...

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ENABLED=true

BINANCE_TESTNET_API_KEY=your_key_here
BINANCE_TESTNET_SECRET=your_secret_here
BINANCE_PROD_API_KEY=your_real_api_key_here
BINANCE_PROD_SECRET=your_real_secret_here
```

---

### 2. Build and Run

```bash
docker-compose up -d --build
make docker-up
```

### 3. Stop / Cleanup

```bash
docker-compose stop
docker-compose down
make docker-stop
make docker-down
```

---

## Local Development (Optional)

### Installation

```bash
make install
```

### Run Tests

```bash
make test
```

### Usage (Local)

```bash
make sim        # Simulation mode
make prod       # Production mode
make verbose    # Production with detailed logs
make dry-run    # Production dry run (no execution)
```

```bash
python -m scripts.arb_bot --mode test
python -m scripts.arb_bot --mode prod
python -m scripts.arb_bot --mode prod --verbose
python -m scripts.arb_bot --mode prod --dry-run
```

---

## Debug & Analysis

Run arbitrage validation tool:

```bash
make demo-arb
```

This runs a diagnostic checker for a given pair and trade size.

---

## Example Output (Verbose)

```text
INFO Bot starting in production mode
INFO Loaded 1 pool from mainnet
INFO MARKET ETH/USDT | CEX (Bid: 2311.25, Ask: 2311.26) | DEX (Sell: 2305.55, Buy: 2320.76)
INFO Spread=12.4 bps | Score=58.5 → REJECTED (low score)
INFO ACTIONABLE SIGNAL: spread=80.0 bps score=66.6
INFO SUCCESS: PnL=$8.56
```

---

## Project Structure

```
src/
  core/              # Domain models and math
  pricing/           # DEX pricing and routing
  executor/          # Trade execution engine
  strategy/          # Signal + scoring logic
  integration/       # External services (CEX, RPC)

scripts/
  arb_bot.py         # Main entry point

tests/
  unit and integration tests
```

---

## Tech Stack

* Python 3.10+
* Web3.py
* CCXT (Binance)
* Uniswap V3 Quoter (Arbitrum)
* Docker / Docker Compose
* Decimal (fixed-point arithmetic)

Quality:

* Pytest
* Ruff
* Black

---

## Engineering Notes

* The system is designed to be deterministic and reproducible
* All calculations are side-effect free until execution stage
* Execution failures are handled explicitly via state transitions
* Separation of concerns allows independent testing of each module

---

## Make Commands

```bash
make install     # Install dependencies
make test        # Run tests
make check       # Format + lint + test

make sim         # Run bot in simulation mode
make prod        # Run bot in production mode
make verbose     # Detailed logging
make dry-run     # Production dry run (no execution)

make docker-build # Build Docker image
make docker-up    # Start Docker services
make docker-stop  # Stop Docker services
make docker-down  # Stop and remove containers

make demo-arb    # Arbitrage checker
make clean       # Cleanup cache files
```
