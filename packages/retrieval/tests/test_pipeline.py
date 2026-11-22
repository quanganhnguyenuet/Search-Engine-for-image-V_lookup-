import numpy as np
from qdrant_client import models

from visualseek_retrieval import lexical
from visualseek_retrieval.pipeline import SearchPipeline
from visualseek_retrieval.qdrant_store import EntityBoosts, build_request
from visualseek_retrieval.spotting import EntitySpotter

GRAPH = {
    "fingerprint": "test",
    "entities": [
        {
            "id": "kraken",
            "name": "Tai nghe Razer Kraken",
            "core": "Tai nghe Razer Kraken",
            "brand_id": "brand:razer",
            "aliases": ["kraken", "tai nghe razer kraken"],
        },
        {
            "id": "viper",
            "name": "Chuột Razer Viper",
            "core": "Chuột Razer Viper",
            "brand_id": "brand:razer",
            "aliases": ["chuot razer viper", "viper"],
        },
        {
            "id": "mx3",
            "name": "Chuột Logitech MX Master 3",
            "core": "Chuột Logitech MX Master 3",
            "brand_id": "brand:logitech",
            "aliases": ["chuot logitech mx master 3", "mx master 3"],
        },
        {
            "id": "chay",
            "name": "Bánh chay",
            "core": "Bánh chay",
            "brand_id": None,
            "aliases": ["banh chay"],
        },
        {"id": "ao1", "name": "Áo dài", "core": "Áo dài", "brand_id": None, "aliases": ["ao dai"]},
        {
            "id": "ao2",
            "name": "Áo dài (lụa)",
            "core": "Áo dài",
            "brand_id": None,
            "aliases": ["ao dai", "ao dai lua"],
        },
    ],
    "brands": [
        {"id": "brand:razer", "name": "Razer", "aliases": ["razer"]},
        {"id": "brand:logitech", "name": "Logitech", "aliases": ["logitech"]},
    ],
    "edges": [
        {"src": "kraken", "rel": "MADE_BY", "dst": "brand:razer"},
        {"src": "viper", "rel": "MADE_BY", "dst": "brand:razer"},
        {"src": "mx3", "rel": "MADE_BY", "dst": "brand:logitech"},
    ],
}
DENSE = np.eye(6, dtype=np.float32)


def pipeline() -> SearchPipeline:
    return SearchPipeline(GRAPH, DENSE, encoder=None, client=None)


def test_lexical_keeps_folded_and_accented_forms():
    assert lexical.tokens("Bánh chày") == ["banh", "~bánh", "chay", "~chày"]
    vector = lexical.query_vector("banh banh")
    assert len(vector.indices) == 1 and vector.values == [1.0]
    doc = lexical.document_vector("banh banh chay", avg_length=3)
    assert len(doc.indices) == 2 and max(doc.values) > min(doc.values)


def test_spotting_longest_match_and_accents():
    spotter = EntitySpotter(GRAPH, DENSE)
    found = spotter.spot("chuot logitech mx master 3 va kraken", None)
    assert found.entities == ["mx3", "kraken"]
    # Both sides accented and the tone differs: no link.
    assert spotter.spot("bánh chày", None).entities == []
    assert spotter.spot("banh chay", None).entities == ["chay"]
    assert spotter.spot("đồ Razer", None).brands == ["brand:razer"]


def test_ambiguous_alias_asks_back():
    plan = pipeline().plan("áo dài", EntitySpotter(GRAPH, DENSE).spot("áo dài", None))
    assert plan.clarification and {o["entity_id"] for o in plan.clarification[0]["options"]} == {
        "ao1",
        "ao2",
    }


def test_multi_hop_excludes_the_anchor():
    p = pipeline()
    query = "sản phẩm khác của hãng làm tai nghe Kraken"
    plan = p.plan(query, p.spotter.spot(query, None))
    assert plan.relation and plan.negation
    assert plan.anchors == ["kraken"] and plan.hops == ["viper"] and plan.seeds == []


def test_carve_is_not_negation():
    p = pipeline()
    query = "Chuột Razer Viper chạm khắc"
    plan = p.plan(query, p.spotter.spot(query, None))
    assert not plan.negation and plan.seeds == ["viper"]


def test_multi_intent_counts_intents():
    p = pipeline()
    query = "Chuột Logitech MX Master 3 và Kraken"
    plan = p.plan(query, p.spotter.spot(query, None))
    assert plan.seeds == ["mx3", "kraken"] and plan.intents == 2 and plan.hops == []


def test_request_is_one_query_with_formula_boosts():
    request = build_request(
        [0.1] * 4,
        lexical.query_vector("kraken"),
        EntityBoosts(seeds=["a"], hops=["b"], anchors=["c"]),
        depth=50,
    )
    assert isinstance(request["query"], models.FormulaQuery)
    terms = request["query"].formula.sum
    assert terms[0] == "$score" and len(terms) == 4
    fused = request["prefetch"][0]
    assert isinstance(fused.query, models.FusionQuery) and len(fused.prefetch) == 3
    plain = build_request([0.1] * 4, lexical.query_vector("x"), EntityBoosts())
    assert isinstance(plain["query"], models.FusionQuery) and len(plain["prefetch"]) == 2
