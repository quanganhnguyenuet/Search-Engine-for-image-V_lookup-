"""One search path for every query type: S1 spotting -> S2 brand expansion -> S3 Qdrant.

No router and no LLM at query time. Relation and negation cues are a small rule set; the
graph decides which entities they reach. See PLAN_PRODUCTS_V1.md section 4.4.
"""

from __future__ import annotations

import dataclasses
import math
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from qdrant_client.http.exceptions import ApiException

from visualseek_retrieval import connectors, lexical, type_request
from visualseek_retrieval.categories import CategoryIntent, CategoryLexicon, knn_votes
from visualseek_retrieval.entity_review import EntityReview
from visualseek_retrieval.fusion import VisualFusion, fuse, starts_with_visual_attribute
from visualseek_retrieval.qdrant_store import (
    COLLECTION,
    EntityBoosts,
    branch_scores,
    build_request,
    point_id,
)
from visualseek_retrieval.spotting import EntitySpotter, Spotting, query_words
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
    # P1: a brand query keeps the brand's products of the type the query asks for.
    type_filter: bool = True
    # Extend a requested type to the categories of the products it names ("túi" -> bags).
    # Off: on hops-v1 it added no correct product to multi_hop and pulled wrong ones into
    # multi_intent ("vợt tennis Yonex" -> shuttlecocks, "bugi Bosch" -> batteries).
    type_expand: bool = False
    # When the query has target entities, fewer than top k images are shown rather than
    # unrelated ones: a brand/relation query shows only its targets, a query naming products
    # fills with images of their categories ("lốp Michelin Pro4 và săm xe đạp").
    fill_related_only: bool = True
    # A query of >= 2 category groups gets one category-filtered dense channel per group.
    group_prefetch: bool = True
    # P6: S4 visual fusion (bge-m3 doc + SigLIP2 img) for descriptive/category queries.
    visual_fusion: bool = True


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
    # One entry per brand whose products were looked at for the requested type.
    type_requests: list[dict] = field(default_factory=list)
    # Entities matched by a generic product-type clause in a mixed multi-intent query.
    generic_hits: list[str] = field(default_factory=list)


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
        image_encoder=None,
        fusion: VisualFusion | None = None,
        payloads: dict[str, dict] | None = None,
    ) -> None:
        self.graph = graph
        self.settings = settings or Settings()
        self.encoder = encoder
        self.client = client
        self.collection = collection
        self.review = review or EntityReview()
        self.image_encoder = image_encoder
        self.fusion = fusion
        self.payloads = payloads or {}
        self.spotter = EntitySpotter(
            graph, entity_dense, self.review, short_aliases, suppressed_aliases
        )
        self.entities = {entity["id"]: entity for entity in graph["entities"]}
        self.short_aliases = short_aliases or {}
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
        # canonical entity id -> category and product_type words, for the type request (P1).
        self.product_types: dict[str, dict] = {}
        for entity_id, info in (categories or {}).get("entities", {}).items():
            words = surface(info.get("product_type") or "").split()
            self.product_types[entity_id] = {
                "category": info.get("category"),
                "accented": tuple(words),
                "folded": tuple(normalize(word) for word in words),
                "name_accented": tuple(surface(info.get("name") or "").split()),
                "name_folded": tuple(normalize(info.get("name") or "").split()),
            }
        # Every product type of the catalogue, per script: is a phrase a product noun?
        self.catalogue_types = {
            key: {p: info[key] for p, info in self.product_types.items()}
            for key in ("accented", "folded")
        }
        self._head_nouns: dict[tuple[tuple[str, ...], bool], bool] = {}
        self.by_brand: dict[str, list[str]] = defaultdict(list)
        self.brand_edges: dict[tuple[str, str], dict] = {}
        for edge in graph["edges"]:
            if edge["rel"] == "MADE_BY":
                self.by_brand[edge["dst"]].append(edge["src"])
                self.brand_edges[edge["src"], edge["dst"]] = edge
        # Product lines ("Redmi" under Xiaomi) that start >= 2 canonical entities of one brand.
        # A word of any product_type ("Nhẫn ... PNJ" -> "nhan") names a kind of product, and a
        # word of the brand's own name is spotted as the brand: neither is a line.
        type_words = {word for info in self.product_types.values() for word in info["folded"]}
        brand_words = {
            brand["id"]: {
                word
                for name in (brand["name"], *brand.get("aliases", ()))
                for word in normalize(name).split()
            }
            for brand in graph["brands"]
        }
        line_brands: dict[str, set[str]] = defaultdict(set)
        line_counts: dict[tuple[str, str], int] = defaultdict(int)
        for entity in graph["entities"]:
            bid = entity.get("brand_id")
            core_words = normalize(entity.get("core") or entity.get("name") or "").split()
            if bid and core_words:
                first = core_words[0]
                if (
                    len(first) >= 3
                    and first.isascii()
                    and not first.isdigit()
                    and first not in type_request.FILLER_FOLDED
                    and first not in type_words
                    and first not in brand_words.get(bid, ())
                ):
                    line_brands[first].add(bid)
                    line_counts[first, bid] += 1
        self.product_lines: dict[str, str] = {
            word: next(iter(bids))
            for word, bids in line_brands.items()
            if len(bids) == 1 and line_counts[word, next(iter(bids))] >= 2
        }

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
                request, request_accented = self._query_request(query, spotting, accented)
                siblings, dropped = self._narrow(
                    plan, sorted(anchor_brands)[0], siblings, request, request_accented
                )
                plan.anchors = list(dict.fromkeys([*(entities if plan.negation else []), *dropped]))
                plan.hops = (
                    [o for o in siblings if o not in entities] if plan.negation else siblings
                )
                plan.intents = len(anchor_brands)
                # e.g. the brand's only other product was merged into the anchor (SAME_AS).
                plan.no_target = not plan.hops
                return plan
        plan.seeds = entities
        plan.value_ids = sorted(set(plan.value_ids) | set(brands))
        requests, unlinked = (
            self._brand_requests(query, bool(entities or brands))
            if s.type_filter and (brands or entities)
            else ({}, [])
        )
        for brand in requests:
            if brand not in brands:
                brands.append(brand)
                plan.value_ids = sorted(set(plan.value_ids) | {brand})
        dropped: list[str] = []
        for brand in brands:
            products = [self.review.canonical(o) for o in self.by_brand.get(brand, ())]
            products = list(dict.fromkeys(products))
            if brand in requests:
                products, gone = self._narrow(plan, brand, products, *requests[brand])
                dropped.extend(gone)
            plan.hops.extend(products)
        plan.hops = list(dict.fromkeys(plan.hops))
        if plan.negation and plan.seeds and plan.hops:
            # "Shimano trừ BB-9000": the spotted entity is excluded from its brand's results.
            plan.anchors, plan.seeds = plan.seeds, []
            plan.hops = [o for o in plan.hops if o not in plan.anchors]
        plan.anchors = list(dict.fromkeys([*plan.anchors, *dropped]))
        if not plan.relation and not plan.negation and (plan.seeds or brands):
            self._generic_intents(plan, query, spotting, unlinked)
        if (
            not plan.hops
            and not plan.seeds
            and not plan.generic_hits
            and any(t["status"] == "absent" for t in plan.type_requests)
        ):
            # Every brand asked for lacks the type: content search would show other brands'
            # products or the brand's other ones, so the search shows none.
            plan.no_target = True
        plan.intents = len(plan.seeds) + len(brands) + (1 if plan.generic_hits else 0)
        return plan

    # ---- P1: the type of product a brand query asks for ------------------------------------
    def _query_request(
        self, query: str, spotting: Spotting, accented: bool
    ) -> tuple[type_request.Request, bool]:
        """Type phrases of one query piece (a relation piece keeps its cue and its anchor)."""
        found = [*spotting.matches, *spotting.ambiguous]
        request = type_request.extract(
            query_words(query),
            [(m.start, m.end) for m in found],
            [(m.start, m.end) for m in found if m.kind == "entity"],
            accented,
            RELATION_ACCENTED if accented else RELATION_FOLDED,
        )
        return request, accented

    def _entity_brands(self, ids: list[str]) -> set[str]:
        return {
            self.entities[self.review.canonical(i)].get("brand_id")
            for i in ids
            if self.review.canonical(i) in self.entities
        } - {None}

    def _brand_products(self, brand: str) -> list[str]:
        return list(dict.fromkeys(self.review.canonical(o) for o in self.by_brand.get(brand, ())))

    def _brand_requests(
        self, query: str, has_named: bool = True
    ) -> tuple[
        dict[str, tuple[type_request.Request, bool]],
        list[tuple[type_request.Request, bool, str]],
    ]:
        """Per brand, what the clauses about it ask for, plus any unlinked generic clauses.

        - "bugi Bosch và nhớt Mobil": each clause asks its own brand for its own type;
        - "trang sức PNJ nhưng không phải nhẫn": a clause that only excludes belongs to the
          last brand named before it;
        - "Bosch có bugi và lọc dầu nào": a clause that names no brand belongs to a brand of
          an earlier/neighbouring clause when it names a product type that brand actually makes
          (otherwise it is a separate intent of its own, returned in ``unlinked``).
        """
        clauses = []
        # What S1 finds in the whole query is never cut: "bình nước lock&lock inox 600ml" is one
        # product, not "lock" and "lock inox 600ml".
        sp = self.spotter.spot(query, None)
        pieces = connectors.segments(
            query, [(m.start, m.end) for m in [*sp.matches, *sp.ambiguous]]
        )
        if len(pieces) == 1 and has_named:
            # "chuột Logitech MX Master 3 với bàn phím": "với" after a spotted entity can
            # introduce a second generic object when the tail is not a visual attribute.
            words = query_words(query)
            if any(m.kind == "entity" for m in sp.matches):
                last_end = max(m.end for m in sp.matches if m.kind == "entity")
                for idx in range(last_end, len(words) - 1):
                    if normalize(words[idx]) == "voi" and words[idx] in ("với", "voi"):
                        tail = " ".join(words[idx + 1 :])
                        # "... với màu trắng", "... với dây da" describe the entity itself.
                        if tail and not starts_with_visual_attribute(tail):
                            pieces = [" ".join(words[:idx]), tail]
                        break
        spotted = [self.spotter.spot(piece, None) for piece in pieces]
        query_brands = {b for sp in spotted for m in sp.matches if m.kind == "brand" for b in m.ids}
        for number, (piece, spotting) in enumerate(zip(pieces, spotted, strict=True)):
            accented = surface(piece) != normalize(piece)
            if query_brands and connectors.has_negation(piece):
                # "PNJ nhưng không phải nhẫn kim cương": the spotter finds the brandless entity
                # "Nhẫn kim cương", but what is excluded is that type of the brand's products.
                # An entity of none of the query's brands is read as type words here.
                spotting = dataclasses.replace(
                    spotting,
                    matches=[
                        m
                        for m in spotting.matches
                        if m.kind != "entity" or self._entity_brands(m.ids) & query_brands
                    ],
                )
            request, _ = self._query_request(piece, spotting, accented)
            request.clauses = [number * 100 + c for c in request.clauses]
            brands = list(
                dict.fromkeys(b for m in spotting.matches if m.kind == "brand" for b in m.ids)
            )
            if not brands and not spotting.matches and request.include:
                for run in request.include:
                    for word in run:
                        line_brand = self.product_lines.get(normalize(word))
                        if line_brand and line_brand not in brands:
                            brands.append(line_brand)
            clauses.append((brands, bool(spotting.matches), request, accented, piece))
        own: dict[str, list[tuple[type_request.Request, bool]]] = defaultdict(list)
        lent: dict[str, list[tuple[type_request.Request, bool]]] = defaultdict(list)
        unlinked: list[tuple[type_request.Request, bool, str]] = []
        last_brands: list[str] = []
        for index, (brands, named, request, accented, piece) in enumerate(clauses):
            for brand in brands:
                own[brand].append((request, accented))
            if brands:
                last_brands = brands
                continue
            if named:
                continue  # the clause has an entity of its own ("..., trừ Maxxis Receptor")
            if request.exclude and not request.include:
                for brand in last_brands:
                    lent[brand].append((request, accented))
                continue
            if request.include:
                near = [
                    *last_brands,
                    *(
                        b
                        for i in (index - 1, index + 1)
                        if 0 <= i < len(clauses)
                        for b in clauses[i][0]
                    ),
                ]
                borrowed = False
                for brand in dict.fromkeys(near):
                    # Only a type the brand makes, read from its own products: "dây chuyền
                    # vàng" next to "nhẫn cưới Bvlgari" is jewellery, but not a Bvlgari ask.
                    # The type must be the head of the product's type: "máy in" next to
                    # "máy ảnh Canon" is not Canon's "Hộp mực máy in".
                    products = self._brand_products(brand)
                    if self._type_hits(products, request, accented, lexicon=False, head="only"):
                        lent[brand].append((request, accented))
                        borrowed = True
                if not borrowed:
                    unlinked.append((request, accented, piece))
        merged = {}
        for brand, items in own.items():
            borrowed = lent.get(brand, [])
            # A clause that asks for no type leaves the brand whole, unless another clause
            # says which of its products are meant.
            asked = all(r.include for r, _ in items) or any(r.include for r, _ in borrowed)
            everything = items + borrowed
            include = [run for r, _ in everything for run in r.include] if asked else []
            clauses_out = [r.clause_of(i) for r, _ in everything for i in range(len(r.include))]
            exclude = [run for r, _ in everything for run in r.exclude]
            anchor = [run for r, _ in everything for run in r.anchor]
            merged[brand] = (
                type_request.Request(include, exclude, clauses_out if asked else [], anchor),
                items[0][1],
            )
        return merged, unlinked

    def _generic_intents(
        self,
        plan: Plan,
        query: str,
        spotting: Spotting,
        unlinked: list[tuple[type_request.Request, bool, str]],
    ) -> None:
        """Resolve unlinked generic product-type clauses in a mixed multi-intent query (P3)."""
        if not self.product_types or not unlinked:
            return
        all_products = list(self.product_types)
        existing = set(plan.seeds) | set(plan.hops) | set(plan.anchors)
        hits: list[str] = []
        for req, accented, _ in unlinked:
            found = self._type_hits(all_products, req, accented, lexicon=False, catalogue=True)
            for eid in sorted(found):
                if eid not in existing and eid not in hits:
                    hits.append(eid)
        if hits:
            plan.generic_hits = hits

    def _mentions(
        self, request: type_request.Request, products: list[str]
    ) -> tuple[type_request.Request, set[str], set[str]]:
        """Brand products named in the runs by one of their aliases, and the runs without them.

        Within one brand an alias the global spotter suppresses as ambiguous ("vios", "magic
        mary") is safe. An alias that is also a product-type phrase of the brand ("lốp") is a
        type, not a name.
        """
        types = [self.product_types[p]["folded"] for p in products if p in self.product_types]
        index: dict[tuple[str, ...], set[str]] = defaultdict(set)
        for product in products:
            entity = self.entities.get(product) or {}
            names = {*entity.get("aliases", ()), normalize(entity.get("core") or "")}
            names |= {normalize(a) for a in self.short_aliases.get(product, ())}
            names |= {normalize(a) for a in self.review.aliases.get(product, ())}
            for name in names:
                words = tuple(name.split())
                if (
                    len("".join(words)) >= 3
                    and not all(w in type_request.FILLER_FOLDED for w in words)
                    and not any(type_request.contains(t, {words}) for t in types)
                ):
                    index[words].add(product)
        include, clauses, named_in = [], [], set()
        for i, run in enumerate(request.include):
            left, found = type_request.take_mentions([run], index)
            named_in |= found
            include += left
            clauses += [request.clause_of(i)] * len(left)
        exclude, named_out = type_request.take_mentions(request.exclude, index)
        anchor, named_anchor = type_request.take_mentions(request.anchor, index)
        named_in |= named_anchor
        return type_request.Request(include, exclude, clauses, anchor), named_in, named_out

    def _type_hits(
        self,
        products: list[str],
        request: type_request.Request,
        accented: bool,
        expand: bool = False,
        lexicon: bool = True,
        catalogue: bool = False,
        head: str = "prefer",
    ) -> set[str]:
        """Products of the requested type among ``products``.

        Each run hits the products whose ``product_type`` holds its longest matching phrase
        (``catalogue``: the stricter ``type_request.phrase_hits``, for the whole catalogue),
        at the head of the type when possible (``type_request.by_head``); with ``head="only"``
        nothing else, and no match on names.
        The runs of one clause narrow each other ("phụ tùng ... xe ... Air Blade" keeps the
        Air Blade parts, not every "xe"); when they share no product, the most specific run
        wins. Clauses add up. With ``expand`` the hits extend to their categories; without
        any hit, the categories the lexicon reads in the runs ("trang sức") decide.
        """
        key = "accented" if accented else "folded"
        typed = {p: self.product_types[p][key] for p in products if p in self.product_types}
        named = {p: self.product_types[p]["name_" + key] for p in typed}
        # Per run (strict hits, loose hits): loose ones also count the phrase anywhere in the
        # type, for runs that narrow each other ("xe" in "phụ tùng cho xe Honda Air Blade"
        # qualifies the Air Blade parts; alone it means the vehicles).
        by_clause: dict[int, list[tuple[set[str], set[str]]]] = defaultdict(list)
        for i, run in enumerate(request.include):
            if catalogue:
                hits = loose = type_request.phrase_hits(typed, run, accented)
            else:
                hits = type_request.longest_hits(typed, [run], accented, head="only")
                loose = (
                    hits if head == "only" else type_request.longest_hits(typed, [run], accented)
                )
                if not hits and head == "prefer" and not self._head_noun(run, accented):
                    # "ô tô" heads almost no type: "đồ Bosch cho ô tô" asks for the products
                    # it qualifies ("Bugi ô tô"). "máy in" mostly heads its types: Canon's
                    # "Hộp mực máy in" is not a printer.
                    hits = loose
            if not hits and head != "only" and (
                len(run) >= 2
                or any(c.isdigit() for c in "".join(run))
                or any(normalize(w) in self.product_lines for w in run)
            ):
                # A product line in the names, whole ("Air Blade" in "Piston Honda Air Blade",
                # "Redmi" in "Redmi Note 11").
                lines = [normalize(w) for w in run if normalize(w) in self.product_lines]
                if lines:
                    grams = {(w,) for w in lines}
                    hits = {
                        p
                        for p in typed
                        if type_request.contains(self.product_types[p]["name_folded"], grams)
                    }
                else:
                    whole = tuple(run) if accented else tuple(normalize(w) for w in run)
                    hits = {
                        p for p, words in named.items() if type_request.contains(words, {whole})
                    }
                loose = loose | hits
            if hits:
                by_clause[request.clause_of(i)].append((hits, loose))
        direct: set[str] = set()
        for pairs in by_clause.values():
            shared = set.intersection(*(h for h, _ in pairs))
            if not shared and len(pairs) > 1:
                shared = set.intersection(*(loose for _, loose in pairs))
            direct |= shared or min((h for h, _ in pairs), key=len)
        categories = {self.product_types[p]["category"] for p in direct} if expand else set()
        if not direct and lexicon and self.lexicon is not None:
            for run in request.include:
                categories |= set(self.lexicon.detect(" ".join(run)).categories)
        categories.discard(None)
        return {
            p
            for p in products
            if p in direct or (self.product_types.get(p) or {}).get("category") in categories
        }

    def _head_noun(self, run: tuple[str, ...], accented: bool) -> bool:
        """Whether the phrase heads at least half of the catalogue types that hold it."""
        cache_key = (run, accented)
        if cache_key not in self._head_nouns:
            types = self.catalogue_types["accented" if accented else "folded"]
            anywhere = type_request.longest_hits(types, [run], accented)
            heads = type_request.longest_hits(types, [run], accented, head="only")
            self._head_nouns[cache_key] = bool(heads) and 2 * len(heads) >= len(anywhere)
        return self._head_nouns[cache_key]

    def _catalogue_categories(self, request: type_request.Request, accented: bool) -> set[str]:
        """Categories of the catalogue types the requested words name, or that the lexicon
        reads in them; empty for words that name no type ("xịn", "giá rẻ", "chính hãng")."""
        types = self.catalogue_types["accented" if accented else "folded"]
        categories: set[str] = set()
        for run in request.include:
            hits = type_request.phrase_hits(types, run, accented, head_word=False)
            categories |= {self.product_types[p]["category"] for p in hits}
            if self.lexicon is not None:
                categories |= set(self.lexicon.detect(" ".join(run)).categories)
        return categories - {None}

    def _narrow(
        self,
        plan: Plan,
        brand: str,
        products: list[str],
        request: type_request.Request,
        accented: bool,
    ) -> tuple[list[str], list[str]]:
        """(products the query asks for, products it excludes) among a brand's products.

        1. Names: brand products named in the request ("Magic Mary", "Vios") are excluded
           when they sit in an exclusion phrase, or when the query asks for "other" ones.
        2. Requested type: products whose ``product_type`` holds the phrase, extended to
           their categories ("túi" -> Túi du lịch -> the brand's other bags).
        3. Excluded type: products whose ``product_type`` (or name) holds the phrase, else
           the products of the category the lexicon reads in it ("trừ phụ tùng ô tô").
        Nothing is narrowed when it would leave no product or change nothing, except when the
        requested type exists in the catalogue but not in the brand ("điện thoại Samsung"
        without a Samsung phone): the brand then has no product to show (status "absent").
        """
        if not self.settings.type_filter or not self.product_types:
            return products, []
        if not (request or request.anchor):
            return products, []
        request, named_in, named_out = self._mentions(request, products)
        include, exclude = request.include, request.exclude
        anchor_hits: set[str] = set()
        if request.anchor and not named_in:
            anchor_hits = self._type_hits(
                products,
                type_request.Request(request.anchor, [], list(range(len(request.anchor)))),
                accented,
                lexicon=False,
            )
            if 0 < len(anchor_hits) < len(products):
                for eid in sorted(anchor_hits):
                    if eid not in plan.origin_ids:
                        plan.origin_ids.append(eid)
            else:
                anchor_hits = set()
        info = {
            "brand": self.brands[brand]["name"] if brand in self.brands else brand,
            "include": [" ".join(run) for run in include],
            "exclude": [" ".join(run) for run in exclude],
            "named": [self.entities[p]["name"] for p in sorted(named_in | named_out | anchor_hits)],
            "of": len(products),
            "kept": len(products),
            "status": "none",
        }
        kept = list(products)
        spotted = {self.review.canonical(e) for e in plan.seeds}
        named_away = set(named_out) | (
            (set(named_in) | anchor_hits | spotted) if plan.negation else set()
        )
        if include:
            # The type is read on the products the query leaves: in "Canon còn máy ảnh nào
            # khác ngoài PowerShot" the excluded PowerShot is the only "Máy ảnh ..." type.
            remaining = [p for p in kept if p not in named_away] or kept
            chosen = self._type_hits(
                remaining, request, accented, expand=self.settings.type_expand
            )
            info["status"] = "no_match" if not chosen else "no_narrowing"
            if not chosen and (categories := self._catalogue_categories(request, accented)):
                # The type exists in the catalogue: keep the brand's products of its category
                # ("máy công trình" -> Komatsu's dump truck), or none ("điện thoại Samsung").
                chosen = {
                    p
                    for p in remaining
                    if (self.product_types.get(p) or {}).get("category") in categories
                }
                if not chosen:
                    kept, info["status"] = [], "absent"
            if 0 < len(chosen) < len(remaining):
                # Excluded products stay until the exclusion step drops them as anchors.
                kept = [p for p in kept if p in chosen or p in named_away]
                info["status"] = "filtered"
        out = set(named_away)
        if exclude:
            # Every excluded phrase counts on its own: one clause per run.
            out |= self._type_hits(
                kept, type_request.Request(exclude, [], list(range(len(exclude)))), accented
            )
            key = "name_accented" if accented else "name_folded"
            # A multi-word or numbered phrase of a name identifies one product ("Pegasus 40").
            out |= type_request.longest_hits(
                {p: self.product_types[p][key] for p in kept if p in self.product_types},
                [run for run in exclude if len(run) >= 2 or any(c.isdigit() for c in "".join(run))],
                accented,
            )
        out &= set(kept)
        dropped: list[str] = []
        if 0 < len(out) < len(kept):
            dropped = [p for p in kept if p in out]
            kept = [p for p in kept if p not in out]
            info["status"] = "filtered"
        if request or dropped:
            info["kept"] = len(kept)
            plan.type_requests.append(info)
        return kept, dropped

    def _category_intent(self, plan: Plan, query: str, spotting: Spotting) -> None:
        """Category/tier cue of a query that names no entity. It never lifts the OOS gate."""
        if plan.out_of_scope:
            # The gate found the query far from every entity card; a cue must not override it
            # ("lời bài hát Nối vòng tay lớn" contains the jewelry cue "vòng tay").
            return
        protected = [(m.start, m.end) for m in [*spotting.matches, *spotting.ambiguous]]
        parts = (
            connectors.segments(query, protected) if self.settings.segment_categories else [query]
        )
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
            # such as "huyết áp" is also in drug names, which the cards disagree with. When
            # the cards form no majority (< 0.5), a pure lexicon cue (share 1.0) is kept.
            top_vote = max(votes.values()) if votes else 0.0
            pure = {m["category"] for m in intent.matches if m.get("share", 0.0) >= 1.0}
            kept = [
                c
                for c in intent.categories
                if votes.get(c, 0.0) >= s.lexicon_min_share or (c in pure and top_vote < 0.5)
            ]
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
            "type_requests": plan.type_requests,
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
            all_hops = list(dict.fromkeys([*plan.hops, *plan.generic_hits]))
            boosts = EntityBoosts(
                seeds=self.review.indexed_ids(plan.seeds),
                hops=self.review.indexed_ids(all_hops),
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
                category_groups=plan.category_groups if s.group_prefetch else [],
            )
            request = build_request(
                vector.tolist(),
                lexical.query_vector(query),
                boosts,
                depth=s.depth,
                filter_brand=brand_id,
                blocked_assets=self.review.blocked_assets,
            )
            grouped = (
                plan.intents >= 2
                or bool(plan.hops)
                or bool(plan.generic_hits)
                or len(plan.category_groups) >= 2
            )
            use_fusion = (
                s.visual_fusion
                and self.fusion is not None
                and self.image_encoder is not None
                and not grouped
                and not plan.seeds
                and not all_hops
                and not plan.anchors
                and not plan.value_ids
                and not brand_id
                and not (plan.category and plan.category.tiers)
            )
            img_vector = None
            if use_fusion:
                tick = time.perf_counter()
                img_vector = self.image_encoder.encode([query])[0]
                timings["embed_image"] = _ms(tick)
            tick = time.perf_counter()
            # Fetch the independent diagnostics concurrently with the existing ranker.
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(branch_scores, self.client, self.collection, request)
                points = self._rank(plan, grouped, request, use_fusion=use_fusion)
                timings["qdrant"] = _ms(tick)
                try:
                    scores = pending.result()
                except ApiException:
                    scores = {}
                timings["qdrant_with_trace"] = _ms(tick)
            img_scores_by_asset: dict[str, float] = {}
            if use_fusion and img_vector is not None:
                points, img_scores_by_asset = self._fuse_visual(
                    query, vector, img_vector, points, boosts
                )
            for name in ("dense", "sparse", "graph_prefetch"):
                if name != "graph_prefetch" or (boosts.targets() and boosts.graph_prefetch):
                    channels[name] = "ready" if name in scores else "unavailable"
            channels["graph"] = "ready" if boosts.targets() or boosts.anchors else "inactive"
            category_on = bool(boosts.categories or boosts.tiers)
            channels["category"] = "ready" if category_on else "inactive"
            channels["img"] = "ready" if use_fusion else "inactive"
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
                point_id_str = str(point.id)
                parts = {
                    name: scores.get(name, {}).get(point_id_str)
                    for name in ("dense", "sparse", "graph_prefetch")
                }
                img_val = img_scores_by_asset.get(asset_id)
                parts.update(
                    graph=adjustment if channels["graph"] == "ready" else None,
                    category=category_adjustment if category_on else None,
                    img=round(float(img_val), 4) if img_val is not None else None,
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

    def _fuse_visual(
        self,
        query: str,
        text_vector: np.ndarray,
        img_vector: np.ndarray,
        points: list,
        boosts: EntityBoosts,
    ) -> tuple[list, dict[str, float]]:
        """Re-rank ungrouped content/category candidates with VisualFusion (bge-m3 + SigLIP2)."""
        from types import SimpleNamespace

        s = self.settings
        blocked = set(self.review.blocked_assets)
        by_asset = {
            p.payload["asset_id"]: p
            for p in points
            if p.payload and p.payload.get("asset_id") and p.payload["asset_id"] not in blocked
        }
        text_ids = list(by_asset)[: self.fusion.settings.text_candidates]
        cands = [a for a in self.fusion.candidates(text_ids, img_vector) if a not in blocked]
        if not cands:
            return points, {}
        text_scores, img_scores = self.fusion.scores(cands, text_vector, img_vector)
        fused = fuse(text_scores, img_scores, self.fusion.alpha(query))
        if boosts.categories or boosts.tiers:
            bonuses = np.array(
                [
                    boosts.category_score(
                        (
                            by_asset[a].payload
                            if a in by_asset
                            else self.payloads.get(a) or {}
                        ).get("categories")
                        or [],
                        (
                            by_asset[a].payload
                            if a in by_asset
                            else self.payloads.get(a) or {}
                        ).get("market_tiers")
                        or [],
                    )
                    for a in cands
                ],
                dtype=np.float32,
            )
            fused = fused + bonuses
        order = np.argsort(-fused, kind="stable")
        missing = [
            cands[i]
            for i in order[: s.top_k * 2]
            if cands[i] not in by_asset and cands[i] not in self.payloads
        ]
        if missing and self.client is not None:
            try:
                fetched = self.client.retrieve(
                    self.collection, [point_id(a) for a in missing], with_payload=True
                )
                for p in fetched:
                    if p.payload and p.payload.get("asset_id"):
                        by_asset[p.payload["asset_id"]] = p
            except Exception:
                pass
        out_points = []
        img_map: dict[str, float] = {}
        for idx in order:
            asset_id = cands[idx]
            score_val = float(fused[idx])
            img_map[asset_id] = float(img_scores[idx])
            if asset_id in by_asset:
                orig = by_asset[asset_id]
                out_points.append(
                    SimpleNamespace(id=orig.id, score=score_val, payload=orig.payload)
                )
            elif asset_id in self.payloads:
                out_points.append(
                    SimpleNamespace(
                        id=point_id(asset_id), score=score_val, payload=self.payloads[asset_id]
                    )
                )
            if len(out_points) >= s.top_k * 2:
                break
        return out_points, img_map

    def _rank(self, plan: Plan, grouped: bool, request: dict, use_fusion: bool = False) -> list:
        s = self.settings
        if not grouped:
            limit = (
                max(s.top_k, self.fusion.settings.text_candidates)
                if use_fusion and self.fusion is not None
                else s.top_k
            )
            result = self.client.query_points(
                self.collection, limit=limit, with_payload=True, **request
            )
            return self._related(plan, result.points)
        has_targets = bool(plan.hops or plan.seeds or plan.generic_hits)
        if not has_targets and len(plan.category_groups) >= 2:
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
        primary_targets = {self.review.canonical(e) for e in plan.seeds + plan.hops}
        secondary_targets = {
            self.review.canonical(e) for e in plan.generic_hits
        } - primary_targets
        if primary_targets and secondary_targets:
            # Mixed query ("Kindle Paperwhite và tai nghe", "nhẫn cưới Bvlgari và dây chuyền
            # vàng"): a fair share of top-k for the named/brand targets, the rest for generic hits.
            share = max(1, s.top_k // max(2, plan.intents))
            primary_quota = s.top_k - share
            size = max(
                s.min_group_size,
                min(primary_quota, math.ceil(primary_quota / len(primary_targets))),
            )
            result = self.client.query_points_groups(
                self.collection,
                group_by="entity_ids",
                limit=s.top_k * GROUP_OVERFETCH,
                group_size=size,
                with_payload=True,
                **request,
            )
            first = merge_groups(
                result.groups, self.review.canonical, size, primary_targets, only_targets=True
            )[:primary_quota]
            second_size = min(
                s.top_k - len(first),
                max(s.min_group_size, math.ceil((s.top_k - len(first)) / len(secondary_targets))),
            )
            second = merge_groups(
                result.groups,
                self.review.canonical,
                second_size,
                secondary_targets,
                only_targets=True,
            )
            used = {str(h.id) for h in first}
            combined = first + [h for h in second if str(h.id) not in used]
            return self._related(plan, combined)
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
        targets = {self.review.canonical(e) for e in plan.seeds + plan.hops + plan.generic_hits}
        return self._related(
            plan, merge_groups(result.groups, self.review.canonical, size, targets)
        )

    def _related(self, plan: Plan, points: list) -> list:
        """Images of the query's target entities and nothing unrelated.

        A brand or relation query (graph targets) shows only its targets: another brand's
        product of the same type is still wrong ("ổn áp Lioa" is not a Ruler). A query that
        names products may fill with non-excluded images of the products' categories, which
        can answer a generic part of it ("lốp Michelin Pro4 và săm xe đạp").
        """
        targets = {self.review.canonical(e) for e in plan.seeds + plan.hops + plan.generic_hits}
        if not self.settings.fill_related_only or not targets:
            return points
        anchors = {self.review.canonical(e) for e in plan.anchors}
        allowed = (
            set()
            if plan.hops
            else {(self.product_types.get(t) or {}).get("category") for t in targets} - {None}
        )
        kept = []
        for point in points:
            ids = {self.review.canonical(e) for e in point.payload.get("entity_ids") or []}
            if ids & targets or (
                not ids & anchors and allowed & set(point.payload.get("categories") or [])
            ):
                kept.append(point)
        return kept


GROUP_OVERFETCH = 2


def merge_groups(
    groups, canonical, size: int, targets: set[str] | None = None, only_targets: bool = False
) -> list:
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
        if only_targets:
            order = [k for k in order if k in targets]
        else:
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
