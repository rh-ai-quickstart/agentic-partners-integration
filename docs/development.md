# Development Guide

## Makefile Targets

```bash
make help                  # Show all available targets
```

### Setup & Deploy

| Command | What it does |
|---------|-------------|
| `make setup` | Full stack: build images, start all containers, run migrations, ingest RAG data, verify health. This is the only command you need. |
| `make build` | Build container images only (no start). Useful for pre-building before `setup`. |
| `make stop` | Stop all running containers (preserves data). |
| `make clean` | Stop and remove all containers, volumes, and network. Run `make setup` again after this. |

### Testing

| Command | What it does |
|---------|-------------|
| `make test` | Run E2E tests against running services (requires `make setup` first). |
| `make test-unit` | Run unit tests for all packages (no containers needed). |
| `make test-coverage` | Run unit tests with coverage report. |

### Development

| Command | What it does |
|---------|-------------|
| `make install` | Install all package dependencies locally (via uv). |
| `make format` | Run isort and Black formatting. |
| `make lint` | Run flake8, isort check, and mypy. |
| `make logs-request-manager` | Tail request-manager container logs. |
| `make logs-agent-service` | Tail agent-service container logs. |
| `make logs-rag-api` | Tail RAG API container logs. |

## Build Containers

`make setup` builds automatically. To build images without starting:

```bash
make build
```

To build individual images:

```bash
docker build -t partner-agent-service:latest -f agent-service/Containerfile .
docker build -t partner-request-manager:latest -f request-manager/Containerfile .
docker build -t partner-rag-api:latest -f rag-service/Containerfile .
docker build -t partner-pf-chat-ui:latest -f pf-chat-ui/Containerfile .
```

## Typical Workflow

```bash
make setup          # First time: build + start everything (~3-5 min)
make test           # Verify all 24 E2E tests pass
# ... use the UI at http://localhost:3000 ...
make stop           # Done for the day

make setup          # Next time: rebuilds and starts fresh
make clean          # When you want to wipe everything
```

`make setup` is idempotent -- it stops existing containers, rebuilds images, and starts fresh every time.

## Alternative: Docker Compose

```bash
docker compose up   # Starts stack with different port mappings
```

| Method | Command | PG Port | RAG Port |
|--------|---------|---------|----------|
| Makefile (recommended) | `make setup` | 5433 | 8003 |
| Docker Compose | `docker compose up` | 5432 | 8080 |

Both expose Web UI on 3000, Request Manager on 8000, and Agent Service on 8001.

## Stop / Clean

```bash
make stop          # Stop containers (preserves data, fast restart)
make clean         # Stop + remove containers and network (full reset)
```

## Kubernetes Deployment

See [helm/README.md](../helm/README.md) for Helm chart deployment to Kubernetes/OpenShift.

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/setup.sh` | Full setup: build, start, migrate, ingest, verify. Called by `make setup`. |
| `scripts/build_containers.sh` | Build all four container images. Called by `make build`. |
| `scripts/test.sh` | 24 E2E tests covering auth, authorization, RAG, and workflows. Called by `make test`. |
