"""Shard the local allowlist across isolated GPU processes and merge portable artifacts."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import _bootstrap  # noqa: F401
import numpy as np
from kaggle_embed import corpus_rows, log

from visualseek_embedding.metadata import METADATA_VERSION
from visualseek_embedding.siglip2 import MODEL_ID, MODEL_REVISION, PREPROCESS_VERSION
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json


def write_rows(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    temporary.replace(path)


def run_workers(jobs: list[dict]) -> None:
    """Start all workers before waiting; CUDA_VISIBLE_DEVICES isolates each GPU."""
    processes = []
    with ExitStack() as stack:
        try:
            for job in jobs:
                stream = stack.enter_context(job["log"].open("w", encoding="utf-8"))
                environment = dict(os.environ)
                environment.update(
                    CUDA_VISIBLE_DEVICES=str(job["gpu"]),
                    TOKENIZERS_PARALLELISM="false",
                    OMP_NUM_THREADS="2",
                    MKL_NUM_THREADS="2",
                )
                process = subprocess.Popen(
                    job["command"], env=environment, stdout=stream, stderr=subprocess.STDOUT
                )
                processes.append((job, process))
                log(f"[GPU {job['gpu']}] Started PID {process.pid}; log: {job['log']}")
            last_lines = {}
            while True:
                finished = 0
                for job, process in processes:
                    # Read only the tail, even for long runs.
                    with job["log"].open("rb") as stream:
                        stream.seek(0, 2)
                        stream.seek(max(0, stream.tell() - 4096))
                        lines = stream.read().decode("utf-8", errors="replace").splitlines()
                    line = lines[-1] if lines else "Starting Python/model imports"
                    if last_lines.get(job["gpu"]) != line:
                        log(f"[GPU {job['gpu']}] {line}")
                        last_lines[job["gpu"]] = line
                    code = process.poll()
                    if code is not None:
                        if code:
                            raise RuntimeError(
                                f"GPU {job['gpu']} worker exited with code {code}. "
                                f"Inspect {job['log']}; checkpoints are retained.\n"
                                + "\n".join(lines[-15:])
                            )
                        finished += 1
                if finished == len(processes):
                    break
                time.sleep(5)
        finally:
            # Also stop peers on an exception or Ctrl+C; do not leave GPU jobs running.
            for _, process in processes:
                if process.poll() is None:
                    process.terminate()
            for _, process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def merge_artifacts(
    rows: list[dict], directories: list[Path], output: Path, run: dict
) -> dict:
    """Restore the original asset order for both image and metadata vectors."""
    indexes = [ArtifactIndex(directory) for directory in directories]
    positions = {}
    dimension = indexes[0].dimension
    for rank, index in enumerate(indexes):
        manifest = index.manifest
        if not manifest.get("complete") or manifest["count"] != manifest["requested"]:
            raise ValueError(f"Incomplete worker artifact: {index.directory}")
        if (
            manifest["model_revision"] != MODEL_REVISION
            or index.dimension != dimension
            or (index.metadata_vectors is not None) != run["with_metadata"]
        ):
            raise ValueError("Worker model/dimension/metadata configuration differs")
        for position, asset in enumerate(index.assets):
            asset_id = asset["asset_id"]
            if asset_id in positions:
                raise ValueError("Duplicate asset IDs across workers")
            positions[asset_id] = (rank, position)
    expected_ids = [row["asset_id"] for row in rows]
    if len(expected_ids) != len(set(expected_ids)) or set(positions) != set(expected_ids):
        raise ValueError("Worker assets differ from the selected local corpus")
    vectors = np.empty((len(rows), dimension), dtype=np.float32)
    metadata = np.empty_like(vectors) if run["with_metadata"] else None
    for target, row in enumerate(rows):
        rank, source = positions[row["asset_id"]]
        if indexes[rank].assets[source] != row:
            raise ValueError("Worker metadata differs from the selected local corpus")
        vectors[target] = indexes[rank].vectors[source]
        if metadata is not None:
            metadata[target] = indexes[rank].metadata_vectors[source]
    output.mkdir(parents=True, exist_ok=True)
    for name, values in (("vectors", vectors), ("metadata_vectors", metadata)):
        if values is not None:
            temporary = output / f"{name}.tmp.npy"
            np.save(temporary, values, allow_pickle=False)
            temporary.replace(output / f"{name}.npy")
    write_rows(output / "assets.jsonl", rows)
    names = ["vectors.npy", "assets.jsonl"]
    if metadata is not None:
        names.append("metadata_vectors.npy")
    manifest = {
        "schema_version": 2 if metadata is not None else 1,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "preprocess_version": PREPROCESS_VERSION,
        "inventory_sha256": run["inventory_sha256"],
        "dimension": dimension,
        "count": len(rows),
        "requested": len(rows),
        "normalization": "l2",
        "complete": True,
        "scope": {"limit_per_category": run["limit_per_category"]},
        "device": "cuda-multiprocess",
        "gpu_ids": run["gpu_ids"],
        "files": {name: sha256_file(output / name) for name in names},
    }
    if metadata is not None:
        manifest["metadata_version"] = METADATA_VERSION
    write_json(output / "manifest.json", manifest)
    ArtifactIndex(output)
    reports = [json.loads((directory / "smoke_report.json").read_text())
               for directory in directories]
    report = {
        "indexed": len(rows),
        "requested": len(rows),
        "dimension": dimension,
        "failures": 0,
        "gpu_ids": run["gpu_ids"],
        "workers": reports,
        "latency_note": "Worker smoke queries search worker subsets, not the merged corpus",
        "relevance_metrics": "Not measured: human relevance judgments required",
    }
    write_json(output / "smoke_report.json", report)
    (output / "failures.jsonl").write_text("", encoding="utf-8")
    temporary_zip = output / "culture-embeddings.tmp.zip"
    with ZipFile(temporary_zip, "w", ZIP_DEFLATED) as archive:
        for name in [*names, "manifest.json", "smoke_report.json"]:
            archive.write(output / name, name)
    temporary_zip.replace(output / "culture-embeddings.zip")
    return report


def export_multi_gpu(args: argparse.Namespace, launcher=run_workers) -> dict:
    root, rows = corpus_rows(args.input_root, args.manifest, args.limit_per_category)
    # corpus_rows applies a deterministic sample sort. Restore the allowlist's
    # original order after selection so the final matrices align with that file.
    source_rows = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8")
                   .splitlines() if line.strip()]
    if any("asset_id" not in row for row in source_rows):
        raise ValueError("Multi-GPU export requires the local SHA-256 asset allowlist")
    source_order = {row["asset_id"]: position for position, row in enumerate(source_rows)}
    rows.sort(key=lambda row: source_order[row["asset_id"]])
    if len(rows) < len(args.gpu_ids):
        raise ValueError("The selected corpus must have at least one image per GPU")
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    run = {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "preprocess_version": PREPROCESS_VERSION,
        "metadata_version": METADATA_VERSION if args.with_metadata else None,
        "inventory_sha256": hashlib.sha256(
            json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "gpu_ids": args.gpu_ids,
        "with_metadata": args.with_metadata,
        "limit_per_category": args.limit_per_category,
    }
    run_file = output / "multi-gpu-run.json"
    if run_file.exists():
        if json.loads(run_file.read_text()) != run:
            raise ValueError("Output belongs to another corpus/model/GPU run; use a new output")
    elif any(output.iterdir()):
        raise ValueError("Output is not empty and has no multi-GPU run identity")
    write_json(run_file, run)
    jobs, directories = [], []
    for rank, gpu in enumerate(args.gpu_ids):
        workspace = output / "workers" / f"rank-{rank}"
        workspace.mkdir(parents=True, exist_ok=True)
        manifest = workspace / "selection.jsonl"
        write_rows(manifest, rows[rank::len(args.gpu_ids)])
        directory = workspace / "artifact"
        directories.append(directory)
        command = [
            sys.executable, "-u", str(Path(__file__).with_name("kaggle_embed.py")),
            "--input-root", str(root), "--manifest", str(manifest),
            "--output", str(directory), "--cache-dir", str(args.cache_dir),
            "--limit-per-category", "0", "--batch-size", str(args.batch_size),
            "--device", "cuda",
        ]
        if args.with_metadata:
            command.append("--with-metadata")
        jobs.append({"gpu": gpu, "command": command, "log": workspace / "worker.log"})
    started = time.perf_counter()
    launcher(jobs)
    report = merge_artifacts(rows, directories, output, run)
    log(f"[done] {len(rows)} assets, {len(jobs)} GPU workers, "
        f"{time.perf_counter() - started:.1f}s; {output / 'culture-embeddings.zip'}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path("/kaggle/working/model-cache"))
    parser.add_argument("--gpu-ids", nargs="+", type=int, default=[0, 1])
    parser.add_argument("--batch-size", type=int, default=16, help="Images per GPU batch")
    parser.add_argument("--limit-per-category", type=int, default=0)
    parser.add_argument("--with-metadata", action="store_true")
    args = parser.parse_args()
    if (args.batch_size < 1 or args.limit_per_category < 0 or not args.gpu_ids
        or min(args.gpu_ids) < 0 or len(set(args.gpu_ids)) != len(args.gpu_ids)):
        parser.error("Use positive batch size, nonnegative limit and unique nonnegative GPU IDs")
    import torch

    if not torch.cuda.is_available() or max(args.gpu_ids) >= torch.cuda.device_count():
        parser.error("Requested GPUs unavailable. In Kaggle select Accelerator > GPU T4 x2")
    for gpu in args.gpu_ids:
        log(f"[hardware] GPU {gpu}: {torch.cuda.get_device_name(gpu)}")
    export_multi_gpu(args)


if __name__ == "__main__":
    main()
