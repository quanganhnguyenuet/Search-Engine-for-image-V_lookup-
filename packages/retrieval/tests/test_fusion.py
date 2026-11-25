import numpy as np
import pytest

from visualseek_retrieval.fusion import (
    FusionSettings,
    VisualFusion,
    fuse,
    has_visual_attribute,
    zscore,
)


@pytest.mark.parametrize(
    "query",
    [
        "chuột máy tính màu trắng",
        "ghế gỗ",
        "bình nước bằng inox",
        "sản phẩm họa tiết caro",
        "đồng hồ treo tường hình tròn",
        "đồng hồ đeo tay dây da",
    ],
)
def test_visual_attribute_detected(query):
    assert has_visual_attribute(query)


@pytest.mark.parametrize(
    "query",
    [
        "máy lọc nước",
        "thuốc kháng sinh",
        "đồng hồ thông minh",
        "sữa dưỡng da",
        "chuot mau trang",
        "",
    ],
)
def test_object_names_are_not_attributes(query):
    assert not has_visual_attribute(query)


def test_zscore_constant_is_zero():
    assert not zscore(np.array([0.5, 0.5, 0.5])).any()


def test_fuse_alpha_extremes_follow_one_channel():
    text, img = np.array([0.9, 0.1, 0.5]), np.array([0.1, 0.9, 0.5])
    assert np.argmax(fuse(text, img, 1.0)) == 0
    assert np.argmax(fuse(text, img, 0.0)) == 1


def test_rank_unions_image_candidates_and_uses_both_scores():
    ids = ["a", "b", "c"]
    doc = np.eye(3, dtype=np.float32)
    img = np.array([[1, 0], [0, 1], [0.6, 0.8]], dtype=np.float32)
    fusion = VisualFusion(ids, doc, img, FusionSettings(img_candidates=1, alpha_attribute=0.0))
    # "b" is not a text candidate but is the image top-1, so it enters and wins on looks.
    ranked = fusion.rank("áo màu đỏ", ["a", "c"], doc[0], np.array([0, 1], dtype=np.float32))
    assert [a for a, _ in ranked] == ["b", "c", "a"]
    assert fusion.candidates(["a", "zz"], np.array([1, 0], dtype=np.float32), 0) == ["a"]


def test_row_count_mismatch_raises():
    with pytest.raises(ValueError):
        VisualFusion(["a"], np.zeros((2, 3)), np.zeros((1, 2)))


def test_pipeline_wires_visual_fusion_on_descriptive_queries_only():
    from qdrant_client import QdrantClient, models

    from visualseek_retrieval import lexical
    from visualseek_retrieval.pipeline import SearchPipeline, Settings

    graph = {
        "fingerprint": "test",
        "entities": [
            {
                "id": "e1",
                "name": "Chuột Razer Viper",
                "core": "Chuột Razer Viper",
                "brand_id": "brand:razer",
                "aliases": ["chuot razer viper", "viper"],
            },
            {
                "id": "e2",
                "name": "Bàn gỗ sồi",
                "core": "Bàn gỗ sồi",
                "brand_id": None,
                "aliases": ["ban go soi"],
            },
        ],
        "brands": [{"id": "brand:razer", "name": "Razer", "aliases": ["razer"]}],
        "edges": [{"src": "e1", "rel": "MADE_BY", "dst": "brand:razer"}],
    }
    entity_dense = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    doc_dense = np.array([[0.8, 0.2], [0.7, 0.3]], dtype=np.float32)
    img_dense = np.array([[0.1, 0.9], [0.95, 0.05]], dtype=np.float32)
    client = QdrantClient(location=":memory:", force_disable_check_same_thread=True)
    client.create_collection(
        "test",
        vectors_config={"doc": models.VectorParams(size=2, distance=models.Distance.COSINE)},
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    for idx, (aid, eid, vec) in enumerate([("a1", "e1", doc_dense[0]), ("a2", "e2", doc_dense[1])]):
        sp = lexical.query_vector("bàn gỗ màu nâu")
        client.upsert(
            "test",
            points=[
                models.PointStruct(
                    id=idx,
                    vector={
                        "doc": vec.tolist(),
                        "sparse": models.SparseVector(indices=sp.indices, values=sp.values),
                    },
                    payload={
                        "asset_id": aid,
                        "entity_ids": [eid],
                        "categories": ["home.furniture"],
                    },
                )
            ],
        )

    class TextEnc:
        def encode(self, texts):
            return np.array([[0.6, 0.6] for _ in texts], dtype=np.float32)

    class ImgEnc:
        def encode(self, texts):
            return np.array([[1.0, 0.0] for _ in texts], dtype=np.float32)

    fusion = VisualFusion(
        ["a1", "a2"],
        doc_dense,
        img_dense,
        FusionSettings(text_candidates=10, img_candidates=2, alpha_attribute=0.1, alpha_plain=0.1),
    )
    pipe = SearchPipeline(
        graph,
        entity_dense,
        TextEnc(),
        client,
        "test",
        settings=Settings(dense_seed_min=2.0, out_of_scope_max=0.0),
        image_encoder=ImgEnc(),
        fusion=fusion,
    )
    desc = pipe.search("bàn gỗ màu nâu")
    assert desc["trace"]["channels"]["img"] == "ready"
    assert desc["results"][0]["asset_id"] == "a2"
    assert desc["results"][0]["scores"]["img"] is not None

    named = pipe.search("Chuột Razer Viper")
    assert named["trace"]["channels"]["img"] == "inactive"
    assert named["results"][0]["scores"]["img"] is None

    # Two objects: one image vector would lean to one of them, so the query stays on text.
    two = pipe.search("bàn gỗ màu nâu và ghế nhựa trắng")
    assert two["trace"]["channels"]["img"] == "inactive"


def test_an_attribute_phrase_starts_with_the_attribute():
    from visualseek_retrieval.fusion import starts_with_visual_attribute

    assert starts_with_visual_attribute("màu trắng")
    assert starts_with_visual_attribute("dây da")
    assert not starts_with_visual_attribute("bàn phím màu trắng")
    assert not starts_with_visual_attribute("dây chuyền vàng")
    assert not starts_with_visual_attribute("đồng hồ dây da")


def test_asset_payloads_carry_the_categories_of_canonical_entities():
    from visualseek_retrieval.entity_review import EntityReview
    from visualseek_retrieval.fusion import asset_payloads

    rows = [
        {"asset_id": "a1", "entity_ids": ["dup"], "brand_ids": [], "image_path": "x.jpg"},
        {"asset_id": "a2", "entity_ids": ["bag"], "brand_ids": ["brand:b"], "image_path": "y"},
    ]
    categories = {
        "entities": {
            "shoe": {"category": "fashion.shoes", "category_l1": "fashion", "market_tier": "mass"},
            "bag": {"category": "household.kitchenware", "category_l1": "household"},
        }
    }
    out = asset_payloads(rows, EntityReview(canonical_ids={"dup": "shoe"}), categories)
    assert out["a1"]["categories"] == ["fashion.shoes"] and out["a1"]["market_tiers"] == ["mass"]
    assert out["a1"]["entity_ids"] == ["dup"]  # the payload keeps the corpus ids
    assert out["a2"]["categories"] == ["household.kitchenware"] and out["a2"]["market_tiers"] == []


def test_load_visual_needs_both_vector_files(tmp_path):
    from visualseek_retrieval.fusion import load_visual

    assert load_visual(tmp_path, ["a1"], "cpu") == (None, None)
