import copy
import csv
import json

import pytest
from evaluate_fusion import export_labels, import_labels, replay_config

from visualseek_evaluation.fusion import calibrate_gate, compare_modes, fused_ranking, tune_fusion
from visualseek_retrieval.hybrid import FusionConfig
from visualseek_retrieval.relevance import RelevancePolicy, context_signature


def pool_sample(query_id, answerable, split="development"):
    evidence = {
        "good": {
            "scores": {
                "image_cosine": 0.9 if answerable else 0.08,
                "metadata_cosine": 0.7 if answerable else 0.9,
                "lexical": 4,
            },
            "ranks": {"image": 1, "metadata": 2, "lexical": 1},
            "metadata_complete": True,
            "source_position": 0,
        },
        "bad": {
            "scores": {"image_cosine": 0.05, "metadata_cosine": 0.95, "lexical": 0},
            "ranks": {"image": 2, "metadata": 1, "lexical": None},
            "metadata_complete": True,
            "source_position": 1,
        },
    }
    return {
        "query_id": query_id,
        "query": query_id,
        "group": "objects",
        "split": split,
        "answerable": answerable,
        "pipeline_signature": "a" * 64,
        "judgments": {"good": 2 if answerable else 0, "bad": 0},
        "rankings": {"weighted": ["bad", "good"], "image_only": ["good", "bad"]},
        "channel_evidence": evidence,
        "candidates": [
            {
                "id": id_,
                "title": id_,
                "description": "description",
                "image_path": f"images/{id_}.png",
            }
            for id_ in evidence
        ],
    }


def pool():
    return {
        "schema_version": 1,
        "query_profile": "local",
        "pipeline_signature": "a" * 64,
        "pipeline_context": {"version": "test"},
        "fusion_config": FusionConfig().as_dict(),
        "samples": [
            pool_sample("positive", True),
            pool_sample("outside", False),
            pool_sample("held-out", True, split="held-out"),
        ],
    }


def test_fusion_replay_and_labelled_mode_comparison():
    sample = pool_sample("q", True)
    assert fused_ranking(sample, FusionConfig()) == ["good", "bad"]
    assert fused_ranking(
        sample, FusionConfig(image_weight=0, metadata_weight=1, lexical_weight=0)
    ) == ["bad", "good"]
    reports = compare_modes(pool(), "held-out", k=1)
    image = next(report for report in reports if report["mode"] == "image_only")
    weighted = next(report for report in reports if report["mode"] == "weighted")
    assert image["metrics"]["all"]["Precision@1"] == 1
    assert weighted["metrics"]["all"]["HitRate@1"] == 0
    outside = compare_modes(pool(), "development", k=1)[0]
    assert outside["rejection"]["outside_false_accept_rate"] == 1


def test_tuning_and_calibration_never_read_held_out_labels():
    original = pool()
    changed = copy.deepcopy(original)
    held = changed["samples"][-1]
    held["judgments"] = {"good": None, "bad": None}
    held["answerable"] = None
    held["channel_evidence"]["good"]["scores"]["image_cosine"] = -1
    assert tune_fusion(original, k=1) == tune_fusion(changed, k=1)
    calibrated = calibrate_gate(original, k=1)
    assert calibrated == calibrate_gate(changed, k=1)
    policy = RelevancePolicy(
        "a" * 64, calibrated["min_image_cosine"], calibrated["min_metadata_cosine"]
    )
    assert policy.accepts(original["samples"][0]["channel_evidence"]["good"])
    assert not policy.accepts(original["samples"][1]["channel_evidence"]["good"])
    assert calibrated["calibration"]["outside_false_accept_rate"] == 0
    assert calibrated["calibration"]["candidate_precision"] == 1


def test_unjudged_pools_cannot_be_tuned_scored_or_calibrated():
    value = pool()
    value["samples"][0]["judgments"]["good"] = None
    for operation in (tune_fusion, calibrate_gate):
        with pytest.raises(ValueError, match="human grade"):
            operation(value)
    with pytest.raises(ValueError):
        compare_modes(value, "development")
    value = pool()
    value["samples"] = [value["samples"][0]]
    with pytest.raises(ValueError, match="unanswerable"):
        calibrate_gate(value)


def test_csv_labels_roundtrip_and_rejects_missing_rows(tmp_path):
    unjudged = pool()
    for sample in unjudged["samples"]:
        sample["answerable"] = None
        sample["judgments"] = {key: None for key in sample["judgments"]}
    file = tmp_path / "labels.csv"
    export_labels(unjudged, file)
    with file.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
        fields = rows[0].keys()
    for row in rows:
        row["answerable"] = "false" if row["query_id"] == "outside" else "true"
        row["grade"] = "2" if row["asset_id"] == "good" and row["query_id"] != "outside" else "0"
    with file.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    result = import_labels(copy.deepcopy(unjudged), file, reviewer="test reviewer")
    assert result["samples"][0]["judgments"]["good"] == 2
    assert result["samples"][1]["answerable"] is False
    assert result["label_provenance"]["reviewer"] == "test reviewer"
    with file.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows[:-1])
    with pytest.raises(ValueError, match="incomplete"):
        import_labels(copy.deepcopy(unjudged), file, reviewer="test reviewer")


def test_replay_rebinds_policy_signature_and_rejects_deeper_unjudged_pool(tmp_path):
    config = FusionConfig(image_weight=2, metadata_weight=1, lexical_weight=0.5)
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"best": {"config": config.as_dict()}}))
    value = replay_config(pool(), config_file)
    assert value["pipeline_signature"] == context_signature(value["pipeline_context"])
    assert value["pipeline_signature"] != "a" * 64
    assert all(s["pipeline_signature"] == value["pipeline_signature"] for s in value["samples"])
    config_file.write_text(json.dumps(FusionConfig(image_depth=200).as_dict()))
    with pytest.raises(ValueError, match="recollection"):
        replay_config(pool(), config_file)


def test_development_labels_can_be_imported_before_held_out_review(tmp_path):
    value = pool()
    for sample in value["samples"]:
        sample["answerable"] = None
        sample["judgments"] = {key: None for key in sample["judgments"]}
    path = tmp_path / "development.csv"
    export_labels(value, path, split="development")
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert {row["query_id"] for row in rows} == {"positive", "outside"}
    for row in rows:
        row["answerable"] = "true" if row["query_id"] == "positive" else "false"
        row["grade"] = "2" if row["query_id"] == "positive" and row["asset_id"] == "good" else "0"
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    result = import_labels(value, path, "reviewer", split="development")
    assert result["samples"][-1]["answerable"] is None
    assert calibrate_gate(result, k=1)["calibration"]["fit_split"] == "development"
    with pytest.raises(ValueError, match="answerable"):
        compare_modes(result, "held-out")
