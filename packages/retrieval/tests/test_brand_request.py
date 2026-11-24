"""P1/P2/P4 on a tiny catalogue: what a brand query asks of the brand's own products."""

import dataclasses

import numpy as np

from visualseek_retrieval.pipeline import SearchPipeline, Settings


def product(entity_id, name, brand, aliases, product_type, category):
    entity = {
        "id": entity_id,
        "name": name,
        "core": name,
        "brand_id": brand,
        "aliases": aliases,
    }
    label = {"name": name, "product_type": product_type, "category": category}
    return entity, label


ITEMS = [
    product(
        "bugi",
        "Bugi Bosch Super Plus",
        "brand:bosch",
        ["bugi bosch super plus"],
        "Bugi ô tô",
        "car.fluids_electrical",
    ),
    product(
        "acquy",
        "Ắc-quy Bosch AGM",
        "brand:bosch",
        ["ac quy bosch agm"],
        "Ắc quy ô tô",
        "car.fluids_electrical",
    ),
    product(
        "locdau",
        "Lọc dầu Bosch Premium",
        "brand:bosch",
        ["loc dau bosch premium"],
        "Lọc dầu ô tô",
        "car.parts",
    ),
    product(
        "catco",
        "Máy cắt cỏ Bosch Universal",
        "brand:bosch",
        ["may cat co bosch universal"],
        "Máy cắt cỏ",
        "industrial.agri_machinery",
    ),
    product(
        "mary",
        "Schwalbe Magic Mary",
        "brand:schwalbe",
        ["magic mary", "schwalbe magic mary"],
        "Lốp xe đạp",
        "bike.tyres_wheels",
    ),
    product(
        "rob",
        "Schwalbe Rapid Rob",
        "brand:schwalbe",
        ["rapid rob", "schwalbe rapid rob"],
        "Lốp xe đạp",
        "bike.tyres_wheels",
    ),
    product(
        "mobil",
        "Mobil Super 1000",
        "brand:mobil",
        ["mobil super 1000"],
        "Dầu nhớt ô tô",
        "car.fluids_electrical",
    ),
    product(
        "chuyen",
        "Dây chuyền vàng PNJ",
        "brand:pnj",
        ["day chuyen vang pnj"],
        "Dây chuyền vàng",
        "fashion.jewelry",
    ),
    # Two PNJ names start with "Nhẫn": a product type word, not a product line.
    product(
        "nhan1",
        "Nhẫn PNJ vàng tây",
        "brand:pnj",
        ["nhan pnj vang tay"],
        "Nhẫn vàng",
        "fashion.jewelry",
    ),
    product(
        "nhan2",
        "Nhẫn kim cương PNJ",
        "brand:pnj",
        ["nhan kim cuong pnj"],
        "Nhẫn kim cương",
        "fashion.jewelry",
    ),
    # Two Xiaomi names start with "Redmi": a real product line.
    product(
        "redmi1", "Redmi Note 11", "brand:xiaomi", ["redmi note 11"], "Điện thoại", "tech.phone"
    ),
    product("redmi2", "Redmi 13C", "brand:xiaomi", ["redmi 13c"], "Điện thoại", "tech.phone"),
    product(
        "bep",
        "Bếp gas dã ngoại",
        None,
        ["bep gas da ngoai"],
        "Bếp gas dã ngoại",
        "outdoor.camping",
    ),
    product("dao", "Dao làm bếp", None, ["dao lam bep"], "Dao làm bếp", "household.kitchenware"),
    product(
        "maychieu", "Máy chiếu mini", None, ["may chieu mini"], "Máy chiếu mini", "tech.display"
    ),
    product(
        "dongho", "Đồng hồ Orient", None, ["dong ho orient"], "Đồng hồ dây da", "fashion.watch"
    ),
    # Canon makes cameras and printer cartridges, no printer; a printer of another make.
    product(
        "powershot",
        "Canon PowerShot SX740",
        "brand:canon",
        ["canon powershot sx740"],
        "Máy ảnh compact",
        "tech.camera",
    ),
    product(
        "toner",
        "Hộp mực Canon 046",
        "brand:canon",
        ["hop muc canon 046"],
        "Hộp mực máy in",
        "office.supply",
    ),
    product(
        "laser", "Máy in Brother HL", None, ["may in brother hl"], "Máy in laser", "tech.printer"
    ),
    # A diamond ring without a brand: the spotter finds it inside "không phải nhẫn kim cương".
    product(
        "kimcuong",
        "Nhẫn kim cương",
        None,
        ["nhan kim cuong"],
        "Nhẫn kim cương",
        "fashion.jewelry",
    ),
    # Names with "&" and "," in them: the spotter reads them whole, the separators are theirs.
    product(
        "lock",
        "Bình nước Lock&Lock Inox 600ml",
        None,
        ["binh nuoc lock lock inox 600ml"],
        "Bình nước",
        "household.kitchenware",
    ),
    product(
        "indapamid",
        "Indapamid DWP 2,5mg",
        None,
        ["indapamid dwp 2 5mg"],
        "Thuốc huyết áp",
        "drug.oral",
    ),
]
GRAPH = {
    "fingerprint": "test",
    "entities": [entity for entity, _ in ITEMS],
    "brands": [
        {"id": f"brand:{name}", "name": name.capitalize(), "aliases": [name]}
        for name in ("bosch", "schwalbe", "mobil", "pnj", "xiaomi", "canon")
    ],
    "edges": [
        {"src": entity["id"], "rel": "MADE_BY", "dst": entity["brand_id"]}
        for entity, _ in ITEMS
        if entity["brand_id"]
    ],
}
CATEGORIES = {
    "category_names": {},
    "entities": {entity["id"]: label for entity, label in ITEMS},
}


def plan(query: str, **settings):
    pipeline = SearchPipeline(
        GRAPH,
        np.eye(len(ITEMS), dtype=np.float32),
        encoder=None,
        client=None,
        settings=dataclasses.replace(Settings(), **settings),
        # The global spotter drops "magic mary" (as build_short_aliases.py does for aliases
        # shared across the catalogue); inside its brand it is still a name.
        suppressed_aliases={"mary": ["magic mary"]},
        categories=CATEGORIES,
    )
    return pipeline.plan(query, pipeline.spotter.spot(query, None))


def test_each_clause_asks_its_own_brand_for_its_own_type():
    p = plan("bugi Bosch và nhớt Mobil")
    assert sorted(p.hops) == ["bugi", "mobil"]


def test_expansion_brings_the_category_of_the_named_type():
    # Off by default: the battery shares the spark plug's category but is not a spark plug.
    p = plan("bugi Bosch", type_expand=True)
    assert sorted(p.hops) == ["acquy", "bugi"]


def test_short_name_inside_the_brand_is_excluded():
    p = plan("lốp Schwalbe khác ngoài Magic Mary")
    assert p.hops == ["rob"]
    assert p.anchors == ["mary"]
    assert p.type_requests[0]["named"] == ["Schwalbe Magic Mary"]


def test_a_clause_without_brand_belongs_to_the_brand_that_makes_it():
    p = plan("Bosch có bugi và lọc dầu nào", type_expand=False)
    assert sorted(p.hops) == ["bugi", "locdau"]


def test_a_generic_clause_is_not_lent_to_a_brand_that_does_not_make_it():
    p = plan("bugi Bosch và dây chuyền vàng", type_expand=False)
    assert p.hops == ["bugi"]
    assert p.type_requests[0]["include"] == ["bugi"]


def test_excluded_type_after_a_contrast_belongs_to_the_previous_brand():
    p = plan("đồ Bosch nhưng không phải máy cắt cỏ")
    assert "catco" in p.anchors and "catco" not in p.hops
    assert {"bugi", "acquy", "locdau"} <= set(p.hops)


def test_runs_of_one_clause_narrow_each_other():
    # "ô tô" alone hits three Bosch products; with "lọc" in the same clause only one is meant.
    p = plan("lọc cho ô tô của Bosch", type_expand=False)
    assert p.hops == ["locdau"]


def test_filter_off_keeps_the_whole_brand():
    p = plan("bugi Bosch", type_filter=False)
    assert sorted(p.hops) == ["acquy", "bugi", "catco", "locdau"]
    assert p.type_requests == []


def test_brand_query_shows_only_its_targets_and_named_query_fills_by_category():
    from types import SimpleNamespace

    pipeline = SearchPipeline(
        GRAPH,
        np.eye(len(ITEMS), dtype=np.float32),
        encoder=None,
        client=None,
        categories=CATEGORIES,
    )

    def point(entity_id, categories):
        return SimpleNamespace(payload={"entity_ids": [entity_id], "categories": categories})

    points = [
        point("rob", ["bike.tyres_wheels"]),
        point("other-tyre", ["bike.tyres_wheels"]),  # same type, another brand
        point("mary", ["bike.tyres_wheels"]),
        point("wiper", ["car.accessory"]),
    ]

    def kept(query):
        found = pipeline._related(plan(query), points)
        return [x.payload["entity_ids"][0] for x in found]

    # Brand query: only the brand's requested products, no filler at all.
    assert kept("lốp Schwalbe khác ngoài Magic Mary") == ["rob"]
    # Named product: filler of its category, never an unrelated one.
    assert kept("Rapid Rob") == ["rob", "other-tyre", "mary"]


def test_relation_anchor_descriptor_excludes_the_anchor_product():
    p = plan("những sản phẩm khác của hãng làm máy cắt cỏ Bosch")
    assert p.origin_ids == ["catco"]
    assert p.anchors == ["catco"]
    assert sorted(p.hops) == ["acquy", "bugi", "locdau"]


def test_mixed_query_populates_generic_hits_and_splits_after_voi():
    p1 = plan("bugi Bosch và dây chuyền vàng", type_expand=False)
    assert p1.hops == ["bugi"]
    assert p1.generic_hits == ["chuyen"]
    assert p1.intents == 2
    p2 = plan("Bugi Bosch Super Plus với dây chuyền vàng", type_expand=False)
    assert p2.seeds == ["bugi"]
    assert p2.generic_hits == ["chuyen"]
    assert p2.intents == 2


def test_only_real_product_lines_stand_for_their_brand():
    pipeline = SearchPipeline(
        GRAPH, np.eye(len(ITEMS), dtype=np.float32), None, None, categories=CATEGORIES
    )
    assert pipeline.product_lines == {"redmi": "brand:xiaomi"}
    p = plan("Bugi Bosch Super Plus và nhẫn bạc")
    assert "brand:pnj" not in p.value_ids
    assert p.hops == []
    # Rings of any brand, by the head word.
    assert sorted(p.generic_hits) == ["kimcuong", "nhan1", "nhan2"]


def test_a_visual_attribute_after_voi_describes_the_entity():
    p = plan("Bugi Bosch Super Plus với dây da")
    assert p.generic_hits == []
    assert p.intents == 1


def test_a_catalogue_wide_clause_needs_a_phrase_or_its_head_word():
    # "bếp mini": no product type holds the phrase; "mini" alone would bring the projector,
    # "bếp" anywhere the kitchen knife. Only types that start with the head word count.
    p = plan("Bugi Bosch Super Plus và bếp mini")
    assert p.generic_hits == ["bep"]


def test_a_generic_clause_is_lent_only_on_a_head_match():
    # "máy in" is only a modifier in Canon's "Hộp mực máy in": printers of any make instead.
    p = plan("máy ảnh Canon và máy in")
    assert p.hops == ["powershot"]
    assert p.generic_hits == ["laser"]


def test_a_type_the_brand_does_not_make_leaves_no_product_of_it():
    p = plan("máy in Canon")
    assert p.hops == []
    assert p.no_target
    assert p.type_requests[0]["status"] == "absent"
    # Words that name no product type anywhere narrow nothing.
    p = plan("đồ Canon xịn")
    assert sorted(p.hops) == ["powershot", "toner"]
    assert not p.no_target


def test_a_brandless_entity_in_an_exclusion_is_read_as_the_brand_type():
    p = plan("nhẫn đính hôn của PNJ nhưng không phải nhẫn kim cương")
    assert "nhan2" in p.anchors  # PNJ's diamond ring
    assert "nhan2" not in p.hops
    assert "nhan1" in p.hops


def test_an_entity_name_with_a_separator_is_not_cut_into_generic_intents():
    p = plan("bình nước lock&lock inox 600ml")
    assert p.seeds == ["lock"]
    assert p.generic_hits == [] and p.intents == 1
    # The comma inside "2,5mg" is not a list comma; the one after it is.
    p = plan("Indapamid DWP 2,5mg, Bugi Bosch Super Plus")
    assert sorted(p.seeds) == ["bugi", "indapamid"]
    assert p.generic_hits == [] and p.intents == 2
    # A real "&" between two things still separates them.
    p = plan("Bugi Bosch Super Plus & dây chuyền vàng", type_expand=False)
    assert p.generic_hits == ["chuyen"] and p.intents == 2
