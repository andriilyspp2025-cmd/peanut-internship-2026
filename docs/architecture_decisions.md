# Architecture Decisions

This document outlines the design architecture and decisions made in Week 1 for the Peanut Trading infrastructure.

## 1. No Floats Policy
**Context:** Arbitrage algorithms often fail due to precision errors ("dust") left behind from fractional floating-point math in Python.
**Decision:** We entirely prohibit the use of `float` within our token mathematics (`src/core/types.py -> TokenAmount`).
**Implementation:** `TokenAmount.from_human()` and logical operators (`+`, `-`, `*`) intentionally raise `TypeError`s if a `float` is detected. Only integers and precise `Decimal` objects are allowed. Data is persistently stored natively as raw integer `wei` formats matching EVM contract constants.

## 2. Robust RPC Fallbacks
**Context:** High-Frequency Trading operations encounter severe reliability uncertanties with public/shared RPC node rate limits (such as 429 Too Many Requests errors) and timeouts.
**Decision:** We embedded persistent node rotation loops inside the network layer.
**Implementation:** `src/chain/client.py -> ChainClient` accepts a list of endpoints. The `_execute` meta-function wraps any Web3 calls. If it encounters a subset of retriable errors (HTTP 429, timeouts), it instantly rotates to the next node in the ring (`self._rotate_rpc()`) and applies an exponential backoff before the next attempt. This isolates network flakiness from business logic entirely.

## 3. Strict Wallet Isolation (Anti-Leak)
**Context:** Storing Private Keys loosely exposes critical financial assets in tracebacks or standard output logs when code crashes.
**Decision:** Private Keys must evaporate from object namespaces and memory representations the moment they are loaded securely into `eth_account`.
**Implementation:** The `WalletManager` hides the underlying `eth_account.LocalAccount`. Furthermore, the `__repr__` and `__str__` dunder methods have been explicitly overridden. Printing or logging a `WalletManager` instance exclusively returns the checksummed public address, guaranteeing keys are masked even during deep unhandled exception debugging.

## 4. Object Identity (Address Validations)
**Context:** A smart contract identity is definitively its EVM `Address`. Sometimes exchanges assign different symbols, or decimals might be queried independently.
**Decision:** Systemic uniqueness should be enforced around proper Addresses.
**Implementation:**
- `Address`: A robust wrapper that auto-verifies utilizing `eth_utils` checksum logic upon instantiation. Invalid strings will instantly crash logic before processing.
- `Token`: Two token objects with arbitrary metadata mismatching (e.g., `USDC` vs `USD Coin`, but identical smart contract addresses) will resolve to identically true during set operations and comparisons (`__eq__` delegates exclusively to the `Address` layer comparison).

## 5. Security & Deterministic Serialization
**Context:** Forming signatures across non-standard dictionaries or networks demands exact predictability in encoding formats.
**Decision:** Build a rigid serialization framework (`CanonicalSerializer`).
**Implementation:** Dict keys are alphabetized, whitespace is stripped entirely, integers exceeding safe Javascript limits (`Number.MAX_SAFE_INTEGER`, 2^53 - 1) emit warnings to prevent silent frontend mismatches, and parsing ignores unreliable variables like emojis, keeping serialization completely deterministic across iteration tests.
