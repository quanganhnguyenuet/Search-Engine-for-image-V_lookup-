# Architecture boundaries

Current implementation: [contextual image search](contextual-image-search.md).
`web -> FastAPI -> local query router` selects exact title/tag lookup, direct local
SigLIP2 + NumPy hybrid RRF, or an OpenAI planner for logical/multi-clause requests.
LLM reranking is optional and only runs on the LLM path; no production DB
adapter is in the search path. The initialization notes below describe the original boundaries.

VisualSeek starts as a modular monorepo with explicit dependency directions:

```text
web client -> HTTP API -> application services (future)
                            |-> preprocessing contracts
                            |-> embedding contracts
                            |-> retrieval contracts
                            `-> infrastructure adapters (future)

evaluation contracts -> offline evaluation runner (future)
```

## Current responsibilities

- `apps/web` owns browser rendering and client-side interaction.
- `services/api` owns HTTP transport, configuration, observability, and error mapping.
- `packages/preprocessing` owns boundaries for image normalization, OCR, and VLMs.
- `packages/embedding` owns boundaries for text and image vectorization.
- `packages/retrieval` owns vector search requests and results.
- `packages/evaluation` owns offline evaluation inputs and reports.

## Intended dependency rules

1. Contracts do not import vendor SDKs or framework types.
2. Infrastructure adapters implement contracts and remain replaceable.
3. API routes delegate to application services once use cases are added.
4. The frontend communicates only through documented API contracts.
5. Offline preprocessing and evaluation do not depend on the web application.

No production adapters or application workflows are included in this initialization.

