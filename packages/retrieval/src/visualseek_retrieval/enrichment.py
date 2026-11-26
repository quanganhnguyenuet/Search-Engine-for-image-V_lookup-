"""Multi-hop graph relations (TREATS, HAS_INGREDIENT, FITS, ORIGIN, IS_A) and entity attributes.

Loads explicit edges and attributes from ``graph.json`` and ``entity_categories.json``, and
falls back to deterministic extraction from the entity's name and ``product_type`` when the
enriched fields have not been written yet. Used at query time in S2 (no LLM at query time).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from visualseek_retrieval.text import normalize, surface, token_matches

REL_KIND = {
    "MADE_BY": "Brand",
    "TREATS": "Purpose",
    "HAS_INGREDIENT": "Ingredient",
    "FITS": "TargetModel",
    "ORIGIN": "Place",
    "IS_A": "Category",
}

PURPOSE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "dạ dày",
        ("dạ dày", "loét dạ dày", "đau dạ dày", "trào ngược dạ dày", "dư axit", "kháng axit"),
    ),
    ("đầy hơi chướng bụng", ("đầy hơi", "chướng bụng", "khó tiêu", "rối loạn tiêu hóa")),
    ("tiêu chảy", ("tiêu chảy", "đi ngoài", "bù nước và điện giải")),
    ("táo bón", ("táo bón", "nhuận tràng")),
    ("giảm đau hạ sốt", ("giảm đau", "hạ sốt", "đau đầu", "đau răng", "đau bụng kinh")),
    ("đau nửa đầu", ("đau nửa đầu", "migraine")),
    ("kháng sinh", ("kháng sinh", "nhiễm khuẩn", "nhiễm trùng")),
    ("kháng viêm", ("kháng viêm", "chống viêm", "viêm khớp", "thoái hóa khớp")),
    ("kháng virus", ("kháng virus", "viêm gan b", "viêm gan c", "herpes")),
    ("dị ứng", ("chống dị ứng", "kháng dị ứng", "viêm mũi dị ứng", "mề đay", "kháng histamin")),
    ("hạ huyết áp", ("hạ huyết áp", "tăng huyết áp", "cao huyết áp")),
    ("hạ mỡ máu", ("hạ mỡ máu", "mỡ máu", "cholesterol", "rối loạn lipid máu")),
    ("tiểu đường", ("tiểu đường", "đái tháo đường", "hạ đường huyết", "insulin")),
    ("hen suyễn", ("hen suyễn", "hen phế quản", "co thắt phế quản")),
    ("ho long đờm", ("trị ho", "giảm ho", "long đờm", "tiêu đờm", "viên ngậm ho", "siro ho")),
    ("an thần", ("an thần", "mất ngủ", "chống trầm cảm", "lo âu", "động kinh")),
    ("gút", ("trị gút", "bệnh gút", "gout", "hạ acid uric")),
    ("tẩy giun", ("tẩy giun", "giun sán", "ký sinh trùng")),
    ("ung thư", ("điều trị ung thư", "chống ung thư", "hóa trị")),
    ("bổ gan", ("bổ gan", "giải độc gan", "mát gan", "bảo vệ gan")),
    ("bổ não", ("bổ não", "tuần hoàn não", "tăng cường trí nhớ", "hoạt huyết")),
    ("xương khớp", ("xương khớp", "loãng xương", "giãn cơ")),
    ("nhỏ mắt", ("nhỏ mắt", "khô mắt", "mỏi mắt", "viêm kết mạc")),
    ("trị mụn", ("trị mụn", "ngừa mụn", "giảm mụn")),
    ("dưỡng ẩm", ("dưỡng ẩm", "cấp ẩm", "dưỡng da", "phục hồi da")),
    ("chống nắng", ("chống nắng", "kem chống nắng")),
    ("làm sạch tẩy rửa", ("tẩy rửa", "tẩy cặn", "tẩy rỉ sét", "lau bóng inox")),
    ("chống thấm", ("chống thấm", "sơn chống thấm", "keo chống thấm")),
    ("đo huyết áp", ("đo huyết áp", "máy đo huyết áp")),
)

VEHICLE_MODELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Honda Air Blade", ("air blade", "airblade")),
    ("Honda Vision", ("honda vision", "xe vision")),
    ("Honda Winner", ("honda winner", "winner x")),
    ("Honda Wave", ("honda wave", "wave alpha", "wave rsx")),
    ("Honda SH", ("honda sh", "sh 125", "sh 150", "sh 350", "sh mode")),
    ("Honda Lead", ("honda lead", "xe lead")),
    ("Honda Vario", ("honda vario", "vario 125", "vario 150", "vario 160")),
    ("Honda Dream", ("honda dream", "super dream")),
    ("Honda Future", ("honda future", "future 125")),
    ("Honda Civic", ("honda civic",)),
    ("Honda CR-V", ("honda cr-v", "honda crv")),
    ("Honda City", ("honda city",)),
    ("Honda BR-V", ("honda br-v", "honda brv")),
    ("Yamaha Exciter", ("exciter", "exciter 135", "exciter 150", "exciter 155")),
    ("Yamaha Sirius", ("yamaha sirius", "xe sirius")),
    ("Yamaha Grande", ("yamaha grande",)),
    ("Yamaha NVX", ("yamaha nvx", "nvx 155")),
    ("Suzuki Raider", ("suzuki raider", "raider 150")),
    ("Suzuki Satria", ("suzuki satria", "satria f150")),
    ("Ford EcoSport", ("ford ecosport", "ecosport")),
    ("Ford Ranger", ("ford ranger", "ranger wildtrak")),
    ("Ford Everest", ("ford everest",)),
    ("Ford Transit", ("ford transit",)),
    ("Toyota Vios", ("toyota vios", "xe vios")),
    ("Toyota Innova", ("toyota innova", "innova")),
    ("Toyota Fortuner", ("toyota fortuner", "fortuner")),
    ("Toyota Camry", ("toyota camry", "camry")),
    ("Toyota Corolla Altis", ("corolla altis", "toyota altis", "corolla cross")),
    ("Toyota Hilux", ("toyota hilux", "hilux")),
    ("Hyundai Stargazer", ("hyundai stargazer", "stargazer")),
    ("Hyundai Accent", ("hyundai accent",)),
    ("Hyundai SantaFe", ("hyundai santafe", "santa fe")),
    ("Hyundai Tucson", ("hyundai tucson",)),
    ("Hyundai Grand i10", ("grand i10", "hyundai i10")),
    ("Kia Morning", ("kia morning",)),
    ("Kia Seltos", ("kia seltos",)),
    ("Kia Cerato", ("kia cerato", "kia k3")),
    ("Kia Sorento", ("kia sorento",)),
    ("Mazda 3", ("mazda 3", "mazda3")),
    ("Mazda CX-5", ("mazda cx-5", "mazda cx5")),
    ("Mitsubishi Xpander", ("mitsubishi xpander", "xpander")),
    ("Mitsubishi Triton", ("mitsubishi triton",)),
    ("VinFast Fadil", ("vinfast fadil", "fadil")),
    ("VinFast VF8", ("vinfast vf8", "vf 8", "vf5", "vf9", "vfe34")),
)

KNOWN_INGREDIENTS: tuple[str, ...] = (
    "famotidin",
    "omeprazol",
    "pantoprazol",
    "rabeprazol",
    "esomeprazol",
    "lansoprazol",
    "domperidon",
    "simethicon",
    "paracetamol",
    "ibuprofen",
    "meloxicam",
    "celecoxib",
    "diclofenac",
    "aspirin",
    "morphin",
    "tramadol",
    "amoxicillin",
    "ampicillin",
    "cefuroxim",
    "cefixim",
    "ceftriaxon",
    "cephalexin",
    "azithromycin",
    "clarithromycin",
    "spiramycin",
    "metronidazol",
    "levofloxacin",
    "ciprofloxacin",
    "doxycyclin",
    "clindamycin",
    "gentamicin",
    "amlodipin",
    "losartan",
    "valsartan",
    "telmisartan",
    "perindopril",
    "enalapril",
    "captopril",
    "bisoprolol",
    "metoprolol",
    "atenolol",
    "indapamid",
    "furosemid",
    "spironolacton",
    "nifedipin",
    "atorvastatin",
    "rosuvastatin",
    "simvastatin",
    "fenofibrat",
    "metformin",
    "glimepirid",
    "gliclazid",
    "insulin",
    "sitagliptin",
    "cetirizin",
    "levocetirizin",
    "loratadin",
    "desloratadin",
    "fexofenadin",
    "chlorpheniramin",
    "montelukast",
    "salbutamol",
    "budesonid",
    "prednisolon",
    "prednison",
    "methylprednisolon",
    "dexamethason",
    "betamethason",
    "acyclovir",
    "entecavir",
    "tenofovir",
    "albendazol",
    "mebendazol",
    "colchicin",
    "allopurinol",
    "citalopram",
    "escitalopram",
    "sertralin",
    "fluoxetin",
    "clonazepam",
    "diazepam",
    "piracetam",
    "citicolin",
    "cinnarizin",
    "flunarizin",
    "betahistin",
    "ginkgo biloba",
    "silymarin",
    "lactulose",
    "loperamid",
    "acarbose",
    "clopidogrel",
    "warfarin",
    "rivaroxaban",
    "vitamin c",
    "vitamin d3",
    "vitamin e",
    "acid folic",
    "glucosamin",
    "collagen",
    "retinol",
    "niacinamide",
)

PLACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Bến Tre", ("bến tre",)),
    ("Phú Quốc", ("phú quốc",)),
    ("Bát Tràng", ("bát tràng",)),
    ("Thái Nguyên", ("thái nguyên",)),
    ("Bắc Giang", ("bắc giang",)),
    ("Tây Ninh", ("tây ninh",)),
    ("Đà Lạt", ("đà lạt",)),
    ("Cà Mau", ("cà mau",)),
    ("An Giang", ("an giang", "châu đốc")),
    ("Sóc Trăng", ("sóc trăng",)),
    ("Đồng Tháp", ("đồng tháp",)),
    ("Cần Thơ", ("cần thơ",)),
    ("Miền Tây", ("miền tây", "nam bộ")),
    ("Huế", ("cố đô huế", "đặc sản huế", "xứ huế")),
    ("Hội An", ("hội an",)),
    ("Quảng Nam", ("quảng nam", "mỹ sơn")),
    ("Đà Nẵng", ("đà nẵng",)),
    ("Nha Trang", ("nha trang", "khánh hòa")),
    ("Phan Thiết", ("phan thiết", "bình thuận")),
    ("Bình Định", ("bình định",)),
    ("Hà Giang", ("hà giang",)),
    ("Sapa", ("sapa", "sa pa", "lào cai")),
    ("Đông Hồ", ("đông hồ",)),
    ("Hàng Trống", ("hàng trống",)),
    ("Chu Đậu", ("chu đậu",)),
    ("Đông Sơn", ("đông sơn",)),
    ("Phù Lãng", ("phù lãng",)),
    ("Sơn Đồng", ("sơn đồng",)),
)

MATERIAL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "đồng",
        (
            "bằng đồng",
            "đúc đồng",
            "đỉnh đồng",
            "trống đồng",
            "tượng đồng",
            "hạc đồng",
            "đồ đồng",
            "đồng đỏ",
            "đồng thau",
            "câu đối đồng",
        ),
    ),
    (
        "đá",
        (
            "bằng đá",
            "đá tự nhiên",
            "đá nguyên khối",
            "hương án đá",
            "bàn thờ đá",
            "bia đá",
            "tượng đá",
        ),
    ),
    (
        "gỗ",
        (
            "bằng gỗ",
            "gỗ mít",
            "gỗ hương",
            "gỗ gụ",
            "gỗ sồi",
            "gỗ trắc",
            "sập gỗ",
            "tượng gỗ",
            "ghế gỗ",
            "bàn gỗ",
            "tủ gỗ",
        ),
    ),
    (
        "gốm sứ",
        (
            "gốm sứ",
            "bằng sứ",
            "bằng gốm",
            "men lam",
            "men rạn",
            "men da lươn",
            "đất nung",
            "chum sành",
            "niêu đất",
            "đồ gốm",
            "đồ sứ",
        ),
    ),
    ("inox", ("bằng inox", "inox 304", "inox 316", "thép không gỉ")),
    ("thép", ("bằng thép", "hợp kim thép", "thép hợp kim")),
    ("nhôm", ("bằng nhôm", "hợp kim nhôm")),
    ("vàng", ("vàng 18k", "vàng 24k", "vàng 14k", "vàng 10k", "vàng 9999", "bằng vàng", "mạ vàng")),
    ("bạc", ("bạc 925", "bạc ta", "bằng bạc")),
    ("thủy tinh", ("bằng thủy tinh", "bằng thuỷ tinh", "pha lê")),
    ("da", ("bằng da", "dây da", "da bò", "da thật", "da cá sấu")),
    ("nhựa", ("bằng nhựa", "nhựa abs", "nhựa pp", "nhựa hdpe", "nhựa pvc")),
    ("mây tre", ("mây tre", "bằng tre", "bằng mây", "bằng cói")),
)

# Explicit query-time material phrase triggers ("bằng đồng", "đồ đồng", "bằng gỗ", "bằng đá"...).
QUERY_MATERIAL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("đồng", ("bằng đồng", "đúc đồng", "đồ đồng", "đồng đỏ", "đồng thau")),
    ("đá", ("bằng đá", "đá tự nhiên")),
    ("gỗ", ("bằng gỗ", "gỗ mít", "gỗ hương", "gỗ sồi", "gỗ gụ")),
    ("gốm sứ", ("bằng sứ", "bằng gốm", "gốm sứ", "men lam", "đất nung")),
    ("inox", ("bằng inox", "inox")),
    ("nhựa", ("bằng nhựa",)),
    ("da", ("bằng da", "dây da", "da thật", "da bò")),
)


def slug_id(prefix: str, label: str) -> str:
    slug = "-".join(normalize(label).split())
    return f"{prefix}:{slug}" if slug else ""


def _match_rules(
    text: str, rules: tuple[tuple[str, tuple[str, ...]], ...], limit: int = 4
) -> list[str]:
    out = []
    for canonical, triggers in rules:
        if any(re.search(r"\b" + re.escape(t) + r"\b", text) for t in triggers):
            out.append(canonical)
            if len(out) >= limit:
                break
    return out


def query_material(text: str) -> str | None:
    """Return the explicit material requested in a query clause, if any."""
    found = _match_rules(surface(text), QUERY_MATERIAL_RULES, limit=1)
    return found[0] if found else None


# A product's origin is only a "region" (a craft village, a province) where the place defines
# the product. Countries are a different scope: "cùng vùng" never means "cùng nước".
COUNTRIES = frozenset(
    normalize(name)
    for name in (
        "Việt Nam", "Nhật Bản", "Hàn Quốc", "Korea", "Trung Quốc", "Đài Loan", "Hồng Kông",
        "Thái Lan", "Malaysia", "Indonesia", "Singapore", "Philippines", "Ấn Độ", "Pakistan",
        "Sri Lanka", "Israel", "Mỹ", "Hoa Kỳ", "Canada", "Mexico", "Brazil", "Úc", "Australia",
        "New Zealand", "Nam Phi", "Nigeria", "Anh", "Scotland", "Pháp", "Đức", "Ý", "Italia",
        "Tây Ban Nha", "Bồ Đào Nha", "Thụy Sĩ", "Thụy Điển", "Đan Mạch", "Na Uy", "Phần Lan",
        "Hà Lan", "Bỉ", "Áo", "Ba Lan", "Séc", "Cộng hòa Séc", "Hungary", "Slovenia", "Romania",
        "Rumani", "Nga", "Ukraine", "Thổ Nhĩ Kỳ", "Hy Lạp", "Síp", "Ireland", "Thái",
    )
)  # fmt: skip
# Categories whose products are named after a place of Vietnam (specialties, craft villages).
LOCAL_ORIGIN_L1 = frozenset({"food", "culture", "agri", "other", "animal"})


def place_scope(label: str) -> str:
    return "country" if normalize(label) in COUNTRIES else "region"


# Places outside Vietnam that are not countries, so COUNTRIES misses them (a Cuban cigar, a
# Milan brand). The place named in a query must be one of Vietnam: this corpus is a Vietnamese
# one, and "đặc sản" or "đồ thủ công" of a foreign place is not a question it can answer.
FOREIGN_PLACES = frozenset(
    normalize(name)
    for name in ("Ai Cập", "Bermuda", "Cuba", "Liên Xô", "Milan", "Quảng Đông", "Sudan",
                 "Triều Tiên", "USA")
)  # fmt: skip


def is_vietnamese_place(label: str) -> bool:
    folded = normalize(label)
    return folded not in COUNTRIES and folded not in FOREIGN_PLACES


def origin_allowed(origin: str, l1: str) -> bool:
    """A province or village is kept for specialties only: for a drug or a machine it is the
    manufacturer's address ("Công ty Dược phẩm Bến Tre"), not where the product comes from."""
    return bool(origin) and (place_scope(origin) == "country" or l1 in LOCAL_ORIGIN_L1)


# Free-form LLM materials folded onto the canonical labels the query-time material filter
# compares against ("bằng gốm" asks for "gốm sứ"; an entity labelled "gốm" must not conflict).
MATERIAL_ALIASES: dict[str, str] = {
    "gốm": "gốm sứ",
    "sứ": "gốm sứ",
    "đất nung": "gốm sứ",
    "sành": "gốm sứ",
    "thép không gỉ": "inox",
    "pha lê": "thủy tinh",
    "thuỷ tinh": "thủy tinh",
    "mây": "mây tre",
    "tre": "mây tre",
    "cói": "mây tre",
    "đồng thau": "đồng",
    "đồng đỏ": "đồng",
    "da thật": "da",
    "da bò": "da",
}


def canonical_material(raw: str) -> str:
    key = surface(raw or "")
    return MATERIAL_ALIASES.get(key, key)


def purpose_buckets(labels: list[str]) -> list[str]:
    """Canonical PURPOSE_RULES labels that free-form purposes fall under ('viêm loét dạ dày'
    -> 'dạ dày'), so products labelled with different wordings share a TREATS node."""
    out: list[str] = []
    for label in labels:
        folded = surface(label)
        for canonical in _match_rules(folded, PURPOSE_RULES, limit=4):
            # "ung thư dạ dày" is a cancer, not a stomach complaint: it keeps its own node.
            if "ung thư" in folded and canonical != "ung thư":
                continue
            if canonical not in out:
                out.append(canonical)
    return out


def normalize_ingredient(raw: str) -> str:
    return _normalize_ingredient(raw)


def _normalize_ingredient(raw: str) -> str:
    folded = normalize(raw).strip()
    # Strip pharmaceutical salt suffixes so "Metformin Hydrochloride" and "Metformin" share one node.
    for salt in (
        " hydrochloride",
        " hcl",
        " potassium",
        " sodium",
        " maleate",
        " maleat",
        " tartrate",
        " tartrat",
        " sulfate",
        " sulfat",
        " besylate",
        " besylat",
        " mesylate",
        " mesylat",
        " citrate",
        " citrat",
        " acetate",
        " acetat",
        " fumarate",
        " fumarat",
        " succinate",
        " succinat",
        " phosphate",
        " phosphat",
        " calcium",
        " magnesium",
        " dihydrate",
        " monohydrate",
        " trihydrate",
    ):
        if folded.endswith(salt) and len(folded) > len(salt) + 3:
            folded = folded[: -len(salt)].strip()
            break
    for suffix in ("ine", "ole", "one", "ate"):
        if folded.endswith(suffix) and len(folded) > 6 and not folded.endswith("clone"):
            folded = folded[:-1]
            break
    return folded.capitalize()


GENERIC_FITS_STOP = {
    "o to",
    "xe o to",
    "xe hoi",
    "xe con",
    "xe tai",
    "xe khach",
    "xe buyt",
    "xe may",
    "mo to",
    "xe mo to",
    "xe tay ga",
    "xe so",
    "xe dap",
    "xe dap dien",
    "xe may dien",
    "xe dien",
    "dien thoai",
    "smartphone",
    "may tinh",
    "laptop",
    "pc",
    "may anh",
    "may in",
}


def canonical_purpose(raw: str) -> str:
    """Map synonymous medical indications or functional purposes to one canonical label."""
    surf = surface(raw).strip()
    if not surf:
        return ""
    matched = _match_rules(surf, PURPOSE_RULES, limit=1)
    return matched[0] if matched else surf


def canonical_fits_model(raw: str) -> str:
    """Keep specific vehicle/device models; filter out generic vehicle nouns ('ô tô', 'xe đạp')."""
    surf = surface(raw).strip()
    folded = normalize(surf)
    if not folded or folded in GENERIC_FITS_STOP or len(folded) < 2:
        return ""
    matched = _match_rules(f"{surf} ; {folded}", VEHICLE_MODELS, limit=1)
    return matched[0] if matched else raw.strip()


@dataclass
class RelationIndex:
    """In-memory multi-hop graph index for TREATS, HAS_INGREDIENT, FITS, ORIGIN, IS_A."""

    by_entity: dict[tuple[str, str], list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    by_node: dict[tuple[str, str], list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], dict] = field(default_factory=dict)
    materials: dict[str, str] = field(default_factory=dict)
    form_factors: dict[str, str] = field(default_factory=dict)
    audiences: dict[str, str] = field(default_factory=dict)
    ingredient_lookup: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    model_lookup: dict[str, str] = field(default_factory=dict)
    # Folded words of a Vietnamese place -> (node id, its accented words), for place queries.
    place_forms: dict[tuple[str, ...], tuple[str, tuple[str, ...]]] = field(default_factory=dict)

    def add_edge(
        self,
        rel: str,
        entity_id: str,
        node_id: str,
        label: str,
        source: str = "graph",
        confidence: float = 0.9,
        reviewed: bool = True,
    ) -> None:
        if not entity_id or not node_id:
            return
        node = {"id": node_id, "label": label, "kind": REL_KIND.get(rel, rel)}
        if rel == "ORIGIN":
            node["scope"] = place_scope(label)
        self.nodes.setdefault(node_id, node)
        if node_id not in self.by_entity[rel, entity_id]:
            self.by_entity[rel, entity_id].append(node_id)
        if entity_id not in self.by_node[rel, node_id]:
            self.by_node[rel, node_id].append(entity_id)
        self.edges.setdefault(
            (rel, entity_id, node_id),
            {
                "src": entity_id,
                "rel": rel,
                "dst": node_id,
                "source": source,
                "confidence": confidence,
                "reviewed": reviewed,
            },
        )
        if rel == "HAS_INGREDIENT":
            folded = normalize(label)
            if entity_id not in self.ingredient_lookup[folded]:
                self.ingredient_lookup[folded].append(entity_id)
        elif rel == "FITS":
            self.model_lookup.setdefault(normalize(label), node_id)

    @classmethod
    def build(cls, graph: dict, categories: dict | None, review) -> RelationIndex:
        idx = cls()
        canonical = review.canonical if review else (lambda e: e)
        cat_entities = (categories or {}).get("entities") or {}
        node_tables = {
            "TREATS": (categories or {}).get("purposes") or {},
            "HAS_INGREDIENT": (categories or {}).get("ingredients") or {},
            "FITS": (categories or {}).get("models") or {},
            "ORIGIN": (categories or {}).get("places") or {},
            "IS_A": (categories or {}).get("category_names") or {},
        }

        # 1. Explicit edges in graph["edges"] and categories["edges"]
        for edge in [*(graph.get("edges") or ()), *((categories or {}).get("edges") or ())]:
            rel = edge.get("rel")
            if rel not in ("TREATS", "HAS_INGREDIENT", "FITS", "ORIGIN", "IS_A"):
                continue
            eid = canonical(edge["src"])
            dst = edge["dst"]
            table = node_tables.get(rel) or {}
            raw_id = dst.removeprefix("category:") if rel == "IS_A" else dst
            label = edge.get("label") or table.get(raw_id) or table.get(dst) or raw_id
            idx.add_edge(
                rel,
                eid,
                dst,
                label,
                source=edge.get("source") or "graph",
                confidence=edge.get("confidence", 0.9),
                reviewed=edge.get("reviewed", True),
            )

        if not cat_entities:
            idx.index_places()
            return idx

        # 2. Entity-level enriched fields + fallback inference from name & product_type
        for entity in graph.get("entities") or ():
            eid = canonical(entity["id"])
            info = cat_entities.get(eid) or {}
            if not info:
                continue
            l1 = info.get("category_l1") or ""
            l2 = info.get("category") or ""
            name_surf = surface(entity.get("name") or "")
            ptype_surf = surface(info.get("product_type") or "")
            combined_surf = f"{name_surf} ; {ptype_surf}"
            combined_folded = normalize(combined_surf)

            # TREATS
            treats = list(info.get("treats") or ())
            if not treats and l1 in ("drug", "medical", "beauty", "household", "home", "agri"):
                treats = _match_rules(combined_surf, PURPOSE_RULES, limit=3)
            for item in treats:
                canon_p = canonical_purpose(item)
                if canon_p and (nid := slug_id("purpose", canon_p)):
                    idx.add_edge(
                        "TREATS", eid, nid, canon_p, source=info.get("source", "inferred")
                    )

            # HAS_INGREDIENT
            ingredients = list(info.get("ingredients") or ())
            if not ingredients and l1 in ("drug", "beauty", "agri"):
                for ing in KNOWN_INGREDIENTS:
                    ing_f = normalize(ing)
                    if re.search(r"\b" + re.escape(ing_f) + r"e?\b", combined_folded):
                        canon = _normalize_ingredient(ing)
                        if canon not in ingredients:
                            ingredients.append(canon)
            for item in ingredients:
                canon = _normalize_ingredient(item)
                if canon and (nid := slug_id("ingredient", canon)):
                    idx.add_edge(
                        "HAS_INGREDIENT", eid, nid, canon, source=info.get("source", "inferred")
                    )

            # FITS
            fits = list(info.get("fits") or ())
            is_part = (
                l1 in ("car", "moto", "bike")
                and not l2.endswith(".vehicle")
                or l2 in ("phone.accessory", "tech.printer", "tech.peripheral", "tech.camera")
            )
            if not fits and is_part:
                fits = _match_rules(
                    f"{combined_surf} ; {combined_folded}", VEHICLE_MODELS, limit=3
                )
            for item in fits:
                canon_m = canonical_fits_model(item)
                if canon_m and (nid := slug_id("model", canon_m)):
                    idx.add_edge("FITS", eid, nid, canon_m, source=info.get("source", "inferred"))

            # ORIGIN
            origin = info.get("origin") or ""
            if not origin and l1 in ("food", "culture", "agri", "fashion"):
                places = _match_rules(combined_surf, PLACES, limit=1)
                origin = places[0] if places else ""
            if origin and (nid := slug_id("place", origin)):
                idx.add_edge("ORIGIN", eid, nid, origin, source=info.get("source", "inferred"))

            # Attributes: material, form_factor, target_audience
            material = info.get("material") or ""
            if not material:
                mats = _match_rules(combined_surf, MATERIAL_RULES, limit=1)
                material = mats[0] if mats else ""
            if material:
                idx.materials.setdefault(eid, material)

            if info.get("form_factor"):
                idx.form_factors.setdefault(eid, info["form_factor"])
            if info.get("target_audience"):
                idx.audiences.setdefault(eid, info["target_audience"])

        idx.index_places()
        return idx

    def index_places(self) -> None:
        """The Vietnamese places that have at least one entity, keyed by their folded words. A
        folded form shared by two places is dropped: it cannot say which one is meant."""
        forms: dict[tuple[str, ...], dict[str, tuple[str, ...]]] = defaultdict(dict)
        for node_id, node in self.nodes.items():
            label = node["label"]
            if (
                node["kind"] != REL_KIND["ORIGIN"]
                or not is_vietnamese_place(label)
                or not self.by_node.get(("ORIGIN", node_id))
            ):
                continue
            folded = tuple(normalize(label).split())
            if folded:
                forms[folded][node_id] = tuple(surface(label).split())
        self.place_forms = {
            folded: next(iter(ids.items())) for folded, ids in forms.items() if len(ids) == 1
        }

    def resolve_places_from_query(self, query: str) -> tuple[list[str], str]:
        """Place nodes named in a query (longest name first, an unaccented spelling accepted)
        and the words left over, which say what is asked of them ("đặc sản", "đồ gốm")."""
        words = surface(query).split()
        longest = max((len(folded) for folded in self.place_forms), default=0)
        covered = [False] * len(words)
        found: list[tuple[int, str]] = []
        for size in range(min(longest, len(words)), 0, -1):
            for start in range(len(words) - size + 1):
                if any(covered[start : start + size]):
                    continue
                window = words[start : start + size]
                entry = self.place_forms.get(tuple(normalize(word) for word in window))
                if entry and all(
                    token_matches(w, t) for w, t in zip(window, entry[1], strict=True)
                ):
                    covered[start : start + size] = [True] * size
                    found.append((start, entry[0]))
        node_ids = list(dict.fromkeys(node for _, node in sorted(found)))
        rest = " ".join(word for i, word in enumerate(words) if not covered[i])
        return node_ids, rest

    def values_of(self, rel: str, entity_ids: list[str]) -> list[str]:
        out: list[str] = []
        for eid in entity_ids:
            for nid in self.by_entity.get((rel, eid), ()):
                if nid not in out:
                    out.append(nid)
        return out

    def entities_of(self, rel: str, value_ids: list[str]) -> list[str]:
        out: list[str] = []
        for nid in value_ids:
            for eid in self.by_node.get((rel, nid), ()):
                if eid not in out:
                    out.append(eid)
        return out

    def resolve_origins_from_query(self, query: str) -> list[str]:
        """Resolve origin entities when S1 missed an ingredient-named drug ('... với Famotidin')."""
        folded = normalize(query)
        out: list[str] = []
        for ing_folded, eids in self.ingredient_lookup.items():
            if re.search(r"\b" + re.escape(ing_folded) + r"e?\b", folded):
                for eid in eids:
                    if eid not in out:
                        out.append(eid)
        return out

    def resolve_fits_from_query(self, query: str) -> tuple[list[str], list[str]]:
        """Return (model_node_ids, fitting_entity_ids) when a query asks for parts fitting a model."""
        surf = surface(query)
        folded = normalize(query)
        models = _match_rules(f"{surf} ; {folded}", VEHICLE_MODELS, limit=2)
        node_ids = [
            nid
            for m in models
            if (nid := slug_id("model", m)) and self.by_node.get(("FITS", nid))
        ]
        if not node_ids:
            for model_folded in sorted(self.model_lookup, key=len, reverse=True):
                if len(model_folded) >= 3 and re.search(
                    r"\b" + re.escape(model_folded) + r"\b", folded
                ):
                    nid = self.model_lookup[model_folded]
                    if self.by_node.get(("FITS", nid)) and nid not in node_ids:
                        node_ids.append(nid)
                        break
        return node_ids, self.entities_of("FITS", node_ids)
