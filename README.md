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

## Quick start (local search stack)

The search API reads Qdrant Cloud (`QDRANT_URL` / `QDRANT_API_KEY` in `.env`) and the
artifacts under `data/processed/products-v1/search`. Start the three parts in separate
terminals from the repository root. The commands use the `product-ingest` venv.

```powershell
# 1. API on :8000 (loads bge-m3 and SigLIP2, takes 1-2 minutes before it answers)
$env:SEARCH_ENABLED = "true"
$env:PYTHONPATH = "packages/retrieval/src"
.\.venv\product-ingest\Scripts\python.exe -m uvicorn visualseek_api.main:app --app-dir services/api/src --host 127.0.0.1 --port 8000

# 2. Web UI on :3000
cd apps/web; npm run dev

# 3. Neo4j on :7474 / :7687 (optional, only to view the graph; needs Docker running)
docker compose --profile graph up -d --wait neo4j
```

Qdrant is reached over gRPC (port 6334) with a keep-alive heartbeat; `search/info` shows the
`qdrant_transport` in use. The API falls back to REST by itself if 6334 is unreachable, or set
`QDRANT_PREFER_GRPC=false` in `.env` to force REST. Always call the API as `127.0.0.1`, not
`localhost`, when timing it from Windows tools (`localhost` adds ~200 ms to connect).

Check that the API is up: <http://localhost:8000/api/v1/search/info>, then open
<http://localhost:3000>. That is all you need day to day: the enriched graph is already
on disk, in Qdrant and in Neo4j.

### Only when you change the graph

Skip this unless you enrich or review the graph again. The API keeps the graph and
`entity_categories.json` in memory, so restart it after any change to them. Run in this order:

```powershell
python scripts/graph_enrich.py free                   # zero-quota extraction from evidence
python scripts/graph_enrich.py run --ids all          # Gemini enrichment (cached per entity)
python scripts/graph_apply.py merge                   # writes graph/entity_categories.json
python scripts/neo4j_load_graph.py                    # reload Neo4j (drops edges no longer in the snapshot)
# then restart the API (step 1 above)
```

Run `make check` before relying on the result.

## Neo4j for graph review

Set `NEO4J_AUTH=neo4j/<local-password>` in `.env` before the first start. Use a
password of at least eight characters.

```bash
docker compose --profile graph up -d --wait neo4j
pip install "neo4j>=5.28,<6"
python scripts/neo4j_load_graph.py   # copies search/graph.json into Neo4j
```

Open Neo4j Browser at <http://127.0.0.1:7474> and sign in as `neo4j`. Bolt listens
on `bolt://127.0.0.1:7687`. The `neo4j_data` volume persists the graph between
container runs. B5 takes its inputs from B4. The search API reads a local graph snapshot.

`NEO4J_AUTH` only sets the password when the volume is created. After you change it in
`.env`, the old password still applies to an existing `neo4j_data` volume.

`neo4j_load_graph.py` loads entities, brands, categories, `MADE_BY` (with `reviewed` and
`reviewer`), `IS_A` and `SAME_AS`. A reload deletes whatever the new snapshot no longer
has. Neo4j is a copy for viewing the graph: the API never reads it, and nothing is
written back to `graph.json`.

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
