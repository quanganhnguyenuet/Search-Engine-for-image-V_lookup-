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
compose.yaml          Local stack with web, API, Qdrant and a graph-review profile
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

## Neo4j for graph review

Set `NEO4J_AUTH=neo4j/<local-password>` in `.env` before the first start. Use a
password of at least eight characters.

```bash
docker compose up -d --wait neo4j
```

Open Neo4j Browser at <http://127.0.0.1:7474> and sign in as `neo4j`. Bolt listens
on `bolt://127.0.0.1:7687`. The `neo4j_data` volume persists the graph between
container runs. B5 takes its inputs from B4. The search API reads a local graph snapshot.

## Text search (products-v1 MVP)

The MVP implements PLAN_PRODUCTS_V1.md with a reduced scope: a free-source graph file instead
of Neo4j, no VLM caption, a silver eval set. Steps after the frozen corpus (P0):

```bash
python scripts/build_graph_lite.py          # aliases, model codes, MADE_BY edges -> search/graph.json
python scripts/build_doc_texts.py           # doc_text per image + bge-m3 vectors (GPU, ~4 min)
python scripts/qdrant_setup.py              # collection products_v1_images on QDRANT_URL
python scripts/embed_images.py              # SigLIP2 image vectors (GPU, ~7 min)
python scripts/qdrant_add_image_vectors.py  # adds the `img` named vector to existing points
python scripts/build_eval_silver.py         # dev/test/warmup under eval/silver-v1
SEARCH_ENABLED=true uvicorn visualseek_api.main:app --app-dir services/api/src
python scripts/evaluate_v2.py run --split dev --out data/processed/products-v1/eval/runs/dev.json
```

### Descriptive eval (human judgments)

Queries such as "chuột máy tính màu trắng" are judged by hand, because the silver set only
covers catalogue names. The queries (`eval/descriptive-v1/queries.jsonl`) and the judgments
(`eval/descriptive-v1/judgments.jsonl`) are versioned; the pool is rebuilt from them.

```bash
python scripts/build_eval_descriptive.py --device cpu   # pool: top-10 of text, img, rerank
python scripts/judge_server.py                          # judging page on http://127.0.0.1:8010
```

Grades: 2 = right object and the asked attribute is visible, 1 = right object only,
0 = not relevant. Use `--device cpu` while the API holds the GPU.

The encoder needs the `encoder` extra of `visualseek-retrieval` (sentence-transformers, torch).
`SEARCH_EMBED_DEVICE` defaults to `cuda` and falls back to CPU. Note that `.env` may still hold
settings of the removed culture baseline (`SEARCH_*`, `EMBEDDING_*`); the API ignores them.

## Checks

```bash
make check   # pytest + ruff + web typecheck
```
