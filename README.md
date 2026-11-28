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

Check that the API is up: <http://localhost:8000/api/v1/health>, then open
<http://localhost:3000>. That is all you need day to day: the enriched graph is already
on disk, in Qdrant and in Neo4j.

### Login

The web app signs users in with Supabase Auth (email + password, 6-digit codes by email).
Setup of the Supabase project is described in
[docs/ke-hoach-auth-hom-nay-2026-11-26.md](docs/ke-hoach-auth-hom-nay-2026-11-26.md).

| File | Variables |
| --- | --- |
| `.env` | `SUPABASE_URL`, `REQUIRE_LOGIN` (default `true`) |
| `apps/web/.env.local` | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_API_URL` |

The search, search-info and graph routes need a Supabase access token. `/health` and
`/images/{asset_id}` are public. `scripts/evaluate_v2.py` and `scripts/bench_latency.py`
send no token: restart the API with `REQUIRE_LOGIN=false` before running them
(PowerShell: `$env:REQUIRE_LOGIN = "false"`), on `127.0.0.1` only, and turn it back on
afterwards.

### Admin

Two roles: `user` (search) and `admin` (also `/admin` in the web app and `/api/v1/admin/*`). The role
lives in `public.profiles` on Supabase and reaches the access token as the `user_role` claim.

1. Run [supabase/migrations/0001_profiles_roles.sql](supabase/migrations/0001_profiles_roles.sql) in the
   Supabase SQL Editor, then enable it under **Authentication → Hooks → Customize Access Token (JWT)
   Claims** (`public.custom_access_token_hook`).
2. Make someone an admin: `update public.profiles set role = 'admin' where email = '...';`. They sign
   out and in again (or wait for the token to refresh) to get the claim.

In the web UI a release is "Áp dụng vào tìm kiếm" (apply to search). It has no page of its own: it
starts from the entity page or, for the new products of a CSV, from the import page. The entity page
previews the
relations the next release would build, from the saved drafts and the form's unsaved values
(`POST /api/v1/admin/entities/{id}/preview`, which runs the release's own edge builder on a copy),
and can start the release itself. Right-clicking a value node of that graph (or selecting it, on a
touch screen) removes it from the product: the form draft drops every value that produces the node, so a
purpose bucket cannot come back on release. Category and product nodes cannot be removed. A release applies every pending change, wherever it starts; the
confirm step lists them. Products with pending edits are the "Chờ áp dụng" filter of the product list.

`REQUIRE_LOGIN=false` never opens the admin routes. What an admin can do, and when it takes effect:

| Action | Takes effect |
| --- | --- |
| Find and check entities, view their relations and images | read only |
| Edit brand, category, product type, tier, TREATS / HAS_INGREDIENT / FITS / ORIGIN, attributes; mark as checked | written to `graph/admin/changes.jsonl` (who, when, before, after); pending until a release |
| Hide an image from search, or show it again | at once (`graph/admin/images.jsonl`) |
| Upload an image for an existing entity | at once: embedded on the API's GPU and upserted to Qdrant; file, vectors and log in `data/admin/` |
| Delete an uploaded image | at once: Qdrant point removed, file moved to `data/admin/trash/` |
| Import approved images from a CSV (`/admin/imports`) | background job; images of existing entities at once, new entities at the next release (below) |
| Release | `scripts/admin_release.py` on a copy of `search/`; the old one stays as `search-prev-<time>` |

A release from the web page runs inside the API: brand edits change `graph.json`, so the changed doc
texts are re-embedded with the API's own bge-m3 (a second copy does not fit beside it on a 4 GB GPU),
the changed Qdrant points are synced and `short_aliases.json` is rebuilt; label and relation edits
update `entity_categories.json` and the category payload of the affected points. The API then reloads
the new artifacts without a restart. `python scripts/admin_release.py` shows the same plan as a dry
run; with `--write` from the command line, stop the API first on a small GPU and restart it afterwards.
`build_graph_lite.py` and `graph_apply.py merge` apply the change log as their highest-priority source,
so a rebuild from the raw sources keeps the admin's edits. `qdrant_setup.py --recreate` drops uploaded
images.

**CSV import.** Columns `entity_name` and `image_url` are required; `page_url`, `page_title`,
`entity_id` and `approve` are optional, and the Vietnamese headers of the labelling CSVs work too
(`Tên entity`, `URL ảnh`, `URL trang nguồn`, `Tiêu đề nguồn`, `Entity_ID`). Every row is taken as an
approved image (a row whose `approve` is not yes is skipped). The job
([imports.py](services/api/src/visualseek_api/application/imports.py)) downloads each image and reads
its source page like the corpus was read
([page_context.py](packages/retrieval/src/visualseek_retrieval/page_context.py)), OCRs it with the
corpus settings, and drops an image that is already in the collection (sha256) or is the same picture
at another size as an image of its target entity (pHash + pixel check of `find_near_duplicates.py`).
Each entity name is then matched against the graph
([csv_import.py](packages/retrieval/src/visualseek_retrieval/csv_import.py)):

* the CSV `entity_id` is a graph entity, or the name equals one entity's name or alias → its images are
  embedded and upserted at once, like an upload (doc text = entity card + page lines + OCR);
* a close name (edit similarity, or one name opening the other) → waits in the "Cần so khớp" tab;
  the admin picks a candidate or any entity, or says it is a new product;
* nothing close → a new entity: its evidence pack is classified and enriched with the prompts of
  `graph_classify.py` / `graph_enrich.py` (`ADMIN_LLM_ENGINE=agy` by default; `api` uses
  `GEMINI_API_KEY`; `off` registers it unlabelled) and written to `graph/admin/new_entities.jsonl`.
  The next release adds it to `graph.json` (aliases, MADE_BY to a known brand or one the name spells)
  and `entity_categories.json` (flagged `imported` and `needs_review`), then its images are upserted.
  `build_graph_lite.py` and `graph_apply.py merge` read the same file. Before the release an admin can
  still say it is an existing product; afterwards it is edited like any entity.

State lives in `data/admin/imports/<id>/`; an interrupted import resumes when the API starts.

### Only when you change the graph

Skip this unless you enrich or review the graph again. The API keeps the graph and
`entity_categories.json` in memory, so restart it after any change to them. Run in this order:

```powershell
python scripts/graph_enrich.py free                   # zero-quota extraction from evidence
python scripts/graph_enrich.py run --ids all          # Gemini enrichment (cached per entity)
python scripts/graph_apply.py merge                   # writes graph/entity_categories.json (admin edits on top)
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
