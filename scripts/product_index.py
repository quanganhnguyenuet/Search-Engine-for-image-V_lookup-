"""Embed mirrored product images with SigLIP2 and upsert them into a Qdrant collection."""

import argparse
import json
import os
import uuid
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from qdrant_client import QdrantClient, models

from visualseek_embedding.siglip2 import Siglip2Encoder, load_image

PAYLOAD_FIELDS = ("image_id", "entity_id", "entity_name", "url", "source_url", "source_title",
                  "domain", "path", "width", "height", "sha256")


def load_env_file(path: Path) -> None:
    """Minimal .env loader so the script works outside docker compose."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def make_client() -> QdrantClient:
    load_env_file(Path(".env"))
    return QdrantClient(url=os.environ["QDRANT_URL"],
                        api_key=os.environ.get("QDRANT_API_KEY") or None, timeout=60)


def point_id(image_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, image_id))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/product-images"))
    parser.add_argument("--collection", default="product_images_poc")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--recreate", action="store_true")
    args = parser.parse_args()

    records = [json.loads(line) for line in
               (args.data / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    records = [r for r in records if r["ok"]]
    vectors_path = args.data / "vectors.npy"
    ids_path = args.data / "vector_ids.json"
    cached = (json.loads(ids_path.read_text()) if ids_path.exists() else [])
    if vectors_path.exists() and cached == [r["image_id"] for r in records]:
        vectors = np.load(vectors_path)
        print(f"[embed] reusing {len(vectors)} cached vectors")
    else:
        encoder = Siglip2Encoder(device="auto", progress=print)
        chunks = []
        for start in range(0, len(records), args.batch_size):
            batch = records[start:start + args.batch_size]
            images = [load_image(args.data / r["path"]) for r in batch]
            chunks.append(encoder.encode_images(images))
            print(f"[embed] {min(start + args.batch_size, len(records))}/{len(records)}", flush=True)
        vectors = np.vstack(chunks)
        np.save(vectors_path, vectors)
        ids_path.write_text(json.dumps([r["image_id"] for r in records]), encoding="utf-8")

    client = make_client()
    exists = client.collection_exists(args.collection)
    if exists and args.recreate:
        client.delete_collection(args.collection)
        exists = False
    if not exists:
        client.create_collection(
            args.collection,
            vectors_config={"image": models.VectorParams(
                size=vectors.shape[1], distance=models.Distance.COSINE)},
        )
        for field in ("entity_id", "image_id"):
            client.create_payload_index(args.collection, field, models.PayloadSchemaType.KEYWORD)
    points = [
        models.PointStruct(
            id=point_id(r["image_id"]),
            vector={"image": vector.tolist()},
            payload={key: r[key] for key in PAYLOAD_FIELDS if key in r},
        )
        for r, vector in zip(records, vectors, strict=True)
    ]
    for start in range(0, len(points), 128):
        client.upsert(args.collection, points=points[start:start + 128], wait=True)
    print(json.dumps({"collection": args.collection, "points": client.count(
        args.collection, exact=True).count, "dimension": int(vectors.shape[1])}))


if __name__ == "__main__":
    main()
