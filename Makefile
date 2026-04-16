\.PHONY: install test format lint check demo-arb

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

demo-arb:
	python -m src.integration.arb_checker ETH/USDT --size 2.0
