"""Build the silver eval set (MVP stand-in for P1) under the query rules of section 5.1.

Silver means: queries come from templates (no paraphrase pass) and Rel comes from the raw
``yes`` labels and the unreviewed graph-lite edges, with ~10% label noise. "Single mô tả" is
not built (it needs human judgments). Multi-hop has 2 hops only (entity -> brand -> entity).

Split unit = brand cluster (an entity without a brand is its own cluster), 40/60 dev/test,
so a brand query or a multi-hop query never mixes dev and test entities (rule 4).
Enforced checks (the script exits on violation): one row per query_id; normalized and
accent-key forms pairwise distinct across dev, test and warmup; at most one query per
(type, anchor set, relation); no entity on both sides; every Rel asset is in the corpus;
>= 40% priority rows per type except no_answer.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from visualseek_retrieval.text import accent_key, normalize, surface  # noqa: E402

SEED = 20261122
DEV_SHARE = 40
MIN_ASSETS = 3
TARGETS = {"single_named": 150, "single_brand": 50, "multi_intent": 150, "multi_hop": 150}
PRIORITY_SHARE = 0.45

# Matched on accented lower-case words: folded "vi"/"dep"/"quan" would also hit "vị", "đẹp",
# "quán". Brand names are ASCII, so they match either way.
PRIORITY_TERMS = {
    "fashion_jewelry": [
        "nhẫn",
        "vòng tay",
        "vòng cổ",
        "dây chuyền",
        "bông tai",
        "lắc tay",
        "trang sức",
        "kim cương",
        "đồng hồ",
        "túi",
        "ví",
        "giày",
        "dép",
        "áo",
        "quần",
        "đầm",
        "váy",
        "balo",
        "khăn",
        "mũ",
        "nón",
        "kính mát",
        "nước hoa",
        "thắt lưng",
        "gucci",
        "chanel",
        "dior",
        "bvlgari",
        "cartier",
        "burberry",
        "swarovski",
        "pnj",
        "lalique",
        "chrome hearts",
        "pierre cardin",
        "nike",
        "adidas",
        "converse",
        "new balance",
        "under armour",
        "the north face",
        "uniqlo",
        "carhartt",
        "samsonite",
        "fila",
    ],
    "phone": [
        "điện thoại",
        "iphone",
        "galaxy",
        "oppo",
        "vivo",
        "xiaomi",
        "redmi",
        "nokia",
        "oneplus",
        "vsmart",
        "realme",
        "huawei",
        "rog phone",
        "smartphone",
    ],
    "tech": [
        "laptop",
        "máy tính",
        "chuột",
        "bàn phím",
        "tai nghe",
        "loa",
        "màn hình",
        "máy ảnh",
        "camera",
        "máy in",
        "máy scan",
        "router",
        "ssd",
        "ổ cứng",
        "ipad",
        "kindle",
        "smartwatch",
        "sạc",
        "pin dự phòng",
        "cáp",
        "usb",
        "máy chiếu",
        "nvidia",
        "flycam",
        "dji",
        "tivi",
        "logitech",
        "razer",
        "asus",
        "acer",
        "dell",
        "lenovo",
        "hp",
        "msi",
        "canon",
        "nikon",
        "sony",
        "epson",
        "anker",
        "keychron",
        "akko",
        "wacom",
        "benq",
        "elgato",
        "garmin",
        "ống kính",
    ],
    "vehicle_parts": [
        "ca lăng",
        "phanh",
        "má phanh",
        "lốp",
        "vành",
        "bugi",
        "lọc gió",
        "lọc dầu",
        "ắc quy",
        "gương chiếu hậu",
        "đèn pha",
        "giảm xóc",
        "xích",
        "líp",
        "đĩa phanh",
        "ghi đông",
        "yên xe",
        "hộp số",
        "turbo",
        "két nước",
        "dầu nhớt",
        "phụ tùng",
        "ô tô",
        "xe máy",
        "shimano",
        "sram",
        "maxxis",
        "michelin",
        "schwalbe",
        "continental",
        "pirelli",
        "denso",
        "valeo",
        "bosch",
        "brembo",
        "rotor",
        "groupset",
        "derailleur",
        "củ đề",
        "mô tơ",
        "ford",
        "toyota",
        "honda",
        "hyundai",
        "vinfast",
    ],
}
PRIORITY = {
    group: re.compile(r"(?<!\w)(" + "|".join(map(re.escape, terms)) + r")(?!\w)")
    for group, terms in PRIORITY_TERMS.items()
}
NO_ANSWER = [
    "lịch thi đấu Ngoại hạng Anh tuần này",
    "công thức tính diện tích hình tròn",
    "tỷ giá đô la Mỹ hôm nay",
    "cách khai thuế thu nhập cá nhân online",
    "dự báo thời tiết Đà Nẵng ngày mai",
    "giải phương trình bậc hai",
    "lời bài hát Nối vòng tay lớn",
    "tiểu sử Albert Einstein",
    "kết quả xổ số miền Bắc",
    "giờ mở cửa Bộ Ngoại giao",
    "định lý Pythagoras chứng minh",
    "cách viết đơn xin nghỉ phép",
    "mã bưu chính quận Hoàn Kiếm",
    "lịch sử chiến tranh Peloponnesus",
    "quy tắc chia động từ tiếng Pháp",
    "học bổng du học Phần Lan 2027",
    "thủ tục đăng ký kết hôn",
    "bảng xếp hạng V-League",
    "cách tính lãi suất kép",
    "luật giao thông đường bộ mức phạt nồng độ cồn",
    "thuyết tương đối hẹp là gì",
    "triệu chứng sốt xuất huyết",
    "cách luyện phát âm tiếng Anh",
    "danh sách tổng thống Hoa Kỳ",
    "hướng dẫn cài đặt Ubuntu",
    "điểm chuẩn đại học Bách khoa",
    "ý nghĩa ngày Quốc tế Lao động",
    "câu đố toán lớp 3",
    "cách thiền chánh niệm cho người mới",
    "nhịp sinh học của giấc ngủ",
    "quy trình bầu cử Quốc hội",
    "tác dụng của giấc ngủ trưa",
    "cách tính chỉ số BMI",
    "ngữ pháp thì hiện tại hoàn thành",
    "múi giờ của New Zealand",
    "chính sách bảo hiểm thất nghiệp",
    "lịch nghỉ Tết Nguyên đán của học sinh",
    "lý thuyết trò chơi Nash",
    "cách đổi giấy phép lái xe",
    "nguyên lý thứ hai nhiệt động lực học",
    "hội chứng sợ đám đông",
    "tâm lý học hành vi Skinner",
    "nghị định về làm thêm giờ",
    "phân biệt virus và vi khuẩn",
    "thuật toán sắp xếp nhanh quicksort",
    "định nghĩa lạm phát",
    "luật bản quyền tác giả",
    "cách viết thư xin việc",
    "tháp nhu cầu Maslow",
    "quy định nghỉ thai sản",
]
WARMUP = [
    "đồ dùng nhà bếp",
    "phụ kiện xe đạp",
    "thuốc giảm đau",
    "đèn trang trí",
    "tượng gỗ",
    "món ăn đường phố",
    "đồ chơi trẻ em",
    "dụng cụ cầm tay",
    "mỹ phẩm dưỡng da",
    "đồ cắm trại",
    "thiết bị y tế gia đình",
    "vật liệu xây dựng",
    "phụ kiện thú cưng",
    "đồ gốm",
    "nhạc cụ",
    "thiết bị văn phòng",
    "đồ uống đóng chai",
    "máy móc nông nghiệp",
    "đồ thờ cúng",
    "bánh kẹo",
]
SINGLE_TEMPLATES = ["{name}", "{name}", "{name}", "ảnh {name}", "{lower}", "{folded}"]
BRAND_TEMPLATES = [
    "sản phẩm {brand}",
    "các sản phẩm của hãng {brand}",
    "đồ của thương hiệu {brand}",
]
HOP_TEMPLATES = [
    "sản phẩm khác của hãng làm {name}",
    "các sản phẩm khác cùng hãng với {name}",
    "ngoài {name} thì hãng sản xuất còn làm gì khác",
]


def stable(text: str) -> int:
    return int(hashlib.sha256(f"{SEED}:{text}".encode()).hexdigest(), 16)


def priority_group(name: str) -> str | None:
    words = surface(name)
    for group, pattern in PRIORITY.items():
        if pattern.search(words):
            return group
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts", type=Path, default=ROOT / "data/processed/products-v1/search"
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / "data/processed/products-v1/eval/silver-v1"
    )
    args = parser.parse_args()

    graph = json.loads((args.artifacts / "graph.json").read_text(encoding="utf-8"))
    entities = {entity["id"]: entity for entity in graph["entities"]}
    brands = {brand["id"]: brand for brand in graph["brands"]}
    assets_of: dict[str, list[str]] = collections.defaultdict(list)
    corpus: set[str] = set()
    for line in (args.artifacts / "doc_texts.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        corpus.add(row["asset_id"])
        for eid in row["entity_ids"]:
            assets_of[eid].append(row["asset_id"])
    members: dict[str, list[str]] = collections.defaultdict(list)
    for entity in graph["entities"]:
        if entity["brand_id"]:
            members[entity["brand_id"]].append(entity["id"])

    def cluster(eid: str) -> str:
        return entities[eid]["brand_id"] or eid

    side = {eid: "dev" if stable(cluster(eid)) % 100 < DEV_SHARE else "test" for eid in entities}
    group = {eid: priority_group(entities[eid]["name"]) for eid in entities}
    rng = random.Random(SEED)
    usable = sorted(eid for eid in entities if len(assets_of[eid]) >= MIN_ASSETS)

    def pick(pool: list, n: int, is_priority) -> list:
        """Sample n items with >= PRIORITY_SHARE priority items when the pool allows it."""
        pool = sorted(pool, key=lambda item: stable(json.dumps(item)))
        hot = [item for item in pool if is_priority(item)]
        cold = [item for item in pool if not is_priority(item)]
        n_hot = min(len(hot), max(int(round(n * PRIORITY_SHARE)), n - len(cold)))
        return hot[:n_hot] + cold[: n - n_hot]

    rows: list[dict] = []

    def add(
        kind: str,
        query: str,
        eids_rel: list[list[str]],
        anchors: list[str],
        k: int,
        relation: str | None,
        extra: dict | None = None,
    ) -> None:
        sides = {side[e] for intent in eids_rel for e in intent} | {side[e] for e in anchors}
        if len(sides) != 1:
            return
        rel_by_intent = [sorted({a for e in intent for a in assets_of[e]}) for intent in eids_rel]
        rows.append(
            {
                "type": kind,
                "query": query,
                "split": sides.pop(),
                "k": k,
                "rel_by_intent": rel_by_intent,
                "target_entities": sorted({e for intent in eids_rel for e in intent}),
                "anchor_entities": anchors,
                "relation": relation,
                "priority": any(group[e] for intent in eids_rel for e in intent)
                or any(group[e] for e in anchors),
                "priority_groups": sorted(
                    {group[e] for intent in eids_rel for e in intent if group[e]}
                    | {group[e] for e in anchors if group[e]}
                ),
                **(extra or {}),
            }
        )

    # Single named (entity level): one surface form per entity, never two for the same intent.
    for eid in pick(usable, TARGETS["single_named"], lambda e: group[e] is not None):
        entity = entities[eid]
        template = SINGLE_TEMPLATES[stable(eid) % len(SINGLE_TEMPLATES)]
        query = template.format(
            name=entity["core"], lower=entity["core"].lower(), folded=normalize(entity["core"])
        )
        add("single_named", query, [[eid]], [], 1, None)

    # Single by brand: Rel = images of every entity linked to the brand.
    brand_pool = sorted(b for b, m in members.items() if len(m) >= 2)
    for brand_id in pick(
        brand_pool, TARGETS["single_brand"], lambda b: any(group[e] for e in members[b])
    ):
        template = BRAND_TEMPLATES[stable(brand_id) % len(BRAND_TEMPLATES)]
        add(
            "single_brand",
            template.format(brand=brands[brand_id]["name"]),
            [members[brand_id]],
            [],
            1,
            "MADE_BY",
        )

    # Multi-intent: k = 2 (2/3 of rows) or 3, entities from one side and different brands.
    used: set[tuple] = set()
    for split in ("dev", "test"):
        pool = [e for e in usable if side[e] == split]
        hot = [e for e in pool if group[e]]
        quota = int(
            TARGETS["multi_intent"] * (DEV_SHARE if split == "dev" else 100 - DEV_SHARE) / 100
        )
        attempts = 0
        made = 0
        while made < quota and attempts < quota * 50:
            attempts += 1
            k = 2 if made % 3 else 3
            first = rng.choice(hot if made / max(quota, 1) < PRIORITY_SHARE + 0.05 else pool)
            rest = rng.sample(pool, k - 1)
            combo = [first, *rest]
            brand_set = [entities[e]["brand_id"] for e in combo if entities[e]["brand_id"]]
            key = tuple(sorted(combo))
            if len(set(combo)) < k or len(brand_set) != len(set(brand_set)) or key in used:
                continue
            used.add(key)
            names = [entities[e]["core"] for e in combo]
            query = " và ".join(names) if k == 2 else f"{names[0]}, {names[1]} và {names[2]}"
            add("multi_intent", query, [[e] for e in combo], [], k, None)
            made += 1

    # Multi-hop (2 hops): anchor -> MADE_BY -> brand <- MADE_BY <- targets (anchor excluded).
    hop_pool = sorted(
        e for e in usable if entities[e]["brand_id"] and len(members[entities[e]["brand_id"]]) >= 2
    )
    for eid in pick(hop_pool, TARGETS["multi_hop"], lambda e: group[e] is not None):
        siblings = [o for o in members[entities[eid]["brand_id"]] if o != eid and assets_of[o]]
        if not siblings:
            continue
        template = HOP_TEMPLATES[stable(eid) % len(HOP_TEMPLATES)]
        add(
            "multi_hop",
            template.format(name=entities[eid]["core"]),
            [siblings],
            [eid],
            1,
            "MADE_BY>MADE_BY^-1",
            {"hops": 2},
        )

    for query in NO_ANSWER:
        rows.append(
            {
                "type": "no_answer",
                "query": query,
                "split": "dev" if stable(query) % 100 < DEV_SHARE else "test",
                "k": 0,
                "rel_by_intent": [],
                "target_entities": [],
                "anchor_entities": [],
                "relation": None,
                "priority": False,
                "priority_groups": [],
            }
        )

    # ---- checks (rules 1-5 of section 5.1) ------------------------------------------------
    seen_forms: dict[str, str] = {}
    seen_keys: set[tuple] = set()
    kept: list[dict] = []
    for row in rows:
        forms = {
            "n:" + normalize(row["query"]),
            "a:" + " ".join(accent_key(w) for w in surface(row["query"]).split()),
        }
        key = (
            row["type"],
            tuple(sorted(row["anchor_entities"] or row["target_entities"])),
            row["relation"],
        )
        if any(form in seen_forms for form in forms) or (
            row["type"] != "no_answer" and key in seen_keys
        ):
            continue  # duplicate intent or surface form: drop, never keep a parallel variant
        for form in forms:
            seen_forms[form] = row["query"]
        seen_keys.add(key)
        kept.append(row)
    for query in WARMUP:
        forms = {
            "n:" + normalize(query),
            "a:" + " ".join(accent_key(w) for w in surface(query).split()),
        }
        if forms & set(seen_forms):
            raise SystemExit(f"warmup query collides with the eval set: {query}")
    for index, row in enumerate(sorted(kept, key=lambda r: (r["type"], stable(r["query"])))):
        row["query_id"] = f"{row['type']}-{index:04d}"
    kept.sort(key=lambda r: r["query_id"])

    dev_entities = {
        e for r in kept if r["split"] == "dev" for e in r["target_entities"] + r["anchor_entities"]
    }
    test_entities = {
        e for r in kept if r["split"] == "test" for e in r["target_entities"] + r["anchor_entities"]
    }
    if dev_entities & test_entities:
        raise SystemExit("an entity appears in both dev and test")
    if any(a not in corpus for r in kept for intent in r["rel_by_intent"] for a in intent):
        raise SystemExit("Rel contains an asset outside the frozen corpus")
    if any(not intent for r in kept for intent in r["rel_by_intent"]):
        raise SystemExit("empty Rel for an answerable query")
    counts: dict = collections.defaultdict(lambda: collections.Counter())
    for row in kept:
        counts[row["type"]][row["split"]] += 1
        counts[row["type"]]["priority"] += row["priority"]
    for kind, counter in counts.items():
        total = counter["dev"] + counter["test"]
        if kind != "no_answer" and counter["priority"] < 0.4 * total:
            raise SystemExit(f"{kind}: priority share {counter['priority']}/{total} < 40%")

    args.out.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    for split in ("dev", "test"):
        body = "".join(
            json.dumps(r, ensure_ascii=False) + "\n" for r in kept if r["split"] == split
        )
        (args.out / f"{split}.jsonl").write_text(body, encoding="utf-8")
        digest.update(body.encode("utf-8"))
    (args.out / "warmup.jsonl").write_text(
        "".join(
            json.dumps({"query_id": f"warmup-{i:02d}", "query": q}, ensure_ascii=False) + "\n"
            for i, q in enumerate(WARMUP)
        ),
        encoding="utf-8",
    )
    manifest = {
        "version": "silver-v1",
        "silver": True,
        "seed": SEED,
        "split": "brand cluster, 40/60",
        "counts": {k: dict(v) for k, v in sorted(counts.items())},
        "queries_sha256": digest.hexdigest(),
        "graph_fingerprint": graph["fingerprint"],
        "corpus_fingerprint": graph["corpus_fingerprint"],
        "not_built": [
            "single_described (needs human judgments)",
            "3-hop (graph-lite has one relation)",
        ],
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
