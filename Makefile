.PHONY: help install install-dev test test-fast test-coverage lint format typecheck check clean clean-all run-mcp qmd-up qmd-down qmd-status

# Default target
.DEFAULT_GOAL := help

# Colors for output
CYAN := \033[0;36m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RESET := \033[0m

help: ## Show this help message
	@echo "$(CYAN)Thestill - Development Makefile$(RESET)"
	@echo ""
	@echo "$(GREEN)Available targets:$(RESET)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-20s$(RESET) %s\n", $$1, $$2}'

install: ## Install package in development mode
	./venv/bin/pip install -e .

install-dev: ## Install package with development dependencies
	./venv/bin/pip install -e ".[dev]"
	./venv/bin/pre-commit install || true

test: ## Run all tests with coverage report
	./venv/bin/pytest --cov=thestill --cov-report=term-missing --cov-report=html:reports/coverage --ignore=tests/e2e

test-fast: ## Run tests without coverage (faster)
	./venv/bin/pytest -v --ignore=tests/e2e

test-unit: ## Run unit tests only
	./venv/bin/pytest tests/unit --cov=thestill --cov-report=term-missing

test-integration: ## Run integration tests only
	./venv/bin/pytest tests/integration --cov=thestill --cov-report=term-missing

test-e2e: ## Run E2E tests (requires running server)
	node tests/e2e/web/test_web_auth.cjs

test-coverage: ## Run tests and open HTML coverage report
	./venv/bin/pytest --cov=thestill --cov-report=html:reports/coverage --ignore=tests/e2e
	@echo "$(GREEN)Opening coverage report...$(RESET)"
	open reports/coverage/index.html || xdg-open reports/coverage/index.html

lint: ## Run all linters (ruff + pylint + mypy)
	@echo "$(CYAN)Running ruff...$(RESET)"
	./venv/bin/ruff check thestill/
	@echo "$(CYAN)Running pylint...$(RESET)"
	./venv/bin/pylint thestill/
	@echo "$(CYAN)Running mypy...$(RESET)"
	./venv/bin/mypy thestill/

format: ## Format code with black and isort
	@echo "$(CYAN)Running black...$(RESET)"
	./venv/bin/black thestill/ tests/
	@echo "$(CYAN)Running isort...$(RESET)"
	./venv/bin/isort thestill/ tests/

typecheck: ## Run type checking with mypy
	./venv/bin/mypy thestill/

check: ## Run all checks (format, lint, test) - use before committing
	@echo "$(YELLOW)Running all checks...$(RESET)"
	@echo ""
	@echo "$(CYAN)Step 1/4: Formatting code...$(RESET)"
	@make format
	@echo ""
	@echo "$(CYAN)Step 2/4: Running linters...$(RESET)"
	@make lint
	@echo ""
	@echo "$(CYAN)Step 3/4: Running type checks...$(RESET)"
	@make typecheck
	@echo ""
	@echo "$(CYAN)Step 4/4: Running tests...$(RESET)"
	@make test
	@echo ""
	@echo "$(GREEN)✓ All checks passed!$(RESET)"

pre-commit: ## Run pre-commit hooks on all files
	./venv/bin/pre-commit run --all-files || true

clean: ## Clean generated files (cache, coverage, etc.)
	@echo "$(YELLOW)Cleaning generated files...$(RESET)"
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf htmlcov
	rm -rf reports
	rm -rf .coverage
	rm -rf dist
	rm -rf build
	rm -rf *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	@echo "$(GREEN)✓ Cleaned!$(RESET)"

clean-all: clean ## Clean everything including data directory
	@echo "$(YELLOW)WARNING: This will delete all downloaded/processed data!$(RESET)"
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		rm -rf data/original_audio/*; \
		rm -rf data/downsampled_audio/*; \
		rm -rf data/raw_transcripts/*; \
		rm -rf data/clean_transcripts/*; \
		rm -rf data/summaries/*; \
		echo "$(GREEN)✓ All data cleaned!$(RESET)"; \
	else \
		echo "$(YELLOW)Cancelled.$(RESET)"; \
	fi

run-mcp: ## Run the MCP server
	./venv/bin/thestill-mcp

# ---------------------------------------------------------------------------
# Spec #28 §2.1 — qmd index management (corpus search foundations)
# ---------------------------------------------------------------------------
# These targets manage the qmd collection that backs lexical+vector
# search over the rendered Markdown corpus at data/corpus/. qmd is an
# external Node.js binary (≥ Node 22). Install via:
#     brew install qmd                  (macOS)
#     npm install -g qmd-cli            (Linux / WSL)
QMD_COLLECTION := thestill-corpus
QMD_CORPUS_DIR := data/corpus

qmd-up: ## Bootstrap the qmd collection over data/corpus/ (idempotent).
	@command -v qmd >/dev/null 2>&1 || { \
		echo "$(YELLOW)qmd not found on PATH. Install it (https://qmd.dev) before running this target.$(RESET)"; \
		exit 1; \
	}
	./venv/bin/thestill corpus bootstrap

qmd-down: ## Remove the qmd collection (data/corpus stays).
	@command -v qmd >/dev/null 2>&1 || exit 0
	-qmd collection remove $(QMD_COLLECTION)

qmd-status: ## Show qmd collection health for thestill-corpus.
	@command -v qmd >/dev/null 2>&1 || { echo "qmd not installed"; exit 1; }
	@qmd collection show $(QMD_COLLECTION) || qmd collection list

# Development shortcuts
dev-refresh: ## Quick: Refresh all podcast feeds
	./venv/bin/thestill refresh

dev-download: ## Quick: Download new episodes
	./venv/bin/thestill download

dev-status: ## Quick: Show system status
	./venv/bin/thestill status

dev-list: ## Quick: List all podcasts
	./venv/bin/thestill list
