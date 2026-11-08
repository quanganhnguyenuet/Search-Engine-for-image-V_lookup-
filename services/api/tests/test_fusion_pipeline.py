import hashlib
import json
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image
from test_contextual import DocumentEncoder, call_model, metadata_artifact

from visualseek_api.application.query_understanding import QueryPlan, QueryUnderstanding
from visualseek_api.application.search import SearchService
from visualseek_api.core.config import Settings
from visualseek_api.core.errors import ApplicationError
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json
from visualseek_retrieval.hybrid import FusionConfig, HybridRetriever
from visualseek_retrieval.relevance import RelevancePolicy, apply_relevance
from visualseek_retrieval.text import normalize


@pytest.fixture
def index(tmp_path):
    return ArtifactIndex(metadata_artifact(tmp_path / "artifact"))


def test_postings_preserve_old_scoring_with_repeated_tokens_fields_and_accents(index):
    retriever = HybridRetriever(index)
    for terms in (["áo áo dài đỏ"], ["ao dai", "xanh"], [], ["missing"], ["Evidence 2"]):
        tokens = set(normalize(" ".join(terms)).split())
        expected = np.asarray(
            [
                sum(
                    weight * len(tokens.intersection(field.split()))
                    for weight, field in zip((3, 2, 1), parts, strict=True)
                )
                for parts in retriever.fields
            ]
        )
        assert np.array_equal(retriever.lexical_scores(terms), expected)
    first = retriever._lexical_postings
    retriever.prepare()
    assert retriever._lexical_postings is first


def test_weighted_channels_and_candidate_membership(index):
    retriever = HybridRetriever(index)
    query = np.array([[0, 1, 0]], dtype=np.float32)
    scores = retriever.channel_scores(query, ["áo dài đỏ"])
    visual = retriever.rank(
        scores,
        category=None,
        limit=3,
        config=FusionConfig(image_weight=1, metadata_weight=0, lexical_weight=0),
    )
    text = retriever.rank(
        scores,
        category=None,
        limit=3,
        config=FusionConfig(image_weight=0, metadata_weight=1, lexical_weight=0),
    )
    assert visual[0]["asset"]["asset_id"] == "2" * 64
    assert text[0]["asset"]["asset_id"] == "1" * 64
    lexical = retriever.rank(
        scores,
        category=None,
        limit=3,
        config=FusionConfig(image_weight=0, metadata_weight=0, lexical_weight=1),
    )
    assert len(lexical) == 2  # The second asset shares 'áo'; the third has zero evidence.
    assert retriever.search(query, [], category="missing", limit=2) == []
    for bad in (
        dict(image_weight=float("nan")),
        dict(image_weight=-1),
        dict(image_weight=0, metadata_weight=0, lexical_weight=0),
    ):
        with pytest.raises(ValueError):
            FusionConfig(**bad)


def test_role_queries_do_not_cross_contaminate_channels(index):
    class RoleEncoder(DocumentEncoder):
        def encode_documents(self, texts):
            return np.array(
                [
                    {"original": [0, 0, 1], "visual": [1, 0, 0], "metadata": [0, 1, 0]}[t]
                    for t in texts
                ],
                dtype=np.float32,
            )

    service = SearchService(
        Settings(
            embedding_artifact_dir=str(index.directory),
            search_query_roles=True,
            search_llm_rerank=False,
        ),
        encoder_factory=RoleEncoder,
    )
    plan = QueryPlan(
        intent="intent",
        visual_query="visual",
        metadata_query="metadata",
        keywords=[],
        exclusions=[],
    )
    with patch.object(service.understanding, "plan", return_value=plan):
        result = service.text_search(query="original", use_llm=True, top_k=3)
    item = next(row for row in result["items"] if row["id"] == "2" * 64)
    assert item["scores"]["image_cosine"] == 0
    assert item["scores"]["metadata_cosine"] == 0
    assert result["search_config"]["query_roles"] is True


def test_reranking_sees_32_candidates_and_can_return_24(index):
    rng = np.random.default_rng(3)
    vectors = rng.normal(size=(40, 3)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    rows = [
        {**index.assets[0], "asset_id": f"{i + 100:064x}", "keyword": f"label {i}"}
        for i in range(40)
    ]
    np.save(index.directory / "vectors.npy", vectors)
    np.save(index.directory / "metadata_vectors.npy", vectors)
    (index.directory / "assets.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    manifest = dict(index.manifest)
    manifest["count"] = 40
    manifest["files"] = {name: sha256_file(index.directory / name) for name in manifest["files"]}
    write_json(index.directory / "manifest.json", manifest)
    service = SearchService(
        Settings(
            embedding_artifact_dir=str(index.directory),
            search_llm_rerank=True,
            search_rerank_scope="all",
            search_rerank_candidates=32,
        ),
        encoder_factory=DocumentEncoder,
    )
    seen = []
    planned = QueryPlan(intent='Cảnh ở xa', visual_query='distant landscape',
                        metadata_query='cảnh ở xa', keywords=['phong cảnh'], exclusions=[])

    def select(query, plan, hits, limit):
        seen.append(len(hits))
        assert plan is planned
        return hits[:limit]

    with patch.object(service.understanding, "select", side_effect=select), \
         patch.object(service.understanding, "plan", return_value=planned):
        result = service.text_search(query="cảnh ở xa", top_k=24)
    assert seen == [32] and len(result["items"]) == 24
    assert result["llm_used"] and result["llm_call_count"] == 2
    assert result["rerank_candidates"] == 32


def test_excluded_phrases_do_not_boost_positive_lexical_keywords():
    plan = {
        "intent": "áo dài không nón",
        "scope": "in_scope",
        "scope_reason": "Trang phục Việt Nam",
        "visual_query": "person wearing ao dai",
        "metadata_query": "người mặc áo dài",
        "keywords": ["áo dài", "không đội nón", "nón"],
        "exclusions": ["nón"],
    }
    result, _ = call_model([plan], lambda: QueryUnderstanding(Settings()).plan("áo dài không nón"))
    assert result.keywords == ["áo dài"]


def test_pixel_reranker_binds_local_image_to_candidate_without_vqa(tmp_path):
    file = tmp_path / "images/photo.png"
    file.parent.mkdir()
    Image.new("RGB", (8, 8), "red").save(file)
    row = {
        "asset_id": hashlib.sha256(file.read_bytes()).hexdigest(),
        "image_path": "images/photo.png",
        "keyword": "ảnh",
        "description": "mô tả",
        "questions": [{"answer": "SECRET_VQA_LABEL"}],
    }
    model = QueryUnderstanding(
        Settings(search_image_root=str(tmp_path), search_rerank_evidence="pixels")
    )
    result, captured = call_model(
        [{"suggestions": [{"candidate_id": "C1", "reason": "Có màu đỏ."}]}],
        lambda: model.select("ảnh đỏ", None, [{"asset": row}], 1),
    )
    content = captured[0]["messages"][1]["content"]
    assert content[1]["text"] == "Ảnh ứng viên C1:"
    assert content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert result[0]["asset"]["asset_id"] == row["asset_id"]
    assert "SECRET_VQA_LABEL" not in json.dumps(captured)
    row["image_path"] = "../outside.png"
    with pytest.raises(ApplicationError, match="Không đọc được ảnh"):
        model.candidate_images({"C1": {"asset": row}})


def test_relevance_shadow_enforcement_and_missing_description():
    policy = RelevancePolicy("a" * 64, 0.2, 0.7)
    good = {"scores": {"image_cosine": 0.3, "metadata_cosine": 0.8}, "metadata_complete": True}
    outside = {"scores": {"image_cosine": 0.05, "metadata_cosine": 0.9}}
    unknown_metadata = {
        "scores": {"image_cosine": 0.3, "metadata_cosine": 0.1},
        "metadata_complete": False,
    }
    hits = [good, outside, unknown_metadata]
    shadow, status = apply_relevance(hits, policy, "shadow")
    assert shadow == hits and status["would_reject"] == 1 and not status["enforced"]
    filtered, status = apply_relevance(hits, policy, "enforce")
    assert filtered == [good, unknown_metadata] and status["enforced"]
    assert apply_relevance(hits, None, "shadow")[1]["status"] == "calibration_required"


def test_policy_fingerprint_mismatch_fails_enforcement_and_not_shadow(index, tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "human-calibrated-cosine-gate",
                "pipeline_signature": "b" * 64,
                "development_pool_sha256": "a" * 64,
                "query_profile": "local",
                "min_image_cosine": 0.2,
                "min_metadata_cosine": 0.7,
            }
        )
    )
    settings = Settings(
        embedding_artifact_dir=str(index.directory),
        search_relevance_mode="enforce",
        search_relevance_policy=str(path),
    )
    service = SearchService(settings, encoder_factory=DocumentEncoder)
    with pytest.raises(ApplicationError) as error:
        service.text_search(query="cảnh ở xa", use_llm=False)
    assert error.value.code == "relevance_policy_unavailable"
    shadow = SearchService(
        settings.model_copy(update={"search_relevance_mode": "shadow"}),
        encoder_factory=DocumentEncoder,
    )
    response = shadow.text_search(query="cảnh ở xa", use_llm=False)
    assert response["items"]
    assert response["relevance_gate"]["status"] == "policy_mismatch_or_invalid"


def test_explicit_no_llm_also_disables_all_scope_reranking(index):
    service = SearchService(
        Settings(
            embedding_artifact_dir=str(index.directory),
            search_llm_rerank=True,
            search_rerank_scope="all",
        ),
        encoder_factory=DocumentEncoder,
    )
    with patch.object(service.understanding, "select", side_effect=AssertionError("No LLM")):
        response = service.text_search(query="cảnh ở xa", use_llm=False)
    assert response["llm_call_count"] == 0 and not response["llm_used"]


def test_default_fusion_matches_original_rank_fusion_on_same_scores(index):
    retriever = HybridRetriever(index)
    rng = np.random.default_rng(7)
    for category in (None, "am_thuc", "kien_truc"):
        for _ in range(5):
            queries = rng.normal(size=(3, index.dimension)).astype(np.float32)
            scores = retriever.channel_scores(queries, ["ao dai", "xanh"])
            selected = retriever.positions[category]
            expected = np.zeros(len(selected), dtype=np.float64)
            for channel in ("image", "metadata", "lexical"):
                values = scores[channel][selected]
                order = np.argsort(-values, kind="stable")[:100]
                for rank, position in enumerate(order, 1):
                    if channel != "lexical" or values[position] > 0:
                        expected[position] += 1 / (60 + rank)
            expected_order = np.argsort(-expected, kind="stable")
            actual = retriever.rank(scores, category=category, limit=len(selected))
            assert [hit["asset"]["asset_id"] for hit in actual] == [
                index.assets[selected[i]]["asset_id"] for i in expected_order
            ]
            assert np.allclose([hit["score"] for hit in actual], expected[expected_order])


def test_valid_calibrated_policy_can_abstain_and_is_profile_scoped(index, tmp_path):
    path = tmp_path / "valid-policy.json"
    service = SearchService(
        Settings(
            embedding_artifact_dir=str(index.directory),
            search_relevance_mode="enforce",
            search_relevance_policy=str(path),
        ),
        encoder_factory=DocumentEncoder,
    )
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "human-calibrated-cosine-gate",
                "pipeline_signature": service.search_signature(),
                "development_pool_sha256": "a" * 64,
                "query_profile": "local",
                "min_image_cosine": 0.9,
                "min_metadata_cosine": 0.9,
            }
        )
    )
    response = service.text_search(query="cảnh ở xa", use_llm=False)
    assert response["items"] == [] and response["abstained"] is True
    assert response["relevance_gate"]["enforced"] is True
    hits = [{"scores": {"image_cosine": 0.1, "metadata_cosine": 0.2}}]
    kept, info = service.gate(hits, query_profile="planned")
    assert kept == hits and info["status"] == "not_applicable_query_profile"
