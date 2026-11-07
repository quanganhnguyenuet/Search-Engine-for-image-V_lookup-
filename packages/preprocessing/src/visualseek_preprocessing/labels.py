"""Reviewed corpus spellings; restore labels without guessing unrestricted Vietnamese text."""

import re
import unicodedata

LABEL_VERSION = "culture-labels-nfc-v1"

# Scoped by category: identical ASCII words can denote unrelated objects.
_REVIEWED = {
    "am_thuc": """
banh bao|Bánh bao
banh bot lọc|Bánh bột lọc
banh chay|Bánh chay
banh chung Tet|Bánh chưng Tết
banh chung gù|Bánh chưng gù
banh cuốn|Bánh cuốn
banh căn Phan Thiet|Bánh căn Phan Thiết
banh day|Bánh dày
banh flan|Bánh flan
banh gai|Bánh gai
banh gio|Bánh giò
banh in|Bánh in
banh it|Bánh ít
banh khọt|Bánh khọt
banh mi Việt Nam|Bánh mì Việt Nam
banh pia|Bánh pía
banh ran|Bánh rán
banh trang nuớng|Bánh tráng nướng
banh tét|Bánh tét
banh xèo|Bánh xèo
banh đậu xanh|Bánh đậu xanh
bún bò Hue|Bún bò Huế
bún chả Ha Noi|Bún chả Hà Nội
bún thịt nuớng|Bún thịt nướng
ca phê phin|Cà phê phin
canh chua|Canh chua
cao lầu Hoi An|Cao lầu Hội An
chao ca|Cháo cá
chao lòng|Cháo lòng
chè Việt Nam|Chè Việt Nam
chè buởi|Chè bưởi
chả ca La Vọng|Chả cá Lã Vọng
chả lụa|Chả lụa
chả que Hung Yên|Chả quế Hưng Yên
cơm tấm Sai Gòn|Cơm tấm Sài Gòn
cơm âm phu Hue|Cơm âm phủ Huế
ga nuớng|Gà nướng
giò thu|Giò thủ
goi cuốn|Gỏi cuốn
kẹo dua Ben Tre|Kẹo dừa Bến Tre
mam ruốc|Mắm ruốc
mam tôm|Mắm tôm
mi Quảng|Mì Quảng
mut Tet|Mứt Tết
nem chua|Nem chua
nem nuớng|Nem nướng
nuớc mam Phú Quốc|Nước mắm Phú Quốc
phở Việt Nam|Phở Việt Nam
tuơng ớt|Tương ớt
tôm khô|Tôm khô
vịt quay|Vịt quay
xôi gấc|Xôi gấc
""",
    "trang_phuc": """
ao ba ba|Áo bà ba
ao choang lemur|Áo choàng Lemur
ao dai Việt Nam|Áo dài Việt Nam
ao dai cach tân|Áo dài cách tân
ao dai cuới|Áo dài cưới
ao dai học sinh|Áo dài học sinh
ao dai nam|Áo dài nam
ao dai trẻ em|Áo dài trẻ em
ao gấm Hue|Áo gấm Huế
ao tu thân|Áo tứ thân
ao yem truyen thống|Áo yếm truyền thống
dây chuyen bạc|Dây chuyền bạc
khuyên tai dân toc|Khuyên tai dân tộc
khăn piêu Thai|Khăn piêu Thái
khăn ran Nam Bo|Khăn rằn Nam Bộ
khăn thổ cẩm|Khăn thổ cẩm
khăn đóng Hue|Khăn đóng Huế
nón bai thơ|Nón bài thơ
nón la Việt Nam|Nón lá Việt Nam
nón nón la Hue|Nón lá Huế
nón quai thao|Nón quai thao
sarong Chăm|Sarong Chăm
that lung thổ cẩm|Thắt lưng thổ cẩm
thổ cẩm|Thổ cẩm
trang phục Chăm|Trang phục Chăm
trang phục Cơ Tu|Trang phục Cơ Tu
trang phục Dao|Trang phục Dao
trang phục Giay|Trang phục Giáy
trang phục HMông|Trang phục H'Mông
trang phục Khmer|Trang phục Khmer
trang phục La Chi|Trang phục La Chí
trang phục Muong|Trang phục Mường
trang phục Nùng|Trang phục Nùng
trang phục Tay|Trang phục Tày
trang phục dân toc|Trang phục dân tộc
trang phục Ê Đê|Trang phục Ê Đê
turban Chăm|Turban Chăm
túi thổ cẩm|Túi thổ cẩm
vay thổ cẩm|Váy thổ cẩm
vòng cổ HMông|Vòng cổ H'Mông
vòng tay bạc|Vòng tay bạc
yem đao|Yếm đào
""",
    "kien_truc": """
Văn Mieu Quốc Tử Giam|Văn Miếu Quốc Tử Giám
biệt thự Phap|Biệt thự Pháp
buu điện Sai Gòn|Bưu điện Sài Gòn
chùa Ba Đen|Chùa Bà Đen
chùa Bai Đinh|Chùa Bái Đính
chùa Cao Đai|Chùa Cao Đài
chùa Mot Cot|Chùa Một Cột
chùa Ngọc Hoang|Chùa Ngọc Hoàng
chùa Tam Chúc|Chùa Tam Chúc
chùa Trấn Quốc|Chùa Trấn Quốc
chùa Việt Nam|Chùa Việt Nam
chợ Đồng Xuân|Chợ Đồng Xuân
cot co Ha Noi|Cột cờ Hà Nội
cung An Định|Cung An Định
cầu Long Biên|Cầu Long Biên
cầu Nhật Bản Hoi An|Cầu Nhật Bản Hội An
cầu Thê Húc|Cầu Thê Húc
cổng thanh Ha Noi|Cổng thành Hà Nội
dinh Đoc Lập|Dinh Độc Lập
hoang thanh Thăng Long|Hoàng thành Thăng Long
kien trúc Gothic|Kiến trúc Gothic
kien trúc Hue|Kiến trúc Huế
kinh thanh Hue|Kinh thành Huế
lăng Hồ Chi Minh|Lăng Hồ Chí Minh
lăng Khải Định|Lăng Khải Định
lăng Minh Mạng|Lăng Minh Mạng
lăng Tu Đuc|Lăng Tự Đức
nha Bac Hồ|Nhà Bác Hồ
nha cổ Hoi An|Nhà cổ Hội An
nha dai Ê Đê|Nhà dài Ê Đê
nha hat Lớn Ha Noi|Nhà hát Lớn Hà Nội
nha rồng HCMC|Nhà Rồng TP.HCM
nha san HMông|Nhà sàn H'Mông
nha san Tây Bac|Nhà sàn Tây Bắc
nha tho Đuc Ba|Nhà thờ Đức Bà
nha truyen thống mien Tây|Nhà truyền thống miền Tây
nha vuon Hue|Nhà vườn Huế
phu Chu tịch|Phủ Chủ tịch
phố cổ Ha Noi|Phố cổ Hà Nội
quảng truong Ba Đinh|Quảng trường Ba Đình
ruong bậc thang kien trúc|Ruộng bậc thang kiến trúc
thap Rùa|Tháp Rùa
đen Bạch Ma|Đền Bạch Mã
đen Hùng|Đền Hùng
đen Ngọc Sơn|Đền Ngọc Sơn
đinh Bảng|Đình Bảng
đinh lang Việt Nam|Đình làng Việt Nam
""",
    "le_hoi": """
Tet Nguyên Đan Việt Nam|Tết Nguyên Đán Việt Nam
Trung thu Việt Nam|Trung thu Việt Nam
le 20 11|Lễ 20/11
le 8 3|Lễ 8/3
le Quốc khanh|Lễ Quốc khánh
le gio tổ Hùng Vuơng|Lễ giỗ Tổ Hùng Vương
le hoi Bunpchum|Lễ hội Bunpchum
le hoi Chaul Chnam Thmey|Lễ hội Chaul Chnam Thmey
le hoi Dôn Ta|Lễ hội Dôn Ta
le hoi Ghe Ngo|Lễ hội Ghe Ngo
le hoi Nang Hai|Lễ hội Nàng Hai
le hoi Roóng Poọc|Lễ hội Roóng Poọc
le hoi Sene Dolta|Lễ hội Sene Dolta
le hoi banh chung|Lễ hội bánh chưng
le hoi café|Lễ hội cà phê
le hoi chùa Huơng|Lễ hội chùa Hương
le hoi chọi trâu|Lễ hội chọi trâu
le hoi cúng ơn|Lễ hội cúng ơn
le hoi hoa anh đao|Lễ hội hoa anh đào
le hoi hoa ban|Lễ hội hoa ban
le hoi lang|Lễ hội làng
le hoi múa lân|Lễ hội múa lân
le hoi nuớc|Lễ hội nước
le hoi ném còn|Lễ hội ném còn
le hoi phao hoa|Lễ hội pháo hoa
le hoi tinh yêu|Lễ hội tình yêu
le hoi Óc Om Bóc|Lễ hội Óc Om Bóc
le hoi đen Hùng|Lễ hội đền Hùng
le hoi đua thuyen|Lễ hội đua thuyền
ram thang Bảy|Rằm tháng Bảy
ram thang Giêng|Rằm tháng Giêng
tet Han thực|Tết Hàn thực
tet Trung thu|Tết Trung thu
tet Đoan ngọ|Tết Đoan ngọ
đam cuới Việt Nam|Đám cưới Việt Nam
""",
    "thu_cong_my_nghe": """
che tac bạc|Chế tác bạc
chieu cói|Chiếu cói
chạm go|Chạm gỗ
chạm khac|Chạm khắc
dệt thảm|Dệt thảm
giấy dó|Giấy dó
gốm Chu Đậu|Gốm Chu Đậu
gốm Phù Lang|Gốm Phù Lang
gốm su Bat Trang|Gốm sứ Bát Tràng
khac chữ Han|Khắc chữ Hán
khảm trai|Khảm trai
kẹo dân gian|Kẹo dân gian
lam banh dẻo|Làm bánh dẻo
lam banh in|Làm bánh in
lam huơng|Làm hương
mây tre đan|Mây tre đan
múa rối nuớc|Múa rối nước
mặt nạ dân gian|Mặt nạ dân gian
nón la thu công|Nón lá thủ công
rối nuớc Việt Nam|Rối nước Việt Nam
su Hue|Sứ Huế
sơn mai Việt Nam|Sơn mài Việt Nam
thêu Việt Nam|Thêu Việt Nam
thảm cói|Thảm cói
thổ cẩm dệt|Thổ cẩm dệt
trang suc dân toc|Trang sức dân tộc
tranh lụa|Tranh lụa
tranh sơn mai|Tranh sơn mài
tranh Đông Hồ|Tranh Đông Hồ
tuợng go|Tượng gỗ
tuợng đa|Tượng đá
đa my nghệ|Đá mỹ nghệ
đan lat|Đan lát
điêu khac go|Điêu khắc gỗ
đúc đồng|Đúc đồng
đồ bạc|Đồ bạc
đồ đồng|Đồ đồng
""",
    "phong_canh": """
Hồ Guơm|Hồ Gươm
Hồ Tây Ha Noi|Hồ Tây Hà Nội
Tam Cốc|Tam Cốc
biển Hạ Long|Biển Hạ Long
biển Nha Trang|Biển Nha Trang
biển Quy Nhon|Biển Quy Nhơn
biển Sầm Sơn|Biển Sầm Sơn
biển Vung Tau|Biển Vũng Tàu
biển Đa Nẵng|Biển Đà Nẵng
cao nguyên Đồng Văn|Cao nguyên Đồng Văn
cồn Thoi Son|Cồn Thới Sơn
núi Phan Xi Păng|Núi Phan Xi Păng
rung Tram Chim|Rừng Tràm Chim
rung ngập mặn Cần Gio|Rừng ngập mặn Cần Giờ
ruong bậc thang Sapa|Ruộng bậc thang Sa Pa
sông Huơng|Sông Hương
sông Hồng|Sông Hồng
sông Thu Bồn|Sông Thu Bồn
thac Ban Giốc|Thác Bản Giốc
thac Elephant|Thác Elephant
thac Pongour|Thác Pongour
thac Sekumpul|Thác Sekumpul
vuon quốc gia Cúc Phuơng|Vườn quốc gia Cúc Phương
vuon quốc gia U Minh|Vườn quốc gia U Minh
vuon quốc gia Yok Đôn|Vườn quốc gia Yok Đôn
vịnh Hạ Long|Vịnh Hạ Long
đảo Cat Ba|Đảo Cát Bà
đảo Phú Quốc|Đảo Phú Quốc
đồi cat Mui Né|Đồi cát Mũi Né
đồi chè Moc Châu|Đồi chè Mộc Châu
""",
}


def canonical_title(title: str, category: str) -> str:
    cleaned = unicodedata.normalize("NFC", title).replace("_", " ").strip()
    pairs = _REVIEWED.get(category, "")
    for line in pairs.splitlines():
        if "|" in line:
            source, target = line.split("|", 1)
            if cleaned.casefold() == source.casefold():
                return target
    return cleaned


def repair_labels(asset: dict) -> dict:
    """Idempotent, versioned changes; keep source text and SHA-256 image identity."""
    from visualseek_preprocessing.label_overrides import ASSET_OVERRIDES

    if asset.get("label_revision") == LABEL_VERSION:
        return dict(asset)
    row = dict(asset)
    original = {
        "keyword": row.get("keyword", ""),
        "tags": row.get("tags", []),
        "description": row.get("description", ""),
        "cultural_context": row.get("cultural_context", {}),
    }
    row["keyword"] = canonical_title(str(row.get("keyword", "")), row["category"])
    row["tags"] = [unicodedata.normalize("NFC", str(tag)) for tag in row.get("tags", [])]
    row["description"] = unicodedata.normalize("NFC", str(row.get("description") or ""))
    override = ASSET_OVERRIDES.get(row["asset_id"])
    if override is not None:
        if row["image_path"] != override["source_image_path"]:
            raise ValueError("Reviewed label override does not match the source image path")
        row.update(
            {key: override[key] for key in ("keyword", "tags", "description", "search_excluded")}
        )
        row["cultural_context"] = {}
        row["label_review"] = {key: override[key] for key in ("review_method", "reason")}
    elif original["keyword"] in ("giay go", "kẹo dân gian"):
        candidates = (
            ("Tủ giày", "Kệ giày", "Giá để giày", "Giá giày", "Guốc gỗ", "Giày gỗ")
            if original["keyword"] == "giay go"
            else ("Keo dán gạch", "Keo dán")
        )
        for candidate in candidates:
            pattern = r"(?<!\w)" + re.escape(candidate.casefold()) + r"(?!\w)"
            if any(re.search(pattern, tag.casefold()) for tag in row["tags"]) and re.search(
                pattern, row["description"].casefold()
            ):
                row["keyword"] = candidate
                row["cultural_context"] = {}
                row["label_review"] = {
                    "review_method": "tag-description-agreement",
                    "reason": "source-folder-subject-conflict",
                    "pixel_verified": False,
                }
                break
    if any(
        row.get(key, default) != original[key]
        for key, default in (
            ("keyword", ""),
            ("tags", []),
            ("description", ""),
            ("cultural_context", {}),
        )
    ):
        row["label_original"] = original
        row["label_revision"] = LABEL_VERSION
    return row
