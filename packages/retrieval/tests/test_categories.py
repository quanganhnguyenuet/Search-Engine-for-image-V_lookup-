import numpy as np

from visualseek_retrieval import lexical
from visualseek_retrieval.categories import CategoryLexicon, knn_votes
from visualseek_retrieval.pipeline import SearchPipeline, Settings
from visualseek_retrieval.qdrant_store import EntityBoosts, build_request

NAMES = {
    "fashion.shoes": "Giày dép",
    "household.paper": "Giấy & vở",
    "fashion.jewelry": "Trang sức",
}


def entity(product_type: str, category: str) -> dict:
    return {"product_type": product_type, "category": category}


ENTITIES = {
    "e1": entity("Giày chạy bộ", "fashion.shoes"),
    "e2": entity("Giày da nam", "fashion.shoes"),
    "e3": entity("Giấy in A4", "household.paper"),
    "e4": entity("Giấy ăn", "household.paper"),
    "e5": entity("Nhẫn kim cương", "fashion.jewelry"),
    "e6": entity("Nhẫn vàng", "fashion.jewelry"),
    "e8": entity("Nhẫn kim cương 18K", "fashion.jewelry"),
    "e7": entity("Vòng bi", "household.paper"),  # a lone odd label
}


def lexicon() -> CategoryLexicon:
    return CategoryLexicon.build(ENTITIES, NAMES)


def test_accent_keeps_giay_and_giay_apart():
    lex = lexicon()
    assert lex.detect("giày thể thao").categories == ["fashion.shoes"]
    assert lex.detect("giấy in").categories == ["household.paper"]
    # Folded spelling is ambiguous between the two, so it is not a cue.
    assert lex.detect("giay").categories == []


def test_one_word_cue_needs_three_votes():
    lex = lexicon()
    assert lex.detect("nhẫn").categories == ["fashion.jewelry"]  # three entities
    assert lex.detect("vòng").categories == []  # one entity only
    assert lex.detect("nhẫn kim cương").categories == ["fashion.jewelry"]


def test_tier_applies_to_fashion_only():
    lex = lexicon()
    intent = lex.detect("giày cao cấp")
    assert intent.categories == ["fashion.shoes"] and intent.tiers == ["luxury"]
    other = lex.detect("giấy in cao cấp")
    assert other.tiers == [] and other.ignored_tiers[0]["reason"] == "not_fashion"
    # An unambiguous cue works without any object, an ambiguous one does not.
    assert lex.detect("hàng hiệu").tiers == ["luxury"]
    assert lex.detect("đồ cao cấp").tiers == []


def test_knn_votes_share_and_best_similarity():
    scores = np.array([0.9, 0.8, 0.5, 0.2], dtype=np.float32)
    votes, best = knn_votes(scores, ["a", "a", "b", None], k=3, floor=0.3)
    assert best == np.float32(0.9) and set(votes) == {"a", "b"}
    assert votes["a"] > 0.8 and abs(sum(votes.values()) - 1.0) < 1e-6


def test_request_adds_category_boost_terms_and_optional_prefetch():
    boosts = EntityBoosts(
        categories=["fashion.shoes"], tiers=["luxury"], w_category=0.2, w_tier=0.1
    )
    request = build_request([0.0] * 3, lexical.query_vector("giày"), boosts)
    formula = request["query"].formula.sum
    keys = {term.mult[1].key for term in formula[1:]}
    assert keys == {"categories", "market_tiers"} and len(request["prefetch"][0].prefetch) == 2
    assert boosts.category_score(["fashion.shoes"], ["luxury"]) == 0.30000000000000004
    assert boosts.category_score(["car.parts"], []) == 0.0
    boosts.category_prefetch = True
    request = build_request([0.0] * 3, lexical.query_vector("giày"), boosts)
    channel = request["prefetch"][0].prefetch[-1]
    assert [c.key for c in channel.filter.must] == ["categories", "market_tiers"]


GRAPH = {
    "fingerprint": "test",
    "entities": [
        {"id": "x1", "name": "Giày", "core": "Giày", "brand_id": None, "aliases": ["xx1"]},
        {"id": "x2", "name": "Giấy", "core": "Giấy", "brand_id": None, "aliases": ["xx2"]},
    ],
    "brands": [],
    "edges": [],
}


def category_pipeline(settings: Settings | None = None) -> SearchPipeline:
    categories = {
        "category_names": NAMES,
        "entities": {
            "x1": {"category": "fashion.shoes", "product_type": "Giày chạy bộ"},
            "x2": {"category": "household.paper", "product_type": "Giấy in A4"},
        },
    }
    # Row 0 is near "giày"-like queries, row 1 near "giấy"-like queries.
    dense = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    return SearchPipeline(GRAPH, dense, None, None, settings=settings, categories=categories)


def plan_for(pipe: SearchPipeline, query: str, vector: list[float]):
    spotting = pipe.spotter.spot(query, np.array(vector, dtype=np.float32))
    return pipe.plan(query, spotting)


def test_lexicon_cue_needs_agreement_from_the_nearest_entity_cards():
    pipe = category_pipeline(Settings(knn_min_sim=0.0, dense_seed_min=2.0))
    # The cue says shoes and the nearest card is the shoe entity: applied.
    agreed = plan_for(pipe, "giày chạy bộ", [1.0, 0.0])
    assert agreed.category and agreed.category.categories == ["fashion.shoes"]
    assert agreed.category_source == "lexicon"
    # The cue says shoes but the nearest card is paper: the cue is dropped (and with the
    # kNN fallback off nothing replaces it).
    strict = category_pipeline(Settings(knn_min_sim=2.0, dense_seed_min=2.0))
    assert plan_for(strict, "giày chạy bộ", [0.0, 1.0]).category is None


def test_knn_fallback_and_out_of_scope_gate_wins():
    pipe = category_pipeline(Settings(knn_min_sim=0.5, knn_min_share=0.6, dense_seed_min=2.0))
    plan = plan_for(pipe, "đôi xăng đan", [1.0, 0.0])  # no lexicon cue
    assert plan.category_source == "knn" and plan.category.categories == ["fashion.shoes"]
    # Far from every card the gate rejects the query, even with a lexicon cue.
    weak = plan_for(pipe, "đôi xăng đan", [0.3, 0.3])
    assert weak.out_of_scope and weak.category is None


def test_trace_reports_the_category_cue():
    pipe = category_pipeline(Settings(knn_min_sim=0.0, dense_seed_min=2.0))
    plan = plan_for(pipe, "giày chạy bộ", [1.0, 0.0])
    trace = pipe._trace(plan, None)
    assert trace["status"] == "category"
    assert trace["category"]["categories"] == [{"id": "fashion.shoes", "label": "Giày dép"}]
