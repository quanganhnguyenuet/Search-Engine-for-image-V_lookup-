"""Versioned, annotation-derived silver ground truth; no retrieval-based labeling."""

import hashlib
import json
import random
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import combinations
from pathlib import Path

from visualseek_evaluation.text_concepts import concepts
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json
from visualseek_retrieval.text import surface

VERSION = "visible-tag-alias-conjunction-v1"


@lru_cache(maxsize=65536)
def _tag_form(text: str) -> str:
    return surface(text)


def tag_matches(tag: str, alias: str) -> bool:
    tag, alias = _tag_form(tag), _tag_form(alias)
    # Single words require an exact tag: biển must not match biển hiệu.
    if len(alias.split()) == 1:
        return tag == alias
    words, requested = tag.split(), alias.split()
    for start in range(len(words) - len(requested) + 1):
        if words[start:start + len(requested)] == requested:
            if not {"không", "chưa", "thiếu", "without", "no", "not"}.intersection(
                words[max(0, start - 3):start]
            ):
                return True
    return False


def concept_evidence(assets: list[dict], catalog: list[dict]) -> dict:
    postings, tokens = defaultdict(dict), defaultdict(set)
    for asset in assets:
        if asset.get("search_excluded"):
            continue
        for tag in asset.get("tags", []):
            if not isinstance(tag, str) or not (form := _tag_form(tag)):
                continue
            postings[form].setdefault(asset["asset_id"], []).append(tag)
            for token in set(form.split()):
                tokens[token].add(form)
    evidence = {}
    for concept in catalog:
        key = surface(concept["name"])
        evidence[key] = {}
        candidates = set().union(*(tokens[_tag_form(alias).split()[0]]
                                   for alias in concept["aliases"] if _tag_form(alias)))
        for tag in sorted(candidates):
            if any(tag_matches(tag, alias) for alias in concept["aliases"]):
                for asset_id, original in postings[tag].items():
                    evidence[key].setdefault(asset_id, []).extend(original)
    return evidence


def build_ground_truth(assets: list[dict], *, count=500, seed=42, catalog=None) -> dict:
    if count < 4 or count % 4:
        raise ValueError("Query count must be a positive multiple of four")
    catalog = concepts() if catalog is None else catalog
    evidence = concept_evidence(assets, catalog)
    rng = random.Random(seed)
    buckets = defaultdict(list)
    by_category = defaultdict(list)
    for concept in catalog:
        key = surface(concept["name"])
        if len(evidence[key]) >= 5:
            by_category[concept["category"]].append(concept)
            buckets[(concept["category"], "object")].append([concept])
    for category, members in sorted(by_category.items()):
        for left, right in combinations(members, 2):
            a, b = set(evidence[surface(left["name"])]), set(evidence[surface(right["name"])])
            intersection = a & b
            # Avoid logically redundant pairs and single-instance annotation noise.
            if len(intersection) >= 5 and intersection != a and intersection != b:
                buckets[(category, "conjunction")].append([left, right])
    for key in sorted(buckets):
        rng.shuffle(buckets[key])
    selected, seen_positive_sets = [], set()
    keys = [(category, kind) for category in sorted(by_category)
            for kind in ("object", "conjunction")]
    while len(selected) < count // 2:
        added = False
        for key in keys:
            while buckets[key]:
                terms = buckets[key].pop()
                positives = set.intersection(*(set(evidence[surface(t["name"])]) for t in terms))
                signature = frozenset(positives)
                if signature in seen_positive_sets:
                    continue
                seen_positive_sets.add(signature)
                selected.append((key, terms, sorted(positives)))
                added = True
                break
            if len(selected) == count // 2:
                break
        if not added:
            raise ValueError("Insufficient distinct, evidenced intents for the requested count")
    # Split whole intents, keeping paraphrases and identical positive sets together.
    split_order = list(range(len(selected)))
    rng.shuffle(split_order)
    development = set(split_order[:len(selected) // 2])
    queries, intents = [], []
    for number, ((category, kind), terms, positives) in enumerate(selected):
        intent_id = f"intent-{number + 1:04d}"
        names = [term["name"] for term in terms]
        phrase = " và ".join(names)
        variants = ([names[0], f"Tôi muốn tìm ảnh có {names[0]}."] if kind == "object" else
                    [phrase, f"Tìm ảnh có cả {names[0]} và {names[1]}."])
        split = "development" if number in development else "held-out"
        intents.append({"intent_id": intent_id, "category_stratum": category, "kind": kind,
                        "required_concepts": names, "relevant_asset_ids": positives,
                        "ground_truth_source": "annotation_silver", "rule": VERSION,
                        "split": split})
        for variant, query in enumerate(variants, 1):
            queries.append({"query_id": f"q-{number + 1:04d}-{variant}",
                            "intent_id": intent_id, "query": query, "split": split,
                            "category_stratum": category, "kind": kind})
    if len({surface(row["query"]) for row in queries}) != count:
        raise ValueError("Generated query text is not unique")
    used = {surface(name) for intent in intents for name in intent["required_concepts"]}
    return {"queries": queries, "intents": intents, "catalog": catalog,
            "evidence": [{"concept": key, "matches": evidence[key]} for key in sorted(used)]}


def save_ground_truth(index: ArtifactIndex, output: Path, *, count=500, seed=42) -> dict:
    if output.exists():
        raise ValueError("Ground truth output already exists; use a new version")
    value = build_ground_truth(index.assets, count=count, seed=seed)
    output.mkdir(parents=True)
    files = {}
    for name in ("queries", "intents", "evidence"):
        path = output / f"{name}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in value[name]),
                        encoding="utf-8")
        files[path.name] = sha256_file(path)
    manifest = {
        "schema_version": 1, "ground_truth_version": VERSION, "seed": seed,
        "query_count": count, "intent_count": len(value["intents"]),
        "artifact_fingerprint": index.manifest["files"], "gallery_count": index.searchable_count,
        "gallery_policy": "All searchable indexed assets; no category filter or self exclusion",
        "label_source": "annotation_silver", "human_reviewed": False,
        "positive_rule": "AND of required concepts. Each concept matches any explicit alias in "
        "asset tags across all categories. Single-word aliases require exact tags; multiword "
        "aliases match contiguous words, rejecting nearby negations. Titles/descriptions and "
        "retrieval rankings are not used to generate positives.",
        "limitations": "Exhaustive under the tag rules, not exhaustive visual relevance. Missing "
        "or wrong source tags remain possible. Local hybrid/lexical search uses the same tags; "
        "its score is annotation-aligned and not an independent visual relevance validation. "
        "Two query variants per intent; not 500 independent intents. No negative/OOD queries.",
        "selection": "Balanced category/type round robin; two paraphrases per intent; at least "
        "5 positive assets; duplicate positive sets and redundant conjunctions removed. "
        "No selection based on retrieval performance or an upper bound on positive count.",
        "query_splits": dict(Counter(row["split"] for row in value["queries"])),
        "query_strata": dict(Counter(row["category_stratum"] for row in value["queries"])),
        "query_kinds": dict(Counter(row["kind"] for row in value["queries"])),
        "catalog": value["catalog"],
        "catalog_sha256": hashlib.sha256(json.dumps(
            value["catalog"], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "files": files,
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def load_ground_truth(index: ArtifactIndex, directory: Path) -> tuple[dict, list[dict], dict]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("ground_truth_version") != VERSION:
        raise ValueError("Unsupported ground truth version")
    if manifest["artifact_fingerprint"] != index.manifest["files"]:
        raise ValueError("Ground truth and search artifact differ")
    for name in ("queries.jsonl", "intents.jsonl", "evidence.jsonl"):
        if sha256_file(directory / name) != manifest["files"][name]:
            raise ValueError(f"Ground truth checksum mismatch: {name}")
    def read(name):
        return [json.loads(line) for line in (directory / name).read_text(encoding="utf-8")
                .splitlines() if line.strip()]
    queries, rows = read("queries.jsonl"), read("intents.jsonl")
    intents = {row["intent_id"]: row for row in rows}
    assets = {a["asset_id"] for a in index.assets if not a.get("search_excluded")}
    if (len(queries) != manifest["query_count"] or len(intents) != manifest["intent_count"]
            or len(intents) != len(rows) or len({q["query_id"] for q in queries}) != len(queries)):
        raise ValueError("Ground truth query/intent counts or uniqueness invalid")
    for intent in intents.values():
        ids = intent["relevant_asset_ids"]
        if not ids or len(ids) != len(set(ids)) or not set(ids) <= assets:
            raise ValueError("Invalid positive IDs in ground truth")
    for query in queries:
        if (query["intent_id"] not in intents or not query["query"].strip()
                or query["split"] != intents[query["intent_id"]]["split"]):
            raise ValueError("Invalid query or intent split leakage")
    return manifest, queries, intents
