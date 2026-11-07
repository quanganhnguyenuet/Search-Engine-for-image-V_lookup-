import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from import_embeddings import import_archive
from kaggle_embed import export_embeddings
from kaggle_embed_multi_gpu import export_multi_gpu, run_workers
from PIL import Image
from prepare_kaggle_bundle import build_bundle
from prepare_kaggle_selection import prepare_selection

from visualseek_retrieval.artifact import ArtifactIndex


class EncoderDouble:
    dimension = 3
    device = "cpu"
    calls = 0

    def __init__(self, **kwargs):
        pass

    def encode_images(self, images):
        type(self).calls += 1
        values = np.array([image.getpixel((0, 0)) for image in images], dtype=np.float32)
        return values / np.linalg.norm(values, axis=1, keepdims=True)

    def encode_texts(self, texts):
        return np.array([[1, 0, 0] for _ in texts], dtype=np.float32)

    def encode_documents(self, texts, batch_size=32):
        assert all("VQA secret answer" not in text for text in texts)
        return np.array([[0, 1, 0] for _ in texts], dtype=np.float32)


@pytest.fixture
def multi_args(tmp_path):
    root = tmp_path / "images-root"
    rows = []
    for i, color in enumerate(((200, 0, 0), (0, 150, 0), (0, 0, 200), (100, 100, 0),
                               (0, 100, 150))):
        file = root / f"images/am_thuc/item/{i}.png"
        file.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (3, 3), color).save(file)
        rows.append({
            "asset_id": hashlib.sha256(file.read_bytes()).hexdigest(),
            "image_path": str(file.relative_to(root)), "category": "am_thuc",
            "keyword": f"Image {i}", "description": "Visual description",
            "questions": [{"answer": "VQA secret answer"}],
        })
    manifest = tmp_path / "assets.jsonl"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return argparse.Namespace(
        input_root=root, manifest=manifest, limit_per_category=0,
        output=tmp_path / "output", cache_dir=tmp_path / "cache", batch_size=2,
        gpu_ids=[0, 1], with_metadata=True,
    )


def fake_launcher(jobs):
    for job in jobs:
        command = job["command"]

        def option(name, command=command):
            return command[command.index(name) + 1]

        export_embeddings(argparse.Namespace(
            input_root=Path(option("--input-root")), manifest=Path(option("--manifest")),
            output=Path(option("--output")), cache_dir=Path(option("--cache-dir")),
            limit_per_category=int(option("--limit-per-category")), device="cpu",
            batch_size=int(option("--batch-size")), query=["A test image"], zip=False,
            with_metadata="--with-metadata" in command,
        ), encoder_factory=EncoderDouble)


def test_multigpu_roundtrip_preserves_order_and_resumes(multi_args, tmp_path):
    EncoderDouble.calls = 0
    report = export_multi_gpu(multi_args, launcher=fake_launcher)
    assert report["indexed"] == 5
    index = ArtifactIndex(multi_args.output)
    original_rows = [json.loads(line) for line in multi_args.manifest.read_text().splitlines()]
    assert [row["asset_id"] for row in index.assets] == [r["asset_id"] for r in original_rows]
    expected = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1],
                         [2**-0.5, 2**-0.5, 0], [0, 2 / 13**0.5, 3 / 13**0.5]])
    assert np.allclose(index.vectors, expected)
    assert np.array_equal(index.metadata_vectors, np.tile([0, 1, 0], (5, 1)))
    assert all("questions" not in row for row in index.assets)
    calls = EncoderDouble.calls
    export_multi_gpu(multi_args, launcher=fake_launcher)
    assert EncoderDouble.calls == calls
    installed = tmp_path / "installed"
    assert import_archive(multi_args.output / "culture-embeddings.zip", installed) == 5
    imported = ArtifactIndex(installed)
    assert np.array_equal(imported.vectors, index.vectors)
    assert np.array_equal(imported.metadata_vectors, index.metadata_vectors)


def test_multigpu_rejects_changed_selection(multi_args):
    export_multi_gpu(multi_args, launcher=fake_launcher)
    rows = [json.loads(line) for line in multi_args.manifest.read_text().splitlines()]
    rows[0]["keyword"] = "Changed metadata"
    multi_args.manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="another corpus"):
        export_multi_gpu(multi_args, launcher=fake_launcher)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "incomplete", "checksum"])
def test_bad_worker_artifact_never_publishes_zip(multi_args, defect):
    def launcher(jobs):
        fake_launcher(jobs)
        directory = multi_args.output / "workers/rank-1/artifact"
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        if defect in {"missing", "duplicate"}:
            # Produce a valid but wrong artifact, to test set validation after checksums.
            source = multi_args.output / "workers/rank-0/artifact"
            if defect == "duplicate":
                for name in ("manifest.json", "vectors.npy", "metadata_vectors.npy",
                             "assets.jsonl"):
                    shutil.copyfile(source / name, directory / name)
            else:
                assets_path = directory / "assets.jsonl"
                lines = assets_path.read_text().splitlines(keepends=True)
                assets_path.write_text("".join(lines[:1]))
                for name in ("vectors.npy", "metadata_vectors.npy"):
                    values = np.load(directory / name, allow_pickle=False)
                    np.save(directory / name, values[:1])
                from visualseek_retrieval.artifact import sha256_file

                manifest.update(count=1, requested=1)
                manifest["files"] = {name: sha256_file(directory / name)
                                     for name in manifest["files"]}
                manifest_path.write_text(json.dumps(manifest))
        elif defect == "incomplete":
            manifest["complete"] = False
            manifest_path.write_text(json.dumps(manifest))
        else:
            with (directory / "vectors.npy").open("ab") as stream:
                stream.write(b"corruption")

    with pytest.raises(ValueError):
        export_multi_gpu(multi_args, launcher=launcher)
    assert not (multi_args.output / "culture-embeddings.zip").exists()


def test_both_workers_started_before_wait_and_gpu_isolation(tmp_path, monkeypatch):
    environments = []

    class Process:
        pid = 123

        def __init__(self, command, **kwargs):
            environments.append(kwargs["env"])

        def poll(self):
            assert len(environments) == 2  # Waiting for rank 0 must not delay rank 1 launch.
            return 0

        def wait(self, **kwargs):
            return 0

    monkeypatch.setattr("kaggle_embed_multi_gpu.subprocess.Popen", Process)
    monkeypatch.setenv("HF_TOKEN", "example-inherited-token")
    jobs = [{"gpu": gpu, "command": ["worker"], "log": tmp_path / f"{gpu}.log"}
            for gpu in (0, 1)]
    run_workers(jobs)
    assert [env["CUDA_VISIBLE_DEVICES"] for env in environments] == ["0", "1"]
    assert all(env["TOKENIZERS_PARALLELISM"] == "false" for env in environments)


def test_notebook_code_valid_and_targets_user_dataset():
    root = Path(__file__).resolve().parents[2]
    notebook = json.loads((root / "notebooks/visualseek-kaggle-t4x2-huggingface.ipynb").read_text())
    sources = []
    for position, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] == "code":
            source = "".join(cell["source"])
            compile(source, f"cell-{position}", "exec")
            assert not cell["outputs"] and cell["execution_count"] is None
            sources.append(source)
    assert notebook["nbformat"] == 4
    assert "HF_REPO_ID = 'Quanganh6905/Embedding_for_searchengine'" in "\n".join(sources)


def test_notebook_bundle_discovery_through_upload_receipt(multi_args, tmp_path, monkeypatch):
    """Run notebook control flow offline, with CPU encoder doubles and a fake Hub."""
    root = Path(__file__).resolve().parents[2]
    notebook = json.loads((root / "notebooks/visualseek-kaggle-t4x2-huggingface.ipynb").read_text())
    cells = ["".join(cell["source"]) for cell in notebook["cells"]
             if cell["cell_type"] == "code"]
    inputs, working = tmp_path / "input", tmp_path / "working"
    inputs.mkdir()
    build_bundle(inputs / "culture-kaggle-code.zip")
    prepare_selection(multi_args.manifest, multi_args.input_root, inputs / "culture-selection.zip")
    shutil.copytree(multi_args.input_root, inputs / "corpus")
    config = cells[0].replace("'/kaggle/input'", repr(str(inputs)))
    config = config.replace("'/kaggle/working'", repr(str(working)))
    namespace = {}
    # Setup launches pip and --help; covered separately by the isolated bundle test.
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)
    exec(config, namespace)
    original_path = __import__("sys").path.copy()
    try:
        exec(cells[1], namespace)  # ZIP discovery, safe extraction, import preflight.
        # Skip hardware/secret cell: there is no GPU or real token in this offline test.
        exec(cells[3], namespace)  # Selection validation.
        exec(cells[4], namespace)  # Full image-path resolution.
        assert namespace["image_root"] == inputs / "corpus"
        multi_args.input_root = namespace["image_root"]
        multi_args.output = namespace["OUTPUT"]
        export_multi_gpu(multi_args, launcher=fake_launcher)
        exec(cells[6], namespace)  # Full artifact validation.

        class HubDouble:
            def create_repo(self, **kwargs):
                assert kwargs["repo_id"] == "Quanganh6905/Embedding_for_searchengine"
                assert kwargs["repo_type"] == "dataset" and kwargs["private"] is True

            def upload_folder(self, **kwargs):
                from types import SimpleNamespace

                assert kwargs["repo_type"] == "dataset"
                assert "culture-embeddings.zip" in kwargs["allow_patterns"]
                assert "vectors.npy" not in kwargs["allow_patterns"]  # Already in ZIP.
                assert not any("worker" in name for name in kwargs["allow_patterns"])
                self.remote_files = [f"{kwargs['path_in_repo']}/{name}"
                                     for name in kwargs["allow_patterns"]]
                return SimpleNamespace(oid="a" * 40)

            def list_repo_files(self, **kwargs):
                assert kwargs["revision"] == "a" * 40
                return self.remote_files

        namespace["api"] = HubDouble()
        exec(cells[7], namespace)  # Upload allowlist, pinned commit, local receipt.
        receipt = json.loads(namespace["receipt_path"].read_text())
        assert receipt["revision"] == "a" * 40
        assert receipt["count"] == 5
        assert receipt["sha256"] == hashlib.sha256(namespace["archive"].read_bytes()).hexdigest()
    finally:
        __import__("sys").path[:] = original_path
