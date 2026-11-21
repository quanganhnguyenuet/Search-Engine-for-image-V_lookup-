"""Disjoint image queries/gallery with explicit object and attribute silver relevance."""

import json
import random
import re
import shutil
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np

from visualseek_evaluation.text_concepts import concepts
from visualseek_evaluation.text_ground_truth import concept_evidence
from visualseek_retrieval.artifact import ArtifactIndex, sha256_file, write_json
from visualseek_retrieval.text import phrase_position, surface

VERSION = "heldout-object-attribute-v2"


@lru_cache(maxsize=65536)
def _clauses(tag: str) -> tuple:
    return tuple(tuple(surface(clause).split()) for clause in re.split(r"[,;()\n.!?]", tag)
                 if surface(clause))


@lru_cache(maxsize=512)
def _words(text: str) -> tuple[str, ...]:
    return tuple(surface(text).split())


def bound_attribute(tag: str, base: str, value: str | None) -> bool:
    """Bind color/style to its object, allowing 'áo dài gấm đỏ', not 'khăn đỏ'."""
    base_words = _words(base)
    attribute = _words(value) if value else ()
    for words in _clauses(tag):
        for start in range(len(words) - len(base_words) + 1):
            if words[start:start + len(base_words)] != base_words:
                continue
            if {"không", "chưa", "thiếu", "without", "not", "no"}.intersection(
                words[max(0, start - 3):start]
            ):
                continue
            if not value:
                return True
            tail = words[start + len(base_words):start + len(base_words) + 7]
            for end, word in enumerate(tail):
                if word in {"và", "với", "cùng", "kèm", "trong", "tại", "ở"}:
                    tail = tail[:end]
                    break
            for position in range(len(tail) - len(attribute) + 1):
                if tail[position:position + len(attribute)] == attribute:
                    if value == "nam" and position and tail[position - 1] == "việt":
                        continue
                    return True
    return False


def profile_evidence(assets: list[dict], catalog: list[dict]) -> dict:
    evidence = concept_evidence(assets, catalog)
    for profile in catalog:
        if not profile["attributes"]:
            continue
        matches = {}
        for asset in assets:
            if asset.get("search_excluded"):
                continue
            tags = [tag for tag in asset.get("tags", []) if isinstance(tag, str) and all(
                bound_attribute(tag, profile["base"], value)
                for value in profile["attributes"].values())]
            if tags:
                matches[asset["asset_id"]] = tags
        evidence[surface(profile["name"])] = matches
    return evidence


def profiles() -> list[dict]:
    values = {surface(c["name"]): {**c, "base": c["name"], "attributes": {}}
              for c in concepts()}
    attributes = []
    for base, colors, category in (
        ("áo dài", ["đỏ", "trắng", "đen", "tím", "vàng"], "trang_phuc"),
        ("áo yếm", ["đỏ"], "trang_phuc"),
        ("váy", ["đen", "trắng", "đỏ"], "trang_phuc"),
        ("quần", ["trắng", "đen"], "trang_phuc"),
        ("đèn lồng", ["đỏ"], "le_hoi"),
        ("mái ngói", ["đỏ", "vàng"], "kien_truc"),
        ("đồi cát", ["đỏ", "trắng"], "phong_canh"),
        ("lân", ["đỏ", "vàng", "trắng"], "le_hoi"),
    ):
        for color in colors:
            name = f"{base} {color}"
            attributes.append({"name": name, "aliases": [name, f"{base} màu {color}"],
                               "category": category, "base": base,
                               "attributes": {"color": color}})
    for name, base, attribute, value in (
        ("áo dài cách tân", "áo dài", "style", "cách tân"),
        ("áo dài nam", "áo dài", "style", "nam"),
        ("áo dài cưới", "áo dài", "style", "cưới"),
        ("áo dài gấm Huế", "áo dài", "fabric", "gấm"),
        ("váy thổ cẩm", "váy", "pattern", "thổ cẩm"),
        ("khăn thổ cẩm", "khăn", "pattern", "thổ cẩm"),
    ):
        attributes.append({"name": name, "aliases": [name], "category": "trang_phuc",
                           "base": base, "attributes": {attribute: value}})
    for value in attributes:
        values[surface(value["name"])] = value
    return list(values.values())


def select_queries(assets: list[dict], *, count=100, seed=42, catalog=None, review=None
                   ) -> tuple[list, dict]:
    catalog = profiles() if catalog is None else catalog
    review = review or {}
    rejected = set(review.get("reject_query_asset_ids", []))
    pinned = review.get("pinned_queries", {})
    evidence = profile_evidence(assets, catalog)
    rng = random.Random(seed)
    buckets = defaultdict(list)
    for profile in catalog:
        if len(evidence[surface(profile["name"])]) >= 3:
            buckets[profile["category"]].append(profile)
    for category in sorted(buckets):
        rng.shuffle(buckets[category])
        # Prioritize labeled attributes without inspecting retrieval performance.
        buckets[category].sort(key=lambda p: (not bool(p["attributes"]),
                                            p["name"] not in ("áo dài đỏ", "áo dài trắng")))
    if count < 1 or sum(map(len, buckets.values())) < count:
        raise ValueError("Insufficient distinct semantic targets")
    by_id = {a["asset_id"]: a for a in assets}
    selected, query_ids, positive_sets = [], set(), set()
    while len(selected) < count:
        added = False
        for category in sorted(buckets):
            while buckets[category]:
                profile = buckets[category].pop(0)
                positives = set(evidence[surface(profile["name"])])
                if frozenset(positives) in positive_sets:
                    continue
                candidates = sorted(positives - query_ids - rejected)
                rng.shuffle(candidates)
                def priority(id_, profile=profile, category=category):
                    row = by_id[id_]
                    title = str(row.get("keyword", ""))
                    subject = any(phrase_position(title, name) is not None
                                  or phrase_position(name, title) is not None
                                  for name in [profile["base"], *profile["aliases"]] if title)
                    return (id_ != pinned.get(profile["name"]), not subject,
                            row["category"] != category, row.get("split") != "test")
                candidates.sort(key=priority)
                for asset_id in candidates:
                    proposed = query_ids | {asset_id}
                    if len(positives - proposed) < 1 or any(
                        not set(row["source_positive_ids"]) - proposed for row in selected
                    ):
                        continue
                    # A garment photo with conflicting annotated colors is ambiguous.
                    color = profile["attributes"].get("color")
                    if color and asset_id != pinned.get(profile["name"]) and any(
                        other["base"] == profile["base"]
                        and other["attributes"].get("color") not in (None, color)
                        and asset_id in evidence[surface(other["name"])] for other in catalog
                    ):
                        continue
                    selected.append({"asset_id": asset_id, "profile": profile,
                                     "source_positive_ids": sorted(positives)})
                    query_ids.add(asset_id)
                    positive_sets.add(frozenset(positives))
                    added = True
                    break
                else:
                    continue
                break
            if len(selected) == count:
                break
        if not added:
            raise ValueError("Cannot select disjoint queries with remaining gallery positives")
    return selected, evidence


def build_holdout(index: ArtifactIndex, image_root: Path, output: Path, *, count=100, seed=42,
                  review=None):
    if output.exists():
        raise ValueError("Output exists; choose a new benchmark version")
    catalog = profiles()
    review = review or {}
    selected, evidence = select_queries(index.assets, count=count, seed=seed, catalog=catalog,
                                        review=review)
    query_ids = {row["asset_id"] for row in selected}
    retained = [i for i, row in enumerate(index.assets)
                if not row.get("search_excluded") and row["asset_id"] not in query_ids]
    gallery_ids = {index.assets[i]["asset_id"] for i in retained}
    output.mkdir(parents=True)
    gallery = output / "gallery"
    gallery.mkdir()
    queries_dir = output / "query-images"
    queries_dir.mkdir()
    # The derived artifact physically contains no query vector/metadata row.
    np.save(gallery / "vectors.npy", index.vectors[retained])
    names = ["vectors.npy", "assets.jsonl"]
    if index.metadata_vectors is not None:
        np.save(gallery / "metadata_vectors.npy", index.metadata_vectors[retained])
        names.append("metadata_vectors.npy")
    (gallery / "assets.jsonl").write_text(
        "".join(json.dumps(index.assets[i], ensure_ascii=False) + "\n" for i in retained),
        encoding="utf-8")
    gallery_manifest = {**index.manifest, "count": len(retained), "requested": len(retained),
                        "scope": {"kind": VERSION, "held_out_query_count": count},
                        "files": {name: sha256_file(gallery / name) for name in names}}
    gallery_manifest.pop("inventory_sha256", None)
    write_json(gallery / "manifest.json", gallery_manifest)
    gallery_index = ArtifactIndex(gallery)
    bases = {}
    for profile in catalog:
        base = profile["base"]
        bases.setdefault(base, set()).update([base, *profile["aliases"]])
    base_catalog = [{"name": base, "aliases": sorted(aliases)} for base, aliases in bases.items()]
    base_evidence = concept_evidence(index.assets, base_catalog)
    for profile in catalog:
        if profile["attributes"]:
            base_evidence[surface(profile["base"])].update(evidence[surface(profile["name"])])
    rows = []
    root = image_root.resolve()
    for number, row in enumerate(selected, 1):
        profile, asset_id = row["profile"], row["asset_id"]
        asset = index.assets[index.positions[asset_id]]
        source = (root / asset["image_path"]).resolve()
        if not source.is_relative_to(root) or sha256_file(source) != asset_id:
            raise ValueError("Query path/checksum invalid")
        target = queries_dir / (asset_id + source.suffix.lower())
        shutil.copyfile(source, target)
        corrected = set(review.get("reject_object_evidence", {}).get(profile["base"], []))
        target_corrected = set(review.get("reject_target_evidence", {}).get(profile["name"], []))
        positive_ids = sorted(
            (set(row["source_positive_ids"]) & gallery_ids) - corrected - target_corrected)
        object_ids = sorted(
            (set(base_evidence[surface(profile["base"])]) & gallery_ids) - corrected)
        assert positive_ids and set(positive_ids) <= set(object_ids)
        rows.append({"query_id": f"image-{number:03d}", "asset_id": asset_id,
                     "image_path": str(target.relative_to(output)),
                     "source_image_path": asset["image_path"], "original_split": asset.get("split"),
                     "category_stratum": profile["category"], "target": profile["name"],
                     "base_object": profile["base"], "attributes": profile["attributes"],
                     "text_query": f"Tìm ảnh có {profile['name']}",
                     "relevant_asset_ids": positive_ids, "object_relevant_asset_ids": object_ids,
                     "query_tag_evidence": evidence[surface(profile["name"])][asset_id],
                     "ground_truth_source": "annotation_object_attribute_silver"})
    path = output / "queries.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8")
    manifest = {"schema_version": 1, "version": VERSION, "seed": seed, "query_count": len(rows),
                "unique_targets": len({r["target"] for r in rows}),
                "unique_base_objects": len({r["base_object"] for r in rows}),
                "source_fingerprint": index.manifest["files"],
                "gallery_fingerprint": gallery_index.manifest["files"],
                "gallery_count": len(retained), "source_searchable_count": index.searchable_count,
                "query_gallery_overlap": len(query_ids & gallery_ids),
                "attribute_query_count": sum(bool(r["attributes"]) for r in rows),
                "query_categories": dict(Counter(r["category_stratum"] for r in rows)),
                "queries_sha256": sha256_file(path), "human_reviewed": False,
                "query_review": review,
                "relevance": "Same annotated semantic target; attributes bind to the base object "
                "within a tag clause, allowing intervening material/style words. Object-only "
                "and attribute targets are "
                "reported separately. Missing labels remain unknown visual relevance.",
                "selection": "One query per distinct target; round-robin categories, attribute "
                "profiles first; seeded, main-subject/title and matching category preferred, "
                "then test split; optional visual-review overrides; no retrieval-based selection. "
                "All query IDs excluded globally; each target retains gallery positives.",
                "limitations": "Silver annotation labels, not exhaustive human visual judgments. "
                "No near-duplicate family audit yet; byte-identical assets were deduplicated. "
                "Style is measured only where an explicit style tag exists. Original corpus is "
                "preserved; the evaluation API must use this separate derived gallery artifact.",
                "catalog": catalog}
    write_json(output / "manifest.json", manifest)
    return manifest


def load_holdout(directory: Path, index: ArtifactIndex):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest["version"] != VERSION or manifest["gallery_fingerprint"] != index.manifest["files"]:
        raise ValueError("Wrong held-out benchmark/gallery")
    if sha256_file(directory / "queries.jsonl") != manifest["queries_sha256"]:
        raise ValueError("Query file checksum mismatch")
    rows = [json.loads(line) for line in (directory / "queries.jsonl").read_text().splitlines()]
    query_ids = {r["asset_id"] for r in rows}
    gallery_ids = set(index.positions)
    if (len(rows) != manifest["query_count"] or len(query_ids) != len(rows)
            or query_ids & gallery_ids or len({r["target"] for r in rows}) != len(rows)):
        raise ValueError("Query/gallery overlap or duplicate query/target")
    for row in rows:
        path = (directory / row["image_path"]).resolve()
        if not path.is_relative_to(directory.resolve()) or sha256_file(path) != row["asset_id"]:
            raise ValueError("Query image path/checksum invalid")
        for field in ("relevant_asset_ids", "object_relevant_asset_ids"):
            ids = row[field]
            if not ids or len(ids) != len(set(ids)) or not set(ids) <= gallery_ids:
                raise ValueError("Invalid gallery positives")
    return manifest, rows
