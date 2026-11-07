from unittest.mock import patch

import pytest
from test_contextual import DocumentEncoder, metadata_artifact

from visualseek_api.application.query_routing import search_term
from visualseek_api.application.search import SearchService
from visualseek_api.core.config import Settings
from visualseek_retrieval.entities import EntityIndex, phrase_position


def asset(id_, title, tags=(), category="am_thuc", description=""):
    return {"asset_id": f"{id_:064x}", "keyword": title, "tags": list(tags),
            "category": category, "description": description}


@pytest.fixture
def subjects():
    # Incidental mentions precede actual subjects in source order, reproducing the bug.
    return [
        asset(1, "Rằm tháng Giêng", ["bánh chưng"], "le_hoi", "Mâm cúng nhiều món."),
        asset(2, "Lễ hội bánh chưng", ["bánh chưng"], "le_hoi", "Lễ hội gói bánh chưng."),
        asset(3, "Bánh chưng gù", ["lá dong"], description="Cận cảnh bánh chưng gù."),
        asset(4, "Bánh chưng Tết", ["bánh chưng"], description="Đĩa bánh chưng trên bàn."),
        asset(5, "Bánh chưng", ["bánh chưng"], description="Bánh chưng trên đĩa."),
        asset(6, "Bánh tét", ["bánh tét"], description="Bánh gói hình trụ."),
        asset(7, "Bánh chưng gù đặc sản", description="Bánh chưng gù."),
    ]


def test_subject_variants_beat_incidental_tags_and_related_events(subjects):
    hits = EntityIndex(subjects).search("bánh chưng", category=None, limit=10)
    ids = [hit["asset"]["asset_id"] for hit in hits]
    assert ids[0] == subjects[4]["asset_id"]
    assert set(ids[1:3]) == {subjects[2]["asset_id"], subjects[3]["asset_id"]}
    assert ids.index(subjects[6]["asset_id"]) < ids.index(subjects[1]["asset_id"])
    assert ids.index(subjects[1]["asset_id"]) < ids.index(subjects[0]["asset_id"])
    assert subjects[5]["asset_id"] not in ids
    assert hits[1]["match_kind"] == "title_variant"
    assert "biến thể" in hits[1]["match_reason"]


def test_variant_without_exact_tags_is_retrieved(subjects):
    hits = EntityIndex(subjects).search("bánh chưng gù", category=None, limit=10)
    assert [hit["asset"]["keyword"] for hit in hits] == [
        "Bánh chưng gù", "Bánh chưng gù đặc sản"]
    assert hits[0]["scores"]["exact_title"] is True


def test_phrases_do_not_match_substrings_or_reordered_words(subjects):
    index = EntityIndex(subjects)
    assert index.search("chư", category=None, limit=10) is None
    assert index.search("chưng bánh", category=None, limit=10) is None
    assert index.search("bánh chưng đỏ", category=None, limit=10) is None


def test_explicit_accents_disambiguate_pho_noodle_from_pho_street():
    rows = [asset(1, "Phố cổ Hà Nội", ["phố"]),
            asset(2, "phở Việt Nam", ["phở"]),
            asset(3, "pho bo", ["pho"])]
    hits = EntityIndex(rows).search("phở", category=None, limit=10)
    assert {hit["asset"]["asset_id"] for hit in hits} == {rows[1]["asset_id"], rows[2]["asset_id"]}
    assert phrase_position("phố cổ", "phở") is None
    assert phrase_position("pho bo", "phở") == 0


def test_ascii_queries_still_find_vietnamese_variants(subjects):
    index = EntityIndex(subjects)
    accented = index.search("bánh chưng", category=None, limit=10)
    ascii_ = index.search("banh chung", category=None, limit=10)
    assert [hit["asset"]["asset_id"] for hit in accented] == [
        hit["asset"]["asset_id"] for hit in ascii_]
    assert search_term("Tôi muốn tìm ảnh phở", preserve_accents=True) == "phở"
    assert search_term("Tôi muốn tìm ảnh phở") == "pho"


def test_category_applies_after_ranking_before_limit(subjects):
    index = EntityIndex(subjects)
    all_hits = index.search("bánh chưng", category=None, limit=1)
    festival = index.search("bánh chưng", category="le_hoi", limit=1)
    assert all_hits[0]["asset"]["category"] == "am_thuc"
    assert festival[0]["asset"]["keyword"] == "Lễ hội bánh chưng"
    assert index.search("bánh chưng", category="kien_truc", limit=10) == []
    assert index.search("bánh chưng", category=None, limit=1) == all_hits


def test_query_cache_does_not_change_ranking_or_grow_unbounded(subjects):
    index = EntityIndex(subjects)
    index.ranked_matches.cache_clear()
    first = index.search("bánh chưng", category=None, limit=3)
    assert index.search("bánh chưng", category=None, limit=3) == first
    assert index.ranked_matches.cache_info().hits >= 1
    for i in range(150):
        index.search(f"missing term {i}", category=None, limit=3)
    assert index.ranked_matches.cache_info().currsize <= 128


def test_variant_query_does_not_load_encoder_or_call_llm(tmp_path):
    path = metadata_artifact(tmp_path / "artifact")
    service = SearchService(Settings(embedding_artifact_dir=str(path), openai_api_key="",
                                    search_llm_enabled=False),
                            encoder_factory=DocumentEncoder)
    with patch.object(service, "get_encoder", side_effect=AssertionError("No encoder")), \
         patch.object(service.understanding, "plan", side_effect=AssertionError("No LLM")):
        result = service.text_search(query="áo dài", top_k=10)
    assert result["search_route"] == "lexical" and result["llm_call_count"] == 0
    assert result["items"][0]["match_kind"] == "title_variant"
    assert service.encoder is None


def test_multi_clause_query_does_not_get_title_variant_shortcut(tmp_path):
    path = metadata_artifact(tmp_path / "artifact")
    service = SearchService(Settings(embedding_artifact_dir=str(path)),
                            encoder_factory=DocumentEncoder)
    with patch.object(service.understanding, "plan", side_effect=RuntimeError("planner reached")):
        with pytest.raises(RuntimeError, match="planner reached"):
            service.text_search(query="áo dài hoặc áo xanh")
