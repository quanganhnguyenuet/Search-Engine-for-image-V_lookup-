"""Export real SigLIP2 image embeddings on Kaggle (or CPU for a small smoke test)."""

import argparse
import hashlib
import json
import time
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import _bootstrap  # noqa: F401
import numpy as np
from enrich_embeddings import add_metadata

from visualseek_embedding.siglip2 import (
    MODEL_ID,
    MODEL_REVISION,
    PREPROCESS_VERSION,
    Siglip2Encoder,
    load_image,
)
from visualseek_preprocessing.culture_dataset import (
    CATEGORIES,
    image_path,
    make_asset,
    reconcile_records,
    select_records,
)
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def corpus_rows(root: Path, manifest: Path | None, limit: int) -> tuple[Path, list[dict]]:
    log(f"[validate] Reading corpus: {manifest or root}")
    if manifest:
        records = [
            json.loads(line)
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        records = select_records(records, limit or None)
    else:
        candidates = list(root.rglob("vietnamese_vqa_dataset.json"))
        if len(candidates) != 1:
            raise ValueError("Choose --input-root containing exactly one source annotation JSON")
        annotation = candidates[0]
        root = (
            annotation.parent.parent
            if annotation.parent.name == "annotations"
            else annotation.parent
        )
        originals = json.loads(annotation.read_text(encoding="utf-8"))
        paths = [
            str(file.relative_to(root))
            for category in CATEGORIES
            for file in (root / "images" / category).rglob("*")
            if file.is_file()
        ]
        records, _ = reconcile_records(originals, paths)
        records = select_records(records, limit or None)
    root = root.resolve()
    unique: dict[str, dict] = {}
    started = last_log = time.perf_counter()
    log(f"[validate] Checking paths and SHA-256 for {len(records)} images (CPU/disk)")
    for position, record in enumerate(records, 1):
        name = image_path(record)
        file = (root / name).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            raise ValueError(f"Missing/unsafe corpus file: {name}")
        row = dict(record) if "asset_id" in record else make_asset(record, root)
        if row["asset_id"] != sha256_file(file):
            raise ValueError(f"Asset checksum does not match the local image: {name}")
        # Do not export VQA questions/answers; these are not retrieval inputs or RAG evidence.
        row.pop("questions", None)
        row.pop("annotation_metadata", None)
        unique.setdefault(row["asset_id"], row)
        now = time.perf_counter()
        if position == 1 or position == len(records) or now - last_log >= 5:
            elapsed = max(now - started, 1e-9)
            rate = position / elapsed
            log(
                f"[validate] {position}/{len(records)} ({position / len(records):.1%}); "
                f"{rate:.1f} images/s; ETA={(len(records) - position) / rate:.0f}s"
            )
            last_log = now
    if not unique:
        raise ValueError("No corpus images found")
    return root, list(unique.values())


def export_embeddings(args: argparse.Namespace, encoder_factory=Siglip2Encoder) -> dict:
    log(f"[start] Requested device={args.device}; batch_size={args.batch_size}")
    root, rows = corpus_rows(args.input_root, args.manifest, args.limit_per_category)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    inventory = {row["asset_id"]: row for row in rows}
    fingerprint = hashlib.sha256(
        json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    run = {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "preprocess_version": PREPROCESS_VERSION,
        "inventory_sha256": fingerprint,
    }
    run_file = output / "run.json"
    if run_file.exists() and json.loads(run_file.read_text()) != run:
        raise ValueError("Output contains another corpus/model run; use a new output directory")
    write_json(run_file, run)
    shards = output / "shards"
    shards.mkdir(exist_ok=True)
    completed = set()
    vectors_by_shard = []
    ids_order = []
    saved_shards = sorted(shards.glob("*.npz"))
    log(f"[resume] Reading {len(saved_shards)} checkpoint shards")
    for shard in saved_shards:
        with np.load(shard, allow_pickle=False) as saved:
            ids = saved["ids"].tolist()
            vectors = saved["vectors"]
            if len(ids) != len(vectors) or not set(ids).issubset(inventory):
                raise ValueError(f"Invalid checkpoint: {shard.name}")
            if completed.intersection(ids):
                raise ValueError("Duplicate IDs in checkpoints")
            completed.update(ids)
            ids_order.extend(ids)
            vectors_by_shard.append(vectors.copy())
    pending = [row for row in rows if row["asset_id"] not in completed]
    log(
        f"[resume] Selected {len(rows)} unique images; "
        f"cached={len(completed)}, pending={len(pending)}"
    )
    encoder = encoder_factory(device=args.device, cache_dir=str(args.cache_dir), progress=log)
    log(f"[model] Ready; device={encoder.device}")
    failures = []
    started = time.perf_counter()
    total_batches = (len(pending) + args.batch_size - 1) // args.batch_size
    log(f"[embed] Starting {total_batches} batches")
    for start in range(0, len(pending), args.batch_size):
        batch = start // args.batch_size + 1
        log(f"[embed] Batch {batch}/{total_batches}: decoding images (CPU/disk)")
        images, valid = [], []
        for row in pending[start : start + args.batch_size]:
            try:
                # Offline corpus includes legitimate 44 MP photos; online uploads stay at 36 MP.
                images.append(load_image(root / row["image_path"], max_pixels=72_000_000))
                valid.append(row)
            except (OSError, ValueError, Warning) as exc:
                failures.append({"asset_id": row["asset_id"], "error": str(exc)})
                log(f"[warning] Cannot decode {row['image_path']}: {exc}")
        if not valid:
            continue
        try:
            log(f"[embed] Batch {batch}/{total_batches}: encoding on {encoder.device}")
            vectors = encoder.encode_images(images).astype(np.float32)
        finally:
            for image in images:
                image.close()
        ids = [row["asset_id"] for row in valid]
        temporary = shards / "pending.tmp.npz"
        np.savez(temporary, ids=np.asarray(ids, dtype="U64"), vectors=vectors)
        temporary.replace(shards / f"{len(vectors_by_shard):06d}.npz")
        vectors_by_shard.append(vectors)
        ids_order.extend(ids)
        processed = min(start + args.batch_size, len(pending))
        rate = processed / max(time.perf_counter() - started, 1e-9)
        log(
            f"[embed] Embedded {len(ids_order)}/{len(rows)}; failures={len(failures)}; "
            f"{rate:.1f} attempted images/s; ETA={(len(pending) - processed) / rate:.0f}s"
        )
    elapsed = time.perf_counter() - started
    if not ids_order:
        raise ValueError("No valid images were embedded")
    log("[export] Writing vectors, metadata and checksums")
    vectors = np.concatenate(vectors_by_shard).astype(np.float32)
    temporary_vectors = output / "vectors.tmp.npy"
    np.save(temporary_vectors, vectors, allow_pickle=False)
    temporary_vectors.replace(output / "vectors.npy")
    temporary_assets = output / "assets.jsonl.tmp"
    with temporary_assets.open("w", encoding="utf-8") as stream:
        for id_ in ids_order:
            stream.write(json.dumps(inventory[id_], ensure_ascii=False) + "\n")
    temporary_assets.replace(output / "assets.jsonl")
    write_json(
        output / "manifest.json",
        {
            "schema_version": 1,
            **run,
            "dimension": int(vectors.shape[1]),
            "count": len(ids_order),
            "requested": len(rows),
            "normalization": "l2",
            "complete": len(ids_order) == len(rows),
            "scope": {"limit_per_category": args.limit_per_category},
            "device": encoder.device,
            "files": {name: sha256_file(output / name) for name in ("vectors.npy", "assets.jsonl")},
        },
    )
    (output / "failures.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in failures),
        encoding="utf-8",
    )
    log("[smoke] Loading index and checking text-to-image retrieval")
    if getattr(args, "with_metadata", False):
        add_metadata(output, encoder, args.batch_size)
    index = ArtifactIndex(output)
    queries = args.query or [
        "người mặc áo dài",
        "món ăn truyền thống Việt Nam",
        "a traditional Vietnamese temple",
        "ruộng bậc thang",
    ]
    encoder.encode_texts([queries[0]])  # warm-up, excluded from timings
    examples, latencies = [], []
    for query in queries:
        log(f"[smoke] Query: {query}")
        before = time.perf_counter()
        query_vector = encoder.encode_texts([query])[0]
        inference_ms = (time.perf_counter() - before) * 1000
        before = time.perf_counter()
        results = index.search(query_vector, limit=10)
        retrieval_ms = (time.perf_counter() - before) * 1000
        latencies.append(inference_ms + retrieval_ms)
        examples.append(
            {
                "query": query,
                "embedding_ms": inference_ms,
                "retrieval_ms": retrieval_ms,
                "results": [
                    {
                        "asset_id": hit["asset"]["asset_id"],
                        "score": hit["score"],
                        "image_path": hit["asset"]["image_path"],
                        "keyword": hit["asset"].get("keyword", ""),
                    }
                    for hit in results
                ],
            }
        )
    report = {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "device": encoder.device,
        "indexed": len(ids_order),
        "requested": len(rows),
        "dimension": index.dimension,
        "failures": len(failures),
        "new_embedding_seconds": elapsed,
        "query_p90_ms": float(np.percentile(latencies, 90)),
        "query_samples": len(latencies),
        "latency_note": "Smoke timings, not a load-test SLO",
        "relevance_metrics": "Not measured: human relevance judgments required",
        "examples": examples,
    }
    write_json(output / "smoke_report.json", report)
    if args.zip:
        log("[zip] Creating culture-embeddings.zip")
        with ZipFile(output / "culture-embeddings.zip", "w", ZIP_DEFLATED) as archive:
            for name in ("manifest.json", "vectors.npy", "assets.jsonl", "smoke_report.json"):
                archive.write(output / name, name)
            if index.metadata_vectors is not None:
                archive.write(output / "metadata_vectors.npy", "metadata_vectors.npy")
    log(f"[done] Output: {output}; failures={len(failures)}")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "examples"},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=Path("/kaggle/input"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("/kaggle/working/culture-siglip2-smoke")
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("/kaggle/working/model-cache"))
    parser.add_argument(
        "--limit-per-category",
        type=int,
        default=20,
        help="0 means the complete six-category corpus; default is a smoke test",
    )
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--query", action="append")
    parser.add_argument("--zip", action="store_true")
    parser.add_argument("--with-metadata", action="store_true",
                        help="Also embed title/tags/description for contextual hybrid search")
    args = parser.parse_args()
    if args.limit_per_category < 0 or args.batch_size < 1:
        parser.error("limit must be nonnegative and batch-size positive")
    report = export_embeddings(args)
    return int(report["failures"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
