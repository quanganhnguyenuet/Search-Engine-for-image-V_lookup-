import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_corpus_manifest as corpus  # noqa: E402

HEADER = ["Entity_ID", "Tên entity", "URL ảnh", "Chiều rộng (px)", "Chiều cao (px)",
          "URL trang nguồn", "Tiêu đề nguồn", "Tên miền", "approve"]
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-bytes-1"
JPEG_OTHER = b"\xff\xd8\xff\xe0" + b"jpeg-bytes-2"
HTML = b"<html>not an image</html>"


def row(entity, name, url, approve="yes", page="https://shop.vn/p"):
    return [entity, name, url, "600", "400", page, "Trang " + name, "shop.vn", approve]


@pytest.fixture
def handoff(tmp_path):
    root = tmp_path / "LabelPortable"
    review = root / "CodexReview"
    (review / "assets").mkdir(parents=True)
    (review / "pages").mkdir()
    (review / "agy" / "run1" / "ocr").mkdir(parents=True)

    files = {"i1": JPEG, "i2": JPEG, "i3": JPEG_OTHER, "i5": HTML}  # i1 and i2: same content
    for item, content in files.items():
        (review / "assets" / f"{item}.jpg").write_bytes(content)

    db = sqlite3.connect(review / "review.sqlite3")
    db.execute("create table items (id text primary key, data text, evidence text)")
    entries = [
        ("i1", "e1", "https://x/a.jpg", "CodexReview\\assets\\i1.jpg", hashlib.sha256(JPEG)),
        ("i2", "e2", "https://x/b.jpg", "CodexReview\\assets\\i2.jpg", hashlib.sha256(JPEG)),
        ("i3", "e1", "https://x/c.jpg", "CodexReview\\assets\\i3.jpg", hashlib.sha256(JPEG_OTHER)),
        ("i4", "e3", "https://x/d.jpg", "", None),  # reviewed but never downloaded
        ("i5", "e3", "https://x/e.jpg", "CodexReview\\assets\\i5.jpg", hashlib.sha256(HTML)),
        ("i6", "e3", "https://x/f.jpg", "CodexReview\\assets\\gone.jpg", None),
    ]
    for item, entity, url, path, digest in entries:
        evidence = {"asset_path": path, "image_sha256": digest.hexdigest() if digest else ""}
        db.execute("insert into items values (?, ?, ?)", (
            item, json.dumps({"entity_id": entity, "image_url": url}), json.dumps(evidence)))
    db.commit()
    db.close()

    (review / "agy" / "run1" / "ocr" / "i1.json").write_text(json.dumps(
        {"at": "2026-11-18", "ocr": [{"box": [0, 0, 1, 1], "text": "Logitech", "score": 0.9}]}))
    (review / "agy" / "run1" / "ocr" / "i3.json").write_text(
        json.dumps({"at": "2026-11-18", "ocr": []}))
    page = hashlib.sha1(b"https://shop.vn/p").hexdigest()[:24]
    (review / "pages" / f"{page}.json").write_text(json.dumps({
        "status": "ok", "title": "Chuột", "h1": ["Chuột MX"], "og_title": "", "description": "d",
        "products": [{"type": "Product", "name": "Other", "brand": "Acme", "images": []},
                     {"type": "Product", "name": "MX", "brand": "Logitech",
                      "images": ["https://x/a.jpg"]}]}), encoding="utf-8")

    table = tmp_path / "part.csv"
    with table.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        writer.writerows([
            row("e1", "Chuột A", "https://x/a.jpg"),
            row("e2", "Chuột B", "https://x/b.jpg"),
            row("e1", "Chuột A", "https://x/c.jpg", page="https://shop.vn/none"),
            row("e3", "Thuốc C", "https://x/d.jpg"),
            row("e3", "Thuốc C", "https://x/e.jpg"),
            row("e3", "Thuốc C", "https://x/f.jpg"),
            row("e3", "Thuốc C", "https://x/g.jpg"),  # absent from the review database
            row("e3", "Thuốc C", "https://x/a.jpg", approve="no"),
            row("e1", "Chuột A", "https://x/a.jpg"),  # exact duplicate of a yes row
        ])
    return root, table


def run(handoff, tmp_path):
    root, table = handoff
    return corpus.build(root, [table], tmp_path / "out", workers=2), tmp_path / "out"


def test_assets_merge_identical_content_across_entities(handoff, tmp_path):
    manifest, out = run(handoff, tmp_path)
    assets = [json.loads(line) for line in (out / "assets.jsonl").read_text().splitlines()]

    shared = next(a for a in assets if a["asset_id"] == hashlib.sha256(JPEG).hexdigest())
    assert shared["entity_ids"] == ["e1", "e2"]
    assert [s["item_id"] for s in shared["sources"]] == ["i1", "i2"]
    assert shared["image_path"] == "CodexReview/assets/i1.jpg"
    assert manifest["counts"]["assets"] == 2
    assert manifest["counts"]["assets_shared_by_multiple_entities"] == 1
    assert manifest["counts"]["duplicate_yes_rows_dropped"] == 1


def test_missing_rows_carry_a_reason_and_the_gate_still_passes(handoff, tmp_path):
    manifest, out = run(handoff, tmp_path)
    missing = [json.loads(line) for line in (out / "missing.jsonl").read_text().splitlines()]

    assert {m["image_url"]: m["reason"] for m in missing} == {
        "https://x/d.jpg": "no_asset_path",
        "https://x/e.jpg": "not_raster_image",
        "https://x/f.jpg": "file_missing",
        "https://x/g.jpg": "not_in_review_db",
    }
    assert manifest["counts"]["entities_without_assets"] == 1
    assert all(manifest["checks"].values())
    entity = next(json.loads(line) for line in (out / "entities.jsonl").read_text().splitlines()
                  if json.loads(line)["entity_id"] == "e3")
    assert (entity["yes_rows"], entity["usable_rows"], entity["missing_rows"]) == (4, 0, 4)


def test_ocr_and_page_context_are_attached(handoff, tmp_path):
    _, out = run(handoff, tmp_path)
    assets = {a["asset_id"]: a for a in map(json.loads, (out / "assets.jsonl").read_text()
                                            .splitlines())}
    shared = assets[hashlib.sha256(JPEG).hexdigest()]
    other = assets[hashlib.sha256(JPEG_OTHER).hexdigest()]

    assert shared["ocr"] == [{"text": "Logitech", "score": 0.9}]
    assert other["ocr"] == []  # OCR ran and found no text
    page = shared["sources"][0]["page"]
    assert page["title"] == "Chuột"
    assert page["products"][0]["brand"] == "Logitech"  # product listing this image sorts first
    assert page["products"][0]["lists_this_image"] is True
    assert other["sources"][0]["page"] is None


def test_output_is_deterministic_and_does_not_touch_the_handoff(handoff, tmp_path):
    root, table = handoff
    before = sorted((p, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file())
    first = corpus.build(root, [table], tmp_path / "a", workers=1)
    second = corpus.build(root, [table], tmp_path / "b", workers=4)

    assert first["files"] == second["files"]
    assert before == sorted((p, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file())


def test_conflicting_entity_names_abort(handoff, tmp_path):
    root, table = handoff
    with table.open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow(row("e1", "Tên khác", "https://x/z.jpg"))
    with pytest.raises(SystemExit, match="conflicting names"):
        corpus.build(root, [table], tmp_path / "out", workers=1)
