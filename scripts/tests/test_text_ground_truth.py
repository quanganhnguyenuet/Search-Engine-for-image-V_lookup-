import json
import unicodedata
from types import SimpleNamespace

import pytest
from evaluate_text_to_image import summarize_quality, validate

from visualseek_evaluation.image import score_ranking
from visualseek_evaluation.text_ground_truth import (
    build_ground_truth,
    concept_evidence,
    load_ground_truth,
    save_ground_truth,
    tag_matches,
)


def fixture_data():
    assets = [{"asset_id": f"{i:064x}", "category": "a" if i < 6 else "b",
               "tags": (["áo dài"] if i < 9 else []) + (["nón lá"] if i >= 3 else [])}
              for i in range(12)]
    catalog = [{"name": name, "category": "a", "aliases": [name]}
               for name in ("áo dài", "nón lá")]
    return assets, catalog


def test_tag_aliases_keep_word_boundaries_diacritics_and_negations():
    assert tag_matches("người mặc áo dài trắng", "áo dài")
    assert tag_matches(unicodedata.normalize("NFD", "ÁO DÀI"), "áo dài")
    assert tag_matches("trang phục HMông", "trang phục H'Mông")
    assert not tag_matches("biển hiệu", "biển")
    assert not tag_matches("bánh chày", "bánh chay")
    assert not tag_matches("không có nón lá", "nón lá")
    assert not tag_matches("áo dàix", "áo dài")


def test_positives_use_tags_across_categories_and_never_title_or_rank():
    assets, catalog = fixture_data()
    assets += [{"asset_id": "title-only", "keyword": "áo dài", "tags": [], "category": "a"},
               {"asset_id": "excluded", "tags": ["áo dài"], "search_excluded": True}]
    evidence = concept_evidence(assets, catalog)
    assert len(evidence["áo dài"]) == 9
    assert f"{8:064x}" in evidence["áo dài"]  # Different folder/category is still positive.
    assert "title-only" not in evidence["áo dài"]
    assert "excluded" not in evidence["áo dài"]


def test_query_variants_and_conjunctions_have_stable_nonleaking_ground_truth():
    assets, catalog = fixture_data()
    data = build_ground_truth(assets, count=4, seed=42, catalog=catalog)
    assert data == build_ground_truth(assets, count=4, seed=42, catalog=catalog)
    assert len(data["queries"]) == 4 and len(data["intents"]) == 2
    conjunction = next(row for row in data["intents"] if row["kind"] == "conjunction")
    assert set(conjunction["relevant_asset_ids"]) == {f"{i:064x}" for i in range(3, 9)}
    for intent in data["intents"]:
        variants = [q for q in data["queries"] if q["intent_id"] == intent["intent_id"]]
        assert len(variants) == 2
        assert {q["split"] for q in variants} == {intent["split"]}
    with pytest.raises(ValueError):
        build_ground_truth(assets, count=6, catalog=catalog)
    with pytest.raises(ValueError, match="Insufficient"):
        build_ground_truth(assets, count=40, catalog=catalog)


def test_ground_truth_checksums_artifact_identity_and_independent_rule_validation(
    tmp_path, monkeypatch
):
    assets, catalog = fixture_data()
    monkeypatch.setattr("visualseek_evaluation.text_ground_truth.concepts", lambda: catalog)
    index = SimpleNamespace(assets=assets, searchable_count=len(assets),
                            manifest={"files": {"vectors.npy": "pinned"}})
    directory = tmp_path / "truth"
    save_ground_truth(index, directory, count=4)
    assert validate(index, directory)["positive_rule_mismatches"] == 0
    manifest, queries, _ = load_ground_truth(index, directory)
    assert manifest["human_reviewed"] is False
    assert len(queries) == 4
    changed = SimpleNamespace(**{**vars(index), "manifest": {"files": {"vectors.npy": "other"}}})
    with pytest.raises(ValueError, match="artifact differ"):
        load_ground_truth(changed, directory)
    with (directory / "queries.jsonl").open("a") as stream:
        stream.write(json.dumps(queries[0]) + "\n")
    with pytest.raises(ValueError, match="checksum"):
        load_ground_truth(index, directory)


def test_scope_stops_are_zero_recall_and_failed_requests_prevent_a_complete_quality_score():
    good = {"ok": True, "route": "hybrid", "clarification_required": False,
            "metrics": score_ranking(["a"], ["a", "b"], [10])}
    stop = {"ok": True, "route": "scope", "clarification_required": True,
            "metrics": score_ranking([], ["c"], [10])}
    summary = summarize_quality([good, stop])
    assert summary["metrics"]["Recall@10"] == 0.25
    assert summary["scope_stop_queries"] == 1
    assert summarize_quality([good, {"ok": False}])["metrics"] is None
