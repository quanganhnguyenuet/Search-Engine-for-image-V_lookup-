"""Add tag/description embeddings to an existing image artifact without re-encoding pixels."""

import argparse
import json
import shutil
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np

from visualseek_embedding.metadata import METADATA_VERSION, metadata_text
from visualseek_embedding.siglip2 import Siglip2Encoder
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json


def add_metadata(directory: Path, encoder, batch_size: int = 32) -> None:
    index = ArtifactIndex(directory)
    if index.metadata_vectors is not None:
        return
    shards = directory / "metadata-shards"
    shards.mkdir(exist_ok=True)
    run = {"assets_sha256": sha256_file(directory / "assets.jsonl"),
           "model_revision": index.manifest["model_revision"],
           "metadata_version": METADATA_VERSION, "batch_size": batch_size}
    run_path = shards / "run.json"
    if run_path.exists() and json.loads(run_path.read_text()) != run:
        raise ValueError("Metadata checkpoints differ; choose a new output directory")
    write_json(run_path, run)
    values = []
    for start in range(0, len(index.assets), batch_size):
        path = shards / f"{start:06d}.npy"
        count = min(batch_size, len(index.assets) - start)
        if path.exists():
            batch = np.load(path, allow_pickle=False)
        else:
            texts = [metadata_text(row) for row in index.assets[start:start + batch_size]]
            batch = encoder.encode_documents(texts, batch_size=batch_size).astype(np.float32)
            tmp = path.with_suffix(".tmp.npy")
            np.save(tmp, batch, allow_pickle=False)
            tmp.replace(path)
        if (batch.shape != (count, index.dimension) or not np.isfinite(batch).all()
            or not np.allclose(np.linalg.norm(batch, axis=1), 1, atol=2e-3)):
            raise ValueError("Invalid metadata checkpoint")
        values.append(batch)
        print(f"Metadata {start + count}/{len(index.assets)}", flush=True)
    tmp = directory / "metadata_vectors.tmp.npy"
    np.save(tmp, np.concatenate(values).astype(np.float32), allow_pickle=False)
    tmp.replace(directory / "metadata_vectors.npy")
    manifest = dict(index.manifest)
    manifest.update(schema_version=2, metadata_version=METADATA_VERSION)
    manifest["files"]["metadata_vectors.npy"] = sha256_file(directory / "metadata_vectors.npy")
    write_json(directory / "manifest.json", manifest)
    ArtifactIndex(directory)


def enrich(source: Path, output: Path, *, device="cpu", cache_dir=None, batch_size=32,
           encoder_factory=Siglip2Encoder) -> None:
    source_index = ArtifactIndex(source)
    if source.resolve() == output.resolve():
        raise ValueError("Choose a new output directory; keep the source artifact for rollback")
    if output.exists():
        target = ArtifactIndex(output)
        if any(target.manifest["files"][key] != source_index.manifest["files"][key]
               for key in ("vectors.npy", "assets.jsonl")):
            raise ValueError("Output belongs to a different source artifact")
        if target.metadata_vectors is not None:
            return
    else:
        output.mkdir(parents=True)
        for name in ("vectors.npy", "assets.jsonl", "manifest.json"):
            shutil.copyfile(source / name, output / name)
        if source_index.metadata_vectors is not None:
            shutil.copyfile(source / "metadata_vectors.npy", output / "metadata_vectors.npy")
    encoder = encoder_factory(device=device, revision=source_index.manifest["model_revision"],
                              cache_dir=cache_dir, progress=print)
    add_metadata(output, encoder, batch_size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu")
    parser.add_argument("--cache-dir", default="data/models/huggingface")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch-size must be positive")
    enrich(**vars(args))


if __name__ == "__main__":
    main()
