# Peanut Trade Internship - DeFi Arbitrage Bot (Week 1 & 2)

## Overview
This repository contains the completed tasks for Weeks 1 & 2 of the Peanut Trade core DeFi arbitrage architecture. The primary component is the "PricingEngine", which safely navigates automated market makers (AMMs), predicts optimal arbitrage routes via a local graph DFS solver, evaluates historical logic, and monitors mempool synchronizations.

## Architecture

`	ext
PricingEngine (Megazord Component)
├── RouteFinder (Graph DFS Arbitrage & Profit Analytics)
├── ForkSimulator (Local Anvil EVM fork pre-flight verification)
├── MempoolMonitor (WSS mempool pending transaction hooking)
└── UniswapV2Pair (Int-only Exact AMM formula execution)
`

## Quick Start

`console
# 1. Install Dependencies
make install

# 2. Configure environment
cp .env.example .env

# 3. Start your local Anvil fork
make fork

# 4. Run tests
make test
`

## CLI Examples
You can run standalone historical/impact evaluations on current pools without needing to execute trades using the built-in CLI module.

`console
# Measure price impact across various trade sizes
python src/pricing/imp_analyzer.py 0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc --token-in WETH --sizes 1000000000000000000,5000000000000000000
`
"@; Set-Content -Path "d:\Work\Internship\peanut-internship-2026\.env.example" -Value @"
# .env.example
ETH_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY
WSS_URL=wss://eth-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY
PRIVATE_KEY=0x0000000000000000000000000000000000000000000000000000000000000000
FORK_PORT=8545
