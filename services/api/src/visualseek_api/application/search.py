"""Search service: loads artifacts once at startup, then serves the single search path."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import structlog

from visualseek_api.application.graph_view import GraphView
from visualseek_api.core.config import Settings

logger = structlog.get_logger(__name__)
WARMUP_QUERIES = ("chuột không dây", "Shimano", "thuốc giảm đau và tai nghe", "giày hàng hiệu")
# Below the ~30-40 s after which Qdrant Cloud closes an idle connection.
KEEPALIVE_INTERVAL_S = 15


def _keep_connections_warm(client, collection: str) -> None:
    """Touch Qdrant on two connections at once (a search uses two: ranking and its trace), so a
    search after a pause finds both open instead of paying two TCP + TLS handshakes."""
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="qdrant-ping") as pool:
        while True:
            time.sleep(KEEPALIVE_INTERVAL_S)
            try:
                list(pool.map(lambda _: client.collection_exists(collection), range(2)))
            except Exception as error:  # a failed ping must never stop the loop
                logger.warning("qdrant_keepalive_failed", error=str(error))


def load_short_aliases(
    path: Path, graph: dict, review_fingerprint: str | None
) -> tuple[dict | None, str]:
    """The short-alias artifact, unless it was built against another graph or review."""
    if not path.exists():
        return None, "missing"
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        data.get("graph_fingerprint") != graph["fingerprint"]
        or data.get("corpus_fingerprint") != graph["corpus_fingerprint"]
        or data.get("identity_review_fingerprint") != review_fingerprint
    ):
        # A stale file could map a short name to a merged or rejected entity: skip it loudly.
        logger.warning("short_aliases_stale", path=str(path))
        return None, "stale"
    return data, "loaded"


def load_categories(path: Path, graph: dict) -> tuple[dict | None, str]:
    """The B2 category artifact, unless it was built for another corpus."""
    if not path.exists():
        return None, "missing"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("corpus_fingerprint") != graph["corpus_fingerprint"]:
        logger.warning("categories_stale", path=str(path))
        return None, "stale"
    return data, "loaded"


@dataclass
class SearchService:
    pipeline: object
    image_paths: dict[str, str]
    image_root: Path
    info: dict
    graph_view: GraphView
    # asset_id -> {"description": text that was embedded, "page_url": source page}
    details: dict[str, dict]
    # One dedicated thread: CUDA work stays on a warmed thread (per-thread handles were
    # costing 20-30 ms on first use) and GPU access is serialised.
    executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="search")
    )

    @classmethod
    def load(cls, settings: Settings) -> SearchService:
        from visualseek_retrieval.categories import load_descriptions
        from visualseek_retrieval.encoder import TextEncoder
        from visualseek_retrieval.entity_review import EntityReview
        from visualseek_retrieval.fusion import asset_payloads, load_visual
        from visualseek_retrieval.pipeline import SearchPipeline
        from visualseek_retrieval.qdrant_store import connect

        started = time.perf_counter()
        root = Path(settings.search_artifact_dir)
        graph = json.loads((root / "graph.json").read_text(encoding="utf-8"))
        entity_dense = np.load(root / "entity_dense.npy")
        index = json.loads((root / "index_manifest.json").read_text(encoding="utf-8"))
        if index["graph_fingerprint"] != graph["fingerprint"]:
            raise RuntimeError("Qdrant collection was built from a different graph snapshot")
        review_path = root / "identity_review.json"
        if not review_path.exists():
            packaged = Path(__file__).with_name("products-v1-identity-review.json")
            packaged_data = packaged.read_bytes()
            if json.loads(packaged_data)["corpus_fingerprint"] == graph["corpus_fingerprint"]:
                review_path = packaged
        review = (
            EntityReview.load(graph, review_path.read_bytes()) if review_path.exists() else None
        )
        short_aliases, short_status = load_short_aliases(
            root / "short_aliases.json", graph, review.fingerprint if review else None
        )
        tracked_categories = Path(__file__).resolve().parents[5] / "graph/entity_categories.json"
        cat_path = (
            tracked_categories if tracked_categories.exists() else root / "entity_categories.json"
        )
        categories, categories_status = load_categories(cat_path, graph)
        descriptions = (
            load_descriptions(cat_path.with_name("taxonomy-v1.json"), categories["taxonomy_sha256"])
            if categories
            else None
        )
        if categories and descriptions is None:
            logger.warning("category_descriptions_missing_or_stale")
        rows = [
            json.loads(line)
            for line in (root / "doc_texts.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        image_paths = {row["asset_id"]: row["image_path"] for row in rows}
        details = {
            row["asset_id"]: {"description": row.get("doc_text"), "page_url": row.get("page_url")}
            for row in rows
        }
        payloads = asset_payloads(rows, review, categories)
        encoder = TextEncoder(device=settings.search_embed_device)
        image_encoder, fusion = load_visual(
            root, [row["asset_id"] for row in rows], settings.search_embed_device
        )
        client = connect(
            settings.qdrant_url, settings.qdrant_api_key, prefer_grpc=settings.qdrant_prefer_grpc
        )
        transport = "grpc" if settings.qdrant_prefer_grpc else "rest"
        if settings.qdrant_prefer_grpc:
            try:
                client.collection_exists(settings.search_collection)
            except Exception as error:  # port 6334 blocked, TLS interception, ...
                logger.warning("qdrant_grpc_unavailable_using_rest", error=str(error)[:200])
                client = connect(settings.qdrant_url, settings.qdrant_api_key)
                transport = "rest"
        pipeline = SearchPipeline(
            graph,
            entity_dense,
            encoder,
            client,
            settings.search_collection,
            review=review,
            short_aliases=short_aliases["aliases"] if short_aliases else None,
            suppressed_aliases=short_aliases["suppressed"]["aliases"] if short_aliases else None,
            categories=categories,
            category_descriptions=descriptions,
            image_encoder=image_encoder,
            fusion=fusion,
            payloads=payloads,
        )
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="search")
        for query in WARMUP_QUERIES:  # pay TLS, CUDA and tokenizer start-up before serving
            try:
                executor.submit(pipeline.search, query).result()
            except Exception as error:  # a vector-store stall must not keep the API from starting
                logger.warning("warmup_query_failed", query=query, error=str(error)[:200])
        threading.Thread(
            target=_keep_connections_warm,
            args=(client, settings.search_collection),
            name="qdrant-keepalive",
            daemon=True,
        ).start()
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
            "identity_review_fingerprint": pipeline.review.fingerprint,
            "short_aliases_fingerprint": short_aliases["fingerprint"] if short_aliases else None,
            "short_aliases_status": short_status,
            "categories_status": categories_status,
            "category_description_phrases": len(descriptions) if descriptions else 0,
            "categories_taxonomy_sha256": categories["taxonomy_sha256"] if categories else None,
            "corpus_fingerprint": graph["corpus_fingerprint"],
            "doc_texts_sha256": index["doc_texts_sha256"],
            "encoder_device": encoder.device,
            "qdrant_transport": transport,
            "settings": pipeline.settings.__dict__,
        }
        logger.info("search_loaded", seconds=round(time.perf_counter() - started, 1), **info)
        graph_view = GraphView(graph, categories, pipeline.review)
        return cls(pipeline, image_paths, image_root, info, graph_view, details, executor)

    def search(self, query: str, brand_id: str | None = None) -> dict:
        return self.pipeline.search(query, brand_id=brand_id, with_similarity=True)

    def image_file(self, asset_id: str) -> Path | None:
        relative = self.image_paths.get(asset_id)
        if relative is None:
            return None
        path = (self.image_root / relative).resolve()
        return path if path.is_relative_to(self.image_root.resolve()) and path.exists() else None
