````markdown
# Peanut Trade Quant Internship - HFT Arbitrage Bot

This repository contains the final project of the Peanut Trade Quant Internship.

The system is a deterministic, high-performance arbitrage engine operating between centralized exchanges (Binance Testnet) and decentralized exchanges on Ethereum (Uniswap V2). It is designed with production-grade architecture, focusing on execution safety, precision, and resilience under real market conditions.

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
- Uniswap V2 constant product formula
- Graph-based route search (DFS)
- Local fork-based simulation (Anvil/Hardhat)

### Week 3: Inventory & CEX
- Binance Testnet integration via `ccxt`
- Order book analysis with slippage awareness
- Real-time inventory tracking

### Week 4: Strategy & Execution
- Signal generation (CEX vs DEX spreads)
- Opportunity scoring (0–100)
- Async execution engine
- Circuit breaker and replay protection

---

## Quick Start

### 1. Environment Setup

```bash
git clone https://github.com/your-username/peanut-internship-2026.git
cd peanut-internship-2026
cp .env.example .env
````

Fill in:

* `PRIVATE_KEY`
* `RPC_URL`
* Binance API credentials

---

### 2. Installation

```bash
make install
```

---

### 3. Run Tests

```bash
make test
```

---

## Usage

### Using Makefile (Recommended)

```bash
make sim        # Simulation mode
make prod       # Production mode
make verbose    # Production with detailed logs
```

### Direct CLI

```bash
python -m scripts.arb_bot --mode test
python -m scripts.arb_bot --mode prod
python -m scripts.arb_bot --mode prod --verbose
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
  execution/         # Trade execution engine
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
* Anvil / Hardhat (forking)
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

make demo-arb    # Arbitrage checker
make clean       # Cleanup cache files
```
