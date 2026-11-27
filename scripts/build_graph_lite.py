"""Build the free-source entity graph (MVP stand-in for P2) from the frozen products-v1 corpus.

Only sources that cost no LLM quota are used:
  * aliases: the entity name, the name without its parenthetical, and unique model codes;
  * MADE_BY: JSON-LD ``brand`` values that also occur in the entity name, plus capitalised
    Latin tokens that start a brand-like span in at least two entity names.

Every edge records ``source`` and ``confidence`` and is ``reviewed = false``: this graph is
silver, not the reviewed graph of PLAN_PRODUCTS_V1.md section 4.1.

The MADE_BY review (scripts/brand_review_server.py) is then applied: the last decision per
entity keeps the edge (``reviewed = true``), removes it, or moves the product to another
brand; eval/graph-review/brand_apply.json names the new brands and the targets that are not
manufacturers. The decisions must have been made on this same silver graph. Brand edits made in
/admin (graph/admin/changes.jsonl) are applied after it.
Output: ``<out>/graph.json`` with a fingerprint over the sorted edge list.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from visualseek_retrieval.text import normalize  # noqa: E402

BRAND_DECISIONS = ROOT / "eval/graph-review/brand_decisions.jsonl"
BRAND_APPLY = ROOT / "eval/graph-review/brand_apply.json"
ADMIN_DIR = ROOT / "graph/admin"  # admin change log, applied last

# Normalised JSON-LD "brand" values that are categories, generic drugs or noise, not brands.
BRAND_STOP = {
    "groupset",
    "xi mang",
    "ich nhan",
    "giuong ngu",
    "xe nang toyota",
    "black rice",
    "net",
    "safety",
    "silicon",
    "global",
    "hay",
    "campus",
    "montelukast",
    "citalopram",
    "pm",
    "gs",
    "sma",
    "diesel",
    "edu science",
    "rocket",
    "stella",
    "elite",
    "halo",
    "sino",
    "uma",
    "led",
    "dt",
    "sgk",
    "an",
    "android",
    "cocktail",
    "bugi",
    "logo",
    "camera",
    "snack",
    "hdpe",
    "mppt",
    "uav",
    "ocop",
    "vnch",
    "a",
    "van",
    "tan",
    "canh",
    "calci",
    "american",
    "maxi",
    "moist",
    "hunt",
    "john",
    "north",
    "under",
    "shan",
    "shiva",
    "prednison",
    "spiramycin",
    "doxycyclin",
    "colchicine",
    "nalidixic",
    "fexofenadin",
    "lon",
    "baga",
    "gyudon",
    "ruby",
    "hope",
    "liqui",
    "tide",
    "mobin",
    "augbactam",
    "cuon",
    "balo",
}
# Product lines that JSON-LD or the name heuristic report as a brand.
LINE_TO_BRAND = {
    "ipad": "apple",
    "kindle": "amazon",
    "thinkpad": "lenovo",
    "lumix": "panasonic",
    "soundcore": "anker",
    "galaxy": "samsung",
    "redmi": "xiaomi",
    "philips avent": "philips",
    "bmw genuine": "bmw",
    "legrand arteor": "legrand",
    "wd 40 specialist": "wd 40",
    "rays versus": "rays",
    "4moms mamaroo": "4moms",
    "mamaroo": "4moms",
    "haruharu wonder": "haruharu",
    "anafi": "parrot",
    "xiaomi 70mai": "70mai",
    "alienware": "dell",
}
UNIT = re.compile(
    r"^\d+([.,]\d+)?(mm|cm|m|ml|mg|mcg|g|kg|l|gb|tb|mb|in|inch|w|kw|v|mah|iu|hz|k|p|x|s|ah|lb)$"
)
WORD = re.compile(r"[^\W_][\w\-\.&'/+]*")


def strip_parens(name: str) -> str:
    return re.sub(r"\s*[\(\[].*?[\)\]]", "", name).strip()


def parentheticals(name: str) -> list[str]:
    return [part.strip() for part in re.findall(r"[\(\[](.*?)[\)\]]", name) if part.strip()]


def is_code(token: str) -> bool:
    """Model codes such as A129, DS-310, RC132V; units such as 25mm or 5G are not codes."""
    folded = normalize(token).replace(" ", "")
    if len(folded) < 3 or UNIT.match(folded):
        return False
    digits = sum(ch.isdigit() for ch in folded)
    letters = sum(ch.isalpha() for ch in folded)
    return letters >= 1 and digits >= 2 or letters >= 2 and digits >= 1 and len(folded) >= 4


def is_latin_ascii(token: str) -> bool:
    return all(ord(ch) < 128 for ch in token)


def jsonld_brands(assets: list[dict]) -> dict[str, collections.Counter]:
    found: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for asset in assets:
        for source in asset["sources"]:
            for product in (source.get("page") or {}).get("products") or []:
                brand = (product.get("brand") or "").strip()
                if brand:
                    found[source["entity_id"]][brand] += 1
    return found


def brand_surface_in_name(name: str, brand_key: str) -> str | None:
    """Return the name's own spelling of the brand when it occurs capitalised in the name."""
    words = WORD.findall(strip_parens(name))
    size = len(brand_key.split())
    for start in range(len(words) - size + 1):
        span = words[start : start + size]
        if normalize(" ".join(span)) != brand_key:
            continue
        if not (span[0][0].isupper() or span[0][0].isdigit()):
            continue
        # A Vietnamese phrase opening the name ("Tượng Phật ...") is the product type.
        if start == 0 and not is_latin_ascii(" ".join(span)):
            continue
        return " ".join(span)
    return None


def entity_record(eid: str, name: str, brand_key: str | None, brand_id: str | None) -> dict:
    """A graph entity without aliases: core name, notes, model codes, model tail, Latin head."""
    core = strip_parens(name)
    words = WORD.findall(core)
    codes = [word for word in words if is_code(word)]
    tail = None
    if brand_key:
        # The model name after the brand ("Kraken", "MX Master 3") is a common way to ask.
        idx = next(
            (
                i
                for i in range(len(words))
                if normalize(" ".join(words[i : i + len(brand_key.split())])) == brand_key
            ),
            None,
        )
        rest = words[idx + len(brand_key.split()) :] if idx is not None else []
        if rest and (any(ch.isdigit() for ch in "".join(rest)) or rest[0][0].isupper()):
            tail = " ".join(rest)
    # Latin head of the name ("Kindle Paperwhite" in "Kindle Paperwhite đọc sách"): the
    # leading run of capitalised or numbered words, when a Vietnamese descriptor follows.
    head_len = 0
    for word in words:
        if not (word[0].isupper() or any(ch.isdigit() for ch in word)):
            break
        head_len += 1
    head = " ".join(words[:head_len]) if 2 <= head_len < len(words) else None
    return {
        "id": eid,
        "name": name,
        "core": core,
        "notes": parentheticals(name),
        "brand_id": brand_id,
        "codes": codes,
        "model_tail": tail,
        "latin_head": head,
    }


def build(entities: list[dict], assets: list[dict]) -> dict:
    lowercase_words = collections.Counter(
        word.lower()
        for entity in entities
        for word in WORD.findall(entity["name"])[1:]
        if word[0].islower()
    )

    # Brand vocabulary: JSON-LD brands that occur in the entity name ...
    vocab: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    sources: dict[str, set[str]] = collections.defaultdict(set)
    by_entity = jsonld_brands(assets)
    for entity in entities:
        for brand in by_entity.get(entity["entity_id"], {}):
            key = normalize(brand)
            surface = key and brand_surface_in_name(entity["name"], key)
            if surface:
                vocab[key][surface] += 1
                sources[key].add("jsonld")
    # ... plus the first capitalised ASCII token of a name when >= 2 names share it.
    first_capitalised: collections.Counter = collections.Counter()
    spelled: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for entity in entities:
        for word in WORD.findall(strip_parens(entity["name"])):
            if (
                word[0].isupper()
                and is_latin_ascii(word)
                and word.lower() not in lowercase_words
                and not any(ch.isdigit() for ch in word)
                and len(word) >= 2
            ):
                first_capitalised[normalize(word)] += 1
                spelled[normalize(word)][word] += 1
                break
    for key, count in first_capitalised.items():
        if count >= 2:
            vocab[key].update(spelled[key])
            sources[key].add("name")
    for key in list(vocab):
        if key in BRAND_STOP:
            del vocab[key]

    def canonical(key: str) -> str:
        return LINE_TO_BRAND.get(key, key)

    brands: dict[str, dict] = {}
    edges: list[dict] = []
    out_entities: list[dict] = []
    keys_by_length = sorted(vocab, key=lambda key: (-len(key.split()), key))
    for entity in entities:
        eid, name = entity["entity_id"], entity["name"]
        core = strip_parens(name)
        folded = f" {normalize(core)} "
        brand_key = None
        best = None
        for key in keys_by_length:
            position = folded.find(f" {key} ")
            if position < 0 or not brand_surface_in_name(name, key):
                continue
            rank = (position, -len(key.split()))
            if best is None or rank < best:
                best, brand_key = rank, key
        brand_id = None
        if brand_key:
            target = canonical(brand_key)
            brand_id = f"brand:{target.replace(' ', '-')}"
            display = (vocab.get(target) or vocab[brand_key]).most_common(1)[0][0]
            brands.setdefault(brand_id, {"id": brand_id, "name": display, "aliases": set()})
            # A product line ("Kindle") is not a synonym of its maker: "Kindle Paperwhite" must
            # not read as "any Amazon product". Only the canonical brand key is an alias.
            brands[brand_id]["aliases"].add(target)
            edge_sources = sorted(sources[brand_key])
            edges.append(
                {
                    "src": eid,
                    "rel": "MADE_BY",
                    "dst": brand_id,
                    "source": "+".join(edge_sources),
                    "confidence": 0.9 if "jsonld" in edge_sources else 0.7,
                    "reviewed": False,
                }
            )
        out_entities.append(entity_record(eid, name, brand_key, brand_id))

    # Dictionary aliases; codes and model tails count only when they name a single entity.
    alias_owners: dict[str, set[str]] = collections.defaultdict(set)
    for entity in out_entities:
        for alias in {entity["name"], entity["core"]}:
            alias_owners[normalize(alias)].add(entity["id"])
    weak: dict[str, set[str]] = collections.defaultdict(set)
    for entity in out_entities:
        extra = [entity[key] for key in ("model_tail", "latin_head") if entity[key]]
        for alias in entity["codes"] + extra:
            key = normalize(alias)
            if len(key.replace(" ", "")) >= 3 and key not in lowercase_words:
                weak[key].add(entity["id"])
    brand_keys = {alias for brand in brands.values() for alias in brand["aliases"]}
    for key, owners in weak.items():
        if len(owners) == 1 and key not in alias_owners and key not in brand_keys:
            alias_owners[key].update(owners)
    aliases_by_entity: dict[str, list[str]] = collections.defaultdict(list)
    for key, owners in alias_owners.items():
        for owner in owners:
            aliases_by_entity[owner].append(key)
    for entity in out_entities:
        entity["aliases"] = sorted(aliases_by_entity[entity["id"]])

    brand_list = [
        {**brand, "aliases": sorted(brand["aliases"])}
        for brand in sorted(brands.values(), key=lambda b: b["id"])
    ]
    return seal(
        {
            "version": "graph-lite-v1",
            "reviewed": False,
            "entities": out_entities,
            "brands": brand_list,
            "edges": edges,
        }
    )


def seal(graph: dict) -> dict:
    """Sort the edges and (re)compute the edge and alias fingerprints."""
    graph["edges"].sort(key=lambda edge: (edge["src"], edge["rel"], edge["dst"]))
    graph["fingerprint"] = hashlib.sha256(
        json.dumps(graph["edges"], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    # Aliases change S1 behaviour without changing edges; they get their own fingerprint.
    graph["aliases_sha256"] = hashlib.sha256(
        json.dumps(
            [[e["id"], e["aliases"]] for e in graph["entities"]]
            + [[b["id"], b["aliases"]] for b in graph["brands"]],
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return graph


def apply_brand_review(graph: dict, decisions: list[dict], plan: dict) -> dict:
    """Apply the MADE_BY review to a freshly built silver graph; returns counts.

    ``decisions`` are the rows of brand_decisions.jsonl (the last one per entity wins),
    ``plan`` is brand_apply.json. A decision made on another graph is refused: its edge may
    not be the one the reviewer saw.
    """
    latest = {row["entity_id"]: row for row in decisions}
    base = graph["fingerprint"]
    stale = {row["graph_fingerprint"] for row in latest.values()} - {base}
    if stale:
        raise SystemExit(f"brand decisions were made on another graph: {sorted(stale)}")
    not_a_brand, new_brands = plan.get("not_a_brand", {}), plan.get("new_brands", {})
    entities = {entity["id"]: entity for entity in graph["entities"]}
    brands = {brand["id"]: brand for brand in graph["brands"]}
    counts = collections.Counter()
    kept = []
    for edge in graph["edges"]:
        decision = latest.get(edge["src"]) if edge["rel"] == "MADE_BY" else None
        if decision is None:
            kept.append(edge)
            continue
        if decision["from_brand"] != edge["dst"]:
            raise SystemExit(f"{edge['src']}: decided for {decision['from_brand']}")
        verdict, target = decision["verdict"], decision.get("to_brand")
        if verdict == "reassign" and target in not_a_brand:
            verdict = "reject"
        if verdict == "reject":
            entities[edge["src"]]["brand_id"] = None
            counts["removed"] += 1
            continue
        if verdict == "reassign":
            if target.startswith("new:"):
                if target not in new_brands:
                    raise SystemExit(f"{target}: add it to brand_apply.json new_brands")
                spec = new_brands[target]
                brands.setdefault(spec["id"], {**spec, "aliases": sorted(spec["aliases"])})
                target = spec["id"]
            elif target not in brands:
                raise SystemExit(f"{edge['src']}: unknown brand {target}")
            edge = {**edge, "dst": target, "source": "review", "confidence": 1.0}
            entities[edge["src"]]["brand_id"] = target
            counts["moved"] += 1
        else:
            counts["confirmed"] += 1
        kept.append({**edge, "reviewed": True, "reviewer": decision["reviewer"]})
    used = {edge["dst"] for edge in kept if edge["rel"] == "MADE_BY"}
    counts["brands_dropped"] = sum(brand_id not in used for brand_id in brands)
    graph["brands"] = sorted((b for i, b in brands.items() if i in used), key=lambda b: b["id"])
    graph["edges"] = kept
    graph["made_by_review"] = {"base_fingerprint": base, **counts}
    return seal(graph)


def canonical_members(corpus: Path) -> dict[str, list[str]]:
    """canonical entity id -> graph entity ids merged into it (graph/evidence.jsonl packs)."""
    path = corpus / "graph/evidence.jsonl"
    if not path.exists():
        return {}
    rows = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
    return {row["entity_id"]: row["members"] for row in rows}


def add_admin_entities(graph: dict, rows: list[dict]) -> list[str]:
    """Add the entities created by a CSV import in /admin (graph/admin/new_entities.jsonl) that
    the graph does not have yet; returns their ids. The caller re-seals the graph.

    Brand: the LLM's brand, else a JSON-LD brand of the source pages. An existing brand (by name
    or alias) gets a MADE_BY edge; an unknown one only when the entity name spells it
    capitalised, as for corpus brands (build()). Aliases: name and core name, plus model codes,
    model tail and Latin head when no other entity or brand uses them."""
    entities = {entity["id"]: entity for entity in graph["entities"]}
    brands = {brand["id"]: brand for brand in graph["brands"]}
    by_key: dict[str, str] = {}
    for brand in brands.values():
        for key in [normalize(brand["name"]), *brand.get("aliases", ())]:
            by_key.setdefault(key, brand["id"])
    taken = collections.Counter()
    for entity in graph["entities"]:
        taken.update(set(entity.get("aliases") or ()))
    added = []
    for row in rows:
        eid, name = row["entity_id"], row["name"]
        if eid in entities:
            continue
        enrich = row.get("enrich") or {}
        pack_brands = [
            s["text"] for s in (row.get("pack") or {}).get("snippets", ()) if s["kind"] == "brand"
        ]
        brand_id = brand_key = None
        source = None
        for origin, value in [
            ("gemini", enrich.get("brand") or ""),
            *(("jsonld", b) for b in pack_brands),
        ]:
            key = normalize(value)
            if not key or key in BRAND_STOP:
                continue
            target = LINE_TO_BRAND.get(key, key)
            if target in by_key:
                brand_id, brand_key, source = by_key[target], key, origin
                break
            surface = brand_surface_in_name(name, key)
            if surface:
                brand_id = f"brand:{target.replace(' ', '-')}"
                brands[brand_id] = {"id": brand_id, "name": surface, "aliases": [target]}
                by_key[target] = brand_id
                brand_key, source = key, origin
                break
        entity = entity_record(eid, name, brand_key, brand_id)
        aliases = {normalize(entity["name"]), normalize(entity["core"])}
        extra = [entity[key] for key in ("model_tail", "latin_head") if entity[key]]
        for alias in entity["codes"] + extra:
            key = normalize(alias)
            if len(key.replace(" ", "")) >= 3 and not taken[key] and key not in by_key:
                aliases.add(key)
        entity["aliases"] = sorted(a for a in aliases if a)
        taken.update(entity["aliases"])
        graph["entities"].append(entity)
        entities[eid] = entity
        if brand_id:
            graph["edges"].append(
                {
                    "src": eid,
                    "rel": "MADE_BY",
                    "dst": brand_id,
                    "source": source,
                    "confidence": 0.8 if source == "gemini" else 0.7,
                    "reviewed": False,
                }
            )
        added.append(eid)
    if added:
        used = {edge["dst"] for edge in graph["edges"] if edge["rel"] == "MADE_BY"}
        graph["brands"] = sorted((b for i, b in brands.items() if i in used), key=lambda b: b["id"])
        graph["entities"].sort(key=lambda e: e["id"])
    return added


def admin_entity_rows() -> list[dict]:
    from visualseek_retrieval.admin_overlay import load_new_entities

    return list(load_new_entities(ADMIN_DIR).values())


def apply_admin_brands(graph: dict, corpus: Path) -> list[str]:
    """Brand edits made in /admin (graph/admin/changes.jsonl), after the MADE_BY review."""
    from visualseek_retrieval.admin_overlay import apply_made_by, load_state

    changed = apply_made_by(graph, load_state(ADMIN_DIR), canonical_members(corpus))
    if changed:
        graph["admin_review"] = {"made_by_changed": len(changed)}
    seal(graph)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/processed/products-v1")
    parser.add_argument("--out", type=Path, default=ROOT / "data/processed/products-v1/search")
    parser.add_argument("--silver", action="store_true", help="skip the MADE_BY review")
    args = parser.parse_args()

    entities = [
        row
        for line in (args.corpus / "entities.jsonl").read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line))["assets"] > 0
    ]
    assets = [
        json.loads(line)
        for line in (args.corpus / "assets.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    graph = build(entities, assets)
    if BRAND_DECISIONS.exists() and not args.silver:
        decisions = [
            json.loads(line)
            for line in BRAND_DECISIONS.read_text(encoding="utf-8").splitlines()
            if line
        ]
        plan = json.loads(BRAND_APPLY.read_text(encoding="utf-8")) if BRAND_APPLY.exists() else {}
        apply_brand_review(graph, decisions, plan)
    if not args.silver:
        if added := add_admin_entities(graph, admin_entity_rows()):
            graph["admin_entities"] = len(added)
        apply_admin_brands(graph, args.corpus)
    corpus_lock = json.loads((ROOT / "locks/products-v1-corpus.json").read_text(encoding="utf-8"))
    graph["corpus_fingerprint"] = corpus_lock["corpus_fingerprint"]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "graph.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    with_brand = sum(entity["brand_id"] is not None for entity in graph["entities"])
    sizes = collections.Counter(edge["dst"] for edge in graph["edges"])
    print(
        json.dumps(
            {
                "entities": len(graph["entities"]),
                "with_brand": with_brand,
                "brands": len(graph["brands"]),
                "brands_with_2plus_entities": sum(count >= 2 for count in sizes.values()),
                "aliases": sum(len(entity["aliases"]) for entity in graph["entities"]),
                "fingerprint": graph["fingerprint"],
                "made_by_review": graph.get("made_by_review"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
