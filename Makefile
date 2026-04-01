# ============================================================
# CloudCarbon Makefile
# ============================================================
# Usage: make <target>
# Run `make help` for a full list of targets.

.PHONY: dev build stop migrate migrate-down migrate-history seed \
        test test-api test-watch \
        lint typecheck format \
        openapi-gen openapi-validate \
        helm-lint helm-template helm-install helm-upgrade helm-uninstall \
        docker-build docker-push \
        agents-run agents-list \
        clean help

COMPOSE_DEV  = docker compose -f infra/docker-compose.dev.yml
COMPOSE_PROD = docker compose -f infra/docker-compose.yml
API_DIR      = apps/api
AGENTS_DIR   = apps/agents
HELM_CHART   = infra/helm/cloudcarbon
HELM_RELEASE = cloudcarbon
HELM_NS      = cloudcarbon

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------

## dev: Start all services in development mode (hot-reload)
dev:
	$(COMPOSE_DEV) up -d
	@echo ""
	@echo "  CloudCarbon dev stack started:"
	@echo "    API:             http://localhost:8000"
	@echo "    API Docs:        http://localhost:8000/docs"
	@echo "    Metrics:         http://localhost:8000/metrics"
	@echo "    pgAdmin:         http://localhost:5050"
	@echo "    Redis Commander: http://localhost:8081"
	@echo ""

## dev-logs: Tail logs from all dev services
dev-logs:
	$(COMPOSE_DEV) logs -f

## build: Build all production Docker images
build:
	$(COMPOSE_PROD) build

## stop: Stop all running dev services
stop:
	$(COMPOSE_DEV) down

## restart: Restart the API service (hot-reload alternative)
restart:
	$(COMPOSE_DEV) restart api

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

## migrate: Run Alembic migrations against the running database
migrate:
	$(COMPOSE_DEV) exec api poetry run alembic upgrade head

## migrate-down: Roll back the last migration
migrate-down:
	$(COMPOSE_DEV) exec api poetry run alembic downgrade -1

## migrate-history: Show migration history
migrate-history:
	$(COMPOSE_DEV) exec api poetry run alembic history --verbose

## migrate-new: Create a new Alembic migration (usage: make migrate-new MSG="add_foo_table")
migrate-new:
	$(COMPOSE_DEV) exec api poetry run alembic revision --autogenerate -m "$(MSG)"

## seed: Seed the database with reference data
seed:
	$(COMPOSE_DEV) exec api poetry run python -m src.seeds.reference_data

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

## test: Run the full test suite (normalizers + enrichment + recommendations + forecasting)
test:
	cd $(API_DIR) && PYTHONPATH=../../packages/focus-schema/src:../../packages/carbon-models/src:src \
		poetry run pytest -v --tb=short

## test-api: Run only the API tests with coverage
test-api:
	cd $(API_DIR) && PYTHONPATH=../../packages/focus-schema/src:../../packages/carbon-models/src:src \
		poetry run pytest -v --cov=src --cov-report=term-missing --cov-report=html

## test-watch: Run tests in watch mode (requires pytest-watch)
test-watch:
	cd $(API_DIR) && PYTHONPATH=../../packages/focus-schema/src:../../packages/carbon-models/src:src \
		poetry run ptw -- -v

## test-normalizers: Run only normalizer tests
test-normalizers:
	cd $(API_DIR) && PYTHONPATH=../../packages/focus-schema/src:../../packages/carbon-models/src:src \
		poetry run pytest tests/normalizers/ -v

## test-enrichment: Run only enrichment tests
test-enrichment:
	cd $(API_DIR) && PYTHONPATH=../../packages/focus-schema/src:../../packages/carbon-models/src:src \
		poetry run pytest tests/enrichment/ -v

## test-recommendations: Run only recommendation and policy tests
test-recommendations:
	cd $(API_DIR) && PYTHONPATH=../../packages/focus-schema/src:../../packages/carbon-models/src:src \
		poetry run pytest tests/recommendations/ -v

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------

## lint: Run ruff linter on all Python apps
lint:
	cd $(API_DIR) && poetry run ruff check src/ tests/
	cd $(AGENTS_DIR) && poetry run ruff check src/

## typecheck: Run mypy type checks on all Python apps
typecheck:
	cd $(API_DIR) && poetry run mypy src/
	cd $(AGENTS_DIR) && poetry run mypy src/

## format: Auto-format all Python code with ruff
format:
	cd $(API_DIR) && poetry run ruff format src/ tests/
	cd $(AGENTS_DIR) && poetry run ruff format src/

# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------

## openapi-validate: Validate the OpenAPI spec with spectral or python-yaml
openapi-validate:
	python3 -c "import yaml; yaml.safe_load(open('docs/openapi.yaml'))" && \
		echo "docs/openapi.yaml: YAML syntax OK"

## openapi-gen: Generate TypeScript types from the OpenAPI spec
openapi-gen: openapi-validate
	@command -v npx >/dev/null 2>&1 || (echo "npx not found — install Node.js" && exit 1)
	npx --yes @hey-api/openapi-ts \
		--input docs/openapi.yaml \
		--output packages/api-types/src/generated \
		--client @hey-api/client-fetch
	@echo "TypeScript types generated in packages/api-types/src/generated/"

# ---------------------------------------------------------------------------
# Helm
# ---------------------------------------------------------------------------

## helm-lint: Lint the Helm chart
helm-lint:
	helm lint $(HELM_CHART)

## helm-template: Render Helm templates to stdout (dry-run)
helm-template:
	helm template $(HELM_RELEASE) $(HELM_CHART) \
		--namespace $(HELM_NS) \
		--create-namespace

## helm-install: Install the Helm chart (requires kubectl context)
helm-install:
	helm install $(HELM_RELEASE) $(HELM_CHART) \
		--namespace $(HELM_NS) \
		--create-namespace \
		--values $(HELM_CHART)/values.yaml

## helm-upgrade: Upgrade an existing Helm release
helm-upgrade:
	helm upgrade $(HELM_RELEASE) $(HELM_CHART) \
		--namespace $(HELM_NS) \
		--values $(HELM_CHART)/values.yaml \
		--atomic --timeout 5m

## helm-uninstall: Uninstall the Helm release
helm-uninstall:
	helm uninstall $(HELM_RELEASE) --namespace $(HELM_NS)

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

## docker-build: Build and tag production images
docker-build:
	docker build -t ghcr.io/cloudcarbon/api:latest $(API_DIR)/
	docker build -t ghcr.io/cloudcarbon/agents:latest $(AGENTS_DIR)/

## docker-push: Push images to registry (requires docker login)
docker-push:
	docker push ghcr.io/cloudcarbon/api:latest
	docker push ghcr.io/cloudcarbon/agents:latest

# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

## agents-run: Trigger a manual agent run (usage: make agents-run TYPE=anomaly_detector)
agents-run:
	curl -s -X POST http://localhost:8000/agents/$(TYPE)/run \
		-H "Authorization: Bearer $(TOKEN)" \
		-H "Content-Type: application/json" \
		-d '{"dry_run": true}' | python3 -m json.tool

## agents-list: List all agent configurations
agents-list:
	curl -s http://localhost:8000/agents \
		-H "Authorization: Bearer $(TOKEN)" | python3 -m json.tool

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

## clean: Remove build artifacts, Docker volumes, and caches
clean:
	$(COMPOSE_DEV) down -v 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "Clean complete."

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

## help: Show this help message
help:
	@echo ""
	@echo "CloudCarbon Makefile — available targets:"
	@echo ""
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  /' | column -t -s ':'
	@echo ""
