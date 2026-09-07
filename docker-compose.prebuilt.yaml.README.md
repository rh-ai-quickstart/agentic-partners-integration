# Docker Compose Files

This directory contains two Docker Compose configurations:

## 1. `docker-compose.yaml` - Build from Source

**Use when:** You're developing locally or want to test code changes

**What it does:**
- Builds all container images from source code
- Uses local Dockerfiles
- Slower initial startup (builds can take 10-15 minutes)
- Reflects your latest code changes

**Usage:**
```bash
docker compose up --build
```

## 2. `docker-compose.prebuilt.yaml` - Use Pre-Published Images

**Use when:** You want to quickly deploy and test the published version

**What it does:**
- Pulls pre-published images from GitHub Container Registry (ghcr.io)
- Much faster startup (no build time, just image pull)
- Uses the same images as production Helm deployments
- Ideal for e2e testing and demos

**Pre-published images:**
- `ghcr.io/ccamacho/partner-rag-api:latest`
- `ghcr.io/ccamacho/partner-agent-service:latest`
- `ghcr.io/ccamacho/partner-request-manager:latest`
- `ghcr.io/ccamacho/partner-kubernetes-agent:latest`
- `ghcr.io/ccamacho/partner-pf-chat-ui:latest`

**Usage:**
```bash
# Pull images
docker compose -f docker-compose.prebuilt.yaml pull

# Start services
docker compose -f docker-compose.prebuilt.yaml up -d

# View logs
docker compose -f docker-compose.prebuilt.yaml logs -f

# Stop services
docker compose -f docker-compose.prebuilt.yaml down
```

## Which Should I Use?

| Scenario | File to Use |
|----------|-------------|
| First-time setup / Quick demo | `docker-compose.prebuilt.yaml` |
| E2E testing of published release | `docker-compose.prebuilt.yaml` |
| Local development | `docker-compose.yaml` |
| Testing code changes | `docker-compose.yaml` |
| Verifying builds work | `docker-compose.yaml` |

## Environment Variables

Both configurations use the same `.env` file. Make sure to set:

```bash
# Required
GOOGLE_API_KEY=your-key-here

# Optional (defaults shown)
GEMINI_MODEL=gemini-2.5-flash
LLM_BACKEND=gemini
```

## Full E2E Test (Recommended for Quick Start)

```bash
# 1. Pull pre-published images
docker compose -f docker-compose.prebuilt.yaml pull

# 2. Start all services
docker compose -f docker-compose.prebuilt.yaml up -d

# 3. Wait for services to be healthy (30-60 seconds)
docker compose -f docker-compose.prebuilt.yaml ps

# 4. Open browser
open http://localhost:3000

# 5. Login with test credentials
# carlos@example.com / carlos123

# 6. Try a query
# "My app crashes with error 500"

# 7. Check logs
docker compose -f docker-compose.prebuilt.yaml logs -f request-manager

# 8. Stop everything
docker compose -f docker-compose.prebuilt.yaml down
```
