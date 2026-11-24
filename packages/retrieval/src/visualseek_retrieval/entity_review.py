"""Apply reviewed identities and image labels to the frozen corpus at query time."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class EntityReview:
    canonical_ids: dict[str, str] = field(default_factory=dict)
    members: dict[str, list[str]] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    rejected_labels: dict[str, set[str]] = field(default_factory=dict)
    blocked_assets: list[str] = field(default_factory=list)
    fingerprint: str | None = None

    @classmethod
    def load(cls, graph: dict, raw: bytes) -> EntityReview:
        review = json.loads(raw)
        if review["version"] != 1 or review["corpus_fingerprint"] != graph["corpus_fingerprint"]:
            raise ValueError("Entity review belongs to a different schema or corpus")
        entity_ids = {entity["id"] for entity in graph["entities"]}
        redirects: dict[str, str] = {}
        for pair in review["entity_pairs"]:
            src, dst, decision = pair["src"], pair["dst"], pair["decision"]
            if src == dst or not {src, dst} <= entity_ids:
                raise ValueError(f"Invalid reviewed entity pair: {src}, {dst}")
            if decision not in {"SAME_AS", "VARIANT_OF", "DISTINCT"}:
                raise ValueError(f"Unknown entity review decision: {decision}")
            if decision == "SAME_AS":
                if src in redirects:
                    raise ValueError(f"Multiple canonical IDs for {src}")
                redirects[src] = dst

        members = defaultdict(list)
        canonical_ids = {}
        for source in redirects:
            target = source
            seen = set()
            while target in redirects:
                if target in seen:
                    raise ValueError(f"Cycle in SAME_AS decisions for {source}")
                seen.add(target)
                target = redirects[target]
            canonical_ids[source] = target
            members[target].append(source)

        aliases = review["aliases"]
        if not aliases.keys() <= entity_ids:
            raise ValueError("Aliases reference unknown entities")
        rejected = defaultdict(set)
        indexed_entities = {}
        for label in review["rejected_depictions"]:
            asset_id, entity_id = label["asset_id"], label["entity_id"]
            indexed = set(label["indexed_entity_ids"])
            if entity_id not in indexed or not indexed <= entity_ids:
                raise ValueError(f"Invalid rejected label for {asset_id}")
            if indexed_entities.setdefault(asset_id, indexed) != indexed:
                raise ValueError(f"Conflicting entity lists for {asset_id}")
            rejected[asset_id].add(entity_id)
        return cls(
            canonical_ids=canonical_ids,
            members=dict(members),
            aliases=aliases,
            rejected_labels=dict(rejected),
            blocked_assets=sorted(
                asset for asset, ids in rejected.items() if ids == indexed_entities[asset]
            ),
            fingerprint=hashlib.sha256(raw).hexdigest(),
        )

    def canonical(self, entity_id: str) -> str:
        return self.canonical_ids.get(entity_id, entity_id)

    def indexed_ids(self, entity_ids: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                member for eid in entity_ids for member in (eid, *self.members.get(eid, ()))
            )
        )

    def result_ids(self, asset_id: str, entity_ids: list[str]) -> list[str]:
        rejected = self.rejected_labels.get(asset_id, set())
        return list(dict.fromkeys(self.canonical(eid) for eid in entity_ids if eid not in rejected))
