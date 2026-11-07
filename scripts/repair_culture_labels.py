"""Version label repairs and re-embed only changed metadata; retain image vectors and raw files."""

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import _bootstrap  # noqa: F401
import numpy as np

from visualseek_embedding.metadata import metadata_text
from visualseek_embedding.siglip2 import Siglip2Encoder
from visualseek_preprocessing.labels import LABEL_VERSION, repair_labels
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json


def write_rows(path: Path, rows: list[dict]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    temporary.replace(path)


def repair(
    source: Path,
    output: Path,
    *,
    cache_dir="data/models/huggingface",
    batch_size=32,
    apply_manifest: Path | None = None,
    encoder_factory=Siglip2Encoder,
) -> dict:
    if source.resolve() == output.resolve():
        raise ValueError("Use a new versioned artifact directory")
    index = ArtifactIndex(source)
    if index.metadata_vectors is None:
        raise ValueError("Repair requires an existing full image + metadata artifact")
    rows = [repair_labels(row) for row in index.assets]
    for row in rows:
        row.pop('questions', None)
        row.pop('annotation_metadata', None)
    changed = [
        i
        for i, (old, new) in enumerate(zip(index.assets, rows, strict=True))
        if metadata_text(old) != metadata_text(new)
    ]
    fingerprint = hashlib.sha256(
        json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    run = {
        "source_files": index.manifest["files"],
        "label_version": LABEL_VERSION,
        "corrected_rows_sha256": fingerprint,
        "batch_size": batch_size,
    }
    output.mkdir(parents=True, exist_ok=True)
    run_file = output / "label-repair-run.json"
    if run_file.exists():
        if json.loads(run_file.read_text()) != run:
            raise ValueError(
                "Repair checkpoint belongs to a different corpus/rule/batch configuration"
            )
    elif any(output.iterdir()):
        raise ValueError("Output is not empty and has no repair run identity")
    write_json(run_file, run)
    shutil.copyfile(source / "vectors.npy", output / "vectors.npy")
    values = np.array(index.metadata_vectors, dtype=np.float32)
    checkpoints = output / "metadata-repair-shards"
    checkpoints.mkdir(exist_ok=True)
    encoder = None
    started = time.perf_counter()
    last_log = 0
    for start in range(0, len(changed), batch_size):
        positions = changed[start : start + batch_size]
        ids = [rows[i]["asset_id"] for i in positions]
        shard = checkpoints / f"{start:06d}.npz"
        if shard.exists():
            with np.load(shard, allow_pickle=False) as saved:
                if saved["ids"].tolist() != ids:
                    raise ValueError("Repair checkpoint asset IDs differ")
                batch = saved["vectors"].copy()
        else:
            if encoder is None:
                encoder = encoder_factory(
                    device="cpu",
                    revision=index.manifest["model_revision"],
                    cache_dir=cache_dir,
                    cpu_threads=8,
                    progress=print,
                )
            batch = encoder.encode_documents(
                [metadata_text(rows[i]) for i in positions], batch_size=batch_size
            ).astype(np.float32)
            temporary = shard.with_suffix(".tmp.npz")
            np.savez(temporary, ids=np.asarray(ids, dtype="U64"), vectors=batch)
            temporary.replace(shard)
        if (
            batch.shape != (len(positions), index.dimension)
            or not np.isfinite(batch).all()
            or not np.allclose(np.linalg.norm(batch, axis=1), 1, atol=2e-3)
        ):
            raise ValueError("Invalid repaired metadata vectors")
        values[positions] = batch
        if time.perf_counter() - last_log >= 5 or start + batch_size >= len(changed):
            done = min(start + batch_size, len(changed))
            print(
                f"Metadata repair: {done}/{len(changed)}; "
                f"elapsed={time.perf_counter() - started:.1f}s",
                flush=True,
            )
            last_log = time.perf_counter()
    write_rows(output / "assets.jsonl", rows)
    np.save(output / "metadata_vectors.tmp.npy", values, allow_pickle=False)
    (output / "metadata_vectors.tmp.npy").replace(output / "metadata_vectors.npy")
    manifest = dict(index.manifest)
    manifest["files"] = {
        name: sha256_file(output / name)
        for name in ("vectors.npy", "assets.jsonl", "metadata_vectors.npy")
    }
    manifest["label_revision"] = LABEL_VERSION
    manifest["inventory_sha256"] = fingerprint
    write_json(output / "manifest.json", manifest)
    checked = ArtifactIndex(output)
    if manifest["files"]["vectors.npy"] != index.manifest["files"]["vectors.npy"]:
        raise ValueError("Image vectors unexpectedly changed")
    report = {
        "label_version": LABEL_VERSION,
        "source": str(source),
        "output": str(output),
        "assets": len(rows),
        "titles_changed": sum(
            a["keyword"] != b["keyword"] for a, b in zip(index.assets, rows, strict=True)
        ),
        "metadata_reembedded": len(changed),
        "searchable": checked.searchable_count,
        "excluded": len(rows) - checked.searchable_count,
        "image_vectors_unchanged": True,
        "repairs": [
            {
                "asset_id": a["asset_id"],
                "image_path": a["image_path"],
                "before": a["keyword"],
                "after": b["keyword"],
                "search_excluded": bool(b.get("search_excluded")),
                "review": b.get("label_review"),
            }
            for a, b in zip(index.assets, rows, strict=True)
            if a["keyword"] != b["keyword"] or a != b
        ],
    }
    write_json(output / "label-repair-report.json", report)
    if apply_manifest is not None:
        original = [
            json.loads(line)
            for line in apply_manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if {r["asset_id"] for r in original} != {r["asset_id"] for r in rows}:
            raise ValueError("Local manifest IDs differ from the repaired artifact")
        updated = [repair_labels(row) for row in original]
        if updated != original:
            digest = sha256_file(apply_manifest)
            backup = apply_manifest.with_name(f"assets.before-label-repair-{digest[:12]}.jsonl")
            if not backup.exists():
                shutil.copyfile(apply_manifest, backup)
            write_rows(apply_manifest, updated)
            print("Local manifest repaired; original backup:", backup, flush=True)
    with ZipFile(output / "culture-embeddings.tmp.zip", "w", ZIP_DEFLATED) as archive:
        for name in (
            "vectors.npy",
            "metadata_vectors.npy",
            "assets.jsonl",
            "manifest.json",
            "label-repair-report.json",
        ):
            archive.write(output / name, name)
    (output / "culture-embeddings.tmp.zip").replace(output / "culture-embeddings.zip")
    print(json.dumps({k: v for k, v in report.items() if k != "repairs"}, indent=2), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", default="data/models/huggingface")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--apply-manifest", type=Path)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch-size must be positive")
    repair(**vars(args))


if __name__ == "__main__":
    main()
