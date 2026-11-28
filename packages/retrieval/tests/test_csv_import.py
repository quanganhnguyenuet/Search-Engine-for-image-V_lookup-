import csv
import io

import pytest

from visualseek_retrieval.csv_import import (
    CsvError,
    EntityMatcher,
    evidence_pack,
    group_rows,
    new_entity_id,
    read_rows,
)
from visualseek_retrieval.entity_review import EntityReview


def test_labelling_headers_and_approve_are_read() -> None:
    text = (
        "Entity_ID,Tên entity,URL ảnh,URL trang nguồn,Tiêu đề nguồn,approve\n"
        "e1,Siro ho Bảo Thanh,https://a.vn/1.jpg,https://a.vn/p,Siro ho,yes\n"
        "e1,Siro ho Bảo Thanh,https://a.vn/2.jpg,,,no\n"
        ",Bánh pía,ftp://x/3.jpg,,,\n"
        ",,,,,\n"
    )
    rows, skipped = read_rows(text)

    assert [(r["line"], r["entity_id"], r["page_title"]) for r in rows] == [(2, "e1", "Siro ho")]
    assert [(s["line"], s["reason"]) for s in skipped] == [
        (3, "approve khác yes"),
        (4, "URL ảnh không hợp lệ"),
    ]


def test_english_headers_semicolons_and_a_bom() -> None:
    rows, _ = read_rows("﻿entity_name;url;doc_title\nNước dừa;https://b.vn/x.png;Dừa\n")

    assert rows[0]["entity_name"] == "Nước dừa"
    assert rows[0]["image_url"] == "https://b.vn/x.png"
    assert rows[0]["page_title"] == "Dừa"
    assert rows[0]["page_url"] == ""


def test_missing_columns_are_named() -> None:
    with pytest.raises(CsvError, match="URL ảnh"):
        read_rows("Tên entity,page_url\nA,https://x\n")


def test_rows_group_by_dataset_id_else_by_name() -> None:
    rows, _ = read_rows(
        "entity_id,entity_name,image_url\n"
        "abc-123456,Bánh pía,https://x/1.jpg\n"
        "abc-123456,Banh pia (Sóc Trăng),https://x/2.jpg\n"
        ",Nước  Dừa,https://x/3.jpg\n"
        ",nước dừa,https://x/4.jpg\n"
    )
    groups = group_rows(rows)

    assert [(g["key"], g["lines"]) for g in groups] == [
        ("id:abc-123456", [2, 3]),
        ("name:nuoc dua", [4, 5]),
    ]


def matcher() -> EntityMatcher:
    review = EntityReview(canonical_ids={"e1b": "e1"}, members={"e1": ["e1b"]})
    names = {"e1": "Siro ho Bảo Thanh", "e2": "Nước dừa", "e3": "Rabesta-20", "e4": "Bánh pía"}
    strings = {"e1": ["siro bao thanh"], "e2": [], "e3": ["rabesta 20"], "e4": ["banh pia"]}
    return EntityMatcher(names, strings, review.canonical)


def test_dataset_id_and_exact_name_match_an_existing_entity() -> None:
    m = matcher()

    assert m.match("whatever", "e1b").as_dict()["entity_id"] == "e1"  # a merged member
    found = m.match("SIRO HO BẢO THANH")
    assert (found.kind, found.entity_id) == ("existing", "e1")
    assert m.match("Siro ho Bảo Thanh (chai 125ml)").entity_id == "e1"  # parenthetical dropped


def test_close_names_wait_for_an_admin_with_candidates() -> None:
    m = matcher()

    near = m.match("Nước dừa xiêm Bến Tre")
    assert near.kind == "near"
    assert near.candidates[0]["entity_id"] == "e2"

    other_model = m.match("Rabesta-10")
    assert other_model.kind == "near"
    assert other_model.candidates[0]["code_conflict"] is True


def test_unrelated_names_are_new() -> None:
    assert matcher().match("Đèn pin siêu sáng").kind == "new"


def test_new_ids_keep_the_dataset_id_and_are_stable() -> None:
    assert new_entity_id("abc-123456", "X", set()) == "abc-123456"
    first = new_entity_id("", "Bánh pía", set())
    assert new_entity_id("", "bánh  PÍA", set()) == first
    assert new_entity_id("abc-123456", "Bánh pía", {"abc-123456", first}) not in {
        "abc-123456",
        first,
    }


def test_evidence_pack_has_the_corpus_snippet_kinds() -> None:
    items = [
        {
            "page_title": "Bánh pía Sóc Trăng 500g",
            "page": {
                "status": "ok",
                "title": "Bánh pía Tân Huê Viên",
                "description": "Đặc sản Sóc Trăng",
                "products": [{"brand": "Tân Huê Viên"}],
            },
            "ocr": [{"text": "TAN HUE VIEN", "score": 0.95}, {"text": "xx", "score": 0.3}],
        },
        {"page_title": "Bánh pía Sóc Trăng 500g", "page": None, "ocr": []},
    ]
    pack = evidence_pack("n1", "Bánh pía", items)

    assert [(s["id"], s["kind"]) for s in pack["snippets"]] == [
        ("n1", "name"),
        ("b1", "brand"),
        ("t1", "title"),
        ("t2", "title"),
        ("d1", "desc"),
        ("o1", "ocr"),
    ]
    assert pack["members"] == ["n1"]


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_quotes_far_down_the_file_are_read_as_csv_writes_them(delimiter) -> None:
    """csv.Sniffer turned doubled quotes off when its sample had none, which lost later rows."""
    titles = [f"San pham so {i} chinh hang gia tot nhat thi truong hom nay" for i in range(200)]
    titles[150] = 'Tivi 55" Samsung, loại "xịn"; chính hãng'
    titles[170] = 'Kem "Hot" trend, giá rẻ'
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter)
    writer.writerow(["Tên entity", "URL ảnh", "Tiêu đề nguồn", "approve"])
    for i, title in enumerate(titles):
        writer.writerow([f"San pham {i}", f"https://shop.vn/{i}.png", title, "yes"])

    rows, skipped = read_rows(out.getvalue())

    assert skipped == []
    assert [r["page_title"] for r in rows] == titles
    assert [r["image_url"] for r in rows] == [f"https://shop.vn/{i}.png" for i in range(200)]


def test_a_space_after_the_delimiter_still_opens_a_quoted_cell() -> None:
    rows, _ = read_rows('entity_name, image_url, doc_title\nA, https://b.vn/x.png, "Dừa, xiêm"\n')

    assert rows[0]["page_title"] == "Dừa, xiêm"


def test_a_cell_over_the_csv_limit_is_a_csv_error() -> None:
    with pytest.raises(CsvError, match="ô quá dài"):
        read_rows("Tên entity,URL ảnh\n" + "x" * 200_000 + ",https://shop.vn/x.png\n")
