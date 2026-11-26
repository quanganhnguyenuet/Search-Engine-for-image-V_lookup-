"""Read-only graph view for the UI: the search snapshot as nodes and edges.

The API never reads Neo4j. This view is built from the same ``graph.json`` and
``entity_categories.json`` that ``scripts/neo4j_load_graph.py`` copies into Neo4j, so what the UI
draws is what the database holds. One difference: the edges of entities merged by the identity
review are shown on the canonical entity (the search indexes them together), where Neo4j keeps
them on each member linked by SAME_AS.
"""

from __future__ import annotations

from collections import defaultdict

ENTITY_PROPS = ("product_type", "form_factor", "target_audience", "material", "origin")
VALUE_TYPE = {
    "MADE_BY": "brand",
    "IS_A": "category",
    "TREATS": "purpose",
    "HAS_INGREDIENT": "ingredient",
    "FITS": "model",
    "ORIGIN": "place",
}
# In a many-entity (query) view only these always show; the rest show when two entities share one.
ALWAYS = frozenset({"MADE_BY", "IS_A"})
MAX_SEEDS = 40
MAX_SIBLINGS_TOTAL = 30


class GraphView:
    def __init__(self, graph: dict, categories: dict | None, review) -> None:
        categories = categories or {}
        self.review = review
        self.entities: dict[str, dict] = {e["id"]: e for e in graph["entities"]}
        self.labels: dict[str, dict] = categories.get("entities", {})
        self.merged: dict[str, int] = {}
        # value id -> (type, name)
        self.values: dict[str, tuple[str, str]] = {
            brand["id"]: ("brand", brand["name"]) for brand in graph["brands"]
        }
        category_names = categories.get("category_names", {})
        for table, kind in (
            ("purposes", "purpose"),
            ("ingredients", "ingredient"),
            ("models", "model"),
            ("places", "place"),
        ):
            for value_id, name in (categories.get(table) or {}).items():
                self.values[value_id] = (kind, name)
        # canonical entity id -> {(rel, value id): edge meta}
        self.out: dict[str, dict[tuple[str, str], dict]] = defaultdict(dict)
        # value id -> canonical entity ids, in a stable order
        self.by_value: dict[str, list[str]] = defaultdict(list)

        def add(src: str, rel: str, dst: str, meta: dict) -> None:
            owner = review.canonical(src) if review else src
            if owner not in self.entities:
                return
            if src != owner:
                self.merged[owner] = self.merged.get(owner, 0) + 1
            if (rel, dst) not in self.out[owner]:
                self.out[owner][rel, dst] = meta
                self.by_value[dst].append(owner)

        for edge in graph.get("edges", ()):
            if edge.get("rel") == "MADE_BY" and edge.get("dst") in self.values:
                add(edge["src"], "MADE_BY", edge["dst"], _meta(edge))
        for edge in categories.get("edges") or ():
            rel = edge.get("rel")
            if rel in VALUE_TYPE and rel != "IS_A" and edge.get("dst"):
                dst = edge["dst"]
                self.values.setdefault(dst, (VALUE_TYPE[rel], dst.partition(":")[2] or dst))
                add(edge["src"], rel, dst, _meta(edge))
        for entity_id, label in sorted(self.labels.items()):
            category = label.get("category")
            if category and entity_id in self.entities:
                dst = f"category:{category}"
                self.values.setdefault(dst, ("category", category_names.get(category, category)))
                add(entity_id, "IS_A", dst, {"source": label.get("source")})
        for members in self.by_value.values():
            members.sort(key=lambda e: self.entities[e]["name"].casefold())

    def _entity_node(self, entity_id: str, role: str) -> dict:
        label = self.labels.get(entity_id) or {}
        props = {key: label[key] for key in ENTITY_PROPS if label.get(key)}
        if self.merged.get(entity_id):
            props["merged_records"] = self.merged[entity_id] + 1
        return {
            "id": entity_id,
            "label": self.entities[entity_id]["name"],
            "type": "entity",
            "role": role,
            "degree": len(self.out.get(entity_id, ())),
            "props": props,
        }

    def _value_node(self, value_id: str) -> dict:
        kind, name = self.values[value_id]
        return {
            "id": value_id,
            "label": name,
            "type": kind,
            "role": "value",
            "degree": len(self.by_value.get(value_id, ())),
            "props": {},
        }

    def subgraph(
        self,
        ids: list[str],
        origins: list[str] | None = None,
        siblings: int = 0,
        shared_only: bool = False,
    ) -> dict:
        """Nodes and edges around ``ids`` (and ``origins``, drawn as the starting entities).

        ``siblings`` adds up to that many other entities per value node; ``shared_only`` keeps a
        value only when it is a brand or category, or links two or more of the given entities.
        """
        roles: dict[str, str] = {}
        for entity_id in origins or ():
            roles[self._canonical(entity_id)] = "origin"
        for entity_id in ids:
            roles.setdefault(self._canonical(entity_id), "seed")
        known = [e for e in roles if e in self.entities]
        seeds = known[:MAX_SEEDS]
        wanted: list[tuple[str, str, str, dict]] = []
        for entity_id in seeds:
            for (rel, dst), meta in self.out.get(entity_id, {}).items():
                wanted.append((entity_id, rel, dst, meta))
        if shared_only:
            links: dict[str, set[str]] = defaultdict(set)
            for src, _, dst, _ in wanted:
                links[dst].add(src)
            wanted = [w for w in wanted if w[1] in ALWAYS or len(links[w[2]]) >= 2]

        nodes: dict[str, dict] = {e: self._entity_node(e, roles[e]) for e in seeds}
        edges: list[dict] = []
        for src, rel, dst, meta in wanted:
            if dst not in nodes:
                nodes[dst] = self._value_node(dst)
            edges.append(_edge(src, rel, dst, meta))

        truncated = len(known) > len(seeds)
        if siblings:
            budget = MAX_SIBLINGS_TOTAL
            for value_id in [n for n in list(nodes) if nodes[n]["type"] != "entity"]:
                taken = 0
                for entity_id in self.by_value.get(value_id, ()):
                    if entity_id in nodes:
                        continue
                    if taken >= siblings or budget <= 0:
                        break
                    nodes[entity_id] = self._entity_node(entity_id, "sibling")
                    own = self.out[entity_id]
                    rel = next(r for (r, d) in own if d == value_id)
                    edges.append(_edge(entity_id, rel, value_id, own[rel, value_id]))
                    taken += 1
                    budget -= 1
        return {"nodes": list(nodes.values()), "edges": edges, "truncated": truncated}

    def _canonical(self, entity_id: str) -> str:
        return self.review.canonical(entity_id) if self.review else entity_id


def _meta(edge: dict) -> dict:
    return {
        "source": edge.get("source"),
        "confidence": edge.get("confidence"),
        "reviewed": edge.get("reviewed"),
    }


def _edge(src: str, rel: str, dst: str, meta: dict) -> dict:
    return {
        "id": f"{src}|{rel}|{dst}",
        "source": src,
        "target": dst,
        "rel": rel,
        "origin": meta.get("source"),
        "confidence": meta.get("confidence"),
        "reviewed": meta.get("reviewed"),
    }
