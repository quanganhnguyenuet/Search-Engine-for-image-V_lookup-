"""Compose ``doc_text`` per asset and embed it, plus entity cards (MVP stand-in for P3).

doc_text = entity card (name, brand, notes) + page context (title, h1, JSON-LD product) + OCR.
There is no VLM caption in the MVP, so purely visual attributes are only findable when the
page or the OCR mentions them.

Outputs in ``<out>``:
  doc_texts.jsonl     asset_id, entity_ids, brand_ids, doc_text, payload fields
  doc_dense.npy       float32 [assets, 1024], row order = doc_texts.jsonl
  entity_dense.npy    float32 [entities, 1024], row order = graph.json entities
  texts_manifest.json fingerprints and settings
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from visualseek_retrieval.encoder import (  # noqa: E402
    DOC_MAX_TOKENS,
    MODEL_ID,
    MODEL_REVISION,
    TextEncoder,
)
from visualseek_retrieval.text import normalize  # noqa: E402

OCR_MIN_SCORE = 0.8
OCR_MAX_ITEMS = 30
DESCRIPTION_CHARS = 200
PAGE_MAX_LINES = 6


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for value in values:
        value = " ".join(str(value).split())
        key = normalize(value)
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def entity_card(entity: dict, brand_names: dict[str, str]) -> str:
    parts = [f"Sản phẩm: {entity['name']}"]
    if entity["brand_id"]:
        parts.append(f"Hãng: {brand_names[entity['brand_id']]}")
    if entity["notes"]:
        parts.append(f"Ghi chú: {'; '.join(entity['notes'])}")
    return " | ".join(parts)


def page_lines(asset: dict) -> list[str]:
    lines: list[str] = []
    for source in asset["sources"]:
        page = source.get("page") or {}
        lines += [
            source.get("page_title") or "",
            page.get("title") or "",
            page.get("og_title") or "",
        ]
        lines += list(page.get("h1") or [])
        for product in page.get("products") or []:
            if product.get("lists_this_image"):
                lines.append(" ".join(filter(None, [product.get("name"), product.get("model")])))
        if page.get("description"):
            lines.append(page["description"][:DESCRIPTION_CHARS])
    return unique(lines)[:PAGE_MAX_LINES]


def ocr_text(asset: dict) -> list[str]:
    items = [
        item["text"]
        for item in asset.get("ocr") or []
        if item.get("score", 0) >= OCR_MIN_SCORE and len(normalize(item["text"])) >= 2
    ]
    return unique(items)[:OCR_MAX_ITEMS]


def compose(asset: dict, entities: dict[str, dict], brand_names: dict[str, str]) -> str:
    sections = [entity_card(entities[eid], brand_names) for eid in asset["entity_ids"]]
    pages = page_lines(asset)
    if pages:
        sections.append("Trang: " + " | ".join(pages))
    ocr = ocr_text(asset)
    if ocr:
        sections.append("Chữ trên ảnh: " + " ".join(ocr))
    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/processed/products-v1")
    parser.add_argument("--out", type=Path, default=ROOT / "data/processed/products-v1/search")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--texts-only", action="store_true")
    args = parser.parse_args()

    graph = json.loads((args.out / "graph.json").read_text(encoding="utf-8"))
    entities = {entity["id"]: entity for entity in graph["entities"]}
    brand_names = {brand["id"]: brand["name"] for brand in graph["brands"]}
    assets = [
        json.loads(line)
        for line in (args.corpus / "assets.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assets.sort(key=lambda asset: asset["asset_id"])

    rows = []
    for asset in assets:
        first = asset["sources"][0]
        rows.append(
            {
                "asset_id": asset["asset_id"],
                "entity_ids": asset["entity_ids"],
                "brand_ids": sorted(
                    {entities[eid]["brand_id"] for eid in asset["entity_ids"]} - {None}
                ),
                "image_path": asset["image_path"],
                "domain": first.get("domain"),
                "page_url": first.get("page_url"),
                "page_title": first.get("page_title") or "",
                "doc_text": compose(asset, entities, brand_names),
            }
        )
    with (args.out / "doc_texts.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    texts_hash = hashlib.sha256(
        "\n".join(row["asset_id"] + "\t" + row["doc_text"] for row in rows).encode("utf-8")
    ).hexdigest()
    print(f"doc_texts: {len(rows)} rows, sha256 {texts_hash}")
    if args.texts_only:
        return

    cards = [entity_card(entity, brand_names) for entity in graph["entities"]]
    encoder = TextEncoder(device=args.device, max_tokens=DOC_MAX_TOKENS)
    started = time.perf_counter()
    # Sorting by length keeps padding small; vectors are written back in the original order.
    order = np.argsort([len(row["doc_text"]) for row in rows])
    dense = np.empty((len(rows), encoder.model.get_sentence_embedding_dimension()), np.float32)
    dense[order] = encoder.encode([rows[i]["doc_text"] for i in order], batch_size=args.batch_size)
    entity_dense = encoder.encode(cards, batch_size=64)
    elapsed = time.perf_counter() - started
    np.save(args.out / "doc_dense.npy", dense)
    np.save(args.out / "entity_dense.npy", entity_dense)
    manifest = {
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "max_tokens": DOC_MAX_TOKENS,
        "device": encoder.device,
        "assets": len(rows),
        "entities": len(cards),
        "doc_texts_sha256": texts_hash,
        "graph_fingerprint": graph["fingerprint"],
        "corpus_fingerprint": graph["corpus_fingerprint"],
        "ocr_min_score": OCR_MIN_SCORE,
        "embed_seconds": round(elapsed, 1),
    }
    (args.out / "texts_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
