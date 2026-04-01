# CloudCarbon

**Open-source multi-cloud GreenOps platform for unified cost and carbon optimisation.**

CloudCarbon ingests billing data from AWS, Azure, GCP, and Alibaba Cloud in the [FOCUS 1.0](https://focus.finops.org/) format, enriches each record with GHG Protocol Scope 1/2/3 carbon emissions and water consumption estimates, and surfaces actionable recommendations to reduce both cost and environmental impact simultaneously.

---

## Architecture Overview

CloudCarbon is a Turborepo monorepo with three runtime services and three shared packages.

| Component | Technology | Purpose |
|---|---|---|
| `apps/api` | FastAPI + Python 3.11 | REST API, authentication, ingestion, enrichment, recommendations |
| `apps/agents` | Python 3.11 + APScheduler | Autonomous agents: anomaly detection, rightsizing, idle reaping, green scheduling |
| `apps/web` | React + Vite (Lovable) | Dashboard UI — managed separately via Lovable |
| `packages/focus-schema` | Zod + Pydantic | FOCUS 1.0 and enrichment schemas shared across TypeScript and Python |
| `packages/carbon-models` | TypeScript + Python | Carbon and water estimation logic |
| `packages/api-types` | TypeScript (openapi-typescript) | Auto-generated types from `docs/openapi.yaml` |

The data flow is: **Cloud Provider → FOCUS Normalisation → PostgreSQL → Enrichment Pipeline → Recommendations Engine → API → Dashboard**.

All infrastructure is defined in `infra/` as Docker Compose (local), Helm (Kubernetes), and Terraform (AWS/GCP/Azure).

---

## Contributor Paths

CloudCarbon welcomes contributions across three primary tracks:

**1. Data Pipeline** (`apps/api/src/services/`, `packages/focus-schema/`)
Work on FOCUS ingestion connectors for each cloud provider, the enrichment pipeline (carbon intensity lookups via Electricity Maps, hardware embodied carbon via Boavizta, water stress via WRI Aqueduct), and the Alembic migration history. Good first issues involve adding new cloud provider adapters or improving enrichment methodology coverage.

**2. AI / Intelligence Layer** (`apps/agents/`, `apps/api/src/routers/`)
Build and improve the autonomous agents (anomaly detector, carbon spike monitor, rightsizing agent, idle reaper, green scheduler), the natural language query interface (Anthropic Claude), and the recommendation scoring engine. This track requires familiarity with LLM tool use and async Python.

**3. UI** (`apps/web/`)
The React frontend is built with Lovable and consumes the REST API defined in `docs/openapi.yaml`. UI contributors work on dashboards, charts, recommendation workflows, and the policy rule builder. TypeScript types are auto-generated from the OpenAPI spec via `@cloudcarbon/api-types`.

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/cloudcarbon/cloudcarbon.git
cd cloudcarbon

# 2. Copy environment variables
cp .env.example .env
# Edit .env and fill in your credentials

# 3. Start the full development stack
make dev

# 4. Run database migrations
make migrate

# 5. Verify the API is running
curl http://localhost:8000/health
# → {"status": "ok"}

# 6. Open the API docs
open http://localhost:8000/docs
```

### Prerequisites

- Docker and Docker Compose v2
- Node.js 20+ and pnpm 9+
- Python 3.11+ and Poetry 1.8+
- GNU Make

---

## Project Structure

```
cloudcarbon/
├── apps/
│   ├── api/              # FastAPI backend (Python 3.11)
│   ├── agents/           # Agent runner service (Python 3.11)
│   └── web/              # React + Vite frontend (Lovable)
├── packages/
│   ├── api-types/        # TypeScript types from OpenAPI spec
│   ├── focus-schema/     # FOCUS 1.0 + enrichment schemas
│   └── carbon-models/    # Carbon and water estimation logic
├── infra/
│   ├── docker-compose.yml       # Production compose
│   ├── docker-compose.dev.yml   # Development compose (+ pgAdmin, Redis Commander)
│   ├── helm/                    # Helm chart skeleton
│   └── terraform/               # Terraform (AWS, GCP, Azure)
├── docs/
│   └── openapi.yaml      # OpenAPI 3.1 spec — source of truth
├── .env.example
├── turbo.json
├── pnpm-workspace.yaml
├── Makefile
└── README.md
```

---

## Make Targets

| Target | Description |
|---|---|
| `make dev` | Start all services in development mode |
| `make build` | Build all Docker images |
| `make migrate` | Run pending Alembic migrations |
| `make seed` | Seed development fixtures (Session 2+) |
| `make test` | Run all tests |
| `make lint` | Run all linters |
| `make typecheck` | Run TypeScript and Python type checks |
| `make format` | Auto-format all code |
| `make clean` | Remove build artefacts and Docker volumes |

---

## Documentation

- [`docs/openapi.yaml`](docs/openapi.yaml) — Full REST API specification
- [`apps/api/alembic/`](apps/api/alembic/) — Database migration history
- [`packages/focus-schema/`](packages/focus-schema/) — FOCUS 1.0 schema reference

---

## Session Roadmap

| Session | Scope |
|---|---|
| **Session 1** (current) | Monorepo scaffold, toolchain, database schema, authentication |
| Session 2 | Ingestion connectors (AWS CUR, Azure Cost Export, GCP Billing), enrichment pipeline |
| Session 3 | Recommendation engine, autonomous agents, natural language query |
| Session 4 | Reports API, Helm chart, Terraform modules, CI/CD |
| Session 5 | Frontend integration, E2E tests, performance tuning, public launch prep |

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
