import hashlib
import io
import json
import shutil
from types import SimpleNamespace

import numpy as np
import pytest
from evaluate_image_holdout import rescore
from PIL import Image

from visualseek_embedding.siglip2 import MODEL_ID, MODEL_REVISION, PREPROCESS_VERSION
from visualseek_evaluation.image_holdout import (
    bound_attribute,
    build_holdout,
    load_holdout,
    select_queries,
)
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json


def source(tmp_path):
    directory = tmp_path / "source"
    root = tmp_path / "images"
    directory.mkdir()
    root.mkdir()
    rows = []
    for i in range(6):
        content = io.BytesIO()
        Image.new("RGB", (3, 3), (100 + i, 10, 10)).save(content, format="PNG")
        asset_id = hashlib.sha256(content.getvalue()).hexdigest()
        path = root / f"{i}.png"
        path.write_bytes(content.getvalue())
        rows.append({"asset_id": asset_id, "image_path": path.name, "category": "trang_phuc",
                     "keyword": "Áo dài", "tags": ["áo dài đỏ" if i < 3 else "áo dài trắng"],
                     "split": "test"})
    np.save(directory / "vectors.npy", np.asarray([[1, 0]] * 3 + [[0, 1]] * 3, dtype=np.float32))
    np.save(directory / "metadata_vectors.npy", np.asarray(
        [[0, 1]] * 3 + [[1, 0]] * 3, dtype=np.float32))
    (directory / "assets.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    write_json(directory / "manifest.json", {
        "schema_version": 2, "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
        "preprocess_version": PREPROCESS_VERSION, "dimension": 2, "count": len(rows),
        "normalization": "l2", "metadata_version": "title-tags-description-chunk-mean-l2-v2",
        "files": {name: sha256_file(directory / name) for name in (
            "vectors.npy", "metadata_vectors.npy", "assets.jsonl")},
    })
    return ArtifactIndex(directory), root


def test_holdout_removes_all_query_vectors_and_metadata_without_changing_the_original(tmp_path):
    index, root = source(tmp_path)
    original = sha256_file(index.directory / "assets.jsonl")
    benchmark = tmp_path / "holdout"
    manifest = build_holdout(index, root, benchmark, count=2)
    gallery = ArtifactIndex(benchmark / "gallery")
    _, queries = load_holdout(benchmark, gallery)
    query_ids = {q["asset_id"] for q in queries}
    assert manifest["query_count"] == 2 and manifest["gallery_count"] == 4
    assert gallery.vectors.shape == gallery.metadata_vectors.shape == (4, 2)
    assert query_ids.isdisjoint(gallery.positions)
    assert len(index.assets) == 6 and sha256_file(index.directory / "assets.jsonl") == original
    assert all((benchmark / q["image_path"]).is_file() for q in queries)
    for query in queries:
        assert set(query["relevant_asset_ids"]) < set(query["object_relevant_asset_ids"])
        assert query_ids.isdisjoint(query["object_relevant_asset_ids"])
        assert query["attributes"]["color"] in ("đỏ", "trắng")


def test_heldout_selection_is_seeded_and_has_distinct_semantic_targets(tmp_path):
    index, _ = source(tmp_path)
    selected, _ = select_queries(index.assets, count=2, seed=42)
    repeated, _ = select_queries(index.assets, count=2, seed=42)
    assert selected == repeated
    assert len({r["asset_id"] for r in selected}) == 2
    assert len({r["profile"]["name"] for r in selected}) == 2
    with pytest.raises(ValueError):
        select_queries(index.assets, count=50)


def test_query_photos_are_verified_by_content_hash(tmp_path):
    index, root = source(tmp_path)
    directory = tmp_path / "heldout"
    build_holdout(index, root, directory, count=2)
    gallery = ArtifactIndex(directory / "gallery")
    _, queries = load_holdout(directory, gallery)
    (directory / queries[0]["image_path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        load_holdout(directory, gallery)


def test_gallery_positives_cannot_reference_any_other_query(tmp_path):
    index, root = source(tmp_path)
    directory = tmp_path / "heldout"
    build_holdout(index, root, directory, count=2)
    gallery = ArtifactIndex(directory / "gallery")
    _, queries = load_holdout(directory, gallery)
    queries[0]["relevant_asset_ids"].append(queries[1]["asset_id"])
    path = directory / "queries.jsonl"
    path.write_text("".join(json.dumps(q) + "\n" for q in queries))
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["queries_sha256"] = sha256_file(path)
    write_json(directory / "manifest.json", manifest)
    with pytest.raises(ValueError, match="gallery positives"):
        load_holdout(directory, gallery)


def test_color_binds_to_the_garment_not_another_object_in_the_tag():
    assert bound_attribute("áo dài gấm đỏ", "áo dài", "đỏ")
    assert bound_attribute("áo dài Lemur đỏ đun", "áo dài", "đỏ")
    assert bound_attribute("áo dài màu tím", "áo dài", "tím")
    assert not bound_attribute("áo dài đen, khăn đỏ", "áo dài", "đỏ")
    assert not bound_attribute("áo dài đen và khăn đỏ", "áo dài", "đỏ")
    assert not bound_attribute("không có áo dài đỏ", "áo dài", "đỏ")
    assert not bound_attribute("áo dài Việt Nam", "áo dài", "nam")


def test_rescore_preserves_actual_measurements_and_rejects_changed_query_inputs(tmp_path):
    index, root = source(tmp_path)
    original = tmp_path / "original"
    build_holdout(index, root, original, count=2)
    manifest, queries = load_holdout(original, ArtifactIndex(original / "gallery"))
    samples = [{"query_id": q["query_id"], "asset_id": q["asset_id"], "mode": "image",
                "ok": True, "elapsed_ms": 123.5, "timings": {"embedding_ms": 120},
                "route": "semantic", "clarification_required": False,
                "ranking": q["relevant_asset_ids"][:1]} for q in queries]
    path = tmp_path / "measured.json"
    write_json(path, {"protocol": manifest, "reports": {"image": {}}, "samples": samples,
                      "benchmark_manifest_sha256": sha256_file(original / "manifest.json"),
                      "created_at": "original-measurement-time", "complete": True, "errors": 0})
    revised = tmp_path / "revised"
    shutil.copytree(original, revised)
    for q in queries:
        q["relevant_asset_ids"] = q["relevant_asset_ids"][:1]
    def save_queries():
        query_path = revised / "queries.jsonl"
        query_path.write_text("".join(json.dumps(q) + "\n" for q in queries))
        manifest["queries_sha256"] = sha256_file(query_path)
        write_json(revised / "manifest.json", manifest)
    save_queries()
    args = SimpleNamespace(benchmark=revised, source_benchmark=original, source_report=path)
    result = rescore(args)
    assert result["created_at"] == "original-measurement-time"
    assert result["reports"]["image"]["target_metrics"]["Recall@10"] == 1
    assert result["reports"]["image"]["http_latency_ms"]["p95"] == 123.5
    assert result["samples"][0]["ranking"] == samples[0]["ranking"]
    assert result["samples"][0]["timings"] == samples[0]["timings"]
    queries[0]["text_query"] = "different request"
    save_queries()
    with pytest.raises(ValueError, match="Changed request inputs"):
        rescore(args)
