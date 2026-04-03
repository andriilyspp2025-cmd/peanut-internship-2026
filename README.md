# Peanut Trade Internship: Week 1 Assignment

## Project Context
This repository contains the foundation of an arbitrage trading system. Week 1 focuses on two core modules:
- `core/` — wallet management, signing, base types
- `chain/` — blockchain interaction, transaction handling, receipt parsing

## What has been done
- Implemented the `WalletManager` with strong privacy protections.
- Created `CanonicalSerializer` for deterministic JSON serialization for signatures.
- Defined immutable primitives (`Address`, `Token`, `TokenAmount`) handling strict decimal mathematics and checksum validation avoiding floats.
- Implemented `ChainClient` with robust RPC fallback and retry logic.
- Built a fluent `TransactionBuilder` for simplifying Ethereum transactions, gas estimation, and dynamic nonce mapping.
- Created a `TransactionAnalyzer` CLI tool that intercepts, decodes, and formats any transaction hash into an analytical summary.
- Deployed a comprehensive `pytest` test suite with 37 edge-case tests.
- Successfully executed a Live Integration Test on the Sepolia testnet!

### Sepolia Testnet Proof
**Successful Transaction Hash:** `0x6ef70e2825c1dac8f1a8ce0b73a1ea0f94d0513fe5f9532b84bfbd8465bf5e51`
(Sent 0.0001 ETH from myself to myself)

## Project Structure
```Plaintext
peanut-internship-2026/
  configs/
  docs/
  scripts/
    integration_test.py
  src/
    chain/
      analyzer.py
      builder.py
      client.py
      errors.py
      __init__.py
    core/
      serializer.py
      types.py
      wallet.py
      __init__.py
    exchange/
    executor/
    inventory/
    pricing/
    safety/
    strategy/
    __init__.py
    main.py
  tests/
    unit/
      chain/
        test_analyzer.py
        test_builder.py
        test_client.py
      core/
        test_serializer.py
        test_token.py
        test_types.py
        test_wallet.py
    test_baseline.py
    __init__.py
  .env.example
  .gitignore
  .pre-commit-config.yaml
  Makefile
  README.md
  requirements.txt
```

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set up your local environment (never commit `.env`!):
```bash
cp .env.example .env
```
Make sure to add your `PRIVATE_KEY` and `SEPOLIA_RPC_URL` to `.env`.

## How to Run

### Tests
Run the full 37-test suite:
```bash
python -m pytest tests
```

### Integration Test
Run the live integration script on Sepolia:
```bash
python scripts/integration_test.py
```

### Transaction Analyzer CLI
Analyze any transaction (Mainnet or Sepolia):
```bash
python -m src.chain.analyzer <tx_hash> --rpc <RPC_URL>
```
*Example on Sepolia:*
```bash
python -m src.chain.analyzer 0x6ef70e2825c1dac8f1a8ce0b73a1ea0f94d0513fe5f9532b84bfbd8465bf5e51 --rpc <YOUR_SEPOLIA_RPC>
```

## Architecture Decisions (Design Memo)
1. **No Floats Policy:** `TokenAmount` strictly rejects Python floats and forces users to use `Decimal` or strings when dealing with tokens. This is because floating point math leads to dust inaccuracies in DeFi.
2. **RPC Fallback Ring:** `ChainClient` keeps an array of RPC URLs and aggressively rotates them on `429 Too Many Requests` or timeouts, pausing exponentially between retries. This is a critical feature for HFT systems where public endpoint failures are frequent.
3. **Wallet Security:** The core `WalletManager` wraps private keys closely and permanently overrides `__repr__` and `__str__` to ensure the private key cannot accidentally leak to logs or terminal tracebacks.
4. **Token Identity:** `Token` equality (`__eq__`) and `hash` ignore metadata (like `symbol` and `decimals`) and rely purely on the underlying lowercased smart contract `Address`.

