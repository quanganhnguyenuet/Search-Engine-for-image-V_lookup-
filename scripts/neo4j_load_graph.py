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
    "same_as": """
        UNWIND $rows AS row
        MATCH (e:Entity {id: row.src}), (c:Entity {id: row.dst})
        MERGE (e)-[r:SAME_AS]->(c)
        SET r.snapshot = $snapshot""",
}
CLEANUP = (
    "MATCH ()-[r:MADE_BY|IS_A|SAME_AS]->() WHERE r.snapshot <> $snapshot DELETE r",
    "MATCH (n) WHERE (n:Entity OR n:Brand OR n:Category) AND n.snapshot <> $snapshot "
    "DETACH DELETE n",
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
    used = {row["dst"] for row in is_a}
    return {
        "categories": [
            {"id": c, "name": names.get(c, c), "l1": c.split(".")[0]} for c in sorted(used)
        ],
        "brands": [
            {"id": b["id"], "name": b["name"], "aliases": b.get("aliases", [])}
            for b in graph["brands"]
        ],
        "entities": entities,
        "made_by": made_by,
        "is_a": is_a,
        "same_as": same_as,
    }


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
    categories = json.loads((args.artifacts / "entity_categories.json").read_text("utf-8"))
    review = EntityReview.load(graph, review_path(args.artifacts).read_bytes())
    batches = rows(graph, categories, review)
    snapshot = graph["fingerprint"]
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
                    "fingerprint": snapshot,
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
            CALL () { MATCH ()-[r:MADE_BY]->() RETURN count(r) AS made_by }
            CALL () { MATCH ()-[r:MADE_BY {reviewed: true}]->() RETURN count(r) AS reviewed }
            CALL () { MATCH ()-[r:IS_A]->() RETURN count(r) AS is_a }
            CALL () { MATCH ()-[r:SAME_AS]->() RETURN count(r) AS same_as }
            RETURN entities, brands, categories, made_by, reviewed, is_a, same_as
            """,
            database_=args.database,
            routing_="r",
        )
    got = dict(records[0])
    expected = {name: len(items) for name, items in batches.items() if name != "same_as"}
    expected["same_as"] = len(batches["same_as"])
    expected["reviewed"] = sum(bool(r["props"].get("reviewed")) for r in batches["made_by"])
    mismatch = {k: (got[k], v) for k, v in expected.items() if got[k] != v}
    print(json.dumps({"snapshot": snapshot[:12], "loaded": got}, ensure_ascii=False))
    if mismatch:
        raise SystemExit(f"Neo4j differs from the snapshot (got, expected): {mismatch}")


if __name__ == "__main__":
    main()
