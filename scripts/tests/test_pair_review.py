import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "pair_review_server", SCRIPTS / "pair_review_server.py"
)
server = importlib.util.module_from_spec(spec)
sys.modules["pair_review_server"] = server  # pydantic resolves postponed annotations here
spec.loader.exec_module(server)

from visualseek_retrieval.entity_review import EntityReview  # noqa: E402

GRAPH = {
    "corpus_fingerprint": "c",
    "entities": [{"id": i, "name": i} for i in ("a", "b", "c")],
}
BASE = {
    "version": 1,
    "corpus_fingerprint": "c",
    "reviewer": "Codex",
    "entity_pairs": [{"src": "a", "dst": "c", "decision": "DISTINCT", "evidence": [{"text": "x"}]}],
    "aliases": {},
    "rejected_depictions": [],
    "eval_query_corrections": {},
}


def decide(**kwargs):
    return server.Decision(**{"note": "", "keep": None, **kwargs})


def load(data):
    return EntityReview.load(GRAPH, json.dumps(data).encode())


def test_same_as_merges_into_the_kept_entity_with_reviewer_and_evidence():
    out = server.apply_decision(
        BASE, decide(src="b", dst="a", decision="SAME_AS", keep="b"), "human-1", "t", ["s1"]
    )
    pair = out["entity_pairs"][-1]
    assert (pair["src"], pair["dst"]) == ("a", "b") and pair["reviewer"] == "human-1"
    assert pair["evidence"][1] == {"asset_id": "s1", "text": "Ảnh dùng chung của hai nhãn."}
    assert load(out).canonical("a") == "b"
    assert BASE["entity_pairs"][0]["decision"] == "DISTINCT"  # input left untouched


def test_new_decision_replaces_the_old_one_for_the_same_pair():
    out = server.apply_decision(
        BASE, decide(src="c", dst="a", decision="VARIANT_OF", note="cùng dòng"), "h", "t", []
    )
    assert [p["decision"] for p in out["entity_pairs"]] == ["VARIANT_OF"]
    assert out["entity_pairs"][0]["evidence"] == [{"text": "cùng dòng"}]


def test_skip_leaves_the_review_unchanged_and_same_as_needs_a_member_to_keep():
    assert (
        server.apply_decision(BASE, decide(src="a", dst="b", decision="SKIP"), "h", "t", []) is BASE
    )
    with pytest.raises(ValueError):
        server.apply_decision(
            BASE, decide(src="a", dst="b", decision="SAME_AS", keep="z"), "h", "t", []
        )


def test_a_second_merge_of_the_same_entity_is_rejected_by_the_loader():
    once = server.apply_decision(
        BASE, decide(src="a", dst="b", decision="SAME_AS", keep="b"), "h", "t", []
    )
    twice = server.apply_decision(
        once, decide(src="a", dst="c", decision="SAME_AS", keep="c"), "h", "t", []
    )
    with pytest.raises(ValueError, match="Multiple canonical"):
        load(twice)
