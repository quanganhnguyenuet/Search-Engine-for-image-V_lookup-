import argparse
import hashlib
import json
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pytest
from import_embeddings import import_archive
from kaggle_embed import corpus_rows, export_embeddings
from PIL import Image
from prepare_kaggle_selection import prepare_selection

from visualseek_retrieval.artifact import ArtifactIndex


class EncoderDouble:
    dimension = 3
    device = "cpu"
    image_calls = 0

    def __init__(self, **kwargs):
        pass

    def encode_images(self, images):
        type(self).image_calls += 1
        return np.array(
            [[1, 0, 0] if image.getpixel((0, 0))[0] > 100 else [0, 1, 0] for image in images],
            dtype=np.float32,
        )

    def encode_texts(self, texts):
        return np.array([[1, 0, 0] for _ in texts], dtype=np.float32)


@pytest.fixture
def export_args(tmp_path):
    root = tmp_path / "source"
    rows = []
    for index, category in enumerate(("am_thuc", "kien_truc")):
        image = root / "images" / category / "subject" / "image.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (3, 3), "red" if index == 0 else "blue").save(image)
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        rows.append(
            {
                "asset_id": digest,
                "image_path": str(image.relative_to(root)),
                "category": category,
                "keyword": "Tên ảnh",
                "tags": ["tag"],
                "description": "Original description must survive export",
                "questions": [{"answer": "Do not export this VQA label"}],
            }
        )
    manifest = tmp_path / "assets.jsonl"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return argparse.Namespace(
        input_root=root,
        manifest=manifest,
        limit_per_category=0,
        output=tmp_path / "output",
        device="cpu",
        cache_dir=tmp_path / "cache",
        batch_size=2,
        query=["A test query"],
        zip=True,
    )


def test_export_resume_and_import_roundtrip(export_args, tmp_path):
    EncoderDouble.image_calls = 0
    report = export_embeddings(export_args, encoder_factory=EncoderDouble)
    assert report["indexed"] == 2
    assert report["relevance_metrics"].startswith("Not measured")
    assert EncoderDouble.image_calls == 1
    index = ArtifactIndex(export_args.output)
    assert index.assets[0]["description"] == "Original description must survive export"
    assert "questions" not in index.assets[0]
    export_args.batch_size = 1
    export_embeddings(export_args, encoder_factory=EncoderDouble)
    assert EncoderDouble.image_calls == 1  # resume never encodes completed images again
    imported = tmp_path / "installed"
    assert import_archive(export_args.output / "culture-embeddings.zip", imported) == 2
    assert np.array_equal(ArtifactIndex(imported).vectors, index.vectors)
    with pytest.raises(ValueError, match="already exists"):
        import_archive(export_args.output / "culture-embeddings.zip", imported)


def test_changed_input_does_not_reuse_old_shards(export_args):
    export_embeddings(export_args, encoder_factory=EncoderDouble)
    rows = [json.loads(line) for line in export_args.manifest.read_text().splitlines()]
    rows[0]["keyword"] = "Changed"
    export_args.manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="another corpus"):
        export_embeddings(export_args, encoder_factory=EncoderDouble)


def test_manifest_excludes_extra_images_even_in_the_same_category(export_args):
    extra = export_args.input_root / "images/am_thuc/subject/not-in-local-corpus.png"
    Image.new("RGB", (3, 3), "green").save(extra)
    _, rows = corpus_rows(export_args.input_root, export_args.manifest, 0)
    expected = {
        json.loads(line)["asset_id"] for line in export_args.manifest.read_text().splitlines()
    }
    assert {row["asset_id"] for row in rows} == expected
    assert len(rows) == 2


def test_selection_bundle_freezes_ids_and_verifies_local_content(export_args, tmp_path):
    output = tmp_path / "selection.zip"
    selection = prepare_selection(export_args.manifest, export_args.input_root, output)
    assert selection["count"] == 2
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == {"culture-assets.jsonl", "selection.json"}
        payload = archive.read("culture-assets.jsonl")
        assert hashlib.sha256(payload).hexdigest() == selection["manifest_sha256"]
        rows = [json.loads(line) for line in payload.decode().splitlines()]
        assert all("questions" not in row for row in rows)
        assert (
            hashlib.sha256("\n".join(sorted(row["asset_id"] for row in rows)).encode()).hexdigest()
            == selection["asset_ids_sha256"]
        )
    changed = export_args.input_root / rows[0]["image_path"]
    changed.write_bytes(b"changed image")
    with pytest.raises(ValueError, match="differs from its manifest"):
        prepare_selection(export_args.manifest, export_args.input_root, tmp_path / "rejected.zip")


def test_import_rejects_corruption_and_ignores_zip_traversal(export_args, tmp_path):
    export_embeddings(export_args, encoder_factory=EncoderDouble)
    archive = export_args.output / "culture-embeddings.zip"
    with ZipFile(archive, "a", ZIP_DEFLATED) as file:
        file.writestr("../../outside.txt", "must never be extracted")
    installed = tmp_path / "safe"
    assert import_archive(archive, installed) == 2
    assert not (tmp_path / "outside.txt").exists()
    corrupt = tmp_path / "corrupt.zip"
    with ZipFile(corrupt, "w") as file:
        for name in ("manifest.json", "vectors.npy", "assets.jsonl"):
            content = (export_args.output / name).read_bytes()
            file.writestr(name, content + b"tamper" if name == "vectors.npy" else content)
    with pytest.raises(ValueError, match="checksum"):
        import_archive(corrupt, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
