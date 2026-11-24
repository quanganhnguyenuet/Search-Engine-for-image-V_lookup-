from types import SimpleNamespace

import numpy as np

from visualseek_retrieval.connectors import find, has_negation, segments
from visualseek_retrieval.pipeline import SearchPipeline, Settings, merge_groups


def kinds(query: str) -> list[tuple[str, str]]:
    return [(cue.kind, cue.text) for cue in find(query)]


def test_ngoai_ra_adds_while_ngoai_x_ra_excludes():
    assert kinds("FSA Gossamer, ngoài ra cho tôi xem Garmin") == [("additive", "ngoài ra")]
    assert has_negation("FSA Gossamer, ngoài ra cho tôi xem Garmin") is None
    cue = has_negation("ngoài FSA Gossamer ra hãng đó còn làm gì")
    assert cue is not None and (cue.kind, cue.text) == ("besides", "ngoài")


def test_exclusion_cues_longest_first():
    assert has_negation("Shimano nhưng không lấy Shimano CUES").text == "không lấy"
    assert has_negation("Shimano ngoại trừ BB-9000").text == "ngoại trừ"
    assert has_negation("Shimano chứ không phải BB-9000").text == "chứ không"
    assert has_negation("giày thể thao và túi xách") is None


def test_segments_split_at_and_or_and_list_punctuation():
    assert segments("lốp xe máy, dầu nhớt và vali") == ["lốp xe máy", "dầu nhớt", "vali"]
    assert segments("trang sức cao cấp hoặc đồng hồ") == ["trang sức cao cấp", "đồng hồ"]
    assert segments("giày thể thao cũng như túi xách") == ["giày thể thao", "túi xách"]


def test_with_is_an_attribute_not_a_second_object():
    assert segments("bàn phím với đèn led") == ["bàn phím với đèn led"]


def test_folded_queries_skip_ambiguous_words():
    # Folded "kem" is cream, "nhung" velvet, "hay" good: none of them may split.
    assert segments("kem duong da va son moi") == ["kem duong da", "son moi"]
    assert segments("ao nhung dep hay") == ["ao nhung dep hay"]
    assert has_negation("tru cau be tong") is None


GRAPH = {
    "fingerprint": "test",
    "entities": [
        {"id": "x1", "name": "Lốp", "core": "Lốp", "brand_id": None, "aliases": ["xx1"]},
        {"id": "x2", "name": "Vali", "core": "Vali", "brand_id": None, "aliases": ["xx2"]},
    ],
    "brands": [],
    "edges": [],
}
CATEGORIES = {
    "category_names": {"moto.tyres": "Lốp xe máy", "fashion.bags": "Túi & vali"},
    "entities": {
        "x1": {"category": "moto.tyres", "product_type": "Lốp xe máy"},
        "x2": {"category": "fashion.bags", "product_type": "Vali kéo"},
    },
}


class PartEncoder:
    """Vector per text: tyres-like texts point at row 0, luggage-like texts at row 1."""

    def encode(self, texts):
        return np.array(
            [
                [1.0, 0.0] if "lốp" in t else [0.0, 1.0] if "vali" in t else [0.6, 0.6]
                for t in texts
            ],
            dtype=np.float32,
        )


def test_each_segment_gets_its_own_category_and_results_are_grouped():
    dense = np.eye(2, dtype=np.float32)
    settings = Settings(dense_seed_min=2.0, knn_min_sim=0.5, knn_min_share=0.6)
    pipe = SearchPipeline(
        GRAPH, dense, PartEncoder(), None, settings=settings, categories=CATEGORIES
    )
    query = "lốp xe và vali"
    plan = pipe.plan(query, pipe.spotter.spot(query, np.array([0.6, 0.6], dtype=np.float32)))
    assert plan.category_groups == ["moto.tyres", "fashion.bags"]
    assert [seg["categories"] for seg in plan.category_segments] == [
        ["moto.tyres"],
        ["fashion.bags"],
    ]
    trace = pipe._trace(plan, None)
    assert trace["category"]["groups"] == ["moto.tyres", "fashion.bags"]


def test_merge_groups_by_category_keeps_both_intents():
    def hit(i: str, score: float):
        return SimpleNamespace(id=i, score=score)

    groups = [
        SimpleNamespace(id="moto.tyres", hits=[hit("a", 0.9), hit("b", 0.8), hit("c", 0.7)]),
        SimpleNamespace(id="fashion.bags", hits=[hit("d", 0.5), hit("a", 0.4)]),
    ]
    out = merge_groups(groups, str, 2, {"moto.tyres", "fashion.bags"})
    assert [h.id for h in out] == ["a", "b", "d"]
