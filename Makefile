.PHONY: install test format lint check run sim prod verbose dry-run docker-build docker-up docker-stop docker-down clean

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

dry-run:
	python -m scripts.arb_bot --mode prod --dry-run

# --- Docker ---

docker-build:
	docker-compose build

docker-up:
	docker-compose up -d --build

docker-stop:
	docker-compose stop

docker-down:
	docker-compose down

# --- Arbitrage Demo / Debug ---

demo-arb:
	python -m src.integration.arb_checker ETH/USDT --size 2.0

# --- Cleanup ---

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete