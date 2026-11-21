# V-Lookup — product image search

Search engine for product images (products, medicines, dishes, spare parts, handicrafts,
medical devices). Queries are handled by one pipeline with no router and no LLM at query time:
entity spotting, then a knowledge graph for entity relations, then Qdrant hybrid retrieval.

The roadmap is in [PLAN_PRODUCTS_V1.md](PLAN_PRODUCTS_V1.md). The previous Vietnamese-culture
baseline was removed; it remains available in git history (branch `feat/text-search-baseline`).

## Repository layout

```text
apps/web/             Next.js frontend (placeholder, to be rebuilt)
services/api/         FastAPI service (health endpoint only for now)
packages/retrieval/   Vietnamese text normalization (surface, normalize, accent_key)
scripts/              Offline tooling (Qdrant collection setup)
compose.yaml          Local stack: web, api, postgres (knowledge graph), qdrant
```

The Python code is a `uv` workspace.

## Prerequisites

- Docker with Docker Compose
- For host development: Node.js 22+, npm 10+, Python 3.12, and `uv`

## Run

```bash
docker compose up --build
```

- Frontend: <http://localhost:3000>
- API health: <http://localhost:8000/api/v1/health>
- Qdrant dashboard: <http://localhost:6333/dashboard>

Host development:

```bash
uv sync
uv run uvicorn visualseek_api.main:app --app-dir services/api/src --reload

cd apps/web && npm ci && npm run dev
```

## Checks

```bash
make check   # pytest + ruff + web typecheck
```
