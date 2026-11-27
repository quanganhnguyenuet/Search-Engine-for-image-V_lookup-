"""Preview of one entity's relations after the next release.

The relation edges are built by the release itself (scripts/admin_release.apply_to_categories)
on a copy of this entity's entry, so value folding and the extra purpose buckets show exactly as
they will be applied. The result is a subgraph in the shape of GraphView.subgraph where nodes and
edges also say whether the release adds or removes them (``change``).
"""

from __future__ import annotations

import copy

from visualseek_api.application.graph_view import (
    ENTITY_PROPS,
    MAX_SIBLINGS_TOTAL,
    VALUE_TYPE,
    GraphView,
)


def preview_relations(
    release,
    view: GraphView,
    index,
    categories: dict,
    entity_id: str,
    fields: dict,
    siblings: int,
) -> dict:
    """``release``: the admin_release module; ``fields``: saved drafts plus unsaved values."""
    live = dict(view.out.get(entity_id, {}))
    after = dict(live)
    names: dict[str, str] = {}
    old_entry = index.labels.get(entity_id) or {}
    new_entry = old_entry
    blocked = None

    if fields:
        own = [e for e in categories.get("edges") or () if e["src"] == entity_id]
        stored = categories.get("entities", {}).get(entity_id)
        tables = [table for table, _ in release.RELATION_TABLES.values()]
        document = {
            "entities": {entity_id: copy.deepcopy(stored)} if stored else {},
            "edges": [dict(e) for e in own],
            "coverage": {},
            **{table: dict(categories.get(table) or {}) for table in tables},
        }
        members = index.members(entity_id)
        graph = {
            "entities": [index.graph_entities[m] for m in members if m in index.graph_entities],
            "edges": [],
        }
        release.apply_to_categories(
            document, {entity_id: fields}, graph, {entity_id: members}, index.category_names
        )
        new_entry = document["entities"].get(entity_id) or {}
        if not new_entry and set(fields) - {"brand", "reviewed"}:
            blocked = "category_required"
        if new_entry:
            # IS_A comes from the entry's category, the other relations from the category edges.
            for edge in own:
                if edge["rel"] != "IS_A":
                    after.pop((edge["rel"], edge["dst"]), None)
            if old_entry.get("category"):
                after.pop(("IS_A", f"category:{old_entry['category']}"), None)
            for edge in document["edges"]:
                key = (edge["rel"], edge["dst"])
                if edge["rel"] == "IS_A":
                    after[key] = live.get(key) or {"source": new_entry.get("source")}
                    names[edge["dst"]] = new_entry.get("category_name") or edge["dst"]
                    continue
                after[key] = live.get(key) or {"source": edge.get("source")}
                for table in tables:
                    if edge["dst"] in document[table]:
                        names[edge["dst"]] = document[table][edge["dst"]]
        if "brand" in fields:
            from visualseek_retrieval.admin_overlay import brand_target

            for key in [k for k in after if k[0] == "MADE_BY"]:
                del after[key]
            brand_id, created = brand_target(fields["brand"], index.brands)
            if brand_id:
                key = ("MADE_BY", brand_id)
                after[key] = live.get(key) or {"source": "admin", "reviewed": True}
                if created:
                    names[brand_id] = created["name"]

    props = {k: new_entry[k] for k in ENTITY_PROPS if new_entry.get(k)}
    if view.merged.get(entity_id):
        props["merged_records"] = view.merged[entity_id] + 1
    entity_node = {**view._entity_node(entity_id, "origin"), "props": props, "degree": len(after)}
    nodes: dict[str, dict] = {entity_id: entity_node}
    edges: list[dict] = []
    table_rows: list[dict] = []
    value_changes: dict[str, set[str | None]] = {}
    for rel, dst in sorted(live.keys() | after.keys()):
        key = (rel, dst)
        status = "added" if key not in live else "removed" if key not in after else None
        meta = after[key] if key in after else live[key]
        if dst not in nodes:
            if dst in view.values:
                nodes[dst] = view._value_node(dst)
            else:  # a value no product has yet
                nodes[dst] = {
                    "id": dst,
                    "label": names.get(dst, dst),
                    "type": VALUE_TYPE[rel],
                    "role": "value",
                    "degree": 1,
                    "props": {},
                }
        value_changes.setdefault(dst, set()).add(status)
        edges.append(_edge(entity_id, rel, dst, meta, status))
        table_rows.append(
            {
                "rel": rel,
                "target": dst,
                "label": nodes[dst]["label"],
                "source": meta.get("source"),
                "reviewed": meta.get("reviewed"),
                "shared_with": len(set(view.by_value.get(dst, ())) - {entity_id}),
                "change": status,
            }
        )
    for dst, statuses in value_changes.items():
        nodes[dst]["change"] = next(iter(statuses)) if len(statuses) == 1 else None

    budget = MAX_SIBLINGS_TOTAL
    for value_id in value_changes if siblings else ():
        taken = 0
        for other in view.by_value.get(value_id, ()):
            if other in nodes:
                continue
            if taken >= siblings or budget <= 0:
                break
            nodes[other] = view._entity_node(other, "sibling")
            own_edges = view.out[other]
            rel = next(r for (r, d) in own_edges if d == value_id)
            edges.append(_edge(other, rel, value_id, own_edges[rel, value_id], None))
            taken += 1
            budget -= 1

    attributes = []
    for field in ("category_name", *ENTITY_PROPS):
        before, now = old_entry.get(field) or "", new_entry.get(field) or ""
        if before != now:
            attributes.append({"field": field, "before": before, "after": now})
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "truncated": False,
        "table": table_rows,
        "attributes": attributes,
        "added": sum(row["change"] == "added" for row in table_rows),
        "removed": sum(row["change"] == "removed" for row in table_rows),
        "blocked": blocked,
    }


def _edge(src: str, rel: str, dst: str, meta: dict, change: str | None) -> dict:
    return {
        "id": f"{src}|{rel}|{dst}",
        "source": src,
        "target": dst,
        "rel": rel,
        "origin": meta.get("source"),
        "confidence": meta.get("confidence"),
        "reviewed": meta.get("reviewed"),
        "change": change,
    }
