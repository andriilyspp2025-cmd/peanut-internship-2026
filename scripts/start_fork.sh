#!/bin/bash
# scripts/start_fork.sh

# Requires: anvil (from foundry)
# Install: curl -L https://foundry.paradigm.xyz | bash && foundryup

if [ -z "$ETH_RPC_URL" ]; then
    echo "Error: ETH_RPC_URL is not set."
    echo "Please set it to your Provider URL (e.g., Alchemy or Infura)."
    exit 1
fi

echo "Starting local Anvil fork..."
anvil \
    --fork-url "$ETH_RPC_URL" \
    --fork-block-number latest \
    --port 8545 \
    --accounts 10 \
    --balance 10000
