# Architecture Decisions

This document outlines the design architecture and decisions made during Weeks 1-5 for the Peanut Trading infrastructure.

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

## 6. Dockerization & Service Orchestration
**Context:** Production deployment required reliable restarts, consistent environments, and independent process monitoring.
**Decision:** Use Docker Compose to orchestrate the core bot and an isolated watchdog process.
**Implementation:** Added a `Dockerfile` and `docker-compose.yml` with two services (`arb-bot`, `watchdog`) and a shared volume for `/tmp` heartbeat exchange.

## 7. Asynchronous Non-Blocking Operations
**Context:** Blocking I/O (e.g., `ccxt.fetch_balance`, Web3 RPC calls, Telegram alerts) can stall the asyncio event loop and reduce HFT responsiveness.
**Decision:** All blocking external calls must be executed in background threads.
**Implementation:** Use `asyncio.to_thread()` for `ccxt` and Web3 calls, and refactor `TelegramAlert.send()` to dispatch in a non-blocking thread when a running loop is present.

## 8. Graceful Shutdown & Dead Man's Switch
**Context:** A frozen process or abrupt termination can leave positions unmanaged and stale state on disk.
**Decision:** Implement a watchdog process and handle system signals for clean termination.
**Implementation:** Handle `SIGTERM` in `scripts/arb_bot.py`, send a shutdown alert in `finally`, and clean up the heartbeat file. A standalone `scripts/watchdog.py` monitors heartbeat freshness and terminates the bot if it stops updating.

## 9. Precision & Exchange Limits
**Context:** Binance orders were rejected when amounts violated `LOT_SIZE` or `PRICE_TICK` filters.
**Decision:** Round quantity and price to exchange-compliant steps before submission.
**Implementation:** Add `round_quantity()` and `round_price()` helpers in `ExchangeClient` using `Decimal` rounding against `LOT_SIZE_STEP` and `PRICE_TICK` constants.
