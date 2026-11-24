import numpy as np

from visualseek_retrieval.short_aliases import build, candidate, suppressed
from visualseek_retrieval.spotting import EntitySpotter
from visualseek_retrieval.text import normalize

BRANDS = {("pharmedic",), ("stada",), ("dell",)}


def test_candidate_trims_only_the_ends():
    assert candidate("Colflox 0.3% Pharmedic", BRANDS) == "Colflox"
    assert candidate("Telilsartan Stada 40 mg", BRANDS) == "Telilsartan"
    assert candidate("Dell Alienware x17", BRANDS) == "Alienware"
    assert candidate("Lexapro 10mg (Escitalopram)", BRANDS) == "Lexapro"
    # Middle words stay: folded "vi" is also "vỉ", but only the accented pack word is trimmed.
    assert candidate("Vòng cổ định vị PetPace", BRANDS) is None
    assert candidate("Nexium", BRANDS) is None


def graph(*entities):
    return {
        "entities": [
            {"id": i, "name": n, "core": n, "aliases": [normalize(n), *extra]}
            for i, n, extra in entities
        ],
        "brands": [
            {"id": "brand:stada", "name": "Stada", "aliases": ["stada"]},
            {"id": "brand:pharmedic", "name": "Pharmedic", "aliases": ["pharmedic"]},
        ],
    }


def texts(g):
    return {e["id"]: normalize(" ".join([e["name"], *e["aliases"]])) for e in g["entities"]}


def test_build_accepts_unique_and_rejects_shared_short_names():
    g = graph(
        ("colflox", "Colflox 0.3% Pharmedic", []),
        ("aug1", "Augbactam 562,5", []),
        ("aug2", "Augbactam 312,5", []),
    )
    result = build(g, lambda e: e, {}, texts(g))
    assert result["aliases"] == {"colflox": ["Colflox"]}
    assert {(r["entity_id"], r["reason"]) for r in result["rejected"]} == {
        ("aug1", "shared_short_name"),
        ("aug2", "shared_short_name"),
    }


def test_build_rejects_alias_collisions_and_text_shared_with_other_entities():
    g = graph(("a", "Famotidin Stada 40", []), ("b", "Famotidine Stada", ["famotidin stada"]))
    existing = {"famotidin": {"b"}}
    rejected = build(g, lambda e: e, existing, texts(g))["rejected"]
    assert rejected[0]["reason"] == "collides_with_alias"
    g = graph(("a", "Hydrus 20W", []), ("b", "Củ sạc khác", []))
    t = texts(g)
    t["b"] += " hydrus"
    assert build(g, lambda e: e, {}, t)["rejected"][0]["reason"] == "no_distinctive_token"


def test_suppressed_drops_derived_aliases_other_entities_use():
    g = graph(("knife", "Dao thái thịt Tojiro Nhật", ["nhat"]), ("card", "Thiệp sinh nhật", []))
    blocked = suppressed(g, lambda e: e, texts(g))
    assert blocked["aliases"] == {"knife": ["nhat"]}
    spotter = EntitySpotter(g, np.zeros((2, 1)), suppressed_aliases=blocked["aliases"])
    assert spotter.spot("thiệp sinh nhật", None).entities == ["card"]
    assert spotter.spot("Dao thái thịt Tojiro Nhật", None).entities == ["knife"]
    with_short = EntitySpotter(g, np.zeros((2, 1)), short_aliases={"knife": ["Tojiro"]})
    assert with_short.spot("dao tojiro", None).entities == ["knife"]
