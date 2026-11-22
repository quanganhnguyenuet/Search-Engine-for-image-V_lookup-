"""Search service: loads artifacts once at startup, then serves the single search path."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import structlog

from visualseek_api.core.config import Settings

logger = structlog.get_logger(__name__)
WARMUP_QUERIES = ("chuột không dây", "Shimano", "thuốc giảm đau và tai nghe")


@dataclass
class SearchService:
    pipeline: object
    image_paths: dict[str, str]
    image_root: Path
    info: dict
    # One dedicated thread: CUDA work stays on a warmed thread (per-thread handles were
    # costing 20-30 ms on first use) and GPU access is serialised.
    executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="search")
    )

    @classmethod
    def load(cls, settings: Settings) -> SearchService:
        from visualseek_retrieval.encoder import TextEncoder
        from visualseek_retrieval.pipeline import SearchPipeline
        from visualseek_retrieval.qdrant_store import connect

        started = time.perf_counter()
        root = Path(settings.search_artifact_dir)
        graph = json.loads((root / "graph.json").read_text(encoding="utf-8"))
        entity_dense = np.load(root / "entity_dense.npy")
        index = json.loads((root / "index_manifest.json").read_text(encoding="utf-8"))
        if index["graph_fingerprint"] != graph["fingerprint"]:
            raise RuntimeError("Qdrant collection was built from a different graph snapshot")
        image_paths = {}
        for line in (root / "doc_texts.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            image_paths[row["asset_id"]] = row["image_path"]
        encoder = TextEncoder(device=settings.search_embed_device)
        client = connect(settings.qdrant_url, settings.qdrant_api_key)
        pipeline = SearchPipeline(graph, entity_dense, encoder, client, settings.search_collection)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="search")
        for query in WARMUP_QUERIES:  # pay TLS, CUDA and tokenizer start-up before serving
            executor.submit(pipeline.search, query).result()
        image_root = (
            Path(settings.products_image_root)
            if settings.products_image_root
            else Path(
                json.loads((root.parent / "manifest.json").read_text(encoding="utf-8"))[
                    "image_root"
                ]
            )
        )
        info = {
            "collection": settings.search_collection,
            "points": index["points"],
            "graph_fingerprint": graph["fingerprint"],
            "aliases_sha256": graph.get("aliases_sha256"),
            "corpus_fingerprint": graph["corpus_fingerprint"],
            "doc_texts_sha256": index["doc_texts_sha256"],
            "encoder_device": encoder.device,
            "settings": pipeline.settings.__dict__,
        }
        logger.info("search_loaded", seconds=round(time.perf_counter() - started, 1), **info)
        return cls(pipeline, image_paths, image_root, info, executor)

    def search(self, query: str, brand_id: str | None = None) -> dict:
        return self.pipeline.search(query, brand_id=brand_id)

    def image_file(self, asset_id: str) -> Path | None:
        relative = self.image_paths.get(asset_id)
        if relative is None:
            return None
        path = (self.image_root / relative).resolve()
        return path if path.is_relative_to(self.image_root.resolve()) and path.exists() else None
