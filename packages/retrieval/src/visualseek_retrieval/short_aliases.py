"""Short names for S1: "Colflox" for "Colflox 0.3% Pharmedic".

A short name is the entity name without parentheticals, then trimmed only at its ends:
strength/volume/pack and brand tokens from the end, brand tokens from the start. Words in the
middle are never removed, so a phrase such as "định vị" stays whole. It is accepted only when
nothing else in the corpus could be meant by it:

  * it differs from every alias already in the S1 dictionary (or points to the same entity);
  * no other canonical entity produces the same short name;
  * it is not a brand name;
  * it has a token of 4+ letters found in no other entity's name, aliases or indexed text;
  * the whole phrase occurs in no other entity's name, aliases or indexed text.

Every rejection keeps its reason, so the list can be reviewed instead of trusted.
Built offline by scripts/build_short_aliases.py; loaded by EntitySpotter as extra aliases.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

from visualseek_retrieval.text import normalize, surface

UNIT = r"%|mg|g|kg|mcg|µg|ug|ml|l|iu|ui|miu|m\.?i\.?u|mmol|meq|cc|mm|cm|inch|w|v|mah|gb|tb"
QUANTITY = re.compile(rf"^[\d.,/x+-]+({UNIT})?$", re.IGNORECASE)
# Compared on accented surfaces: folded "vi" is both "vỉ" (blister) and "vị" (flavour).
PACK_WORDS = frozenset(
    (
        "mg ml g kg mcg iu miu viên vỉ gói ống hộp chai lọ tuýp tube tablet tablets capsule "
        "capsules caps tab sachet sachets bottle pack"
    ).split()
)
PARENS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
MIN_TOKEN = 4


def _raw_tokens(name: str) -> list[str]:
    return [t.strip(".,;:!?\"'") for t in PARENS.sub(" ", name).split() if t.strip(".,;:!?\"'")]


def candidate(name: str, brand_phrases: set[tuple[str, ...]]) -> str | None:
    """The name trimmed at its ends, or None if trimming changed nothing or left nothing."""
    tokens = _raw_tokens(name)
    folded = [normalize(t) for t in tokens]
    longest = max((len(p) for p in brand_phrases), default=0)

    def brand_at(lo: int, hi: int, from_end: bool) -> int:
        """Length of the longest brand phrase at the end (or start) of tokens[lo:hi]."""
        for size in range(min(longest, hi - lo), 0, -1):
            span = folded[hi - size : hi] if from_end else folded[lo : lo + size]
            if tuple(span) in brand_phrases:
                return size
        return 0

    lo, hi = 0, len(tokens)
    while hi > lo:
        raw = tokens[hi - 1]
        if not folded[hi - 1] or QUANTITY.match(raw) or surface(raw) in PACK_WORDS:
            hi -= 1
        elif size := brand_at(lo, hi, from_end=True):
            hi -= size
        else:
            break
    while lo < hi and (size := brand_at(lo, hi, from_end=False)):
        lo += size
    short = " ".join(tokens[lo:hi])
    if not short or normalize(short) == normalize(" ".join(tokens)):
        return None
    return short


def build(
    graph: dict,
    canonical,
    existing: dict[str, set[str]],
    entity_texts: dict[str, str],
) -> dict:
    """Accepted short aliases per canonical entity and every rejection with its reason.

    ``existing`` maps a folded alias key to the canonical ids it already reaches in S1;
    ``entity_texts`` maps a canonical id to its folded name, aliases and indexed doc text.
    """
    brand_phrases = {
        tuple(normalize(alias).split())
        for brand in graph["brands"]
        for alias in (*brand["aliases"], brand["name"])
        if normalize(alias)
    }
    brand_keys = {" ".join(p) for p in brand_phrases}
    token_owners: dict[str, set[str]] = defaultdict(set)
    for entity_id, text in entity_texts.items():
        for token in set(text.split()):
            token_owners[token].add(entity_id)

    proposals: dict[str, set[str]] = defaultdict(set)
    surfaces: dict[tuple[str, str], str] = {}
    names = {}
    for entity in graph["entities"]:
        entity_id = canonical(entity["id"])
        if entity_id != entity["id"]:
            continue  # members are reached through their canonical entity
        names[entity_id] = entity["name"]
        for source in dict.fromkeys((entity["name"], entity["core"])):
            short = candidate(source, brand_phrases)
            if short:
                key = normalize(short)
                proposals[key].add(entity_id)
                surfaces.setdefault((entity_id, key), short)

    accepted: dict[str, list[str]] = defaultdict(list)
    rejected = []
    for key in sorted(proposals):
        owners = sorted(proposals[key])
        for entity_id in owners:
            alias = surfaces[entity_id, key]
            reason, detail = _reject(
                key, entity_id, owners, existing, brand_keys, token_owners, entity_texts
            )
            if reason == "already_alias":
                continue
            if reason:
                rejected.append(
                    {
                        "entity_id": entity_id,
                        "name": names[entity_id],
                        "alias": alias,
                        "reason": reason,
                        "detail": detail,
                    }
                )
            else:
                accepted[entity_id].append(alias)
    aliases = {k: sorted(v) for k, v in sorted(accepted.items())}
    return {
        "aliases": aliases,
        "rejected": rejected,
        "fingerprint": hashlib.sha256(
            json.dumps(aliases, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def full_names(entity: dict) -> set[str]:
    return {
        normalize(entity["name"]),
        normalize(entity["core"]),
        normalize(PARENS.sub(" ", entity["name"])),
    }


def suppressed(graph: dict, canonical, entity_texts: dict[str, str]) -> dict:
    """Derived graph aliases (model tails, codes, short forms) that other entities also use.

    graph-lite derives aliases such as the last word of a name; "nhat" from "... Tojiro Nhật"
    then links every "sinh nhật" or "tốt nhất" to a kitchen knife. A derived alias is kept
    only when its phrase occurs in no other canonical entity's name, aliases or indexed text.
    Full names are never suppressed.
    """
    token_owners: dict[str, set[str]] = defaultdict(set)
    for entity_id, text in entity_texts.items():
        for token in set(text.split()):
            token_owners[token].add(entity_id)
    blocked: dict[str, list[str]] = defaultdict(list)
    details = []
    for entity in graph["entities"]:
        entity_id = canonical(entity["id"])
        for alias in entity["aliases"]:
            if alias in full_names(entity) or not alias.split():
                continue
            pattern = re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])")
            others = sorted(
                other
                for other in token_owners.get(alias.split()[0], ())
                if other != entity_id and pattern.search(entity_texts[other])
            )
            if others:
                blocked[entity["id"]].append(alias)
                details.append(
                    {
                        "entity_id": entity["id"],
                        "name": entity["name"],
                        "alias": alias,
                        "other_entities": len(others),
                        "examples": others[:5],
                    }
                )
    return {"aliases": {k: sorted(v) for k, v in sorted(blocked.items())}, "details": details}


def _reject(key, entity_id, owners, existing, brand_keys, token_owners, entity_texts):
    reached = existing.get(key, set())
    if reached == {entity_id}:
        return "already_alias", None
    if reached:
        return "collides_with_alias", sorted(reached)
    if len(owners) > 1:
        return "shared_short_name", [o for o in owners if o != entity_id]
    if key in brand_keys:
        return "brand_name", None
    tokens = key.split()
    if not any(
        len(t) >= MIN_TOKEN and t.isalpha() and token_owners.get(t, set()) <= {entity_id}
        for t in tokens
    ):
        return "no_distinctive_token", None
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])")
    others = sorted(
        other
        for other in token_owners.get(tokens[0], ())
        if other != entity_id and pattern.search(entity_texts[other])
    )
    if others:
        return "appears_in_other_entities", others[:5]
    return None, None
