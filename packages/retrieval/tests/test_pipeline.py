import numpy as np
from qdrant_client import QdrantClient, models

from visualseek_retrieval import lexical
from visualseek_retrieval.entity_review import EntityReview
from visualseek_retrieval.pipeline import SearchPipeline
from visualseek_retrieval.qdrant_store import EntityBoosts, build_request, point_id
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


def test_multi_hop_trace_reports_evidence_and_ranking_scores():
    graph = {
        **GRAPH,
        "edges": [
            {**edge, "source": "name", "confidence": 0.7, "reviewed": False}
            for edge in GRAPH["edges"]
        ],
    }
    client = QdrantClient(location=":memory:", force_disable_check_same_thread=True)
    client.create_collection(
        "test",
        vectors_config={"doc": models.VectorParams(size=6, distance=models.Distance.COSINE)},
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    query = "sản phẩm khác của hãng làm tai nghe Kraken"
    sparse = lexical.query_vector(query)
    client.upsert(
        "test",
        points=[
            models.PointStruct(
                id=index,
                vector={
                    "doc": vector.tolist(),
                    "sparse": models.SparseVector(indices=sparse.indices, values=sparse.values),
                },
                payload={"asset_id": eid, "entity_ids": [eid], "brand_ids": ["brand:razer"]},
            )
            for index, (eid, vector) in enumerate(zip(("kraken", "viper"), DENSE[:2], strict=True))
        ],
    )

    class Encoder:
        def encode(self, _):
            return DENSE[:1]

    response = SearchPipeline(graph, DENSE, Encoder(), client, "test").search(query)
    trace = response["trace"]
    assert trace["relation"] == "MADE_BY" and trace["status"] == "expanded"
    assert trace["origins"][0]["id"] == "kraken"
    assert trace["targets"][0]["id"] == "viper"
    assert trace["excluded"][0]["id"] == "kraken"
    assert trace["values"][0]["label"] == "Razer"
    assert trace["values"][0]["origin_edges"][0]["evidence"] == [
        {"field": "name", "text": "Tai nghe Razer Kraken"}
    ]
    assert trace["values"][0]["target_edges"][0]["entity_id"] == "viper"
    hit = response["results"][0]
    assert hit["asset_id"] == "viper"
    assert hit["scores"]["dense"]["rank"] == 2
    assert hit["scores"]["sparse"]["score"] > 0
    assert hit["scores"]["graph_prefetch"]["rank"] == 1
    assert hit["scores"]["graph"] == 1.0
    assert hit["scores"]["img"] is None and trace["channels"]["img"] == "inactive"
    assert hit["score"] == hit["scores"]["rrf"] + hit["scores"]["graph"]


def test_review_collapses_already_indexed_aliases_and_blocks_rejected_images():
    review = EntityReview(
        canonical_ids={"ao2": "ao1"},
        members={"ao1": ["ao2"]},
        rejected_labels={"bad": {"ao2"}, "shared": {"ao2"}},
        blocked_assets=["bad"],
    )
    spotter = EntitySpotter(GRAPH, DENSE, review)
    query = "áo dài"
    assert spotter.spot(query, None).entities == ["ao1"]
    assert not spotter.spot(query, None).ambiguous
    assert sum(eid == "ao1" for eid, _ in spotter.spot(query, DENSE[5], 6).dense_top) == 1
    assert review.indexed_ids(["ao1"]) == ["ao1", "ao2"]
    assert review.result_ids("valid", ["ao2"]) == ["ao1"]
    assert review.result_ids("shared", ["ao2", "viper"]) == ["viper"]
    request = build_request(
        DENSE[5].tolist(),
        lexical.query_vector(query),
        EntityBoosts(),
        blocked_assets=review.blocked_assets,
    )
    assert request["query_filter"].must_not[0].has_id == [point_id("bad")]


def test_trace_reports_unsupported_relation_and_ignored_negation():
    p = pipeline()
    query = "Ngoài Chuột Razer Viper, còn thuốc nào điều trị cùng bệnh không"
    plan = p.plan(query, p.spotter.spot(query, None))
    trace = p._trace(plan, None)
    assert [c["relation"] for c in trace["unsupported_cues"]] == ["TREATS"]
    assert trace["unsupported_cues"][0]["cue"] == "điều trị cùng"
    assert trace["status"] == "unsupported_relation"
    # The named entity stays a boosted seed because there is no target set to exclude it from.
    assert plan.seeds == ["viper"]
    assert trace["negation"] == {"cue": "ngoài", "applied": False, "reason": "no_targets"}


def test_trace_marks_applied_negation_and_folded_cues():
    p = pipeline()
    query = "sản phẩm khác của hãng làm tai nghe Kraken"
    trace = p._trace(p.plan(query, p.spotter.spot(query, None)), None)
    assert trace["negation"] == {"cue": "khác", "applied": True, "reason": None}
    assert trace["unsupported_cues"] == [] and trace["status"] == "expanded"
    folded = "thuoc cung hoat chat voi kraken"
    plan = p.plan(folded, p.spotter.spot(folded, None))
    assert [c["relation"] for c in plan.unsupported_cues] == ["HAS_INGREDIENT"]
    assert p._negation_trace(plan) is None


def test_merge_groups_counts_a_merged_entity_once():
    from types import SimpleNamespace as NS

    from visualseek_retrieval.pipeline import merge_groups

    def hit(pid, score):
        return NS(id=pid, score=score)

    groups = [
        NS(id="mx3", hits=[hit("a", 0.9), hit("b", 0.8)]),
        NS(id="ao2", hits=[hit("c", 0.85), hit("d", 0.7)]),  # merged into ao1
        NS(id="ao1", hits=[hit("c", 0.85), hit("e", 0.88)]),
        NS(id="viper", hits=[hit("f", 0.6)]),
    ]
    canonical = {"ao2": "ao1"}.get
    out = merge_groups(groups, lambda e: canonical(e, e), size=2)
    assert [h.id for h in out] == ["a", "b", "e", "c", "f"]
    # Target groups first; an image already taken does not fill a second slot.
    groups.append(NS(id="kraken", hits=[hit("a", 0.95), hit("g", 0.5)]))
    out = merge_groups(groups, lambda e: canonical(e, e), size=2, targets={"kraken", "ao1"})
    assert [h.id for h in out] == ["e", "c", "a", "g", "b", "f"]


def test_relation_without_other_products_returns_no_images():
    review = EntityReview(canonical_ids={"viper": "kraken"}, members={"kraken": ["viper"]})
    p = SearchPipeline(
        GRAPH,
        DENSE,
        encoder=type("E", (), {"encode": staticmethod(lambda texts: DENSE[:1])})(),
        client=None,  # any Qdrant call would fail: the search must stop before it
        review=review,
    )
    response = p.search("sản phẩm khác của hãng làm tai nghe Kraken")
    assert response["results"] == []
    assert response["trace"]["status"] == "no_target"
    assert response["trace"]["values"][0]["label"] == "Razer"
