import numpy as np

from visualseek_retrieval.pipeline import SearchPipeline
from visualseek_retrieval.text import normalize

ENRICHED_GRAPH = {
    "fingerprint": "test-enriched",
    "entities": [
        {
            "id": "famo",
            "name": "Famotidin Stada 40",
            "core": "Famotidin Stada 40",
            "brand_id": "brand:stada",
            "aliases": ["famotidin stada 40"],
        },
        {
            "id": "nexium",
            "name": "Nexium Mups 20mg",
            "core": "Nexium Mups 20mg",
            "brand_id": "brand:astrazeneca",
            "aliases": ["nexium", "nexium mups 20mg"],
        },
        {
            "id": "panadol",
            "name": "Panadol Extra",
            "core": "Panadol Extra",
            "brand_id": "brand:gsk",
            "aliases": ["panadol", "panadol extra"],
        },
        {
            "id": "efferalgan",
            "name": "Efferalgan 500mg",
            "core": "Efferalgan 500mg",
            "brand_id": "brand:upsa",
            "aliases": ["efferalgan", "efferalgan 500mg"],
        },
        {
            "id": "piston_ab",
            "name": "Piston Honda Air Blade 125",
            "core": "Piston Honda Air Blade 125",
            "brand_id": "brand:honda",
            "aliases": ["piston honda air blade 125"],
        },
        {
            "id": "noi_ab",
            "name": "Bộ nồi Honda Air Blade genuine",
            "core": "Bộ nồi Honda Air Blade genuine",
            "brand_id": "brand:honda",
            "aliases": ["bo noi honda air blade genuine"],
        },
        {
            "id": "dinh_dong",
            "name": "Đỉnh đồng thờ cúng",
            "core": "Đỉnh đồng thờ cúng",
            "brand_id": None,
            "aliases": ["dinh dong tho cung"],
        },
        {
            "id": "lu_dat",
            "name": "Lư hương đất nung",
            "core": "Lư hương đất nung",
            "brand_id": None,
            "aliases": ["lu huong dat nung"],
        },
        {
            "id": "tuong_phat",
            "name": "Tượng Phật Thích Ca",
            "core": "Tượng Phật Thích Ca",
            "brand_id": None,
            "aliases": ["tuong phat thich ca"],
        },
    ],
    "brands": [
        {"id": "brand:stada", "name": "Stada", "aliases": ["stada"]},
        {"id": "brand:astrazeneca", "name": "AstraZeneca", "aliases": ["astrazeneca"]},
        {"id": "brand:gsk", "name": "GSK", "aliases": ["gsk"]},
        {"id": "brand:upsa", "name": "UPSA", "aliases": ["upsa"]},
        {"id": "brand:honda", "name": "Honda", "aliases": ["honda"]},
    ],
    "edges": [
        {"src": "famo", "rel": "MADE_BY", "dst": "brand:stada"},
        {"src": "nexium", "rel": "MADE_BY", "dst": "brand:astrazeneca"},
        {"src": "panadol", "rel": "MADE_BY", "dst": "brand:gsk"},
        {"src": "efferalgan", "rel": "MADE_BY", "dst": "brand:upsa"},
        {"src": "piston_ab", "rel": "MADE_BY", "dst": "brand:honda"},
        {"src": "noi_ab", "rel": "MADE_BY", "dst": "brand:honda"},
    ],
}

ENRICHED_CATEGORIES = {
    "category_names": {
        "drug.oral": "Tân dược dạng uống",
        "moto.parts": "Phụ tùng xe máy",
        "culture.worship": "Đồ thờ cúng",
        "culture.statue_fengshui": "Tượng & vật phong thủy",
    },
    "entities": {
        "famo": {
            "name": "Famotidin Stada 40",
            "category": "drug.oral",
            "category_l1": "drug",
            "product_type": "Thuốc đau dạ dày",
            "treats": ["dạ dày"],
            "ingredients": ["Famotidin"],
        },
        "nexium": {
            "name": "Nexium Mups 20mg",
            "category": "drug.oral",
            "category_l1": "drug",
            "product_type": "Thuốc trào ngược dạ dày",
            "treats": ["dạ dày"],
            "ingredients": ["Esomeprazol"],
        },
        "panadol": {
            "name": "Panadol Extra",
            "category": "drug.oral",
            "category_l1": "drug",
            "product_type": "Thuốc giảm đau hạ sốt",
            "treats": ["giảm đau hạ sốt"],
            "ingredients": ["Paracetamol"],
        },
        "efferalgan": {
            "name": "Efferalgan 500mg",
            "category": "drug.oral",
            "category_l1": "drug",
            "product_type": "Thuốc giảm đau hạ sốt",
            "treats": ["giảm đau hạ sốt"],
            "ingredients": ["Paracetamol"],
        },
        "piston_ab": {
            "name": "Piston Honda Air Blade 125",
            "category": "moto.parts",
            "category_l1": "moto",
            "product_type": "Piston xe máy",
            "fits": ["Honda Air Blade"],
        },
        "noi_ab": {
            "name": "Bộ nồi Honda Air Blade genuine",
            "category": "moto.parts",
            "category_l1": "moto",
            "product_type": "Bộ nồi xe máy",
            "fits": ["Honda Air Blade"],
        },
        "dinh_dong": {
            "name": "Đỉnh đồng thờ cúng",
            "category": "culture.worship",
            "category_l1": "culture",
            "product_type": "Đỉnh đồng thờ cúng",
            "material": "đồng",
        },
        "lu_dat": {
            "name": "Lư hương đất nung",
            "category": "culture.worship",
            "category_l1": "culture",
            "product_type": "Lư hương",
            "material": "gốm sứ",
        },
        "tuong_phat": {
            "name": "Tượng Phật Thích Ca",
            "category": "culture.statue_fengshui",
            "category_l1": "culture",
            "product_type": "Tượng Phật",
        },
    },
}

DENSE = np.eye(len(ENRICHED_GRAPH["entities"]), dtype=np.float32)


def enriched_pipeline() -> SearchPipeline:
    return SearchPipeline(
        ENRICHED_GRAPH, DENSE, encoder=None, client=None, categories=ENRICHED_CATEGORIES
    )


def test_treats_relation_expands_stomach_drugs_and_excludes_anchor():
    p = enriched_pipeline()
    query = "thuốc khác chữa cùng bệnh với Famotidin"
    plan = p.plan(query, p.spotter.spot(query, None))
    trace = p._trace(plan, None)
    assert plan.relation and plan.relation_name == "TREATS" and plan.negation
    assert plan.anchors == ["famo"]
    assert plan.hops == ["nexium"]
    assert trace["relation"] == "TREATS" and trace["status"] == "expanded"
    assert trace["values"][0]["id"] == "purpose:da-day"
    assert trace["values"][0]["kind"] == "Purpose"
    assert trace["unsupported_cues"] == []


def test_has_ingredient_relation_expands_same_active_ingredient():
    p = enriched_pipeline()
    query = "thuốc khác cùng hoạt chất với Panadol"
    plan = p.plan(query, p.spotter.spot(query, None))
    trace = p._trace(plan, None)
    assert plan.relation and plan.relation_name == "HAS_INGREDIENT" and plan.negation
    assert plan.anchors == ["panadol"]
    assert plan.hops == ["efferalgan"]
    assert trace["relation"] == "HAS_INGREDIENT"
    assert trace["values"][0]["id"] == "ingredient:paracetamol"
    assert trace["values"][0]["kind"] == "Ingredient"


def test_fits_relation_resolves_parts_fitting_vehicle_model():
    p = enriched_pipeline()
    query = "phụ tùng lắp được cho xe Honda Air Blade"
    plan = p.plan(query, p.spotter.spot(query, None))
    trace = p._trace(plan, None)
    assert plan.relation and plan.relation_name == "FITS"
    assert set(plan.hops) == {"piston_ab", "noi_ab"}
    assert trace["relation"] == "FITS"
    assert trace["values"][0]["id"] == "model:honda-air-blade"
    assert trace["values"][0]["kind"] == "TargetModel"


def test_material_constraint_penalises_conflicting_materials_in_category_segment():
    p = enriched_pipeline()
    query = "tượng phật phong thủy và đồ thờ cúng bằng đồng"
    plan = p.plan(query, p.spotter.spot(query, None))
    assert plan.category_groups == ["culture.statue_fengshui", "culture.worship"]
    assert "lu_dat" in plan.anchors
    assert "dinh_dong" not in plan.anchors


def _scoped_pipeline() -> SearchPipeline:
    def entity(eid: str, name: str) -> dict:
        folded = normalize(name)
        return {"id": eid, "name": name, "core": name, "brand_id": None, "aliases": [folded]}

    def info(category: str, ptype: str, **extra) -> dict:
        return {
            "name": ptype,
            "category": category,
            "category_l1": category.split(".")[0],
            "product_type": ptype,
            **extra,
        }

    ids = {
        "americano": ("Americano Starbucks", info("food.coffee_tea", "Cà phê pha sẵn")),
        "latte": ("Latte Highlands", info("food.coffee_tea", "Cà phê pha sẵn")),
        "scrub": ("Scrub Cocoon", info("beauty.skincare", "Tẩy da chết")),
        "quao": ("Gốm Quao", info("culture.craft", "Gốm thủ công", origin="Bến Tre")),
        "keo": ("Kẹo gừng Thành Long", info("food.cake_snack", "Đặc sản kẹo", origin="Bến Tre")),
        "becolugel": ("Becolugel", info("drug.oral", "Thuốc dạ dày", origin="Bến Tre")),
        "ngong": ("Ngỗng Rheinland", info("food.dish", "Món ngỗng", origin="Đức")),
        "wurst": ("Bratwurst Đức", info("food.dish", "Món xúc xích", origin="Đức")),
        "vali": ("Vali Samsonite", info("fashion.bags", "Vali kéo", origin="Đức")),
    }
    for key in ("americano", "latte", "scrub"):
        ids[key][1]["ingredients"] = ["Cà phê"]
    graph = {
        "fingerprint": "test-scoped",
        "entities": [entity(k, v[0]) for k, v in ids.items()],
        "brands": [],
        "edges": [],
    }
    categories = {
        "category_names": {v[1]["category"]: v[1]["category"] for v in ids.values()},
        "entities": {k: v[1] for k, v in ids.items()},
    }
    return SearchPipeline(
        graph, np.eye(len(ids), dtype=np.float32), encoder=None, client=None, categories=categories
    )


def _hops(query: str) -> tuple[SearchPipeline, object]:
    p = _scoped_pipeline()
    return p, p.plan(query, p.spotter.spot(query, None))


def test_ingredient_relation_stays_in_the_anchors_category():
    _, plan = _hops("món khác cùng thành phần với Americano Starbucks")
    assert plan.relation_name == "HAS_INGREDIENT"
    assert plan.hops == ["latte"]  # the body scrub shares "cà phê" but is not a dish


def test_origin_relation_takes_the_category_the_query_names():
    _, plan = _hops("đặc sản khác cùng vùng với Gốm Quao")
    assert plan.relation_name == "ORIGIN"
    assert plan.hops == ["keo"]  # not the craft itself, and not the drug of a Bến Tre firm


def test_region_cue_does_not_expand_over_a_country():
    _, region = _hops("món khác cùng vùng với Ngỗng Rheinland")
    assert region.relation_name != "ORIGIN"
    _, origin = _hops("món khác cùng xuất xứ với Ngỗng Rheinland")
    assert origin.relation_name == "ORIGIN"
    assert origin.hops == ["wurst"]  # the suitcase is German too, but not a dish


def test_purpose_buckets_keep_cancer_out_of_organ_buckets():
    from visualseek_retrieval.enrichment import purpose_buckets

    assert purpose_buckets(["viêm loét dạ dày"]) == ["dạ dày"]
    assert purpose_buckets(["ung thư dạ dày", "ung thư vú"]) == []


def test_origin_province_is_kept_for_specialties_only():
    from visualseek_retrieval.enrichment import origin_allowed, place_scope

    assert origin_allowed("Bến Tre", "food") and origin_allowed("Bát Tràng", "culture")
    assert not origin_allowed("Bến Tre", "drug")  # the manufacturer's address
    assert origin_allowed("Ấn Độ", "drug") and place_scope("Ấn Độ") == "country"
    assert not origin_allowed("", "food")
