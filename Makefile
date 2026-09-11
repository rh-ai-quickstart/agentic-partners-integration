# Makefile for Partner Agent Integration POC
#
# Primary deployment: scripts/setup.sh (Docker containers)
# Alternative: docker-compose.yaml

.PHONY: help
help:
	@echo "Partner Agent Integration - Available Targets"
	@echo ""
	@echo "Setup & Deploy:"
	@echo "  setup                    - Complete setup: build, start services, initialize data"
	@echo "  stop                     - Stop all running containers"
	@echo "  clean                    - Stop and remove all containers, volumes, and network"
	@echo ""
	@echo "Testing:"
	@echo "  test-unit                - Run unit tests for all packages"
	@echo "  test-shared-models       - Run shared-models unit tests"
	@echo "  test-request-manager     - Run request-manager unit tests"
	@echo "  test-agent-service       - Run agent-service unit tests"
	@echo "  test-k8s-partner         - Run kubernetes-partner-agent unit tests"
	@echo "  test-coverage            - Run all unit tests with coverage report"
	@echo ""
	@echo "Development:"
	@echo "  install                  - Install all package dependencies locally"
	@echo "  install-shared-models    - Install shared-models dependencies"
	@echo "  install-agent-service    - Install agent-service dependencies"
	@echo "  install-request-manager  - Install request-manager dependencies"
	@echo "  install-k8s-partner      - Install kubernetes-partner-agent dependencies"
	@echo "  reinstall                - Force reinstall all dependencies"
	@echo ""
	@echo "Code Quality:"
	@echo "  format                   - Run isort and Black formatting"
	@echo "  lint                     - Run linting (flake8, isort check, mypy)"
	@echo "  lint-shared-models       - Run mypy on shared-models"
	@echo "  lint-agent-service       - Run mypy on agent-service"
	@echo "  lint-request-manager     - Run mypy on request-manager"
	@echo "  lint-k8s-partner         - Run mypy on kubernetes-partner-agent"
	@echo ""
	@echo "Lockfile Management:"
	@echo "  check-lockfiles          - Check if all uv.lock files are up-to-date"
	@echo "  update-lockfiles         - Update all uv.lock files"
	@echo ""
	@echo "Publishing:"
	@echo "  publish                  - Build and publish container images to ghcr.io"
	@echo ""
	@echo "Logs:"
	@echo "  logs-request-manager     - Tail request-manager logs"
	@echo "  logs-agent-service       - Tail agent-service logs"
	@echo "  logs-rag-api             - Tail RAG API logs"

# ============================================================
# Setup & Deploy
# ============================================================

.PHONY: setup
setup:
	@echo "Running complete setup (build, start, seed)..."
	@bash scripts/setup.sh

.PHONY: publish
publish:
	@echo "Building and publishing container images to ghcr.io/rh-ai-quickstart..."
	@echo ""
	@echo "Step 1: Building images..."
	@bash scripts/setup.sh 2>&1 | grep -E "Building|built" || true
	@echo ""
	@echo "Step 2: Tagging images..."
	@docker tag partner-rag-api:latest ghcr.io/rh-ai-quickstart/partner-rag-api:latest
	@docker tag partner-agent-service:latest ghcr.io/rh-ai-quickstart/partner-agent-service:latest
	@docker tag partner-request-manager:latest ghcr.io/rh-ai-quickstart/partner-request-manager:latest
	@docker tag partner-pf-chat-ui:latest ghcr.io/rh-ai-quickstart/partner-pf-chat-ui:latest
	@echo "✓ Images tagged"
	@echo ""
	@echo "Step 3: Pushing images..."
	@docker push ghcr.io/rh-ai-quickstart/partner-rag-api:latest
	@docker push ghcr.io/rh-ai-quickstart/partner-agent-service:latest
	@docker push ghcr.io/rh-ai-quickstart/partner-request-manager:latest
	@docker push ghcr.io/rh-ai-quickstart/partner-pf-chat-ui:latest
	@echo ""
	@echo "✓ All images published to ghcr.io/rh-ai-quickstart"
	@echo ""
	@echo "Published images:"
	@echo "  • ghcr.io/rh-ai-quickstart/partner-rag-api:latest"
	@echo "  • ghcr.io/rh-ai-quickstart/partner-agent-service:latest"
	@echo "  • ghcr.io/rh-ai-quickstart/partner-request-manager:latest"
	@echo "  • ghcr.io/rh-ai-quickstart/partner-pf-chat-ui:latest"

.PHONY: stop
stop:
	@echo "Stopping all containers..."
	@if command -v docker-compose >/dev/null 2>&1; then \
		docker-compose down; \
	elif docker compose version >/dev/null 2>&1; then \
		docker compose down; \
	else \
		echo "Stopping containers manually..."; \
		docker stop $$(docker ps -q --filter "name=partner-") 2>/dev/null || true; \
	fi
	@echo "✓ All containers stopped"

.PHONY: clean
clean:
	@echo "Stopping and removing all containers, networks, and volumes..."
	@if command -v docker-compose >/dev/null 2>&1; then \
		docker-compose down -v; \
	elif docker compose version >/dev/null 2>&1; then \
		docker compose down -v; \
	else \
		echo "Removing containers manually..."; \
		docker stop $$(docker ps -q --filter "name=partner-") 2>/dev/null || true; \
		docker rm $$(docker ps -aq --filter "name=partner-") 2>/dev/null || true; \
		docker volume rm $$(docker volume ls -q --filter "name=partner-") 2>/dev/null || true; \
		docker network rm partner-agent-network 2>/dev/null || true; \
	fi
	@echo "✓ Clean complete"

# ============================================================
# Testing
# ============================================================

.PHONY: test-unit
test-unit: test-shared-models test-request-manager test-agent-service test-k8s-partner
	@echo "✓ All unit tests completed."

.PHONY: test-shared-models
test-shared-models:
	@echo "Running shared-models tests..."
	@cd shared-models && uv run python -m pytest tests/

.PHONY: test-request-manager
test-request-manager:
	@echo "Running request-manager tests..."
	@cd request-manager && uv run python -m pytest tests/

.PHONY: test-agent-service
test-agent-service:
	@echo "Running agent-service tests..."
	@cd agent-service && uv run python -m pytest tests/

.PHONY: test-k8s-partner
test-k8s-partner:
	@echo "Running kubernetes-partner-agent tests..."
	@cd kubernetes-partner-agent && uv run python -m pytest tests/

.PHONY: test-coverage
test-coverage:
	@echo "Running all unit tests with coverage..."
	@echo ""
	@echo "=== shared-models ==="
	@cd shared-models && uv run python -m pytest tests/ --cov=shared_models --cov-report=term-missing -q
	@echo ""
	@echo "=== agent-service ==="
	@cd agent-service && uv run python -m pytest tests/ --cov=agent_service --cov-report=term-missing -q
	@echo ""
	@echo "=== request-manager ==="
	@cd request-manager && uv run python -m pytest tests/ --cov=request_manager --cov-report=term-missing -q
	@echo ""
	@echo "=== kubernetes-partner-agent ==="
	@cd kubernetes-partner-agent && uv run python -m pytest tests/ --cov=kubernetes_agent --cov-report=term-missing -q
	@echo ""
	@echo "Coverage report complete."

# ============================================================
# Development - Install Dependencies
# ============================================================

.PHONY: install
install: install-shared-models install-agent-service install-request-manager install-k8s-partner
	@echo "All dependencies installed."

.PHONY: install-shared-models
install-shared-models:
	@echo "Installing shared-models dependencies..."
	@cd shared-models && uv sync

.PHONY: install-agent-service
install-agent-service:
	@echo "Installing agent-service dependencies..."
	@cd agent-service && uv sync

.PHONY: install-request-manager
install-request-manager:
	@echo "Installing request-manager dependencies..."
	@cd request-manager && uv sync

.PHONY: install-k8s-partner
install-k8s-partner:
	@echo "Installing kubernetes-partner-agent dependencies..."
	@cd kubernetes-partner-agent && uv sync

.PHONY: reinstall
reinstall:
	@echo "Force reinstalling all dependencies..."
	@cd shared-models && uv sync --reinstall
	@cd agent-service && uv sync --reinstall
	@cd request-manager && uv sync --reinstall
	@cd kubernetes-partner-agent && uv sync --reinstall
	@echo "All dependencies reinstalled."

# ============================================================
# Code Quality
# ============================================================

define lint_mypy
	@echo "Running mypy on $(1)..."
	@if [ -d "$(1)" ]; then \
		cd $(1) && uv run --with mypy mypy --strict . && echo "$(1) mypy passed"; \
	else \
		echo "$(1) directory not found, skipping..."; \
	fi
endef

.PHONY: format
format:
	@echo "Running isort..."
	@uv run isort .
	@echo "Running Black..."
	@uv run black .
	@echo "Formatting complete."

.PHONY: lint
lint: format lint-global lint-shared-models lint-agent-service lint-request-manager lint-k8s-partner
	@echo "All linting completed."

.PHONY: lint-global
lint-global:
	@echo "Running flake8..."
	@uv run flake8 .
	@echo "Running isort check..."
	@uv run isort --check-only --diff .

.PHONY: lint-shared-models
lint-shared-models:
	$(call lint_mypy,shared-models)

.PHONY: lint-agent-service
lint-agent-service:
	$(call lint_mypy,agent-service)

.PHONY: lint-request-manager
lint-request-manager:
	$(call lint_mypy,request-manager)

.PHONY: lint-k8s-partner
lint-k8s-partner:
	$(call lint_mypy,kubernetes-partner-agent)


# ============================================================
# Lockfile Management
# ============================================================

LOCKFILE_DIRS := shared-models agent-service request-manager kubernetes-partner-agent

.PHONY: check-lockfiles
check-lockfiles:
	@echo "Checking lockfiles..."
	@for dir in $(LOCKFILE_DIRS); do \
		echo "Checking $$dir..."; \
		(cd "$$dir" && uv lock --check) || exit 1; \
	done
	@echo "All lockfiles up-to-date."

.PHONY: update-lockfiles
update-lockfiles:
	@echo "Updating lockfiles..."
	@for dir in $(LOCKFILE_DIRS); do \
		echo "Updating $$dir..."; \
		(cd "$$dir" && uv lock); \
	done
	@echo "All lockfiles updated."

# ============================================================
# Logs
# ============================================================

.PHONY: logs-request-manager
logs-request-manager:
	@docker logs -f partner-request-manager-full

.PHONY: logs-agent-service
logs-agent-service:
	@docker logs -f partner-agent-service-full

.PHONY: logs-rag-api
logs-rag-api:
	@docker logs -f partner-rag-api-full
