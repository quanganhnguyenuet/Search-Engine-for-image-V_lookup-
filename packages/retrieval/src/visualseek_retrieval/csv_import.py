"""CSV import for /admin: read approved image rows, group them by entity and match the groups
against the graph. No I/O beyond parsing; the API job (visualseek_api/application/imports.py)
downloads, OCRs and stores.

A row is one approved image: entity name, image URL and optionally the entity id of the
labelling dataset, the source page URL and its title. The headers of the labelling CSVs are
accepted (LabelPortable/dataset_schema.py): ``Tên entity``, ``URL ảnh``, ``URL trang nguồn`` ...

Matching a group (all rows of one entity id, else of one normalised name):
  * existing: the CSV entity id is a graph entity, or the name equals the name, core name or
    alias of exactly one canonical entity;
  * near: some entity names are close (string similarity or one name contained in the other);
    an admin picks one of the candidates or says it is a new product;
  * new: nothing is close; the entity is created through the graph flow (classify, enrich,
    release).
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

from visualseek_retrieval.text import normalize

ALIASES = {
    "entity_id": ("entity_id", "entity_ID", "Entity_ID", "id"),
    "entity_name": ("entity_name", "entity", "Tên entity", "name", "ten_entity"),
    "image_url": ("image_url", "url", "URL ảnh", "image", "url_anh"),
    "page_url": ("page_url", "URL trang nguồn", "source_url", "url_trang"),
    "page_title": ("page_title", "title", "Tiêu đề nguồn", "doc_title", "tieu_de"),
    "approve": ("approve",),
}
REQUIRED = ("entity_name", "image_url")
APPROVED = {"", "yes", "y", "1", "true", "x", "có", "co", "ok"}
MAX_ROWS = 5000
MAX_NAME = 200
ENTITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{5,79}$")
# uuid5 namespace of entity ids minted for CSV rows without a dataset id.
NAMESPACE = uuid.UUID("6f1d2a8e-6c3b-4d9e-9a51-3b7c2f0e8d14")

NEAR_SCORE = 0.72
MAX_CANDIDATES = 5
CODE = re.compile(r"[^\W_][\w\-\.&'/+]*")
PARENS = re.compile(r"\s*[\(\[].*?[\)\]]")


class CsvError(ValueError):
    pass


def _key(name: str) -> str:
    return normalize(name or "").replace(" ", "")


def _delimiter(text: str) -> str:
    """The delimiter of the header line, a comma when it has none. Only the delimiter is guessed:
    csv.Sniffer also guesses the quoting, and turned doubled quotes off when its sample had none,
    which lost the later rows that did."""
    header = text.split("\n", 1)[0]
    return max(",;\t", key=header.count)


def read_rows(text: str) -> tuple[list[dict], list[dict]]:
    """(rows, skipped). Each row has every canonical column (missing ones are '') and ``line``."""
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text), delimiter=_delimiter(text), skipinitialspace=True)
    try:
        header = reader.fieldnames or []
        records = list(reader)
    except csv.Error as error:  # a cell over csv.field_size_limit(), often an unclosed quote
        raise CsvError("CSV không đọc được: có ô quá dài hoặc dấu ngoặc kép bị lỗi.") from error
    present = {_key(h): h for h in header if h}
    mapping = {}
    for name, options in ALIASES.items():
        for option in options:
            if _key(option) in present:
                mapping[name] = present[_key(option)]
                break
    missing = [name for name in REQUIRED if name not in mapping]
    if missing:
        wanted = "; ".join(" / ".join(ALIASES[name][:3]) for name in missing)
        raise CsvError(f"CSV thiếu cột: {wanted}")
    rows, skipped = [], []
    for line, raw in enumerate(records, start=2):
        row = {name: " ".join(str(raw.get(col) or "").split()) for name, col in mapping.items()}
        row = {name: row.get(name, "") for name in ALIASES} | {"line": line}
        if not row["entity_name"] and not row["image_url"]:
            continue
        reason = None
        if row["approve"].casefold() not in APPROVED:
            reason = "approve khác yes"
        elif not row["entity_name"]:
            reason = "thiếu tên entity"
        elif not re.match(r"^https?://", row["image_url"], re.I):
            reason = "URL ảnh không hợp lệ"
        elif len(row["entity_name"]) > MAX_NAME:
            reason = "tên entity quá dài"
        if reason:
            skipped.append({**row, "reason": reason})
            continue
        if row["page_url"] and not re.match(r"^https?://", row["page_url"], re.I):
            row["page_url"] = ""
        rows.append(row)
        if len(rows) > MAX_ROWS:
            raise CsvError(f"CSV có quá {MAX_ROWS} dòng ảnh; chia nhỏ file.")
    if not rows and not skipped:
        raise CsvError("CSV không có dòng dữ liệu.")
    return rows, skipped


def group_rows(rows: list[dict]) -> list[dict]:
    """One group per entity: by the CSV entity id when given, else by the normalised name."""
    groups: dict[str, dict] = {}
    for row in rows:
        csv_id = row["entity_id"] if ENTITY_ID.match(row["entity_id"] or "") else ""
        key = f"id:{csv_id}" if csv_id else f"name:{normalize(row['entity_name'])}"
        group = groups.setdefault(
            key, {"key": key, "name": row["entity_name"], "csv_entity_id": csv_id, "lines": []}
        )
        group["lines"].append(row["line"])
    return sorted(groups.values(), key=lambda g: g["name"].casefold())


def strip_parens(name: str) -> str:
    return PARENS.sub("", name).strip()


def codes(name: str) -> set[str]:
    """Model codes in a name (A129, DS-310): letters and at least one digit, 3+ characters."""
    out = set()
    for word in CODE.findall(strip_parens(name)):
        folded = normalize(word).replace(" ", "")
        if (
            len(folded) >= 3
            and any(c.isdigit() for c in folded)
            and any(c.isalpha() for c in folded)
        ):
            out.add(folded)
    return out


PREFIX_SCORE = 0.8


def similarity(a: str, b: str) -> float:
    """0..1 for two normalised names: edit similarity, or PREFIX_SCORE when the shorter name
    (2+ words) opens the longer one. Vietnamese names start with the product type, so "nuoc dua"
    may be "nuoc dua xiem ben tre", while "xe dap" is not "chuong xe dap ..."."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    wa, wb = a.split(), b.split()
    short, long = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    base = PREFIX_SCORE if len(short) >= 2 and long[: len(short)] == short else 0.0
    # Upper bounds of ratio() first: most of the graph is far from any one name.
    if 2 * min(len(a), len(b)) / (len(a) + len(b)) < NEAR_SCORE:
        return base
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    if matcher.quick_ratio() < NEAR_SCORE:
        return base
    return max(base, matcher.ratio())


@dataclass
class Match:
    kind: str  # existing | near | new
    entity_id: str | None
    score: float
    candidates: list[dict]

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "entity_id": self.entity_id,
            "score": round(self.score, 3),
            "candidates": self.candidates,
        }


class EntityMatcher:
    """Canonical entities of the graph with the strings a CSV name is compared with.

    ``names``: canonical id -> display name; ``strings``: canonical id -> names, core names and
    aliases of the entity and its merged members; ``canonical``: maps any graph id (merged
    members too) to its canonical id."""

    def __init__(self, names: dict[str, str], strings: dict[str, list[str]], canonical) -> None:
        self.names = names
        self.canonical = canonical
        self.strings: dict[str, list[str]] = {}
        self.exact: dict[str, set[str]] = defaultdict(set)
        for entity_id, values in strings.items():
            keys = list(
                dict.fromkeys(
                    k
                    for v in [names[entity_id], strip_parens(names[entity_id]), *values]
                    if (k := normalize(v))
                )
            )
            self.strings[entity_id] = keys
            for key in keys:
                self.exact[key].add(entity_id)
        self.codes = {e: codes(n) for e, n in names.items()}

    def known(self, entity_id: str) -> str | None:
        canonical = self.canonical(entity_id)
        return canonical if canonical in self.names else None

    def match(self, name: str, csv_entity_id: str = "") -> Match:
        if csv_entity_id and (known := self.known(csv_entity_id)):
            return Match("existing", known, 1.0, [self._candidate(known, 1.0, name)])
        keys = {k for k in (normalize(name), normalize(strip_parens(name))) if k}
        exact = set().union(*(self.exact.get(k, set()) for k in keys)) if keys else set()
        if len(exact) == 1:
            (entity_id,) = exact
            return Match("existing", entity_id, 1.0, [self._candidate(entity_id, 1.0, name)])
        scored = []
        for entity_id, strings in self.strings.items():
            best = max((similarity(k, s) for k in keys for s in strings), default=0.0)
            if entity_id in exact:
                best = 1.0
            if best >= NEAR_SCORE:
                scored.append((best, entity_id))
        scored.sort(key=lambda item: (-item[0], self.names[item[1]].casefold()))
        candidates = [self._candidate(e, s, name) for s, e in scored[:MAX_CANDIDATES]]
        if candidates:
            return Match("near", None, scored[0][0], candidates)
        return Match("new", None, 0.0, [])

    def _candidate(self, entity_id: str, score: float, name: str) -> dict:
        mine, theirs = codes(name), self.codes.get(entity_id, set())
        return {
            "entity_id": entity_id,
            "name": self.names[entity_id],
            "score": round(score, 3),
            # Both names carry model codes and none is shared: probably another model.
            "code_conflict": bool(mine and theirs and not mine & theirs),
        }


def new_entity_id(csv_entity_id: str, name: str, taken) -> str:
    """The dataset id when it is free, else a uuid5 of the normalised name (stable on re-import)."""
    if csv_entity_id and ENTITY_ID.match(csv_entity_id) and csv_entity_id not in taken:
        return csv_entity_id
    base = str(uuid.uuid5(NAMESPACE, normalize(name) or name))
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = str(uuid.uuid5(NAMESPACE, f"{normalize(name)}#{n}"))
    return candidate


# Evidence pack limits: scripts/graph_classify.py MAX / CLIP, so a new entity is labelled from
# the same kind of prompt as the corpus entities.
PACK_MAX = {"title": 8, "desc": 4, "ocr": 6}
PACK_CLIP = {"title": 200, "desc": 300, "ocr": 160}


def clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def evidence_pack(entity_id: str, name: str, items: list[dict]) -> dict:
    """graph_classify.build_packs for one new entity. ``items``: the group's stored images, each
    with ``page_title``, ``page`` (page summary or None) and ``ocr`` (boxes)."""
    from collections import Counter

    titles, descs, ocr, brands = Counter(), Counter(), Counter(), Counter()
    for item in items:
        page = item.get("page") or {}
        if item.get("page_title"):
            titles[clip(item["page_title"], PACK_CLIP["title"])] += 1
        if page.get("status") == "ok":
            if page.get("title") and normalize(page["title"]) != normalize(
                item.get("page_title") or ""
            ):
                titles[clip(page["title"], PACK_CLIP["title"])] += 1
            if page.get("description"):
                descs[clip(page["description"], PACK_CLIP["desc"])] += 1
            for product in page.get("products") or []:
                if product.get("brand"):
                    brands[product["brand"]] += 1
        text = " ".join(o["text"] for o in item.get("ocr") or [] if o.get("score", 0) >= 0.8)
        if len(text) >= 6:
            ocr[clip(text, PACK_CLIP["ocr"])] += 1
    snippets = [{"id": "n1", "kind": "name", "text": name}]
    for i, (brand, _) in enumerate(brands.most_common(2), 1):
        snippets.append({"id": f"b{i}", "kind": "brand", "text": brand})
    for kind, counter in (("title", titles), ("desc", descs), ("ocr", ocr)):
        for i, (text, _) in enumerate(counter.most_common(PACK_MAX[kind]), 1):
            snippets.append({"id": f"{kind[0]}{i}", "kind": kind, "text": text})
    return {"entity_id": entity_id, "name": name, "members": [entity_id], "snippets": snippets}


def page_summary(page: dict | None, image_url: str) -> dict | None:
    """The page fields the corpus keeps per source (build_corpus_manifest.PageContext.summary)."""
    if not page:
        return None
    products = []
    for item in page.get("products") or []:
        if isinstance(item, dict):
            products.append(
                {
                    "type": item.get("type") or "",
                    "name": str(item.get("name") or "")[:200],
                    "brand": str(item.get("brand") or "")[:100],
                    "model": str(item.get("model") or "")[:100],
                    "lists_this_image": image_url in (item.get("images") or []),
                }
            )
    products.sort(key=lambda product: not product["lists_this_image"])
    return {
        "status": page.get("status") or "",
        "title": page.get("title") or "",
        "h1": [str(x) for x in (page.get("h1") or [])][:3],
        "og_title": page.get("og_title") or "",
        "description": str(page.get("description") or "")[:500],
        "products": products[:5],
    }
