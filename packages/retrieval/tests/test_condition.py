import numpy as np

from visualseek_retrieval.condition import condition_phrase, match_purposes
from visualseek_retrieval.pipeline import SearchPipeline, Settings

PURPOSES = {
    "p:stomach": "dạ dày",
    "p:stomach-pain": "đau dạ dày",
    "p:pain": "giảm đau hạ sốt",
    "p:ache": "đau",
    "p:sleep": "mất ngủ",
    "p:calm": "an thần",
    "p:cough": "chữa ho",
    "p:phlegm": "ho long đờm",
    "p:eye": "nhỏ mắt",
}


def test_only_medicine_for_people_has_a_condition() -> None:
    assert condition_phrase("thuốc đau dạ dày") == "đau dạ dày"
    assert condition_phrase("thuốc ho loại nào tốt") == "ho"
    for query in ("thuốc lá điện tử", "thuốc trừ sâu", "thuốc tẩy giun cho chó", "tủ gỗ"):
        assert condition_phrase(query) is None
    # Without accents "thuoc" is also "thuộc": such a query is left to content search.
    assert condition_phrase("thuoc dau da day") is None


def test_a_query_that_says_who_the_medicine_is_for_is_not_filtered() -> None:
    for query in (
        "thuốc ho cho trẻ em",
        "thuốc hạ sốt cho bé",
        "thuốc dị ứng cho bà bầu",
        "thuốc táo bón khi mang thai",
        "thuốc mất ngủ cho người già",
    ):
        assert condition_phrase(query) is None


def test_a_query_for_a_dosage_form_is_not_filtered() -> None:
    for query in ("thuốc giảm đau dạng viên", "thuốc ho siro", "thuốc bôi ngoài da"):
        assert condition_phrase(query) is None
    assert condition_phrase("thuốc viêm họng") == "viêm họng"


def test_a_bucket_does_not_count_as_exact_for_part_of_its_name() -> None:
    purposes = {"p:fever": "hạ sốt", "p:pain-fever": "giảm đau hạ sốt"}

    assert match_purposes("hạ sốt", purposes)[0] == ["p:fever"]
    assert match_purposes("giảm đau hạ sốt", purposes)[0] == ["p:pain-fever"]


def test_a_shorter_label_inside_the_complaint_does_not_match() -> None:
    tiers = match_purposes("đau dạ dày", PURPOSES)

    assert tiers == [["p:stomach-pain"], ["p:stomach", "p:stomach-pain"]]
    assert all("p:ache" not in tier and "p:pain" not in tier for tier in tiers)


def test_the_wider_bucket_comes_after_the_exact_purpose() -> None:
    assert match_purposes("mất ngủ", PURPOSES)[0] == ["p:sleep"]
    assert match_purposes("mất ngủ", PURPOSES)[-1] == ["p:calm", "p:sleep"]
    assert match_purposes("ho", PURPOSES)[0] == ["p:cough"]  # "ho long đờm" names a bucket


def test_leading_verbs_do_not_change_what_is_found() -> None:
    for phrase in ("trị mất ngủ", "chữa mất ngủ", "hỗ trợ điều trị mất ngủ"):
        assert match_purposes(phrase, PURPOSES) == match_purposes("mất ngủ", PURPOSES)
    assert match_purposes("trị ho", PURPOSES)[0] == match_purposes("ho", PURPOSES)[0]
    # "đau" alone is too vague, so the verb stays and the painkillers are found.
    assert match_purposes("giảm đau", PURPOSES)[0] == ["p:pain"]


def test_dosage_forms_and_vague_words_match_nothing() -> None:
    for phrase in ("nhỏ mắt", "đau", "bổ", "dạng viên", "màu xanh"):
        assert match_purposes(phrase, PURPOSES) == []


DRUGS = ["famo", "nexi", "omic"]
GRAPH = {
    "fingerprint": "g",
    "brands": [],
    "edges": [],
    "entities": [
        {
            "id": i,
            "name": n,
            "core": n,
            "aliases": [n.lower()],
            "brand_id": None,
            "asset_count": 1,
        }
        for i, n in [
            ("famo", "Famotidin"),
            ("nexi", "Nexium"),
            ("omic", "Omicap"),
            ("syrup", "Bổ phế"),
            ("soap", "Xà phòng"),
        ]
    ],
}


def categories(stomach: list[str]) -> dict:
    labels = {
        e["id"]: {
            "category": "drug.oral",
            "category_l1": "drug",
            "name": e["name"],
            "product_type": "Thuốc",
        }
        for e in GRAPH["entities"]
    }
    labels["soap"] = {
        "category": "beauty.skincare",
        "category_l1": "beauty",
        "name": "Xà phòng",
        "product_type": "Xà phòng",
    }
    return {
        "category_names": {"drug.oral": "Tân dược dạng uống"},
        "entities": labels,
        "purposes": {"purpose:da-day": "dạ dày"},
        "edges": [{"src": s, "rel": "TREATS", "dst": "purpose:da-day"} for s in stomach],
    }


def plan(query: str, stomach: list[str], **settings):
    pipeline = SearchPipeline(
        GRAPH,
        np.eye(5, 4, dtype=np.float32),
        encoder=None,
        client=None,
        categories=categories(stomach),
        settings=Settings(**settings),
    )
    return pipeline.plan(query, pipeline.spotter.spot(query, None))


def test_drugs_labelled_with_the_complaint_become_the_targets() -> None:
    result = plan("thuốc đau dạ dày", [*DRUGS, "soap"])

    # The soap carries the same purpose but is not a medicine.
    assert sorted(result.hops) == sorted(DRUGS)
    assert (result.relation_name, result.value_ids) == ("TREATS", ["purpose:da-day"])


def test_too_few_drugs_or_the_switch_off_leave_content_search() -> None:
    assert plan("thuốc đau dạ dày", DRUGS[:2]).hops == []
    assert plan("thuốc đau dạ dày", DRUGS, condition_search=False).hops == []
    assert plan("thuốc đau dạ dày và thuốc ho", DRUGS).hops == []
