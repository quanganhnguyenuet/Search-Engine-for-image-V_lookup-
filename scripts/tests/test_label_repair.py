import json

import numpy as np
from repair_culture_labels import repair

from visualseek_preprocessing.label_overrides import ASSET_OVERRIDES
from visualseek_preprocessing.labels import LABEL_VERSION, canonical_title, repair_labels
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json


def test_reviewed_spellings_are_scoped_and_idempotent():
    assert canonical_title("ao dai cuới", "trang_phuc") == "Áo dài cưới"
    assert canonical_title("nha tho Đuc Ba", "kien_truc") == "Nhà thờ Đức Bà"
    assert canonical_title("phố", "am_thuc") == "phố"  # Never guess a different accented word.
    source = {
        "asset_id": "1" * 64,
        "category": "trang_phuc",
        "keyword": "ao dai cuới",
        "tags": ["áo dài"],
        "description": "Áo dài cưới",
        "image_path": "images/test/a.jpg",
    }
    fixed = repair_labels(source)
    assert fixed["label_original"]["keyword"] == source["keyword"]
    assert fixed["label_revision"] == LABEL_VERSION
    assert repair_labels(fixed) == fixed
    assert source["keyword"] == "ao dai cuới"


def test_reviewed_banh_chay_assets_have_specific_subjects_not_a_folder_label():
    assert len(ASSET_OVERRIDES) == 57
    assert sum(row["search_excluded"] for row in ASSET_OVERRIDES.values()) == 12
    for asset_id, override in ASSET_OVERRIDES.items():
        row = {
            "asset_id": asset_id,
            "category": "am_thuc",
            "keyword": "banh chay",
            "tags": ["banh chay"],
            "description": "generic fallback",
            "image_path": override["source_image_path"],
            "cultural_context": {"old": "bánh chay"},
        }
        fixed = repair_labels(row)
        assert fixed["keyword"] == override["keyword"]
        assert fixed["cultural_context"] == {}
        assert fixed["label_original"]["keyword"] == "banh chay"
        assert "bánh chay" not in fixed["description"].lower()
        assert repair_labels(fixed) == fixed


def test_semantic_correction_requires_tag_and_description_agreement():
    source = {
        "asset_id": "a" * 64,
        "category": "thu_cong_my_nghe",
        "keyword": "kẹo dân gian",
        "tags": ["bao bì keo dán gạch"],
        "description": "Bao bì keo dán gạch.",
        "image_path": "images/x/y/a.jpg",
    }
    fixed = repair_labels(source)
    assert fixed["keyword"] == "Keo dán gạch"
    assert fixed["label_review"]["pixel_verified"] is False
    assert repair_labels({**source, "description": "Hộp kẹo."})["keyword"] == "Kẹo dân gian"


class Encoder:
    calls = 0

    def __init__(self, **kwargs):
        pass

    def encode_documents(self, texts, batch_size=32):
        type(self).calls += len(texts)
        assert all("Áo dài cưới" in text for text in texts)
        return np.asarray([[0, 1, 0] for _ in texts], dtype=np.float32)


def test_repair_versions_metadata_and_preserves_pixel_vectors(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    assets = [
        {
            "asset_id": "1" * 64,
            "category": "trang_phuc",
            "keyword": "ao dai cuới",
            "tags": [],
            "description": "mô tả",
            "image_path": "images/a/b/c.jpg",
            "questions": [{"answer": "NOT_FOR_EXPORT"}],
        },
        {
            "asset_id": "2" * 64,
            "category": "am_thuc",
            "keyword": "unchanged",
            "tags": [],
            "description": "mô tả",
            "image_path": "images/a/b/d.jpg",
        },
    ]
    (source / "assets.jsonl").write_text("".join(json.dumps(r) + "\n" for r in assets))
    vectors = np.array([[1, 0, 0], [0, 0, 1]], dtype=np.float32)
    np.save(source / "vectors.npy", vectors)
    np.save(source / "metadata_vectors.npy", vectors)
    write_json(
        source / "manifest.json",
        {
            "schema_version": 2,
            "count": 2,
            "dimension": 3,
            "model_id": "google/siglip2-base-patch16-224",
            "model_revision": "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2",
            "preprocess_version": "siglip2-fixres224-exif-rgb-l2-v1",
            "normalization": "l2",
            "metadata_version": "title-tags-description-chunk-mean-l2-v2",
            "files": {
                name: sha256_file(source / name)
                for name in ("vectors.npy", "metadata_vectors.npy", "assets.jsonl")
            },
        },
    )
    Encoder.calls = 0
    output = tmp_path / "repaired"
    report = repair(source, output, encoder_factory=Encoder)
    assert report["metadata_reembedded"] == 1 and Encoder.calls == 1
    index = ArtifactIndex(output)
    assert index.assets[0]["keyword"] == "Áo dài cưới"
    assert all("questions" not in row for row in index.assets)
    assert np.array_equal(index.vectors, vectors)
    assert np.array_equal(index.metadata_vectors[1], vectors[1])
    repair(source, output, encoder_factory=Encoder)
    assert Encoder.calls == 1
