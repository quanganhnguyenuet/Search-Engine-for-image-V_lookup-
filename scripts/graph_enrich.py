"""B4 / P5: enrich canonical entities with multi-hop relations and product attributes.

free    deterministic zero-quota extraction from evidence.jsonl + entity_categories.json
        (active ingredients in parens/snippets, therapeutic purposes from product_type &
        descriptions, compatible vehicle/device models, regional origins, material, form
        factor, target audience).
run     Gemini 3.8 Flash structured output per entity (via agy or Gemini API):
        brand, treats, ingredients, fits, origin, form_factor, target_audience, material,
        evidence snippet ids, basis, confidence. Cached per entity in graph/enrich_cache/.

  python scripts/graph_enrich.py free
  python scripts/graph_enrich.py run --ids pilot      # or: --ids all / --ids remaining
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "retrieval" / "src"))

from visualseek_retrieval.text import normalize, surface  # noqa: E402

CORPUS = ROOT / "data/processed/products-v1"
ARTIFACTS = CORPUS / "search"
OUT = CORPUS / "graph"
TRACKED_GRAPH = ROOT / "graph"
CATEGORIES = TRACKED_GRAPH / "entity_categories.json"
MODEL = "gemini-3.8-flash"
PROMPT_VERSION = "b4-enrich-v1"

ENRICH_FIELDS = (
    "brand",
    "treats",
    "ingredients",
    "fits",
    "origin",
    "form_factor",
    "target_audience",
    "material",
    "evidence",
    "basis",
    "confidence",
)

SYSTEM = """You extract structured product graph relations and attributes for a Vietnamese product image search engine.
Each entity has numbered evidence snippets: n = entity names, b = JSON-LD brand candidates, t = page titles, d = page descriptions, o = OCR text from images, plus its known category and product_type.

Return JSON with one object per entity:
- brand: manufacturer / brand name (e.g. "Bosch", "Philips", "Sanofi", "Naturehike"). Leave "" if generic, unknown, or if the only name is a retailer/website (Shopee, Lazada, Bách hóa XANH, Weee, Fado, Websosanh, Shop2banh...).
- treats: 0-4 short Vietnamese phrases (1-4 words each) naming the medical condition, symptom, or functional purpose/use this product treats or serves (e.g. ["dạ dày", "trào ngược dạ dày"], ["giảm đau", "hạ sốt"], ["đầy hơi", "chướng bụng"], ["trị mụn", "dưỡng ẩm"], ["tẩy rỉ sét", "làm sạch inox"]). Empty list [] when not applicable.
- ingredients: 0-4 active pharmaceutical ingredients or main raw components (e.g. ["Famotidin"], ["Paracetamol"], ["Simethicone"], ["Omeprazole", "Domperidone"]). Empty list [] when not applicable.
- fits: 0-4 specific vehicle models, bike groupsets, or device models that this part/accessory fits or is compatible with (e.g. ["Honda Air Blade"], ["Ford EcoSport"], ["Toyota Vios"], ["iPhone 14"]). Empty list [] for standalone products that are not parts/accessories.
- origin: regional specialty place, craft village, or country of origin when explicitly stated or defining the product (e.g. "Bến Tre", "Phú Quốc", "Bát Tràng", "Bắc Giang", "Nhật Bản"). Leave "" otherwise.
- form_factor: dosage form or physical form factor in Vietnamese (e.g. "viên nén", "viên nang", "siro", "hỗn dịch uống", "kem bôi", "thuốc tiêm", "dạng xịt", "bột", "không dây"). Leave "" if not applicable.
- target_audience: target user or target vehicle/animal group if specific (e.g. "trẻ em", "người lớn", "nam", "nữ", "chó", "mèo", "ô tô", "xe máy", "xe đạp"). Leave "" if general.
- material: primary physical material if known (e.g. "đồng", "đá", "gỗ", "inox", "thép", "nhôm", "nhựa", "da", "vàng", "bạc", "gốm sứ", "thủy tinh", "vải", "cao su"). Leave "" if not applicable.
- evidence: list of snippet ids (e.g. ["n1", "t1", "d2"]) that support the extracted fields.
- basis: "evidence" if supported by the snippets; "knowledge" if inferred from general product knowledge.
- confidence: "high", "medium", or "low".

Several entities come in one message under headers like "### e1". Return {"labels": [...]} with matching key.
"""


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def render_pack(p: dict, cat_info: dict | None = None) -> str:
    header = []
    if cat_info:
        if cat_info.get("category"):
            header.append(f"[cat] {cat_info['category']} ({cat_info.get('category_name', '')})")
        if cat_info.get("product_type"):
            header.append(f"[type] {cat_info['product_type']}")
    lines = [f"[{s['id']}] ({s['kind']}) {s['text']}" for s in p["snippets"]]
    return "\n".join(header + lines)


# ---- deterministic free extraction (PLAN_PRODUCTS_V1.md section 7.1) ---------------------

# Canonical therapeutic / functional purpose rules matched on accented text (product_type, name,
# title, desc). Maps canonical purpose label -> tuple of accented regex triggers.
PURPOSE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dạ dày", ("dạ dày", "loét dạ dày", "đau dạ dày", "trào ngược dạ dày", "dư axit", "kháng axit")),
    ("đầy hơi chướng bụng", ("đầy hơi", "chướng bụng", "khó tiêu", "rối loạn tiêu hóa")),
    ("tiêu chảy", ("tiêu chảy", "đi ngoài", "bù nước và điện giải")),
    ("táo bón", ("táo bón", "nhuận tràng")),
    ("giảm đau hạ sốt", ("giảm đau", "hạ sốt", "đau đầu", "đau răng", "đau bụng kinh")),
    ("đau nửa đầu", ("đau nửa đầu", "migraine")),
    ("kháng sinh", ("kháng sinh", "nhiễm khuẩn", "nhiễm trùng")),
    ("kháng viêm", ("kháng viêm", "chống viêm", "viêm khớp", "thoái hóa khớp")),
    ("kháng virus", ("kháng virus", "viêm gan b", "viêm gan c", "herpes")),
    ("dị ứng", ("chống dị ứng", "kháng dị ứng", "viêm mũi dị ứng", "mề đay", "kháng histamin")),
    ("hạ huyết áp", ("hạ huyết áp", "tăng huyết áp", "cao huyết áp", "tim mạch")),
    ("hạ mỡ máu", ("hạ mỡ máu", "mỡ máu", "cholesterol", "rối loạn lipid máu")),
    ("tiểu đường", ("tiểu đường", "đái tháo đường", "hạ đường huyết", "insulin")),
    ("hen suyễn", ("hen suyễn", "hen phế quản", "co thắt phế quản", "khó thở")),
    ("ho long đờm", ("trị ho", "giảm ho", "long đờm", "tiêu đờm", "viên ngậm ho", "siro ho")),
    ("an thần", ("an thần", "mất ngủ", "chống trầm cảm", "lo âu", "động kinh")),
    ("gút", ("trị gút", "bệnh gút", "gout", "hạ acid uric")),
    ("tẩy giun", ("tẩy giun", "giun sán", "ký sinh trùng")),
    ("ung thư", ("điều trị ung thư", "chống ung thư", "hóa trị")),
    ("bổ gan", ("bổ gan", "giải độc gan", "mát gan", "bảo vệ gan")),
    ("bổ não", ("bổ não", "tuần hoàn não", "tăng cường trí nhớ", "hoạt huyết")),
    ("xương khớp", ("xương khớp", "loãng xương", "bổ sung canxi", "giãn cơ")),
    ("nhỏ mắt", ("nhỏ mắt", "khô mắt", "mỏi mắt", "viêm kết mạc")),
    ("trị mụn", ("trị mụn", "ngừa mụn", "giảm mụn")),
    ("dưỡng ẩm", ("dưỡng ẩm", "cấp ẩm", "dưỡng da", "phục hồi da")),
    ("chống nắng", ("chống nắng", "kem chống nắng", "tia uv")),
    ("làm sạch tẩy rửa", ("tẩy rửa", "tẩy cặn", "tẩy rỉ sét", "lau bóng inox", "làm sạch bề mặt")),
    ("chống thấm", ("chống thấm", "sơn chống thấm", "keo chống thấm")),
    ("đo huyết áp", ("đo huyết áp", "máy đo huyết áp")),
)

# Vehicle and device models recognized in parts/accessories names and titles.
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
    ("Honda CR-V", ("honda cr-v", "honda crv", "cr-v")),
    ("Honda City", ("honda city",)),
    ("Yamaha Exciter", ("exciter", "exciter 135", "exciter 150", "exciter 155")),
    ("Yamaha Sirius", ("yamaha sirius", "xe sirius")),
    ("Yamaha Grande", ("yamaha grande",)),
    ("Yamaha NVX", ("yamaha nvx", "nvx 155")),
    ("Yamaha Nouvo", ("yamaha nouvo",)),
    ("Suzuki Raider", ("suzuki raider", "raider 150")),
    ("Suzuki Satria", ("suzuki satria", "satria f150")),
    ("Piaggio Vespa", ("vespa", "piaggio liberty", "piaggio medley")),
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
    ("Toyota Yaris", ("toyota yaris",)),
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
    ("Mazda CX-5", ("mazda cx-5", "mazda cx5", "cx-5")),
    ("Mitsubishi Xpander", ("mitsubishi xpander", "xpander")),
    ("Mitsubishi Triton", ("mitsubishi triton",)),
    ("Mitsubishi Pajero", ("mitsubishi pajero", "pajero sport")),
    ("VinFast Fadil", ("vinfast fadil", "fadil")),
    ("VinFast Lux A2.0", ("vinfast lux", "lux a2.0", "lux sa2.0")),
    ("VinFast VF8", ("vinfast vf8", "vf 8", "vf5", "vf9", "vfe34")),
    ("Mercedes-Benz", ("mercedes", "merc c200", "merc c300", "merc e200", "glc 300")),
    ("BMW", ("bmw 320i", "bmw 520i", "bmw x5", "bmw x3")),
)

# Common active ingredients recognized in drug names, parentheticals, titles, descriptions, OCR.
KNOWN_INGREDIENTS: tuple[str, ...] = (
    "famotidin",
    "omeprazol",
    "pantoprazol",
    "rabeprazol",
    "esomeprazol",
    "lansoprazol",
    "domperidon",
    "simethicon",
    "alverin",
    "drotaverin",
    "trimebutin",
    "paracetamol",
    "acetaminophen",
    "ibuprofen",
    "meloxicam",
    "celecoxib",
    "diclofenac",
    "piroxicam",
    "etoricoxib",
    "ketoprofen",
    "aspirin",
    "morphin",
    "tramadol",
    "codein",
    "amoxicillin",
    "clavulanic",
    "ampicillin",
    "cefuroxim",
    "cefixim",
    "cefpodoxim",
    "ceftriaxon",
    "ceftazidim",
    "cefotaxim",
    "cephalexin",
    "azithromycin",
    "clarithromycin",
    "erythromycin",
    "spiramycin",
    "metronidazol",
    "levofloxacin",
    "ciprofloxacin",
    "moxifloxacin",
    "ofloxacin",
    "doxycyclin",
    "tetracyclin",
    "clindamycin",
    "gentamicin",
    "tobramycin",
    "neomycin",
    "vancomycin",
    "meropenem",
    "imipenem",
    "amlodipin",
    "losartan",
    "valsartan",
    "telmisartan",
    "irbesartan",
    "candesartan",
    "perindopril",
    "enalapril",
    "lisinopril",
    "captopril",
    "ramipril",
    "bisoprolol",
    "metoprolol",
    "atenolol",
    "nebivolol",
    "propranolol",
    "indapamid",
    "furosemid",
    "spironolacton",
    "hydrochlorothiazid",
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
    "vildagliptin",
    "dapagliflozin",
    "empagliflozin",
    "cetirizin",
    "levocetirizin",
    "loratadin",
    "desloratadin",
    "fexofenadin",
    "chlorpheniramin",
    "promethazin",
    "montelukast",
    "salbutamol",
    "budesonid",
    "fluticason",
    "prednisolon",
    "prednison",
    "methylprednisolon",
    "dexamethason",
    "betamethason",
    "hydrocortison",
    "acyclovir",
    "entecavir",
    "tenofovir",
    "oseltamivir",
    "albendazol",
    "mebendazol",
    "ivermectin",
    "colchicin",
    "allopurinol",
    "febuxostat",
    "citalopram",
    "escitalopram",
    "sertralin",
    "fluoxetin",
    "amitriptylin",
    "clonazepam",
    "diazepam",
    "piracetam",
    "citicolin",
    "cinnarizin",
    "flunarizin",
    "betahistin",
    "ginkgo biloba",
    "silymarin",
    "ursodeoxycholic",
    "lactulose",
    "loperamid",
    "diosmectit",
    "saccharomyces boulardii",
    "acarbose",
    "clopidogrel",
    "ticagrelor",
    "warfarin",
    "rivaroxaban",
    "tranexamic",
    "vitamin c",
    "vitamin d3",
    "vitamin e",
    "vitamin b1",
    "vitamin b6",
    "vitamin b12",
    "acid folic",
    "sắt",
    "kẽm",
    "calci",
    "magnesi",
    "glucosamin",
    "chondroitin",
    "collagen",
    "retinol",
    "niacinamide",
    "hyaluronic acid",
    "salicylic acid",
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
    ("Thanh Hóa", ("thanh hóa",)),
    ("Nghệ An", ("nghệ an",)),
    ("Hà Giang", ("hà giang",)),
    ("Sapa", ("sapa", "sa pa", "lào cai")),
    ("Sơn La", ("sơn la", "mộc châu")),
    ("Hải Dương", ("hải dương",)),
    ("Đông Hồ", ("đông hồ",)),
    ("Hàng Trống", ("hàng trống",)),
    ("Chu Đậu", ("chu đậu",)),
    ("Đông Sơn", ("đông sơn",)),
    ("Nhật Bản", ("nhật bản", "(nhật)", "nội địa nhật", "made in japan")),
    ("Hàn Quốc", ("hàn quốc", "(hàn)", "made in korea")),
    ("Đức", ("của đức", "(đức)", "made in germany")),
    ("Mỹ", ("của mỹ", "(mỹ)", "made in usa")),
    ("Pháp", ("của pháp", "(pháp)", "made in france")),
)

MATERIAL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("đồng", ("bằng đồng", "đúc đồng", "đỉnh đồng", "trống đồng", "tượng đồng", "hạc đồng", "đồ đồng")),
    ("đá", ("bằng đá", "đá tự nhiên", "đá nguyên khối", "hương án đá", "bàn thờ đá", "bia đá", "tượng đá")),
    ("gỗ", ("bằng gỗ", "gỗ mít", "gỗ hương", "gỗ gụ", "gỗ sồi", "gỗ trắc", "sập gỗ", "tượng gỗ", "ghế gỗ", "bàn gỗ", "tủ gỗ")),
    ("gốm sứ", ("gốm sứ", "bằng sứ", "bằng gốm", "men lam", "men rạn", "đất nung", "chum sành", "niêu đất")),
    ("inox", ("bằng inox", "inox 304", "inox 316", "thép không gỉ")),
    ("thép", ("bằng thép", "hợp kim thép", "thép hợp kim")),
    ("nhôm", ("bằng nhôm", "hợp kim nhôm")),
    ("vàng", ("vàng 18k", "vàng 24k", "vàng 14k", "vàng 10k", "vàng 9999", "bằng vàng", "mạ vàng")),
    ("bạc", ("bạc 925", "bạc ta", "bằng bạc")),
    ("kim cương", ("kim cương", "đính kim cương")),
    ("thủy tinh", ("bằng thủy tinh", "bằng thuỷ tinh", "pha lê")),
    ("da", ("bằng da", "dây da", "da bò", "da thật", "da cá sấu")),
    ("nhựa", ("bằng nhựa", "nhựa abs", "nhựa pp", "nhựa hdpe", "nhựa pvc")),
    ("cao su", ("cao su", "bằng cao su")),
    ("mây tre", ("mây tre", "bằng tre", "bằng mây", "bằng cói")),
)

FORM_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dạng viên", ("dạng viên", "viên nén", "viên nang", "viên sủi", "viên ngậm", "viên bao phim", "tablet", "capsule")),
    ("siro", ("siro", "syrup")),
    ("hỗn dịch uống", ("hỗn dịch", "dung dịch uống", "ống uống")),
    ("thuốc tiêm", ("thuốc tiêm", "bột pha tiêm", "dịch truyền", "tiêm tĩnh mạch")),
    ("kem bôi", ("kem bôi", "thuốc mỡ", "gel bôi", "cao xoa", "cao dán")),
    ("nhỏ mắt", ("thuốc nhỏ mắt", "dung dịch nhỏ mắt")),
    ("dạng xịt", ("dạng xịt", "xịt mũi", "xịt họng", "chai xịt", "xịt bọt")),
    ("dạng bột", ("dạng bột", "bột pha", "gói bột")),
    ("không dây", ("không dây", "bluetooth", "wireless")),
)

AUDIENCE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("trẻ em", ("trẻ em", "cho bé", "em bé", "sơ sinh", "nhi khoa")),
    ("người lớn", ("người lớn", "người cao tuổi", "người già")),
    ("nam", ("cho nam", "dành cho nam", "nam giới")),
    ("nữ", ("cho nữ", "dành cho nữ", "phụ nữ", "bà bầu")),
    ("chó", ("cho chó", "chó cưng")),
    ("mèo", ("cho mèo", "mèo cưng")),
    ("ô tô", ("ô tô", "xe hơi", "xe tải", "xe con")),
    ("xe máy", ("xe máy", "mô tô", "xe tay ga")),
    ("xe đạp", ("xe đạp",)),
)


def _match_rules(text: str, rules: tuple[tuple[str, tuple[str, ...]], ...], limit: int = 4) -> list[str]:
    out = []
    for canonical, triggers in rules:
        if any(re.search(r"\b" + re.escape(t) + r"\b", text) for t in triggers):
            out.append(canonical)
            if len(out) >= limit:
                break
    return out


def _normalize_ingredient_spelling(raw: str) -> str:
    """Fold minor spelling variants ('Famotidine' -> 'Famotidin', 'Omeprazole' -> 'Omeprazol')."""
    folded = normalize(raw).strip()
    if folded.endswith("ine") and len(folded) > 6:
        folded = folded[:-1]
    elif folded.endswith("ole") and len(folded) > 6:
        folded = folded[:-1]
    elif folded.endswith("one") and len(folded) > 6 and not folded.endswith("clone"):
        folded = folded[:-1]
    elif folded.endswith("ate") and len(folded) > 6:
        folded = folded[:-1]
    return folded.capitalize()


def extract_free_entity(pack: dict, cat_info: dict) -> dict:
    """Deterministic extraction of multi-hop edges and attributes for one canonical entity."""
    l1 = cat_info.get("category_l1") or ""
    l2 = cat_info.get("category") or ""
    ptype = surface(cat_info.get("product_type") or "")
    name_surf = surface(pack["name"])
    titles_surf = " ; ".join(
        surface(s["text"]) for s in pack["snippets"] if s["kind"] in ("name", "title")
    )
    all_surf = " ; ".join(
        surface(s["text"]) for s in pack["snippets"] if s["kind"] in ("name", "title", "desc", "ocr")
    )
    all_folded = normalize(all_surf)
    name_plus_type = f"{name_surf} ; {ptype} ; {titles_surf}"

    # 1. TREATS / USED_FOR
    treats: list[str] = []
    if l1 in ("drug", "medical", "beauty", "household", "home", "agri"):
        # First check name + product_type (highest precision), then titles/descriptions.
        treats = _match_rules(f"{name_surf} ; {ptype}", PURPOSE_RULES, limit=3)
        if len(treats) < 3:
            for item in _match_rules(all_surf, PURPOSE_RULES, limit=4):
                if item not in treats:
                    treats.append(item)
                if len(treats) >= 3:
                    break

    # 2. HAS_INGREDIENT
    ingredients: list[str] = []
    if l1 in ("drug", "beauty", "agri"):
        for ing in KNOWN_INGREDIENTS:
            ing_folded = normalize(ing)
            # Vietnamese names keep their tones: folded, "kẽm" would match every "kem" (cream).
            accented = ing_folded != surface(ing)
            pattern = re.escape(surface(ing)) if accented else re.escape(ing_folded) + "e?"
            if re.search(r"\b" + pattern + r"\b", all_surf if accented else all_folded):
                canon = ing if accented else _normalize_ingredient_spelling(ing)
                if canon not in ingredients:
                    ingredients.append(canon)
                if len(ingredients) >= 3:
                    break

    # 3. FITS (vehicle / device models for parts & accessories)
    fits: list[str] = []
    is_part_or_acc = (
        l1 in ("car", "moto", "bike")
        and not l2.endswith(".vehicle")
        or l2 in ("phone.accessory", "tech.printer", "tech.peripheral", "tech.camera")
    )
    if is_part_or_acc:
        fits = _match_rules(f"{name_folded_and_surf(name_surf)} ; {normalize(titles_surf)}", VEHICLE_MODELS, limit=3)

    # 4. ORIGIN
    origin = ""
    if l1 in ("food", "culture", "agri", "fashion", "beauty", "drug", "animal"):
        places = _match_rules(name_plus_type, PLACES, limit=1)
        if not places and l1 in ("food", "culture"):
            places = _match_rules(all_surf, PLACES, limit=1)
        if places:
            origin = places[0]

    # 5. MATERIAL, FORM_FACTOR, TARGET_AUDIENCE
    materials = _match_rules(name_plus_type, MATERIAL_RULES, limit=1)
    if not materials and l1 in ("culture", "home", "household", "fashion"):
        materials = _match_rules(all_surf, MATERIAL_RULES, limit=1)
    material = materials[0] if materials else ""

    forms = _match_rules(name_plus_type, FORM_RULES, limit=1)
    if not forms and l1 in ("drug", "beauty"):
        forms = _match_rules(all_surf, FORM_RULES, limit=1)
    form_factor = forms[0] if forms else ""

    audiences = _match_rules(name_plus_type, AUDIENCE_RULES, limit=1)
    target_audience = audiences[0] if audiences else ""

    cited = [
        s["id"]
        for s in pack["snippets"]
        if s["kind"] in ("name", "title", "desc")
    ][:3] or ["n1"]

    return {
        "entity_id": pack["entity_id"],
        "name": pack["name"],
        "brand": "",
        "treats": treats,
        "ingredients": ingredients,
        "fits": fits,
        "origin": origin,
        "form_factor": form_factor,
        "target_audience": target_audience,
        "material": material,
        "evidence": cited,
        "basis": "evidence",
        "confidence": "high",
        "model": "free-rules-v1",
        "prompt_version": PROMPT_VERSION,
    }


def name_folded_and_surf(text: str) -> str:
    return f"{text} ; {normalize(text)}"


def free(_: argparse.Namespace) -> None:
    packs = read_jsonl(OUT / "evidence.jsonl")
    cat_path = CATEGORIES if CATEGORIES.exists() else ARTIFACTS / "entity_categories.json"
    cats = json.loads(cat_path.read_text(encoding="utf-8"))["entities"] if cat_path.exists() else {}
    rows = [extract_free_entity(p, cats.get(p["entity_id"]) or {}) for p in packs]
    TRACKED_GRAPH.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    (TRACKED_GRAPH / "enrich-free.jsonl").write_text(body, encoding="utf-8", newline="\n")
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "enrich-free.jsonl").write_text(body, encoding="utf-8", newline="\n")
    except PermissionError:
        pass
    summary = {
        "entities": len(rows),
        "with_treats": sum(bool(r["treats"]) for r in rows),
        "with_ingredients": sum(bool(r["ingredients"]) for r in rows),
        "with_fits": sum(bool(r["fits"]) for r in rows),
        "with_origin": sum(bool(r["origin"]) for r in rows),
        "with_material": sum(bool(r["material"]) for r in rows),
        "with_form_factor": sum(bool(r["form_factor"]) for r in rows),
        "with_target_audience": sum(bool(r["target_audience"]) for r in rows),
    }
    print(json.dumps(summary, ensure_ascii=False))


# ---- Gemini 3.8 Flash structured batch extraction ----------------------------------------
def batch_schema():
    class EntityEnrich(BaseModel):
        key: str
        brand: str = Field(default="", max_length=80)
        treats: list[str] = Field(default_factory=list)
        ingredients: list[str] = Field(default_factory=list)
        fits: list[str] = Field(default_factory=list)
        origin: str = Field(default="", max_length=80)
        form_factor: str = Field(default="", max_length=60)
        target_audience: str = Field(default="", max_length=60)
        material: str = Field(default="", max_length=60)
        evidence: list[str] = Field(default_factory=list)
        basis: Literal["evidence", "knowledge"]
        confidence: Literal["high", "medium", "low"]

    class Labels(BaseModel):
        labels: list[EntityEnrich]

    return Labels


def agy_schema() -> dict:
    item = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "brand": {"type": "string"},
            "treats": {"type": "array", "items": {"type": "string"}},
            "ingredients": {"type": "array", "items": {"type": "string"}},
            "fits": {"type": "array", "items": {"type": "string"}},
            "origin": {"type": "string"},
            "form_factor": {"type": "string"},
            "target_audience": {"type": "string"},
            "material": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "basis": {"type": "string", "enum": ["evidence", "knowledge"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["key", *ENRICH_FIELDS],
    }
    return {
        "type": "object",
        "properties": {"labels": {"type": "array", "items": item}},
        "required": ["labels"],
    }


class EngineError(Exception):
    def __init__(self, message: str, retry: bool):
        super().__init__(message)
        self.retry = retry


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "GEMINI_API_KEY":
            os.environ.setdefault("GEMINI_API_KEY", value.strip().strip('"'))


def api_engine(args, system: str):
    from google import genai
    from google.genai import errors, types

    client = genai.Client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=batch_schema(),
        temperature=0,
        thinking_config=types.ThinkingConfig(thinking_budget=args.thinking_budget),
    )

    def call(user: str) -> tuple[str, dict]:
        try:
            response = client.models.generate_content(model=MODEL, contents=user, config=config)
        except errors.APIError as error:
            retry = error.code == 429 or (error.code or 0) >= 500
            raise EngineError(f"HTTP {error.code}", retry) from error
        usage = response.usage_metadata
        return response.text, {
            "prompt": usage.prompt_token_count or 0,
            "output": usage.candidates_token_count or 0,
            "thoughts": usage.thoughts_token_count or 0,
        }

    return call


AGY_QUOTA = ("quota", "rate limit", "resource_exhausted", "high demand", "try again", "429", "503")


def agy_engine(args, system: str):
    import subprocess
    import tempfile

    schema = OUT / "agy_enrich_schema.json"
    schema.write_text(json.dumps(agy_schema(), ensure_ascii=False), encoding="utf-8")
    workdir = Path(tempfile.mkdtemp(prefix="agy-b4-"))
    cmd = [
        args.agy, "--input-format", "stream-json", "--output-format", "stream-json",
        "--model", args.agy_model, "--disable-slash-commands", "--json-schema", str(schema),
        "--print-timeout", f"{args.timeout}s", "--print=",
    ]  # fmt: skip

    def call(user: str) -> tuple[str, dict]:
        prompt = system + "\n\nDo not use any tool. Answer only with the JSON.\n\n" + user
        message = json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False)
        try:
            done = subprocess.run(
                cmd, input=message + "\n", capture_output=True, text=True, encoding="utf-8",
                cwd=workdir, timeout=args.timeout + 60,
            )  # fmt: skip
        except subprocess.TimeoutExpired as error:
            raise EngineError("agy timed out", True) from error
        result = None
        for line in done.stdout.splitlines():
            if '"event":"result"' in line.replace(" ", ""):
                try:
                    result = json.loads(line)["result"]
                except (ValueError, KeyError):
                    continue
        if result is None:
            text = (done.stdout + done.stderr)[-300:]
            raise EngineError(f"no result: {text}", any(q in text.lower() for q in AGY_QUOTA))
        if result.get("status") != "SUCCESS":
            error = str(result.get("error", ""))[:300]
            raise EngineError(error, any(q in error.lower() for q in AGY_QUOTA))
        usage = result.get("usage") or {}
        return result.get("response", ""), {
            "prompt": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
            "thoughts": usage.get("thinking_tokens", 0),
        }

    return call


def parse_labels(text: str) -> list[dict]:
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        start = text.find("{", pos)
        if start < 0:
            raise ValueError("no usable JSON object in the response")
        obj, end = decoder.raw_decode(text[start:])
        pos = start + end
        if isinstance(obj.get("labels"), list):
            return obj["labels"]
        keyed = [dict(v, key=k) for k, v in obj.items() if isinstance(v, dict)]
        if keyed:
            return keyed


def valid_enrich(label: dict) -> bool:
    return (
        all(f in label for f in ENRICH_FIELDS)
        and isinstance(label["brand"], str)
        and isinstance(label["treats"], list)
        and isinstance(label["ingredients"], list)
        and isinstance(label["fits"], list)
        and isinstance(label["origin"], str)
        and isinstance(label["form_factor"], str)
        and isinstance(label["target_audience"], str)
        and isinstance(label["material"], str)
        and isinstance(label["evidence"], list)
        and label["basis"] in ("evidence", "knowledge")
        and label["confidence"] in ("high", "medium", "low")
    )


def run(args: argparse.Namespace) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    packs = {p["entity_id"]: p for p in read_jsonl(OUT / "evidence.jsonl")}
    cats = (
        json.loads(CATEGORIES.read_text(encoding="utf-8"))["entities"]
        if CATEGORIES.exists()
        else {}
    )
    wanted = (
        json.loads((OUT / "pilot_ids.json").read_text(encoding="utf-8"))
        if args.ids == "pilot"
        else sorted(packs)
    )
    cache_dir = OUT / "enrich_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if args.ids == "remaining":
        done = {
            json.loads(p.read_text(encoding="utf-8"))["entity_id"]
            for p in cache_dir.glob("*.json")
        }
        wanted = [e for e in wanted if e not in done]
    if args.limit:
        wanted = wanted[: args.limit]
    if args.engine == "api":
        load_env()
        call, model, workers, interval = api_engine(args, SYSTEM), MODEL, 1, 60 / args.rpm
    else:
        call, model = agy_engine(args, SYSTEM), f"agy:{args.agy_model}"
        workers, interval = args.workers, 0.0

    def cache_file(entity_id: str) -> Path:
        rendered = render_pack(packs[entity_id], cats.get(entity_id))
        key = json.dumps([PROMPT_VERSION, model, args.thinking_budget, SYSTEM, rendered])
        return cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    todo = [e for e in wanted if not cache_file(e).exists()]
    chunks = [todo[i : i + args.batch] for i in range(0, len(todo), args.batch)]
    print(
        f"{model}: {len(wanted)} wanted, {len(wanted) - len(todo)} cached, {len(chunks)} requests"
    )
    started = time.perf_counter()
    failures: list[dict] = []
    lock = threading.Lock()
    pace = {"last": -interval, "pause_until": 0.0, "pause": 60.0}

    def wait_turn() -> None:
        while True:
            with lock:
                now = time.perf_counter()
                ready = max(pace["last"] + interval, pace["pause_until"])
                if now >= ready:
                    pace["last"] = now
                    return
            time.sleep(min(5.0, ready - now))

    def process(n: int, chunk: list[str]) -> None:
        keys = {f"e{i}": eid for i, eid in enumerate(chunk, 1)}
        user = "\n\n".join(
            f"### {k}\nEntity: {packs[e]['name']}\nEvidence:\n{render_pack(packs[e], cats.get(e))}"
            for k, e in keys.items()
        )
        result = None
        for attempt in range(6):
            wait_turn()
            try:
                result = call(user)
                break
            except EngineError as error:
                if not error.retry:
                    with lock:
                        failures.append({"chunk": n, "error": str(error)})
                    return
                with lock:
                    pace["pause_until"] = time.perf_counter() + pace["pause"]
                    pace["pause"] = min(pace["pause"] * 2, 1800)
                print(f"request {n}: {str(error)[:120]} (retry {attempt + 1})", flush=True)
        if result is None:
            with lock:
                failures.append({"chunk": n, "error": "no result after retries"})
            return
        text, tokens = result
        try:
            labels = parse_labels(text)
        except (ValueError, KeyError, TypeError) as error:
            with lock:
                failures.append({"chunk": n, "error": f"unparsable response: {error}"})
            return
        with lock:
            pace["pause"] = 60.0
        got = {}
        for label in labels:
            if isinstance(label, dict) and label.get("key") in keys and label["key"] not in got:
                if valid_enrich(label):
                    got[label["key"]] = label
        for k, entity_id in keys.items():
            label = got.get(k)
            if label is None:
                with lock:
                    failures.append({"entity_id": entity_id, "error": "missing or invalid label"})
                continue
            known = {s["id"] for s in packs[entity_id]["snippets"]}
            row = {
                "entity_id": entity_id,
                "name": packs[entity_id]["name"],
                **{f: label[f] for f in ENRICH_FIELDS},
                "unknown_evidence_ids": [e for e in label["evidence"] if e not in known],
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "tokens": {t: round(v / len(keys), 1) for t, v in tokens.items()},
            }
            cache_file(entity_id).write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
        print(f"request {n}/{len(chunks)}: {len(got)}/{len(keys)} enriched, {tokens}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda item: process(*item), enumerate(chunks, 1)))

    rows = []
    for entity_id in wanted:
        path = cache_file(entity_id)
        if path.exists():
            rows.append(json.loads(path.read_text(encoding="utf-8")))
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    (OUT / f"enrich-{args.ids}.jsonl").write_text(body, encoding="utf-8", newline="\n")
    tok = {k: round(sum(r["tokens"][k] for r in rows)) for k in ("prompt", "output", "thoughts")}
    summary = {
        "wanted": len(wanted),
        "enriched": len(rows),
        "failures": failures,
        "seconds": round(time.perf_counter() - started, 1),
        "tokens_all": tok,
        "by_basis": dict(Counter(r["basis"] for r in rows)),
        "by_confidence": dict(Counter(r["confidence"] for r in rows)),
        "with_brand": sum(bool(r["brand"]) for r in rows),
        "with_treats": sum(bool(r["treats"]) for r in rows),
        "with_ingredients": sum(bool(r["ingredients"]) for r in rows),
        "with_fits": sum(bool(r["fits"]) for r in rows),
        "with_origin": sum(bool(r["origin"]) for r in rows),
        "with_material": sum(bool(r["material"]) for r in rows),
    }
    (OUT / f"run-enrich-{args.ids}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "failures"}, ensure_ascii=False))
    print(f"failures: {len(failures)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("free")
    p_run = sub.add_parser("run")
    p_run.add_argument("--ids", choices=["pilot", "all", "remaining"], default="pilot")
    p_run.add_argument("--limit", type=int, default=0)
    p_run.add_argument("--batch", type=int, default=20, help="entities per request")
    p_run.add_argument("--rpm", type=float, default=4, help="requests/minute (api engine)")
    p_run.add_argument("--thinking-budget", type=int, default=256, help="api engine only")
    p_run.add_argument("--engine", choices=["agy", "api"], default="agy")
    p_run.add_argument("--agy", default=str(Path.home() / "AppData/Local/agy/bin/agy.exe"))
    p_run.add_argument("--agy-model", default="gemini-3.8-flash-low")
    p_run.add_argument("--workers", type=int, default=3, help="agy engine only")
    p_run.add_argument("--timeout", type=int, default=300, help="seconds per agy call")
    args = parser.parse_args()
    {"free": free, "run": run}[args.cmd](args)


if __name__ == "__main__":
    main()
