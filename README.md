# VisualSeek — Multimodal AI Image Search Engine

VisualSeek is a starter monorepo for a multimodal image-search system. This repository currently contains infrastructure, application shells, and typed boundaries only—OCR, VLM, embeddings, indexing, retrieval algorithms, authentication, and business logic are intentionally not implemented.

## Repository layout

```text
apps/web/                 Next.js frontend
services/api/             FastAPI HTTP service
packages/preprocessing/   OCR, VLM, and preprocessing contracts
packages/embedding/       Text and image embedding contracts
packages/retrieval/       Vector retrieval contracts
packages/evaluation/      Retrieval evaluation contracts
compose.yaml              Local development infrastructure
```

The Python code is managed as a `uv` workspace. Each AI area is an independent package so implementations can evolve without coupling them to the API transport layer.

## Prerequisites

- Docker with Docker Compose (recommended)
- For host development: Node.js 22+, npm 10+, Python 3.12, and `uv`

## Quick start with Docker

1. Create the local environment file:

   ```bash
   cp .env.example .env
   ```

2. Start the stack:

   ```bash
   docker compose up --build
   ```

3. Open:

   - Frontend: <http://localhost:3000>
   - API health: <http://localhost:8000/api/v1/health>
   - Qdrant dashboard: <http://localhost:6333/dashboard>
   - Local object-storage console: <http://localhost:9001>

The local `object-storage` service is S3-compatible and stands in for Cloudflare R2 during development. No storage operations are implemented yet.

## Host development

Backend:

```bash
cp .env.example .env
uv sync
uv run uvicorn visualseek_api.main:app --app-dir services/api/src --reload
```

Frontend (in a second terminal):

```bash
cd apps/web
npm ci
npm run dev
```

## Checks

```bash
uv run pytest
uv run ruff check services packages
npm --prefix apps/web run typecheck
```

Or run all checks with `make check`. Docker-based checks additionally require Docker to be installed.

## Configuration

All supported configuration is documented in `.env.example`. Values there are development defaults only. Do not commit `.env` or production credentials. Cloudflare R2 uses its S3-compatible endpoint and credentials when the placeholder storage adapter is implemented later.

## API conventions

- Base path: `/api/v1`
- Health endpoint: `GET /api/v1/health`
- Errors use a stable JSON envelope with `error.code`, `error.message`, and `request_id`.
- Request logs are structured JSON and include a request ID.

