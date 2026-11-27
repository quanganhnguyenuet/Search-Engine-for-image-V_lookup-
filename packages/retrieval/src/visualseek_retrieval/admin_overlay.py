"""Admin edits to the knowledge graph: an append-only change log and its folded state.

``graph/admin/changes.jsonl`` holds one row per edited field::

    {"seq": 12, "at": "...", "actor": {"id": "...", "email": "..."}, "entity_id": "<canonical id>",
     "field": "treats", "before": [...], "after": [...], "note": ""}

The last row per (entity, field) wins. Nothing here reads the search artifacts: the API writes
rows, ``scripts/admin_release.py`` applies the folded state to a copy of the artifacts, and
``build_graph_lite.py`` / ``graph_apply.py merge`` apply it as their highest-priority source, so
a rebuild from the raw sources keeps the edits.

``graph/admin/releases.jsonl`` holds one row per release with the folded state it applied; a
field is pending while its folded value differs from the one in the last release.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from visualseek_retrieval.text import normalize

# brand: an existing brand id, "new:<name>" for a brand to create, or None for no brand.
BRAND_FIELD = "brand"
# Label fields replace the LLM label of the entity (entity_categories.json "category" ...).
LABEL_FIELDS = ("category", "product_type", "market_tier")
# Relation lists: each item becomes one edge to a value node.
LIST_FIELDS = {"treats": "TREATS", "ingredients": "HAS_INGREDIENT", "fits": "FITS"}
# Single values; "origin" also becomes an ORIGIN edge.
TEXT_FIELDS = ("origin", "form_factor", "target_audience", "material")
# True: a person checked the entity's label; it leaves the needs_review queue.
REVIEWED_FIELD = "reviewed"
ENRICH_FIELDS = (*LIST_FIELDS, *TEXT_FIELDS)
FIELDS = (BRAND_FIELD, *LABEL_FIELDS, *ENRICH_FIELDS, REVIEWED_FIELD)
MARKET_TIERS = ("luxury", "premium", "mass", "unknown", "not_applicable")
NEW_BRAND_PREFIX = "new:"
# Edge "source" of admin edits; a label an admin set or checked counts as a human label.
ADMIN_SOURCE = "admin"
HUMAN_LABEL = "human"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def fold(changes: list[dict]) -> dict[str, dict]:
    """entity id -> {field: latest value}, in change order."""
    state: dict[str, dict] = defaultdict(dict)
    for row in sorted(changes, key=lambda r: r["seq"]):
        state[row["entity_id"]][row["field"]] = row["after"]
    return dict(state)


def last_release(releases: list[dict]) -> dict:
    return releases[-1] if releases else {"through_seq": 0, "state": {}}


def pending_fields(state: dict[str, dict], released: dict[str, dict], live=None) -> dict[str, dict]:
    """entity id -> {field: value} that differ from the last release.

    A field the last release did not carry is compared with ``live(entity_id)`` (the released
    artifacts' value) when given, so an edit set back to that value is not pending."""
    out: dict[str, dict] = {}
    for entity_id, fields in state.items():
        before = released.get(entity_id, {})
        current = live(entity_id) if live is not None else {}

        changed = {
            name: value
            for name, value in fields.items()
            if (
                before[name] != value
                if name in before
                else live is None or current.get(name) != value
            )
        }
        if changed:
            out[entity_id] = changed
    return out


def load_state(admin_dir: Path) -> dict[str, dict]:
    return fold(read_jsonl(admin_dir / "changes.jsonl"))


# Entities created by a CSV import: one row per registration, the last row per entity wins.
#   {"at", "actor", "import_id", "entity_id", "name", "op": "add" | "drop",
#    "pack": evidence pack, "label": classify result | None, "enrich": enrich result | None}
# build_graph_lite.add_admin_entities and graph_apply.admin_entity_labels turn them into graph
# and category entries; once released, they are ordinary entities that admins edit as usual.
NEW_ENTITIES_FILE = "new_entities.jsonl"


def fold_new_entities(rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        latest[row["entity_id"]] = row
    return {eid: row for eid, row in latest.items() if row.get("op", "add") != "drop"}


def load_new_entities(admin_dir: Path) -> dict[str, dict]:
    return fold_new_entities(read_jsonl(admin_dir / NEW_ENTITIES_FILE))


def new_brand_id(name: str) -> str:
    slug = "-".join(normalize(name).split())
    return f"brand:{slug}" if slug else ""


def brand_target(value: str | None, brands: dict[str, dict]) -> tuple[str | None, dict | None]:
    """(brand id, new brand record or None) for a ``brand`` field value."""
    if not value:
        return None, None
    if value.startswith(NEW_BRAND_PREFIX):
        name = " ".join(value[len(NEW_BRAND_PREFIX) :].split())
        brand_id = new_brand_id(name)
        if not brand_id:
            raise ValueError(f"empty brand name: {value!r}")
        if brand_id in brands:
            return brand_id, None
        return brand_id, {"id": brand_id, "name": name, "aliases": [normalize(name)]}
    if value not in brands:
        raise ValueError(f"unknown brand: {value}")
    return value, None


def apply_made_by(graph: dict, state: dict[str, dict], members: dict[str, list[str]]) -> list[str]:
    """Apply the admin ``brand`` values to graph.json in place; returns the changed entity ids.

    ``members`` maps a canonical entity to the graph entities merged into it (identity review);
    a canonical entity's brand is written on every member, as the search reads members' edges.
    Brands left without products are dropped, as in build_graph_lite.apply_brand_review. The
    caller re-seals the graph (edge order and fingerprints).
    """
    entities = {entity["id"]: entity for entity in graph["entities"]}
    brands = {brand["id"]: brand for brand in graph["brands"]}
    wanted: dict[str, str | None] = {}
    for entity_id, fields in state.items():
        if BRAND_FIELD not in fields:
            continue
        target, created = brand_target(fields[BRAND_FIELD], brands)
        if created:
            brands[created["id"]] = created
        for member in members.get(entity_id) or [entity_id]:
            if member in entities:
                wanted[member] = target
    if not wanted:
        return []
    changed = []
    kept = []
    for edge in graph["edges"]:
        if edge["rel"] == "MADE_BY" and edge["src"] in wanted:
            continue
        kept.append(edge)
    old_brand = {e["src"]: e["dst"] for e in graph["edges"] if e["rel"] == "MADE_BY"}
    for member, target in sorted(wanted.items()):
        if target:
            kept.append(
                {
                    "src": member,
                    "rel": "MADE_BY",
                    "dst": target,
                    "source": ADMIN_SOURCE,
                    "confidence": 1.0,
                    "reviewed": True,
                    "reviewer": "admin",
                }
            )
        entities[member]["brand_id"] = target
        if old_brand.get(member) != target:
            changed.append(member)
    used = {edge["dst"] for edge in kept if edge["rel"] == "MADE_BY"}
    graph["brands"] = sorted((b for i, b in brands.items() if i in used), key=lambda b: b["id"])
    graph["edges"] = kept
    return changed


def label_override(fields: dict) -> dict:
    """The label fields an admin set, plus whether the label now counts as human-checked."""
    out = {name: fields[name] for name in LABEL_FIELDS if name in fields}
    if out or fields.get(REVIEWED_FIELD):
        out["source"] = HUMAN_LABEL
    return out


def enrich_override(fields: dict) -> dict:
    return {name: fields[name] for name in ENRICH_FIELDS if name in fields}
