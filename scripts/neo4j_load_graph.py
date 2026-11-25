"""Load the search graph snapshot into Neo4j, to look at it and review it there (P2/P5).

The API never reads Neo4j: it serves ``search/graph.json``, the snapshot the Qdrant collection
was built from. This script copies that snapshot into Neo4j:

  (:Entity {id, name, core, product_type, codes, model_tail, aliases})
  (:Brand {id, name, aliases})            (:Category {id, name, l1})
  (:Entity)-[:MADE_BY {source, confidence, reviewed, reviewer}]->(:Brand)
  (:Entity)-[:IS_A]->(:Category)          (:Entity)-[:SAME_AS]->(:Entity)   identity review
  (:GraphSnapshot {fingerprint, ...})     what was loaded, and when

Every node and relationship carries the ``snapshot`` fingerprint. A reload MERGEs by id and
then deletes what the new snapshot no longer has (a brand edge the review moved), so the
database always equals one snapshot. Nothing is written back to graph.json.

Needs the Neo4j container (``docker compose --profile graph up -d --wait neo4j``),
NEO4J_AUTH in .env and the driver (``pip install "neo4j>=5.28,<6"``).

    python scripts/neo4j_load_graph.py            # then http://127.0.0.1:7474
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from visualseek_retrieval.entity_review import EntityReview  # noqa: E402

ARTIFACTS = ROOT / "data/processed/products-v1/search"
PACKAGED_REVIEW = (
    ROOT / "services/api/src/visualseek_api/application/products-v1-identity-review.json"
)
BATCH = 1000

SCHEMA = (
    "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT brand_id IF NOT EXISTS FOR (n:Brand) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT category_id IF NOT EXISTS FOR (n:Category) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT purpose_id IF NOT EXISTS FOR (n:Purpose) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT ingredient_id IF NOT EXISTS FOR (n:Ingredient) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT target_model_id IF NOT EXISTS FOR (n:TargetModel) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT place_id IF NOT EXISTS FOR (n:Place) REQUIRE n.id IS UNIQUE",
    "CREATE INDEX entity_name IF NOT EXISTS FOR (n:Entity) ON (n.name)",
    "CREATE INDEX brand_name IF NOT EXISTS FOR (n:Brand) ON (n.name)",
)
WRITES = {
    "categories": """
        UNWIND $rows AS row
        MERGE (c:Category {id: row.id})
        SET c.name = row.name, c.l1 = row.l1, c.snapshot = $snapshot""",
    "brands": """
        UNWIND $rows AS row
        MERGE (b:Brand {id: row.id})
        SET b.name = row.name, b.aliases = row.aliases, b.snapshot = $snapshot""",
    "purposes": """
        UNWIND $rows AS row
        MERGE (p:Purpose {id: row.id})
        SET p.name = row.name, p.snapshot = $snapshot""",
    "ingredients": """
        UNWIND $rows AS row
        MERGE (i:Ingredient {id: row.id})
        SET i.name = row.name, i.snapshot = $snapshot""",
    "models": """
        UNWIND $rows AS row
        MERGE (m:TargetModel {id: row.id})
        SET m.name = row.name, m.snapshot = $snapshot""",
    "places": """
        UNWIND $rows AS row
        MERGE (pl:Place {id: row.id})
        SET pl.name = row.name, pl.snapshot = $snapshot""",
    "entities": """
        UNWIND $rows AS row
        MERGE (e:Entity {id: row.id})
        SET e += row.props, e.snapshot = $snapshot""",
    "made_by": """
        UNWIND $rows AS row
        MATCH (e:Entity {id: row.src}), (b:Brand {id: row.dst})
        MERGE (e)-[r:MADE_BY]->(b)
        SET r += row.props, r.snapshot = $snapshot""",
    "is_a": """
        UNWIND $rows AS row
        MATCH (e:Entity {id: row.src}), (c:Category {id: row.dst})
        MERGE (e)-[r:IS_A]->(c)
        SET r.snapshot = $snapshot""",
    "treats": """
        UNWIND $rows AS row
        MATCH (e:Entity {id: row.src}), (p:Purpose {id: row.dst})
        MERGE (e)-[r:TREATS]->(p)
        SET r += row.props, r.snapshot = $snapshot""",
    "has_ingredient": """
        UNWIND $rows AS row
        MATCH (e:Entity {id: row.src}), (i:Ingredient {id: row.dst})
        MERGE (e)-[r:HAS_INGREDIENT]->(i)
        SET r += row.props, r.snapshot = $snapshot""",
    "fits": """
        UNWIND $rows AS row
        MATCH (e:Entity {id: row.src}), (m:TargetModel {id: row.dst})
        MERGE (e)-[r:FITS]->(m)
        SET r += row.props, r.snapshot = $snapshot""",
    "origin": """
        UNWIND $rows AS row
        MATCH (e:Entity {id: row.src}), (pl:Place {id: row.dst})
        MERGE (e)-[r:ORIGIN]->(pl)
        SET r += row.props, r.snapshot = $snapshot""",
    "same_as": """
        UNWIND $rows AS row
        MATCH (e:Entity {id: row.src}), (c:Entity {id: row.dst})
        MERGE (e)-[r:SAME_AS]->(c)
        SET r.snapshot = $snapshot""",
}
CLEANUP = (
    "MATCH ()-[r:MADE_BY|IS_A|SAME_AS|TREATS|HAS_INGREDIENT|FITS|ORIGIN]->() "
    "WHERE r.snapshot <> $snapshot DELETE r",
    "MATCH (n) WHERE (n:Entity OR n:Brand OR n:Category OR n:Purpose OR n:Ingredient "
    "OR n:TargetModel OR n:Place) AND n.snapshot <> $snapshot DETACH DELETE n",
)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def rows(graph: dict, categories: dict, review: EntityReview) -> dict[str, list[dict]]:
    """Node and relationship rows of one snapshot, in the order they must be written."""
    labels = categories["entities"]
    names = categories["category_names"]
    entities = []
    for entity in graph["entities"]:
        label = labels.get(review.canonical(entity["id"])) or {}
        props = {
            "name": entity["name"],
            "core": entity.get("core"),
            "aliases": entity.get("aliases", []),
            "codes": entity.get("codes", []),
            "model_tail": entity.get("model_tail"),
            "product_type": label.get("product_type"),
            "form_factor": label.get("form_factor"),
            "target_audience": label.get("target_audience"),
            "material": label.get("material"),
            "origin": label.get("origin"),
        }
        entities.append({"id": entity["id"], "props": {k: v for k, v in props.items() if v}})
    made_by = [
        {
            "src": edge["src"],
            "dst": edge["dst"],
            "props": {
                key: edge[key]
                for key in ("source", "confidence", "reviewed", "reviewer")
                if edge.get(key) is not None
            },
        }
        for edge in graph["edges"]
        if edge["rel"] == "MADE_BY"
    ]
    ids = {entity["id"] for entity in graph["entities"]}
    is_a = [
        {"src": entity_id, "dst": label["category"]}
        for entity_id, label in sorted(labels.items())
        if entity_id in ids and label.get("category")
    ]
    same_as = [
        {"src": entity_id, "dst": review.canonical(entity_id)}
        for entity_id in sorted(ids)
        if review.canonical(entity_id) != entity_id and review.canonical(entity_id) in ids
    ]
    cat_edges = categories.get("edges") or []
    by_rel: dict[str, list[dict]] = {
        "TREATS": [],
        "HAS_INGREDIENT": [],
        "FITS": [],
        "ORIGIN": [],
    }
    for edge in [*graph.get("edges", ()), *cat_edges]:
        rel = edge.get("rel")
        if rel in by_rel and edge.get("src") in ids and edge.get("dst"):
            props = {
                key: edge[key]
                for key in ("source", "confidence", "reviewed", "reviewer")
                if edge.get(key) is not None
            }
            by_rel[rel].append({"src": edge["src"], "dst": edge["dst"], "props": props})
    used = {row["dst"] for row in is_a}

    def _node_rows(table_key: str, edges_for_rel: list[dict]) -> list[dict]:
        table = categories.get(table_key) or {}
        used_ids = {r["dst"] for r in edges_for_rel}
        return [{"id": nid, "name": table.get(nid, nid)} for nid in sorted(used_ids)]

    return {
        "categories": [
            {"id": c, "name": names.get(c, c), "l1": c.split(".")[0]} for c in sorted(used)
        ],
        "brands": [
            {"id": b["id"], "name": b["name"], "aliases": b.get("aliases", [])}
            for b in graph["brands"]
        ],
        "purposes": _node_rows("purposes", by_rel["TREATS"]),
        "ingredients": _node_rows("ingredients", by_rel["HAS_INGREDIENT"]),
        "models": _node_rows("models", by_rel["FITS"]),
        "places": _node_rows("places", by_rel["ORIGIN"]),
        "entities": entities,
        "made_by": made_by,
        "is_a": is_a,
        "treats": by_rel["TREATS"],
        "has_ingredient": by_rel["HAS_INGREDIENT"],
        "fits": by_rel["FITS"],
        "origin": by_rel["ORIGIN"],
        "same_as": same_as,
    }


def snapshot_id(graph: dict, categories_raw: bytes) -> str:
    """The enriched edges live in entity_categories.json, not in graph.json: an id made of the
    graph fingerprint alone would never delete an edge that a re-merge dropped."""
    return f"{graph['fingerprint']}:{hashlib.sha256(categories_raw).hexdigest()[:12]}"


def review_path(artifacts: Path) -> Path:
    """Same resolution as the API: an artifact-dir review wins over the packaged one."""
    local = artifacts / "identity_review.json"
    return local if local.exists() else PACKAGED_REVIEW


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=ARTIFACTS)
    parser.add_argument("--uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--database", default="neo4j")
    args = parser.parse_args()
    load_env(ROOT / ".env")
    from neo4j import GraphDatabase

    graph = json.loads((args.artifacts / "graph.json").read_text(encoding="utf-8"))
    categories_raw = (args.artifacts / "entity_categories.json").read_bytes()
    categories = json.loads(categories_raw.decode("utf-8"))
    review = EntityReview.load(graph, review_path(args.artifacts).read_bytes())
    batches = rows(graph, categories, review)
    snapshot = snapshot_id(graph, categories_raw)
    user, _, password = os.environ["NEO4J_AUTH"].partition("/")

    with GraphDatabase.driver(args.uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        for statement in SCHEMA:
            driver.execute_query(statement, database_=args.database)
        for name, query in WRITES.items():
            items = batches[name]
            for start in range(0, len(items), BATCH):
                driver.execute_query(
                    query,
                    {"rows": items[start : start + BATCH], "snapshot": snapshot},
                    database_=args.database,
                )
        for statement in CLEANUP:
            driver.execute_query(statement, {"snapshot": snapshot}, database_=args.database)
        driver.execute_query(
            "MERGE (s:GraphSnapshot {id: 'search'}) SET s += $props",
            {
                "props": {
                    "fingerprint": graph["fingerprint"],
                    "snapshot": snapshot,
                    "corpus_fingerprint": graph["corpus_fingerprint"],
                    "loaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "made_by_review": json.dumps(graph.get("made_by_review"), sort_keys=True),
                }
            },
            database_=args.database,
        )
        # Read back: the database must hold exactly this snapshot.
        records, _, _ = driver.execute_query(
            """
            CALL () { MATCH (n:Entity) RETURN count(n) AS entities }
            CALL () { MATCH (n:Brand) RETURN count(n) AS brands }
            CALL () { MATCH (n:Category) RETURN count(n) AS categories }
            CALL () { MATCH (n:Purpose) RETURN count(n) AS purposes }
            CALL () { MATCH (n:Ingredient) RETURN count(n) AS ingredients }
            CALL () { MATCH (n:TargetModel) RETURN count(n) AS models }
            CALL () { MATCH (n:Place) RETURN count(n) AS places }
            CALL () { MATCH ()-[r:MADE_BY]->() RETURN count(r) AS made_by }
            CALL () { MATCH ()-[r:MADE_BY {reviewed: true}]->() RETURN count(r) AS reviewed }
            CALL () { MATCH ()-[r:IS_A]->() RETURN count(r) AS is_a }
            CALL () { MATCH ()-[r:TREATS]->() RETURN count(r) AS treats }
            CALL () { MATCH ()-[r:HAS_INGREDIENT]->() RETURN count(r) AS has_ingredient }
            CALL () { MATCH ()-[r:FITS]->() RETURN count(r) AS fits }
            CALL () { MATCH ()-[r:ORIGIN]->() RETURN count(r) AS origin }
            CALL () { MATCH ()-[r:SAME_AS]->() RETURN count(r) AS same_as }
            RETURN entities, brands, categories, purposes, ingredients, models, places,
                   made_by, reviewed, is_a, treats, has_ingredient, fits, origin, same_as
            """,
            database_=args.database,
            routing_="r",
        )
    got = dict(records[0])
    expected = {name: len(items) for name, items in batches.items() if name != "same_as"}
    expected["same_as"] = len(batches["same_as"])
    expected["reviewed"] = sum(bool(r["props"].get("reviewed")) for r in batches["made_by"])
    mismatch = {k: (got[k], v) for k, v in expected.items() if got[k] != v}
    print(json.dumps({"snapshot": snapshot[-12:], "loaded": got}, ensure_ascii=False))
    if mismatch:
        raise SystemExit(f"Neo4j differs from the snapshot (got, expected): {mismatch}")


if __name__ == "__main__":
    main()
