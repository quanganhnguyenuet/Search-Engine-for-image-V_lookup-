"""One search path for every query type: S1 spotting -> S2 brand expansion -> S3 Qdrant.

No router and no LLM at query time. Relation and negation cues are a small rule set; the
graph decides which entities they reach. See PLAN_PRODUCTS_V1.md section 4.4.
"""

from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from qdrant_client.http.exceptions import ApiException

from visualseek_retrieval import connectors, lexical
from visualseek_retrieval.categories import CategoryIntent, CategoryLexicon, knn_votes
from visualseek_retrieval.entity_review import EntityReview
from visualseek_retrieval.qdrant_store import COLLECTION, EntityBoosts, branch_scores, build_request
from visualseek_retrieval.spotting import EntitySpotter, Spotting
from visualseek_retrieval.text import normalize, surface

# Cues are matched on accented words when the query is accented, else on folded words:
# folded "hang" is also "hàng" (goods), "khac" is also "khắc" (carve), "ngoai" "ngoại".
RELATION_ACCENTED = re.compile(r"\b(hãng|thương hiệu|nhà sản xuất|brand|nhãn hiệu)\b")
RELATION_FOLDED = re.compile(
    r"\b(thuong hieu|nha san xuat|brand|nhan hieu|cung hang|hang san xuat)\b"
)
# Exclusion cues come from connectors.py ("trừ", "không lấy", "ngoài X ra", but not the
# additive "ngoài ra"); "khác" (other) is the multi-hop cue and stays here, as does the
# folded "tru" the folded rule always accepted.
OTHER_ACCENTED = re.compile(r"\b(khác)\b")
OTHER_FOLDED = re.compile(r"\b(khac|tru)\b")

# Relation cues the graph cannot answer yet (PLAN_PRODUCTS_V1.md section 4.1). They are only
# reported in the trace so a content-only answer is visibly partial; they change no ranking.
UNSUPPORTED_CUES: tuple[tuple[str, re.Pattern, re.Pattern], ...] = tuple(
    (relation, re.compile(rf"\b({accented})\b"), re.compile(rf"\b({folded})\b"))
    for relation, accented, folded in (
        (
            "TREATS",
            "cùng bệnh|cùng công dụng|cùng tác dụng|cùng chỉ định|điều trị cùng|trị cùng|chữa cùng",
            "cung benh|cung cong dung|cung tac dung|cung chi dinh|dieu tri cung|tri cung|chua cung",
        ),
        (
            "HAS_INGREDIENT",
            "cùng hoạt chất|cùng thành phần",
            "cung hoat chat|cung thanh phan",
        ),
        (
            "FITS",
            "lắp cùng|lắp cho cùng|cùng dòng xe|tương thích với",
            "lap cung|lap cho cung|cung dong xe|tuong thich voi",
        ),
        (
            "ORIGIN",
            "cùng vùng|cùng xuất xứ|cùng nguồn gốc|cùng quê",
            "cung vung|cung xuat xu|cung nguon goc|cung que",
        ),
        ("IS_A", "cùng loại|tương tự|thay thế cho", "cung loai|tuong tu|thay the cho"),
    )
)


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
    # B2 category intent. A prior, not a filter: labels are ~98 % accurate. Weight chosen with
    # scripts/evaluate_category.py: eval/category-v1 P@10 0.72 -> 0.81, descriptive-v1 nDCG flat.
    w_category: float = 0.2
    w_tier: float = 0.2
    knn_min_share: float = 0.6
    knn_min_sim: float = 0.45
    lexicon_min_share: float = 0.15
    # A category-filtered dense channel raised category precision but lowered nDCG on the
    # human-judged descriptive set (-0.012): it adds candidates the text channels rank low.
    category_prefetch: bool = False
    segment_categories: bool = True
    graph_prefetch: bool = True  # the "dense theo tập đích" channel; off only for ablations


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
    relation_cue: str | None = None
    # A relation was asked and its value found, but no other entity carries it: answering
    # with content search would return unrelated images, so the search returns none.
    no_target: bool = False
    negation_cue: str | None = None
    unsupported_cues: list[dict] = field(default_factory=list)
    origin_ids: list[str] = field(default_factory=list)
    value_ids: list[str] = field(default_factory=list)
    category: CategoryIntent | None = None
    category_source: str | None = None  # "lexicon" | "knn" | "mixed"
    # One category per query segment ("lốp xe máy, dầu nhớt và vali"): results are shared
    # between them like the entity intents of a multi-intent query.
    category_groups: list[str] = field(default_factory=list)
    category_segments: list[dict] = field(default_factory=list)


class SearchPipeline:
    def __init__(
        self,
        graph: dict,
        entity_dense: np.ndarray,
        encoder,
        client,
        collection: str = COLLECTION,
        settings: Settings | None = None,
        review: EntityReview | None = None,
        short_aliases: dict[str, list[str]] | None = None,
        suppressed_aliases: dict[str, list[str]] | None = None,
        categories: dict | None = None,
    ) -> None:
        self.graph = graph
        self.settings = settings or Settings()
        self.encoder = encoder
        self.client = client
        self.collection = collection
        self.review = review or EntityReview()
        self.spotter = EntitySpotter(
            graph, entity_dense, self.review, short_aliases, suppressed_aliases
        )
        self.entities = {entity["id"]: entity for entity in graph["entities"]}
        self.brands = {brand["id"]: brand for brand in graph["brands"]}
        # B2 categories: lexicon cues + the category of every entity-card row for the kNN vote.
        self.category_names: dict[str, str] = {}
        self.lexicon: CategoryLexicon | None = None
        self.entity_category: list[str | None] = []
        if categories:
            self.category_names = categories["category_names"]
            self.lexicon = CategoryLexicon.build(categories["entities"], self.category_names)
            self.entity_category = [
                (categories["entities"].get(self.review.canonical(e["id"])) or {}).get("category")
                for e in graph["entities"]
            ]
        self.by_brand: dict[str, list[str]] = defaultdict(list)
        self.brand_edges: dict[tuple[str, str], dict] = {}
        for edge in graph["edges"]:
            if edge["rel"] == "MADE_BY":
                self.by_brand[edge["dst"]].append(edge["src"])
                self.brand_edges[edge["src"], edge["dst"]] = edge

    # ---- S1 + S2 -------------------------------------------------------------------------
    def plan(self, query: str, spotting: Spotting) -> Plan:
        s = self.settings
        accented = surface(query) != normalize(query)
        text = surface(query) if accented else normalize(query)
        cue = (RELATION_ACCENTED if accented else RELATION_FOLDED).search(text)
        exclusion = connectors.has_negation(query)
        other = (OTHER_ACCENTED if accented else OTHER_FOLDED).search(text)
        found = [(exclusion.start, exclusion.text)] if exclusion else []
        found += [(other.start(), other.group())] if other else []
        negation = min(found) if found else None
        plan = Plan(
            relation=cue is not None,
            relation_cue=cue.group() if cue else None,
            negation=negation is not None,
            negation_cue=negation[1] if negation else None,
            unsupported_cues=[
                {"relation": relation, "cue": found.group()}
                for relation, accented_cue, folded_cue in UNSUPPORTED_CUES
                if (found := (accented_cue if accented else folded_cue).search(text))
            ],
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
            best, score = spotting.dense_top[0]
            runner = spotting.dense_top[1][1] if len(spotting.dense_top) > 1 else -1.0
            if score >= s.dense_seed_min and score - runner >= s.dense_seed_margin:
                entities, plan.dense_seed = [best], True
            elif score < s.out_of_scope_max:
                plan.out_of_scope = True
        if not entities and not brands and self.lexicon is not None:
            self._category_intent(plan, query, spotting)
        if plan.out_of_scope:
            return plan

        if plan.relation and entities:
            # "sản phẩm khác của hãng làm X": X is the anchor, the targets are X's brand siblings.
            anchor_brands = {
                self.entities[e]["brand_id"] for e in self.review.indexed_ids(entities)
            } - {None}
            plan.origin_ids = entities
            plan.value_ids = sorted(anchor_brands)
            siblings = sorted(
                {self.review.canonical(o) for b in anchor_brands for o in self.by_brand[b]}
            )
            if siblings:
                plan.anchors = entities if plan.negation else []
                plan.hops = (
                    [o for o in siblings if o not in entities] if plan.negation else siblings
                )
                plan.intents = len(anchor_brands)
                # e.g. the brand's only other product was merged into the anchor (SAME_AS).
                plan.no_target = not plan.hops
                return plan
        plan.seeds = entities
        plan.value_ids = sorted(set(plan.value_ids) | set(brands))
        for brand in brands:
            plan.hops.extend(self.review.canonical(o) for o in self.by_brand.get(brand, ()))
        plan.hops = list(dict.fromkeys(plan.hops))
        if plan.negation and plan.seeds and plan.hops:
            # "Shimano trừ BB-9000": the spotted entity is excluded from its brand's results.
            plan.anchors, plan.seeds = plan.seeds, []
            plan.hops = [o for o in plan.hops if o not in plan.anchors]
        plan.intents = len(plan.seeds) + len(brands)
        return plan

    def _category_intent(self, plan: Plan, query: str, spotting: Spotting) -> None:
        """Category/tier cue of a query that names no entity. It never lifts the OOS gate."""
        if plan.out_of_scope:
            # The gate found the query far from every entity card; a cue must not override it
            # ("lời bài hát Nối vòng tay lớn" contains the jewelry cue "vòng tay").
            return
        parts = connectors.segments(query) if self.settings.segment_categories else [query]
        if len(parts) >= 2 and self.encoder is not None:
            # Each part gets its own cue and its own nearest cards: voting over the whole
            # query lets one object outvote the others ("tai nghe và chuột máy tính").
            vectors = self.encoder.encode(parts)
            found = [
                (part, *self._segment_intent(part, self.spotter.entity_dense @ vector))
                for part, vector in zip(parts, vectors, strict=True)
            ]
        else:
            found = [(query, *self._segment_intent(query, spotting.entity_scores))]
        useful = [(part, intent, source) for part, intent, source in found if intent]
        if not useful:
            return
        merged = CategoryIntent()
        for _, intent, _ in useful:
            merged.matches += intent.matches
            merged.categories += [c for c in intent.categories if c not in merged.categories]
            merged.tiers += [t for t in intent.tiers if t not in merged.tiers]
            merged.ignored_tiers += intent.ignored_tiers
        sources = {source for _, intent, source in useful if intent.categories}
        plan.category = merged
        plan.category_source = (
            (sources.pop() if len(sources) == 1 else "mixed") if sources else None
        )
        if len(found) >= 2:
            plan.category_segments = [
                {"text": part, "categories": intent.categories, "tiers": intent.tiers}
                for part, intent, _ in found
            ]
            groups = [intent.categories[0] for _, intent, _ in useful if intent.categories]
            if len(set(groups)) >= 2:
                plan.category_groups = list(dict.fromkeys(groups))

    def _segment_intent(self, text: str, scores) -> tuple[CategoryIntent, str]:
        s = self.settings
        intent, source = self.lexicon.detect(text), "lexicon"
        votes, similarity = {}, 0.0
        if scores is not None:
            votes, similarity = knn_votes(scores, self.entity_category)
        if intent.categories and votes:
            # A lexicon cue must be backed by the entity cards nearest to the text: a phrase
            # such as "huyết áp" is also in drug names, which the cards disagree with.
            kept = [c for c in intent.categories if votes.get(c, 0.0) >= s.lexicon_min_share]
            intent.matches = [m for m in intent.matches if m["category"] in kept]
            intent.categories = kept
        if not intent.categories and votes and similarity >= s.knn_min_sim:
            category, share = max(votes.items(), key=lambda item: item[1])
            if share >= s.knn_min_share:
                intent.categories, source = [category], "knn"
                intent.matches = [
                    {"text": text, "category": category, "support": 0, "share": round(share, 3)}
                ]
        return intent, source

    def _trace(self, plan: Plan, brand_filter: str | None) -> dict:
        relation = bool(plan.relation or plan.value_ids)
        targets = set(plan.hops)
        values = []
        for brand_id in plan.value_ids:
            edges = self.by_brand[brand_id]
            values.append(
                {
                    "id": brand_id,
                    "label": self.brands[brand_id]["name"],
                    "kind": "Brand",
                    "origin_edges": [
                        self._edge_trace(e, brand_id)
                        for e in self.review.indexed_ids(plan.origin_ids)
                        if (e, brand_id) in self.brand_edges
                    ],
                    "target_edges": [
                        self._edge_trace(e, brand_id)
                        for e in edges
                        if self.review.canonical(e) in targets
                    ],
                }
            )
        return {
            "version": 1,
            "relation": "MADE_BY" if relation else None,
            "cue": plan.relation_cue,
            "mode": (
                "shared_value"
                if plan.relation and plan.origin_ids
                else "value_filter"
                if plan.value_ids
                else None
            ),
            "origins": [self._entity_ref(e) for e in plan.origin_ids or plan.seeds or plan.anchors],
            "values": values,
            "targets": [self._entity_ref(e) for e in dict.fromkeys(plan.hops)],
            "excluded": [self._entity_ref(e) for e in plan.anchors],
            "brand_filter": (
                {
                    "id": brand_filter,
                    "label": self.brands.get(brand_filter, {}).get("name", brand_filter),
                }
                if brand_filter
                else None
            ),
            "unsupported_cues": plan.unsupported_cues,
            "negation": self._negation_trace(plan),
            "category": self._category_trace(plan),
            "status": (
                "clarification"
                if plan.clarification is not None
                else "out_of_scope"
                if plan.out_of_scope
                else "expanded"
                if plan.hops
                else "no_target"
                if plan.value_ids
                else "no_edge"
                if relation and plan.origin_ids
                else "no_origin"
                if relation
                else "unsupported_relation"
                if plan.unsupported_cues
                else "category"
                if plan.category and plan.category.categories
                else "content"
            ),
        }

    def _category_trace(self, plan: Plan) -> dict | None:
        intent = plan.category
        if intent is None:
            return None
        return {
            "source": plan.category_source,
            "categories": [
                {"id": c, "label": self.category_names.get(c, c)} for c in intent.categories
            ],
            "matches": intent.matches,
            "tiers": intent.tiers,
            "ignored_tiers": intent.ignored_tiers,
            "segments": plan.category_segments,
            "groups": plan.category_groups,
        }

    @staticmethod
    def _negation_trace(plan: Plan) -> dict | None:
        """Whether "ngoài/trừ/khác" removed anything, and why not when it did not."""
        if plan.negation_cue is None:
            return None
        if plan.anchors:
            reason = None
        elif plan.clarification is not None or plan.out_of_scope:
            reason = "not_searched"
        elif not (plan.seeds or plan.origin_ids):
            reason = "no_origin"
        else:
            # Exclusion needs graph targets to exclude from; without them the named entity
            # is searched (and boosted) as an ordinary seed.
            reason = "no_targets"
        return {"cue": plan.negation_cue, "applied": reason is None, "reason": reason}

    def _entity_ref(self, entity_id: str) -> dict:
        return {"id": entity_id, "label": self.entities[entity_id]["name"]}

    def _edge_trace(self, entity_id: str, brand_id: str) -> dict:
        edge = self.brand_edges[entity_id, brand_id]
        entity = self._entity_ref(entity_id)
        # The silver builder always matches the name. A source such as "jsonld+name"
        # describes the brand vocabulary, so it is not evidence from this entity's page.
        return {
            "entity_id": self.review.canonical(entity_id),
            "source_entity_id": entity_id,
            "source": edge.get("source"),
            "confidence": edge.get("confidence"),
            "reviewed": edge.get("reviewed", False),
            "evidence": [{"field": "name", "text": entity["label"]}],
        }

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
        channels = dict.fromkeys(
            ("dense", "sparse", "graph_prefetch", "graph", "category", "img"), "inactive"
        )
        if not plan.out_of_scope and plan.clarification is None and not plan.no_target:
            boosts = EntityBoosts(
                seeds=self.review.indexed_ids(plan.seeds),
                hops=self.review.indexed_ids(plan.hops),
                anchors=self.review.indexed_ids(plan.anchors),
                w_seed=s.w_dense_seed if plan.dense_seed else s.w_seed,
                w_hop=s.w_hop if plan.seeds else s.w_seed,
                w_anchor=s.w_anchor,
                categories=plan.category.categories if plan.category else [],
                tiers=plan.category.tiers if plan.category else [],
                w_category=s.w_category,
                w_tier=s.w_tier,
                category_prefetch=s.category_prefetch,
                graph_prefetch=s.graph_prefetch,
            )
            request = build_request(
                vector.tolist(),
                lexical.query_vector(query),
                boosts,
                depth=s.depth,
                filter_brand=brand_id,
                blocked_assets=self.review.blocked_assets,
            )
            tick = time.perf_counter()
            grouped = plan.intents >= 2 or bool(plan.hops) or len(plan.category_groups) >= 2
            # Fetch the independent diagnostics concurrently with the existing ranker.
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(branch_scores, self.client, self.collection, request)
                points = self._rank(plan, grouped, request)
                timings["qdrant"] = _ms(tick)
                try:
                    scores = pending.result()
                except ApiException:
                    scores = {}
                timings["qdrant_with_trace"] = _ms(tick)
            for name in ("dense", "sparse", "graph_prefetch"):
                if name != "graph_prefetch" or (boosts.targets() and boosts.graph_prefetch):
                    channels[name] = "ready" if name in scores else "unavailable"
            channels["graph"] = "ready" if boosts.targets() or boosts.anchors else "inactive"
            category_on = bool(boosts.categories or boosts.tiers)
            channels["category"] = "ready" if category_on else "inactive"
            seen: set[str] = set()
            for point in points:
                payload = point.payload
                asset_id = payload["asset_id"]
                entity_ids = self.review.result_ids(asset_id, payload["entity_ids"])
                if asset_id in seen or not entity_ids:
                    continue
                seen.add(asset_id)
                adjustment = boosts.score(payload["entity_ids"])
                category_adjustment = boosts.category_score(
                    payload.get("categories") or [], payload.get("market_tiers") or []
                )
                point_id = str(point.id)
                parts = {
                    name: scores.get(name, {}).get(point_id)
                    for name in ("dense", "sparse", "graph_prefetch")
                }
                parts.update(
                    graph=adjustment if channels["graph"] == "ready" else None,
                    category=category_adjustment if category_on else None,
                    img=None,
                    rrf=point.score - adjustment - category_adjustment,
                )
                hits.append(
                    {
                        "asset_id": asset_id,
                        "score": point.score,
                        "scores": parts,
                        **_public(payload),
                        "entity_ids": entity_ids,
                    }
                )
                if len(hits) == s.top_k:
                    break
        trace = self._trace(plan, brand_id)
        trace["channels"] = channels
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
            "trace": trace,
            "cache": {"embedding": False, "result": False},
            "graph_fingerprint": self.graph["fingerprint"],
        }

    def _rank(self, plan: Plan, grouped: bool, request: dict) -> list:
        s = self.settings
        if not grouped:
            result = self.client.query_points(
                self.collection, limit=s.top_k, with_payload=True, **request
            )
            return result.points
        if not plan.hops and not plan.seeds and len(plan.category_groups) >= 2:
            size = max(1, s.top_k // len(plan.category_groups))
            result = self.client.query_points_groups(
                self.collection,
                group_by="categories",
                limit=s.top_k * GROUP_OVERFETCH,
                group_size=size,
                with_payload=True,
                **request,
            )
            return merge_groups(result.groups, str, size, set(plan.category_groups))
        if plan.hops:
            # Spread top-k over the graph targets, including when a brand has few products.
            targets = len(set(plan.seeds) | set(plan.hops))
            size = min(s.top_k, max(s.min_group_size, math.ceil(s.top_k / targets)))
        else:
            size = max(1, s.top_k // plan.intents)
        # Payload entity_ids are the corpus ids, so an entity merged by SAME_AS comes back as
        # several groups and would take several intent slots. Over-fetch groups, then merge
        # them by canonical id and cap each merged group at the same size.
        result = self.client.query_points_groups(
            self.collection,
            group_by="entity_ids",
            limit=s.top_k * GROUP_OVERFETCH,
            group_size=size,
            with_payload=True,
            **request,
        )
        targets = {self.review.canonical(e) for e in plan.seeds + plan.hops}
        return merge_groups(result.groups, self.review.canonical, size, targets)


GROUP_OVERFETCH = 2


def merge_groups(groups, canonical, size: int, targets: set[str] | None = None) -> list:
    """Hits of Qdrant groups merged by canonical entity, up to ``size`` distinct images each.

    Groups of the query's target entities come first (best group first, as Qdrant orders
    them); other groups only fill what is left. An image already taken by an earlier group
    does not count again: an image shared by two entities would otherwise fill two slots.
    """
    merged: dict[str, dict] = {}
    for group in groups:
        bucket = merged.setdefault(canonical(str(group.id)), {})
        for hit in group.hits:
            bucket.setdefault(str(hit.id), hit)
    order = list(merged)
    if targets:
        order = [k for k in order if k in targets] + [k for k in order if k not in targets]
    used: set[str] = set()
    out = []
    for key in order:
        taken = 0
        for hit in sorted(merged[key].values(), key=lambda h: -h.score):
            if taken == size:
                break
            if str(hit.id) not in used:
                used.add(str(hit.id))
                out.append(hit)
                taken += 1
    return out


def _ms(tick: float) -> float:
    return round((time.perf_counter() - tick) * 1000, 2)


def _public(payload: dict) -> dict:
    return {
        key: payload.get(key)
        for key in (
            "entity_ids",
            "brand_ids",
            "image_path",
            "page_title",
            "domain",
            "categories",
            "market_tiers",
        )
    }
