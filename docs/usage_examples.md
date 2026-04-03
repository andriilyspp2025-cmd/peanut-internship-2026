# Usage Examples

This guide provides practical examples on how to use the core modules implemented in Week 1 of the Peanut Trade Internship.

## 1. Wallet Manager (`src/core/wallet.py`)

The `WalletManager` handles Ethereum private keys securely.

```python
from src.core.wallet import WalletManager

# 1. Generate a brand new wallet securely
new_wallet = WalletManager.generate()
print(f"Generated Address: {new_wallet.address}")

# 2. Load an existing wallet from an environment variable (Recommended)
# Expects 'PRIVATE_KEY' in the environment or a .env file.
wallet = WalletManager.from_env("PRIVATE_KEY")
print(f"Active Wallet: {wallet.address}")

# Security check: Printing the object never exposes the private key
print(wallet) # Output: WalletManager(address=0x...)
print(repr(wallet)) # Output: WalletManager(address=0x...)
```

## 2. Base Types (`src/core/types.py`)

Using the strict, math-safe token primitives.

```python
from src.core.types import Address, TokenAmount, Token

# Addresses are automatically checksummed
my_address = Address("0xd8da6bf26964af9d7eed9e03e53415d37aa96045")
print(my_address.checksum) # Output: 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045

# TokenAmounts enforce strict integers to avoid float precision issues.
# Converting a human-readable 1.5 ETH (18 decimals) into Wei:
amount = TokenAmount.from_human("1.5", decimals=18, symbol="ETH")
print(amount.raw)   # Output: 1500000000000000000
print(amount.human) # Output: 1.5 (as a precise Decimal)

# Attempting to pass floats will throw an error!
# TokenAmount.from_human(1.5, 18) -> TypeError
```

## 3. Blockchain Client & Builder (`src/chain/client.py`, `src/chain/builder.py`)

Sending a transaction is straightforward using the fluent builder.

```python
from src.chain.client import ChainClient
from src.chain.builder import TransactionBuilder

# Initialize a client with robust fallback RPCs
client = ChainClient(
    rpc_urls=["https://eth.llamarpc.com", "https://rpc.ankr.com/eth"],
    timeout=30,
    max_retries=5
)

# Build a transaction to send 0.1 ETH
builder = (TransactionBuilder(client, wallet)
           .to(Address("0xRecipientAddress..."))
           .value(TokenAmount.from_human("0.1", 18))
           .data(b"") # Empty data for a plain transfer
           .with_gas_estimate() # Simulates and adds a 1.2x buffer
           .with_gas_price("medium")) # Dynamically queries base fee

# Finally, sign and send it
signed_tx = builder.build_and_sign()
tx_hash = client.send_transaction(signed_tx.raw_transaction)

print(f"Broadcasted! TX Hash: {tx_hash}")

# Wait for it to confirm
receipt = client.wait_for_receipt(tx_hash)
print(f"Success: {receipt.status}")
```

## 4. Transaction Analyzer (`src/chain/analyzer.py`)

A CLI tool is available to inspect any EVM transaction, giving you a detailed breakdown of paths, values, gas fees, and reverts.

**Run via CLI:**
```bash
python -m src.chain.analyzer <tx_hash> --rpc https://eth.llamarpc.com
```

**JSON Output Format:**
```bash
python -m src.chain.analyzer <tx_hash> --format json
```
