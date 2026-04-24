.PHONY: install test format lint check run sim prod verbose clean

install:
	python -m pip install --upgrade pip
	python -m pip install -r requirements.txt

test:
	python -m pytest tests/ -v

format:
	python -m black src/ tests/

lint:
	python -m ruff check src/ tests/

check: format lint test

# --- Bot Execution ---

sim:
	python -m scripts.arb_bot --mode test

prod:
	python -m scripts.arb_bot --mode prod

verbose:
	python -m scripts.arb_bot --mode prod --verbose

# --- Arbitrage Demo / Debug ---

demo-arb:
	python -m src.integration.arb_checker ETH/USDT --size 2.0

# --- Cleanup ---

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete