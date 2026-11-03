# Architecture boundaries

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

