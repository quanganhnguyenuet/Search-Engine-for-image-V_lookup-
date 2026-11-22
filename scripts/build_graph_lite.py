"""Build the free-source entity graph (MVP stand-in for P2) from the frozen products-v1 corpus.

Only sources that cost no LLM quota are used:
  * aliases: the entity name, the name without its parenthetical, and unique model codes;
  * MADE_BY: JSON-LD ``brand`` values that also occur in the entity name, plus capitalised
    Latin tokens that start a brand-like span in at least two entity names.

Every edge records ``source`` and ``confidence`` and is ``reviewed = false``: this graph is
silver, not the reviewed graph of PLAN_PRODUCTS_V1.md section 4.1.
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
        words = WORD.findall(core)
        codes = [word for word in words if is_code(word)]
        tail = None
        if brand_key:
            # The model name after the brand ("Kraken", "MX Master 3") is a common way to ask.
            idx = next(
                i
                for i in range(len(words))
                if normalize(" ".join(words[i : i + len(brand_key.split())])) == brand_key
            )
            rest = words[idx + len(brand_key.split()) :]
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
        out_entities.append(
            {
                "id": eid,
                "name": name,
                "core": core,
                "notes": parentheticals(name),
                "brand_id": brand_id,
                "codes": codes,
                "model_tail": tail,
                "latin_head": head,
            }
        )

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
    edges.sort(key=lambda edge: (edge["src"], edge["rel"], edge["dst"]))
    fingerprint = hashlib.sha256(
        json.dumps(edges, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    # Aliases change S1 behaviour without changing edges; they get their own fingerprint.
    aliases_sha256 = hashlib.sha256(
        json.dumps(
            [[e["id"], e["aliases"]] for e in out_entities]
            + [[b["id"], b["aliases"]] for b in brand_list],
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "version": "graph-lite-v1",
        "aliases_sha256": aliases_sha256,
        "reviewed": False,
        "fingerprint": fingerprint,
        "entities": out_entities,
        "brands": brand_list,
        "edges": edges,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/processed/products-v1")
    parser.add_argument("--out", type=Path, default=ROOT / "data/processed/products-v1/search")
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
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
