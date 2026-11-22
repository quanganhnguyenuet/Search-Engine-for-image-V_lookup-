# V-Lookup — product image search

Search engine for product images (products, medicines, dishes, spare parts, handicrafts,
medical devices). Queries are handled by one pipeline with no router and no LLM at query time:
entity spotting, then a knowledge graph for entity relations, then Qdrant hybrid retrieval.

The roadmap is in [PLAN_PRODUCTS_V1.md](PLAN_PRODUCTS_V1.md). The previous Vietnamese-culture
baseline was removed; it remains available in git history (branch `feat/text-search-baseline`).

## Repository layout

```text
apps/web/             Next.js search page (POST /api/v1/search/text)
services/api/         FastAPI service: health, text search, corpus images
packages/retrieval/   Text forms, BM25, encoder, S1 spotting, pipeline, Qdrant request
scripts/              Offline tooling: corpus, graph-lite, doc texts, Qdrant ingest, eval
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

## Text search (products-v1 MVP)

The MVP implements PLAN_PRODUCTS_V1.md with a reduced scope: a free-source graph file instead
of Neo4j, no VLM caption, a silver eval set. Steps after the frozen corpus (P0):

```bash
python scripts/build_graph_lite.py          # aliases, model codes, MADE_BY edges -> search/graph.json
python scripts/build_doc_texts.py           # doc_text per image + bge-m3 vectors (GPU, ~4 min)
python scripts/qdrant_setup.py              # collection products_v1_images on QDRANT_URL
python scripts/build_eval_silver.py         # dev/test/warmup under eval/silver-v1
SEARCH_ENABLED=true uvicorn visualseek_api.main:app --app-dir services/api/src
python scripts/evaluate_v2.py run --split dev --out data/processed/products-v1/eval/runs/dev.json
```

The encoder needs the `encoder` extra of `visualseek-retrieval` (sentence-transformers, torch).
`SEARCH_EMBED_DEVICE` defaults to `cuda` and falls back to CPU. Note that `.env` may still hold
settings of the removed culture baseline (`SEARCH_*`, `EMBEDDING_*`); the API ignores them.

## Checks

```bash
make check   # pytest + ruff + web typecheck
```
