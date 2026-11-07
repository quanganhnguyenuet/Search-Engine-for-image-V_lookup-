# VisualSeek — Multimodal AI Image Search Engine

Current focus: [contextual text-to-image search](docs/contextual-image-search.md).
Search defaults to local-only: exact names/tags use a local lookup and descriptions
use local image/metadata hybrid retrieval. The web defaults to **Tìm theo yêu cầu**.
OpenAI planning/reranking and RAG require explicit `SEARCH_LLM_ENABLED=true` plus a
backend `OPENAI_API_KEY`; semantic and keyword baselines remain available for comparison.
When enabled, `SEARCH_ROUTING_MODE=auto` keeps names/tags and ordinary descriptions
local; a small learned intent classifier sends reasoning or uncertain requests to
the LLM. Set `SEARCH_ROUTING_MODE=always` for full planning. See
[semantic query planning](docs/semantic-query-planning.md) for constraints and clarification.
See the [approved plan and scope update](docs/text-to-image-search-plan.md).
For Qdrant credentials, see [Qdrant Cloud setup](docs/qdrant-cloud-setup.md).
The current search backend remains NumPy; credentials alone do not enable Qdrant retrieval.

Image/metadata fusion now includes indexed lexical scoring, configurable weighted
RRF, separate query roles, wider annotation/vision reranking, and human-calibrated
relevance tooling. See [rollout and evaluation commands](docs/search-fusion-setup.md).

Vietnamese labels now preserve diacritics and word boundaries, request clarification
for ambiguous unaccented names, and exclude reviewed source-folder contamination.
See [label repairs and regression queries](docs/vietnamese-label-repair.md).

VisualSeek is a monorepo for a multimodal image-search system. The **VietNam Culture**
frontend searches a local cultural corpus through natural-language requests, semantic
similarity, or keywords. SigLIP2 exports image and tag/description vectors; OpenAI
interprets user intent before CPU hybrid retrieval. OCR and production storage adapters
remain future work.

## Embedding on Kaggle and RAG

Notebook cho **Kaggle T4 ×2**, đúng allowlist dataset local và upload embedding lên
`Quanganh6905/Embedding_for_searchengine`:
[visualseek-kaggle-t4x2-huggingface.ipynb](notebooks/visualseek-kaggle-t4x2-huggingface.ipynb).
Xem [hướng dẫn chuẩn bị và chạy](docs/kaggle-t4x2-huggingface.md).

See [Kaggle embedding and local RAG](docs/kaggle-embedding-rag.md) for runnable notebook
cells, model setup, portable vector artifacts, and the semantic/RAG API. The browser
now supports semantic search, image queries, grounded answers, and keyword comparison.
An imported artifact and the real model runtimes are required for the AI modes.

## VietNam Culture keyword search

With the dataset already downloaded, run the web app independently:

```bash
cd apps/web
npm ci
npm run dev
```

Open <http://localhost:3000>. Search Vietnamese text with or without diacritics,
filter six cultural categories, and open a photo to read its description and tags.
Images stay in the existing local data directory; no GPU or external services are
needed for keyword search. See [keyword search setup](docs/culture-search.md).

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

## Vietnamese culture corpus

Download and prepare the six-domain Kaggle corpus (cuisine, architecture, landscapes,
festivals, clothing, handicrafts):

```bash
PYTHONPATH=packages/preprocessing/src python3 -u -m visualseek_preprocessing.culture_dataset \
  --download-archive data/raw/archives/vietnamese-culture-vqa-v1.zip
```

This downloads the official version-1 ZIP, extracts all six selected categories,
and rebuilds the full manifest without a per-category sampling limit.
See [dataset preparation](docs/culture-dataset.md) for category counts, sampling,
manifest fields, deduplication, and the indexing handoff. Downloaded data is ignored by Git.

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

