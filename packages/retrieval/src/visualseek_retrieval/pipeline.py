"""One search path for every query type: S1 spotting -> S2 brand expansion -> S3 Qdrant.

No router and no LLM at query time. Relation and negation cues are a small rule set; the
graph decides which entities they reach. See PLAN_PRODUCTS_V1.md section 4.4.
"""

from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from visualseek_retrieval import lexical
from visualseek_retrieval.qdrant_store import COLLECTION, EntityBoosts, build_request
from visualseek_retrieval.spotting import EntitySpotter, Spotting
from visualseek_retrieval.text import normalize, surface

# Cues are matched on accented words when the query is accented, else on folded words:
# folded "hang" is also "hàng" (goods), "khac" is also "khắc" (carve), "ngoai" "ngoại".
RELATION_ACCENTED = re.compile(r"\b(hãng|thương hiệu|nhà sản xuất|brand|nhãn hiệu)\b")
RELATION_FOLDED = re.compile(
    r"\b(thuong hieu|nha san xuat|brand|nhan hieu|cung hang|hang san xuat)\b"
)
NEGATION_ACCENTED = re.compile(r"\b(khác|ngoài|trừ|không phải)\b")
NEGATION_FOLDED = re.compile(r"\b(khac|ngoai|tru|khong phai)\b")


@dataclass
class Settings:
    top_k: int = 10
    depth: int = 100
    dense_seed_min: float = 0.80
    dense_seed_margin: float = 0.03
    # Calibrated on dev no_answer (21) vs 20 in-domain descriptive probes: 0.48 separates them.
    out_of_scope_max: float = 0.48
    w_seed: float = 1.0
    w_dense_seed: float = 0.3
    w_hop: float = 0.5
    w_anchor: float = 2.0
    min_group_size: int = 2


@dataclass
class Plan:
    seeds: list[str] = field(default_factory=list)
    hops: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    intents: int = 0
    relation: bool = False
    negation: bool = False
    dense_seed: bool = False
    out_of_scope: bool = False
    clarification: list[dict] | None = None


class SearchPipeline:
    def __init__(
        self,
        graph: dict,
        entity_dense: np.ndarray,
        encoder,
        client,
        collection: str = COLLECTION,
        settings: Settings | None = None,
    ) -> None:
        self.graph = graph
        self.settings = settings or Settings()
        self.encoder = encoder
        self.client = client
        self.collection = collection
        self.spotter = EntitySpotter(graph, entity_dense)
        self.entities = {entity["id"]: entity for entity in graph["entities"]}
        self.brands = {brand["id"]: brand for brand in graph["brands"]}
        self.by_brand: dict[str, list[str]] = defaultdict(list)
        for edge in graph["edges"]:
            if edge["rel"] == "MADE_BY":
                self.by_brand[edge["dst"]].append(edge["src"])

    # ---- S1 + S2 -------------------------------------------------------------------------
    def plan(self, query: str, spotting: Spotting) -> Plan:
        s = self.settings
        accented = surface(query) != normalize(query)
        text = surface(query) if accented else normalize(query)
        plan = Plan(
            relation=bool((RELATION_ACCENTED if accented else RELATION_FOLDED).search(text)),
            negation=bool((NEGATION_ACCENTED if accented else NEGATION_FOLDED).search(text)),
        )
        entities, brands = list(spotting.entities), list(spotting.brands)
        if not entities and not brands and spotting.ambiguous:
            plan.clarification = [
                {
                    "text": match.text,
                    "options": [
                        {"entity_id": i, "name": self.entities[i]["name"]} for i in match.ids
                    ],
                }
                for match in spotting.ambiguous
            ]
            return plan
        if not entities and not brands and spotting.dense_top:
            (best, score), runner = spotting.dense_top[0], spotting.dense_top[1][1]
            if score >= s.dense_seed_min and score - runner >= s.dense_seed_margin:
                entities, plan.dense_seed = [best], True
            elif score < s.out_of_scope_max:
                plan.out_of_scope = True
                return plan

        if plan.relation and entities:
            # "sản phẩm khác của hãng làm X": X is the anchor, the targets are X's brand siblings.
            anchor_brands = {self.entities[e]["brand_id"] for e in entities} - {None}
            siblings = sorted({o for b in anchor_brands for o in self.by_brand[b]})
            if siblings:
                plan.anchors = entities if plan.negation else []
                plan.hops = (
                    [o for o in siblings if o not in entities] if plan.negation else siblings
                )
                plan.intents = len(anchor_brands)
                return plan
        plan.seeds = entities
        for brand in brands:
            plan.hops += [o for o in self.by_brand.get(brand, []) if o not in plan.hops]
        if plan.negation and plan.seeds and plan.hops:
            # "Shimano trừ BB-9000": the spotted entity is excluded from its brand's results.
            plan.anchors, plan.seeds = plan.seeds, []
            plan.hops = [o for o in plan.hops if o not in plan.anchors]
        plan.intents = len(plan.seeds) + len(brands)
        return plan

    # ---- S3 ------------------------------------------------------------------------------
    def search(self, query: str, brand_id: str | None = None) -> dict:
        s = self.settings
        timings: dict[str, float] = {}
        started = time.perf_counter()

        tick = time.perf_counter()
        vector = self.encoder.encode([query])[0]
        timings["embed_query"] = _ms(tick)

        tick = time.perf_counter()
        spotting = self.spotter.spot(query, vector)
        timings["s1_spotting"] = _ms(tick)

        tick = time.perf_counter()
        plan = self.plan(query, spotting)
        timings["s2_expand"] = _ms(tick)

        hits: list[dict] = []
        grouped = False
        if not plan.out_of_scope and plan.clarification is None:
            boosts = EntityBoosts(
                seeds=plan.seeds,
                hops=plan.hops,
                anchors=plan.anchors,
                w_seed=s.w_dense_seed if plan.dense_seed else s.w_seed,
                w_hop=s.w_hop if plan.seeds else s.w_seed,
                w_anchor=s.w_anchor,
            )
            request = build_request(
                vector.tolist(),
                lexical.query_vector(query),
                boosts,
                depth=s.depth,
                filter_brand=brand_id,
            )
            tick = time.perf_counter()
            if plan.intents >= 2 or plan.hops:
                grouped = True
                if plan.hops:
                    # Spread top-k over the target entities; a fixed small group size capped
                    # recall when a brand has only one or two other products.
                    targets = len(set(plan.seeds) | set(plan.hops))
                    size = min(s.top_k, max(s.min_group_size, math.ceil(s.top_k / targets)))
                else:
                    size = max(1, s.top_k // plan.intents)
                result = self.client.query_points_groups(
                    self.collection,
                    group_by="entity_ids",
                    limit=s.top_k,
                    group_size=size,
                    with_payload=True,
                    **request,
                )
                points = [point for group in result.groups for point in group.hits]
            else:
                result = self.client.query_points(
                    self.collection, limit=s.top_k, with_payload=True, **request
                )
                points = result.points
            timings["qdrant"] = _ms(tick)
            seen: set[str] = set()
            for point in points:
                asset_id = point.payload["asset_id"]
                if asset_id in seen:
                    continue
                seen.add(asset_id)
                hits.append({"asset_id": asset_id, "score": point.score, **_public(point.payload)})
                if len(hits) == s.top_k:
                    break
        timings["total"] = _ms(started)
        return {
            "query": query,
            "results": hits,
            "out_of_scope": plan.out_of_scope,
            "clarification": plan.clarification,
            "plan": {
                "seeds": plan.seeds,
                "hops": len(plan.hops),
                "anchors": plan.anchors,
                "relation": plan.relation,
                "negation": plan.negation,
                "dense_seed": plan.dense_seed,
                "grouped": grouped,
                "matches": [m.__dict__ for m in spotting.matches],
                "seed_names": [self.entities[e]["name"] for e in plan.seeds + plan.anchors],
            },
            "timings": timings,
            "cache": {"embedding": False, "result": False},
            "graph_fingerprint": self.graph["fingerprint"],
        }


def _ms(tick: float) -> float:
    return round((time.perf_counter() - tick) * 1000, 2)


def _public(payload: dict) -> dict:
    return {
        key: payload.get(key)
        for key in ("entity_ids", "brand_ids", "image_path", "page_title", "domain")
    }
