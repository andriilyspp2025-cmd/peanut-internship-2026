
# Peanut Trade Internship: Lab -1 Baseline

## What has been done
- Created the core project structure (`src/`, `tests/`, `configs/`, `docs/`, `scripts/`).
- Set up `main.py` as the entry point.
- Configured CI tools (`black`, `ruff`, `pre-commit`).
- Added a `Makefile` for quick commands.
- Secured secrets with `.env.example` and `.gitignore`.
- Wrote 3 placeholder tests to verify the CI pipeline and test runner.

## Project Structure
```Plaintext
peanut-internship-2026/
  configs/
    .gitkeep
  docs/
    .gitkeep
  scripts/
    .gitkeep
  src/
    __init__.py
    main.py
  tests/
    __init__.py
    test_baseline.py
  .env.example
  .gitignore
  .pre-commit-config.yaml
  Makefile
  README.md
  requirements.txt
```

## Setup

1. Install dependencies and pre-commit hooks:

```bash
pip install -r requirements.txt
pre-commit install
```

2. Set up your local environment (never commit `.env`!):

```bash
cp .env.example .env
```

## How to Run

Run the main script:

```bash
make run
```

*(If `make` is not available on Windows: `python src/main.py`)*

**Output example:**

```text
Peanut Quant Baseline is running! All systems green.
```

## Tests & Mini Design Memo

Run the tests:

```bash
make test
```

*(If `make` is not available on Windows: `python -m pytest tests/`)*

**What is tested and why (Design Memo):**
Since there is no trading logic yet, the tests in `test_baseline.py` act as a proof-of-concept for our testing infrastructure:

1. **Determinism:** `test_determinism` checks that identical operations yield the exact same result every time.
2. **Invariants:** `test_invariant` verifies a basic mathematical truth (additive identity) to ensure the runner catches logic errors.
3. **Failure Modes:** `test_negative` intentionally triggers a `ZeroDivisionError` to prove that our code correctly reverts on bad input instead of failing silently.

## Linting

Code is automatically formatted and linted on every commit via `pre-commit` (using `black` and `ruff`).

## Limitations / Assumptions

* **No Trading Logic:** This baseline contains no exchange APIs or market data handlers yet.
* **Execution Environment:** Assumes a standard local Python 3.x environment. Dockerization is not part of this baseline.

```
```
