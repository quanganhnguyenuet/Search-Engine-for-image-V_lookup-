import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from test_contextual import DocumentEncoder, metadata_artifact

from visualseek_api.application.query_routing import search_term
from visualseek_api.application.search import SearchService
from visualseek_api.application.search_scope import assess_scope
from visualseek_api.core.config import Settings
from visualseek_retrieval.artifact import sha256_file, write_json
from visualseek_retrieval.corpus import CorpusTerms
from visualseek_retrieval.hybrid import FusionConfig, HybridRetriever


def rows():
    return [
        {"asset_id": "1" * 64, "keyword": "Mâm cơm", "category": "am_thuc",
         "tags": ["hành lá", "rau răm", "bàn thờ", "cửa sổ"], "description": ""},
        {"asset_id": "2" * 64, "keyword": "Bàn ăn", "category": "am_thuc",
         "tags": ["hành lá", "rau răm", "bàn thờ", "cửa sổ"], "description": ""},
        {"asset_id": "3" * 64, "keyword": "Bị loại", "category": "am_thuc",
         "tags": ["zqxvjk"], "search_excluded": True},
    ]


@pytest.fixture
def service(tmp_path):
    directory = metadata_artifact(tmp_path / "artifact")
    (directory / "assets.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows()), encoding="utf-8")
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["files"]["assets.jsonl"] = sha256_file(directory / "assets.jsonl")
    write_json(directory / "manifest.json", manifest)
    return SearchService(Settings(embedding_artifact_dir=str(directory), search_llm_enabled=False),
                         encoder_factory=DocumentEncoder)


@pytest.mark.parametrize("query", ["Tôi muốn tìm ảnh có hành lá.", "bàn thờ và cửa sổ",
                                  "Tìm ảnh có cả hành lá và rau răm."])
def test_indexed_subjects_are_grounded_without_loading_a_model(service, query):
    with patch.object(service, "get_encoder", side_effect=AssertionError("No model")):
        scope = service.check_scope(query)
    assert scope.status == "in_scope" and scope.source == "corpus_title_tags"


@pytest.mark.parametrize("query", ["hành lá trên sao Hỏa", "cửa sổ ở Paris"])
def test_known_corpus_words_cannot_override_outside_scope(service, query):
    with patch.object(service, "get_encoder", side_effect=AssertionError("No model")):
        response = service.text_search(query=query, use_llm=False)
    assert response["scope_rejected"] and response["items"] == []


def test_unknown_and_excluded_subjects_still_require_clarification(service):
    assert service.check_scope("zqxvjk").status == "unclear"
    assert service.check_scope("người zqxvjk").status == "unclear"
    assert assess_scope("áo dài, không có tàu vũ trụ; tàu vũ trụ").status == "unclear"


@pytest.mark.parametrize("query", [
    "cửa sổ bên trong trạm vũ trụ quốc tế", "người trên mặt trăng",
    "cầu Cổng Vàng ở San Francisco", "logo công ty ở Silicon Valley",
    "một bức ảnh hoàn toàn trống màu trắng", "lâu đài băng ở Bắc Âu",
])
def test_outside_locations_and_blank_requests_precede_corpus_grounding(service, query):
    assert service.check_scope(query).status == "out_of_scope"


@pytest.mark.parametrize(("query", "expected"), [
    ("Tôi muốn tìm ảnh có hành lá.", "hành lá"),
    ("Tìm ảnh có cả bàn thờ và cửa sổ.", "bàn thờ và cửa sổ"),
    ("Tìm ảnh có cá chép", "cá chép"),
    ("Tìm ảnh cổ Hội An", "cổ hội an"),
    ("tim anh co ca chep", "ca chep"),
])
def test_request_frames_preserve_meaningful_vietnamese_words(query, expected):
    assert search_term(query, preserve_accents=True) == expected


def test_conjunction_does_not_reinterpret_or_negated_or_unresolved_clauses():
    corpus = CorpusTerms(rows())
    assert corpus.conjunction("hành lá và rau răm") == ["hành lá", "rau răm"]
    for query in ("hành lá hoặc rau răm", "hành lá và không có rau răm", "hành lá và zqxvjk"):
        assert corpus.conjunction(query) == []


def test_full_conjunction_evidence_can_retrieve_beyond_channel_candidate_cutoff():
    assets = [
        {"asset_id": "1", "keyword": "A", "category": "am_thuc", "tags": ["hành lá"]},
        {"asset_id": "2", "keyword": "B", "category": "am_thuc", "tags": ["rau răm"]},
        {"asset_id": "3", "keyword": "C", "category": "am_thuc", "tags": ["hành lá", "rau răm"]},
    ]
    index = SimpleNamespace(assets=assets, counts={"am_thuc": 3})
    retriever = HybridRetriever(
        index, FusionConfig(image_depth=1, metadata_depth=1, lexical_depth=1))
    scores = {key: np.array([.9, .8, .1]) for key in ("image", "metadata", "lexical")}
    first = retriever.rank(scores, category=None, limit=1)
    grounded = retriever.rank(scores, category=None, limit=1,
                               required_terms=["hành lá", "rau răm"])
    assert first[0]["asset"]["asset_id"] == "1"
    assert grounded[0]["asset"]["asset_id"] == "3"
    assert grounded[0]["scores"]["required_terms_matched"] == 2


def test_grounding_respects_accents_boundaries_and_excluded_assets():
    corpus = CorpusTerms(rows())
    assert corpus.mentions("Tôi muốn xem bàn thờ và cửa sổ")
    assert not corpus.mentions("bàn thờx")
    assert not corpus.mentions("zqxvjk")
    assert not corpus.positions("hành lạ")
