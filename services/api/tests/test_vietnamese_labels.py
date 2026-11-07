import json
from unittest.mock import patch

import numpy as np
import pytest
from test_contextual import DocumentEncoder, metadata_artifact

from visualseek_api.application.search import SearchService
from visualseek_api.core.config import Settings
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json
from visualseek_retrieval.entities import EntityIndex
from visualseek_retrieval.hybrid import HybridRetriever
from visualseek_retrieval.text import normalize, phrase_position, surface


@pytest.mark.parametrize(
    ("text", "query", "expected"),
    [
        ("Bánh chay", "bánh chay", 0),
        ("Bánh chày", "bánh chay", None),
        ("Bánh cháy", "bánh chay", None),
        ("Bánh chày", "banh chay", 0),
        ("Phố cổ", "phở", None),
        ("Phong Nha", "phở", None),
        ("Phở bò", "pho", 0),
        ("pho bo", "phở", 0),
        ("Phố cổ", "pho", 0),
        ("Hoà Bình", "Hòa Bình", 0),
        ("Hòa Bình", "Hoà Bình", 0),
        ("bánh chay", "ba\u0301nh chay", 0),
        ("trang phục H'Mông", "trang phục HMông", 0),
    ],
)
def test_vietnamese_word_semantics(text, query, expected):
    assert phrase_position(text, query) == expected


def changed_artifact(tmp_path, rows):
    directory = metadata_artifact(tmp_path / "artifact")
    (directory / "assets.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["files"]["assets.jsonl"] = sha256_file(directory / "assets.jsonl")
    write_json(directory / "manifest.json", manifest)
    return ArtifactIndex(directory)


def test_accented_lexical_evidence_keeps_pho_and_chay_meanings(tmp_path):
    rows = [
        {
            "asset_id": "1" * 64,
            "category": "am_thuc",
            "keyword": "Bánh chay",
            "tags": ["bánh chay"],
            "description": "Món bánh chay.",
        },
        {
            "asset_id": "2" * 64,
            "category": "am_thuc",
            "keyword": "Bánh chày",
            "tags": ["bánh chày"],
            "description": "Tên bánh chày.",
        },
        {
            "asset_id": "3" * 64,
            "category": "kien_truc",
            "keyword": "Phố cổ",
            "tags": ["phố"],
            "description": "Ngôi nhà ở phố cổ.",
        },
    ]
    index = changed_artifact(tmp_path, rows)
    retriever = HybridRetriever(index)
    scores = retriever.lexical_scores(["bánh chay"])
    assert scores[0] > scores[1]
    assert retriever.lexical_scores(["phở"])[2] == 0
    assert retriever.lexical_scores(["phố"])[2] > 0
    assert retriever.lexical_scores(["pho"])[2] > 0
    assert [
        h["asset"]["keyword"] for h in retriever.exact_search("bánh chay", category=None, limit=10)
    ] == ["Bánh chay"]


def test_search_excluded_assets_are_never_returned_by_any_channel(tmp_path):
    rows = [
        {
            "asset_id": "1" * 64,
            "category": "am_thuc",
            "keyword": "Xương bánh chè",
            "search_excluded": True,
            "tags": ["bánh chay"],
            "description": "Minh họa khớp gối",
        },
        {
            "asset_id": "2" * 64,
            "category": "am_thuc",
            "keyword": "Bánh chay",
            "tags": ["bánh chay"],
            "description": "Bánh chay trên đĩa",
        },
        {
            "asset_id": "3" * 64,
            "category": "kien_truc",
            "keyword": "Phố cổ",
            "tags": [],
            "description": "Nhà phố cổ",
        },
    ]
    index = changed_artifact(tmp_path, rows)
    assert index.searchable_count == 2 and index.counts["am_thuc"] == 1
    assert "1" * 64 not in [hit["asset"]["asset_id"] for hit in index.search(np.array([1, 0, 0]))]
    retriever = HybridRetriever(index)
    assert all(
        hit["asset"]["asset_id"] != "1" * 64
        for hit in retriever.search(
            np.array([[1, 0, 0]]), ["xương bánh chè"], category=None, limit=10
        )
    )
    assert EntityIndex(rows).search("xương bánh chè", category=None, limit=10) is None


def test_ambiguous_ascii_name_gets_choices_without_encoder_or_llm(tmp_path):
    rows = [
        {
            "asset_id": "1" * 64,
            "category": "am_thuc",
            "keyword": "Phở Việt Nam",
            "tags": ["phở"],
            "description": "Tô phở",
        },
        {
            "asset_id": "2" * 64,
            "category": "kien_truc",
            "keyword": "Phố cổ Hà Nội",
            "tags": ["phố"],
            "description": "Phố cổ",
        },
        {
            "asset_id": "3" * 64,
            "category": "am_thuc",
            "keyword": "Bánh chay",
            "tags": [],
            "description": "Bánh chay",
        },
    ]
    index = changed_artifact(tmp_path, rows)
    service = SearchService(
        Settings(embedding_artifact_dir=str(index.directory), search_llm_enabled=False),
        encoder_factory=DocumentEncoder
    )
    with patch.object(service, "get_encoder", side_effect=AssertionError("No encoder")):
        response = service.text_search(query="pho")
        assert response["clarification_required"] and response["items"] == []
        assert set(response["suggested_queries"]) == {"phở", "phố"}
        assert service.text_search(query="phở")["items"][0]["title"] == "Phở Việt Nam"
        assert not service.text_search(query="pho", category="am_thuc")["clarification_required"]
    assert normalize("H'Mông") == normalize("HMông")
    assert surface("H’Mông") == surface("HMông")
