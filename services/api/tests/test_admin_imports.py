"""CSV import job, end to end on a fake search service (no network, no GPU, no LLM)."""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from visualseek_api.application import imports as imports_module
from visualseek_api.application.admin import AdminService
from visualseek_api.application.graph_view import GraphView
from visualseek_api.core.config import Settings
from visualseek_retrieval.entity_review import EntityReview

A1 = "a" * 64


def png(color: tuple[int, int, int], size: int = 64, mark: int = 0) -> bytes:
    image = Image.new("RGB", (size, size), color)
    for x in range(mark):  # a few pixels make another picture with the same colour
        image.putpixel((x, x), (255 - color[0], 0, 0))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


IMAGES = {
    "https://shop.vn/siro-1.png": png((200, 30, 30)),
    "https://shop.vn/siro-1-copy.png": png((200, 30, 30)),  # same bytes
    "https://shop.vn/dua.png": png((30, 200, 30)),
    "https://shop.vn/pia-1.png": png((30, 30, 200), mark=40),
    "https://shop.vn/pia-2.png": png((220, 220, 30), mark=40),
}


class FakeEncoder:
    def __init__(self) -> None:
        self.model = SimpleNamespace(max_seq_length=64)

    def encode(self, texts, batch_size: int = 16):
        return np.ones((len(texts), 4), np.float32)


class FakeImages:
    def encode_images(self, images):
        return np.ones((len(images), 3), np.float32)


class FakeFusion:
    def __init__(self) -> None:
        self.ids: list[str] = []

    def extend(self, ids, doc, img) -> None:
        self.ids += ids


class FakeQdrant:
    def __init__(self) -> None:
        self.points: list = []

    def upsert(self, collection, points, wait=True) -> None:
        self.points += points


def fake_service() -> SimpleNamespace:
    graph = {
        "entities": [
            {"id": "e1", "name": "Siro ho Bảo Thanh", "aliases": ["siro ho bao thanh"],
             "notes": [], "brand_id": None},
            {"id": "e2", "name": "Nước dừa", "aliases": ["nuoc dua"], "notes": [],
             "brand_id": None},
        ],
        "brands": [],
        "edges": [],
        "fingerprint": "f",
    }  # fmt: skip
    categories = {"taxonomy_sha256": "t", "category_names": {"food.drink": "Đồ uống"},
                  "entities": {}, "edges": []}  # fmt: skip
    review = EntityReview()
    pipeline = SimpleNamespace(
        graph=graph,
        review=review,
        payloads={},
        fusion=FakeFusion(),
        image_encoder=FakeImages(),
        encoder=FakeEncoder(),
        client=FakeQdrant(),
        collection="c",
    )
    service = SimpleNamespace(
        pipeline=pipeline,
        graph_view=GraphView(graph, categories, review),
        categories=categories,
        asset_entities={A1: ["e1"]},
        image_paths={A1: "x.jpg"},
        details={},
        base_blocked=[],
        upload_paths={},
        info={"graph_fingerprint": "f", "doc_texts_sha256": "d"},
        executor=ThreadPoolExecutor(max_workers=1),
        avg_doc_tokens=10.0,
    )
    service.image_file = lambda asset_id: None
    return service


@pytest.fixture
def setup(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from visualseek_retrieval import page_context

    def fetch_image(url, max_bytes=0, referer=None):
        if url not in IMAGES:
            raise ValueError("HTTP 404")
        return IMAGES[url], url, "image/png"

    def fetch_page(url, polite, retries=2):
        return {"status": "ok", "title": "Bánh pía Sóc Trăng chính hãng", "h1": [],
                "og_title": "", "description": "Đặc sản Sóc Trăng", "products": []}  # fmt: skip

    monkeypatch.setattr(page_context, "fetch_image", fetch_image)
    monkeypatch.setattr(page_context, "fetch_page", fetch_page)
    monkeypatch.setattr(page_context, "check_public", lambda url: "shop.vn")
    monkeypatch.setattr(imports_module, "HOST_INTERVAL", 0.0)
    monkeypatch.setattr(imports_module.ImportService, "_ocr_file", lambda self, path: [])
    labeller = imports_module.Labeller
    monkeypatch.setattr(labeller, "available", lambda self: None)
    monkeypatch.setattr(
        labeller,
        "classify",
        lambda self, packs: {
            p["entity_id"]: {"category": "food.drink", "product_type": "Bánh", "market_tier":
                             "not_applicable", "evidence": ["n1"], "basis": "evidence",
                             "confidence": "high", "model": "agy:test", "prompt_version": "v"}
            for p in packs
        },
    )  # fmt: skip
    monkeypatch.setattr(
        labeller, "enrich", lambda self, packs, labels, names: {
            p["entity_id"]: {"brand": "", "treats": [], "origin": "Sóc Trăng", "model": "agy:t"}
            for p in packs
        },
    )  # fmt: skip
    settings = Settings(
        _env_file=None, admin_dir=str(tmp_path / "admin"), admin_data_dir=str(tmp_path / "data")
    )
    service = fake_service()
    admin = AdminService(settings)
    admin.prepare(service)
    imports = admin.imports
    imports.get_service = lambda: service
    monkeypatch.setattr(imports, "enqueue", lambda import_id: None)  # run by hand below
    return admin, imports, service, tmp_path


CSV = (
    "Tên entity,URL ảnh,URL trang nguồn,Tiêu đề nguồn\n"
    "Siro ho Bảo Thanh,https://shop.vn/siro-1.png,https://shop.vn/p1,Siro ho Bảo Thanh 125ml\n"
    "Siro ho Bảo Thanh,https://shop.vn/siro-1-copy.png,,\n"
    "Siro ho Bảo Thanh,https://shop.vn/missing.png,,\n"
    "Nước dừa xiêm Bến Tre,https://shop.vn/dua.png,,\n"
    "Bánh pía Sóc Trăng,https://shop.vn/pia-1.png,https://shop.vn/p2,Bánh pía\n"
    "Bánh pía Sóc Trăng,https://shop.vn/pia-2.png,https://shop.vn/p2,Bánh pía\n"
)
ACTOR = {"id": "admin-1", "email": "admin@example.com"}


def by_name(view: dict) -> dict:
    return {g["name"]: g for g in view["groups"]}


def test_an_import_adds_existing_waits_for_near_and_registers_new(setup) -> None:
    admin, imports, service, tmp_path = setup

    created = imports.create("batch.csv", CSV.encode("utf-8"), ACTOR)
    imports._run(created["id"])
    view = imports.view(created["id"])
    groups = by_name(view)

    assert view["status"] == "waiting_match"
    siro = groups["Siro ho Bảo Thanh"]
    assert (siro["status"], siro["published"], siro["duplicates"], siro["failed"]) == (
        "published",
        1,
        1,
        1,
    )
    upload = next(iter(admin.store.uploads().values()))
    assert upload["entity_id"] == "e1"
    assert upload["origin"] == f"csv:{created['id']}"
    assert upload["payload"]["page_url"] == "https://shop.vn/p1"
    assert "Trang: Siro ho Bảo Thanh 125ml" in upload["doc_text"]

    dua = groups["Nước dừa xiêm Bến Tre"]
    assert (dua["status"], dua["candidates"][0]["entity_id"]) == ("waiting_match", "e2")

    pia = groups["Bánh pía Sóc Trăng"]
    assert pia["status"] == "ready"
    assert pia["label"]["category"] == "food.drink"
    registered = [
        json.loads(line)
        for line in (tmp_path / "admin/new_entities.jsonl").read_text("utf-8").splitlines()
    ]
    assert [(r["name"], r["op"], r["enrich"]["origin"]) for r in registered] == [
        ("Bánh pía Sóc Trăng", "add", "Sóc Trăng")
    ]
    pack_kinds = {s["kind"] for s in registered[0]["pack"]["snippets"]}
    assert pack_kinds == {"name", "title", "desc"}
    assert [n["name"] for n in imports.pending_new_entities(service)] == ["Bánh pía Sóc Trăng"]

    # The admin says the near name is the existing "Nước dừa".
    imports.decide(created["id"], dua["key"], "e2", False, ACTOR)
    imports._run(created["id"])
    assert by_name(imports.view(created["id"]))["Nước dừa xiêm Bến Tre"]["status"] == "published"

    # A release adds the new entity to the graph; the next run publishes its images.
    new_id = pia["target"]["id"]
    index = service.admin_index
    index.graph_entities[new_id] = {"id": new_id, "name": "Bánh pía Sóc Trăng", "aliases": [],
                                    "notes": [], "brand_id": None}  # fmt: skip
    index.entities.append(new_id)
    imports._run(created["id"])
    view = imports.view(created["id"])
    assert by_name(view)["Bánh pía Sóc Trăng"]["status"] == "released"
    assert view["status"] == "done"
    assert len(admin.store.uploads()) == 4
    assert {p.payload["entity_ids"][0] for p in service.pipeline.client.points} == {
        "e1",
        "e2",
        new_id,
    }


def test_choosing_an_existing_entity_drops_a_registered_new_one(setup) -> None:
    _, imports, service, tmp_path = setup
    created = imports.create("b.csv", CSV.encode("utf-8"), ACTOR)
    imports._run(created["id"])
    pia = by_name(imports.view(created["id"]))["Bánh pía Sóc Trăng"]

    imports.decide(created["id"], pia["key"], "e2", False, ACTOR)

    assert imports.pending_new_entities(service) == []
    ops = [
        json.loads(line)["op"]
        for line in (tmp_path / "admin/new_entities.jsonl").read_text("utf-8").splitlines()
    ]
    assert ops == ["add", "drop"]


def test_an_image_already_in_the_collection_is_a_duplicate(setup) -> None:
    _, imports, service, _ = setup
    import hashlib

    service.image_paths[hashlib.sha256(IMAGES["https://shop.vn/dua.png"]).hexdigest()] = "d.png"
    created = imports.create("c.csv", CSV.encode("utf-8"), ACTOR)
    imports._run(created["id"])

    issues = imports.view(created["id"])["issues"]
    assert {"line": 5, "status": "duplicate", "reason": "ảnh đã có trong kho"}.items() <= next(
        i for i in issues if i["line"] == 5
    ).items()


def test_a_csv_without_the_needed_columns_is_refused(setup) -> None:
    from visualseek_api.core.errors import ApplicationError

    _, imports, _, _ = setup
    with pytest.raises(ApplicationError) as error:
        imports.create("x.csv", b"name_only\nabc\n", ACTOR)
    assert error.value.code == "invalid_csv"


def test_staged_images_are_served_until_published(setup) -> None:
    _, imports, _, _ = setup
    created = imports.create("d.csv", CSV.encode("utf-8"), ACTOR)
    imports._run(created["id"])
    dua = by_name(imports.view(created["id"]))["Nước dừa xiêm Bến Tre"]

    assert imports.staged_file(dua["thumbs"][0]) is not None


def test_the_same_picture_at_another_size_is_a_near_duplicate(setup) -> None:
    _, imports, _, _ = setup
    IMAGES["https://shop.vn/siro-big.png"] = png((200, 30, 30), size=96)
    csv = (
        "Tên entity,URL ảnh\n"
        "Siro ho Bảo Thanh,https://shop.vn/siro-1.png\n"
        "Siro ho Bảo Thanh,https://shop.vn/siro-big.png\n"
    )
    created = imports.create("e.csv", csv.encode("utf-8"), ACTOR)
    imports._run(created["id"])

    view = imports.view(created["id"])
    assert [(i["line"], i["reason"]) for i in view["issues"]] == [(3, "gần trùng một ảnh đã có")]
    assert by_name(view)["Siro ho Bảo Thanh"]["published"] == 1


def registry(tmp_path) -> list[dict]:
    path = tmp_path / "admin/new_entities.jsonl"
    return [json.loads(x) for x in path.read_text("utf-8").splitlines()] if path.exists() else []


def run(imports, text: str) -> dict:
    created = imports.create("f.csv", text.encode("utf-8"), ACTOR)
    imports._run(created["id"])
    return imports.view(created["id"])


def release(service, entity_id: str, name: str) -> None:
    index = service.admin_index
    index.graph_entities[entity_id] = {"id": entity_id, "name": name, "aliases": [],
                                       "notes": [], "brand_id": None}  # fmt: skip
    index.entities.append(entity_id)


PIA = "Bánh pía Sóc Trăng"
PIA_ONE = f"Tên entity,URL ảnh\n{PIA},https://shop.vn/pia-1.png\n"
PIA_TWO = f"Tên entity,URL ảnh\n{PIA},https://shop.vn/pia-2.png\n"


def test_a_picture_with_too_many_pixels_fails_only_its_row(setup, monkeypatch) -> None:
    _, imports, _, _ = setup
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 5000)  # 64x64 passes, 128x128 is refused
    monkeypatch.setitem(IMAGES, "https://shop.vn/bomb.png", png((9, 9, 9), size=128))

    view = run(
        imports,
        "Tên entity,URL ảnh\n"
        "Siro ho Bảo Thanh,https://shop.vn/siro-1.png\n"
        "Siro ho Bảo Thanh,https://shop.vn/bomb.png\n",
    )

    assert (view["status"], view["error"]) == ("done", None)
    assert [(i["line"], i["status"], i["reason"]) for i in view["issues"]] == [
        (3, "failed", "ảnh quá lớn")
    ]
    assert by_name(view)["Siro ho Bảo Thanh"]["published"] == 1


@pytest.mark.parametrize("second", [PIA_ONE, PIA_TWO], ids=["same file", "other picture"])
def test_a_new_product_waiting_for_a_release_is_registered_once(setup, monkeypatch, second) -> None:
    admin, imports, service, _ = setup
    classify, asked = imports_module.Labeller.classify, []

    def counting(self, packs):
        asked.append(len(packs))
        return classify(self, packs)

    monkeypatch.setattr(imports_module.Labeller, "classify", counting)
    one, two = run(imports, PIA_ONE), run(imports, second)

    target = by_name(one)[PIA]["target"]["id"]
    assert by_name(two)[PIA]["target"]["id"] == target
    assert by_name(two)[PIA]["status"] == "ready"
    assert by_name(two)[PIA]["label"]["category"] == "food.drink"
    assert [n["entity_id"] for n in imports.pending_new_entities(service)] == [target]
    assert asked == [1]

    release(service, target, PIA)
    for view in (one, two):
        imports._run(view["id"])
        assert imports.view(view["id"])["status"] == "done"
    assert len(admin.store.uploads()) == (1 if second == PIA_ONE else 2)


def test_leaving_a_shared_new_entity_keeps_it_for_the_other_import(setup) -> None:
    _, imports, service, tmp_path = setup
    one, two = run(imports, PIA_ONE), run(imports, PIA_TWO)
    key = by_name(one)[PIA]["key"]

    imports.decide(two["id"], key, "e2", False, ACTOR)
    assert [n["name"] for n in imports.pending_new_entities(service)] == [PIA]
    assert [r["op"] for r in registry(tmp_path)] == ["add"]

    imports.decide(one["id"], key, "e2", False, ACTOR)
    assert imports.pending_new_entities(service) == []
    assert [r["op"] for r in registry(tmp_path)] == ["add", "drop"]


def test_an_unlabelled_pending_entity_is_labelled_under_the_same_id(setup, monkeypatch) -> None:
    _, imports, service, _ = setup
    available = imports_module.Labeller.available
    monkeypatch.setattr(imports_module.Labeller, "available", lambda self: "off")
    one = run(imports, PIA_ONE)
    assert by_name(one)[PIA]["status"] == "ready_unlabelled"

    monkeypatch.setattr(imports_module.Labeller, "available", available)
    two = run(imports, PIA_TWO)

    assert by_name(two)[PIA]["target"]["id"] == by_name(one)[PIA]["target"]["id"]
    assert [(n["name"], n["labelled"]) for n in imports.pending_new_entities(service)] == [
        (PIA, True)
    ]


@pytest.mark.parametrize("choice", ["existing", "new"])
def test_a_decision_made_while_labelling_wins(setup, monkeypatch, choice) -> None:
    """The admin may change a group while the labeller runs; the old answer is then dropped."""
    _, imports, _, tmp_path = setup
    created = imports.create("batch.csv", CSV.encode("utf-8"), ACTOR)
    classify, decided = imports_module.Labeller.classify, []

    def decide_during_labelling(self, packs):
        if not decided:
            pia = by_name(imports.view(created["id"]))[PIA]
            assert pia["status"] == "labelling"
            entity_id = "e2" if choice == "existing" else None
            imports.decide(created["id"], pia["key"], entity_id, choice == "new", ACTOR)
            decided.append(pia["key"])
        return classify(self, packs)

    monkeypatch.setattr(imports_module.Labeller, "classify", decide_during_labelling)
    imports._run(created["id"])
    assert registry(tmp_path) == []

    imports._run(created["id"])  # decide() queued the job again
    pia = by_name(imports.view(created["id"]))[PIA]
    if choice == "existing":
        assert (pia["status"], pia["target"]["id"], registry(tmp_path)) == ("published", "e2", [])
    else:
        assert pia["status"] == "ready"
        assert pia["target"]["id"] not in (None, "e2")
        assert [(r["op"], r["entity_id"]) for r in registry(tmp_path)] == [
            ("add", pia["target"]["id"])
        ]
