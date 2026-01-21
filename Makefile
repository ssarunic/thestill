.PHONY: help install install-dev test test-fast test-coverage lint format typecheck check clean clean-all run-mcp

# Default target
.DEFAULT_GOAL := help

# Colors for output
CYAN := \033[0;36m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RESET := \033[0m

help: ## Show this help message
	@echo "$(CYAN)thestill.me - Development Makefile$(RESET)"
	@echo ""
	@echo "$(GREEN)Available targets:$(RESET)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-20s$(RESET) %s\n", $$1, $$2}'

install: ## Install package in development mode
	pip install -e .

install-dev: ## Install package with development dependencies
	pip install -e ".[dev]"
	pre-commit install

test: ## Run all tests with coverage report
	pytest --cov=thestill --cov-report=term-missing --cov-report=html:reports/coverage --ignore=tests/e2e

test-fast: ## Run tests without coverage (faster)
	pytest -v --ignore=tests/e2e

test-unit: ## Run unit tests only
	pytest tests/unit --cov=thestill --cov-report=term-missing

test-integration: ## Run integration tests only
	pytest tests/integration --cov=thestill --cov-report=term-missing

test-e2e: ## Run E2E tests (requires running server)
	node tests/e2e/web/test_web_auth.cjs

test-coverage: ## Run tests and open HTML coverage report
	pytest --cov=thestill --cov-report=html:reports/coverage --ignore=tests/e2e
	@echo "$(GREEN)Opening coverage report...$(RESET)"
	open reports/coverage/index.html || xdg-open reports/coverage/index.html

lint: ## Run all linters (pylint + mypy)
	@echo "$(CYAN)Running pylint...$(RESET)"
	pylint thestill/
	@echo "$(CYAN)Running mypy...$(RESET)"
	mypy thestill/

format: ## Format code with black and isort
	@echo "$(CYAN)Running black...$(RESET)"
	black thestill/ tests/
	@echo "$(CYAN)Running isort...$(RESET)"
	isort thestill/ tests/

typecheck: ## Run type checking with mypy
	mypy thestill/

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
	pre-commit run --all-files

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
	thestill-mcp

# Development shortcuts
dev-refresh: ## Quick: Refresh all podcast feeds
	thestill refresh

dev-download: ## Quick: Download new episodes
	thestill download

dev-status: ## Quick: Show system status
	thestill status

dev-list: ## Quick: List all podcasts
	thestill list
