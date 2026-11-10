"""Curated visible concepts and explicit aliases, independent of retrieval rankings.

Category selects the query stratum only. Tag evidence is searched across the whole
gallery. These rules define annotation-derived silver labels, never human gold labels.
"""

CATALOG = {
    "am_thuc": """bánh chưng | bánh tét | bánh xèo | bánh khọt | bánh cuốn | bánh giò |
        bánh mì | bánh bao | bánh flan | bánh bột lọc | bánh gai | bánh in | bánh ít |
        bánh đậu xanh | bánh dày=bánh giầy | bánh pía | nem nướng | nem chua | gỏi cuốn |
        cơm tấm | xôi gấc | chè bưởi | cháo lòng | phở=tô phở,bát phở,bánh phở |
        bún bò Huế=tô bún bò Huế | mì Quảng=tô mì Quảng | cao lầu=tô cao lầu |
        thịt nướng | thịt bò | thịt heo=thịt lợn | tôm | trứng cút | rau sống |
        nước chấm | lá chuối | lá dong | đũa | phin cà phê | mâm tre=mẹt tre |
        đậu phộng=lạc,đậu phộng rang,lạc rang | dưa leo=dưa chuột | hành lá |
        hành phi | chả lụa=giò lụa | giá đỗ | đậu xanh | rau thơm | rau răm |
        nước dùng | bánh tráng | tương ớt | gà nướng | vịt quay | cơm trắng""",
    "kien_truc": """mái ngói | cổng tam quan | cột gỗ | cột đá | tường thành |
        cầu thang | nhà sàn | lư hương | bàn thờ | tháp chuông | ban công | cửa sổ |
        cửa gỗ | mái vòm | tường gạch | sân gạch | cổng chùa | mái chùa | sân vườn |
        nhà cổ Hội An=phố cổ Hội An | Chùa Một Cột | Chùa Ngọc Hoàng | Chùa Tam Chúc |
        Dinh Độc Lập | Cầu Long Biên | Cầu Thê Húc | Tháp Rùa | Hồ Hoàn Kiếm=Hồ Gươm |
        Lăng Chủ tịch Hồ Chí Minh=Lăng Hồ Chí Minh,Lăng Bác | Quảng trường Ba Đình |
        Nhà hát Lớn Hà Nội | Nhà thờ Đức Bà Sài Gòn=Nhà thờ Đức Bà |
        Chợ Đồng Xuân | Đền Ngọc Sơn | Lăng Khải Định | Cung An Định |
        nhà dài Ê Đê | nhà sàn H'Mông=nhà sàn HMông | cổng thành | cầu đá |
        cầu thang đá | cờ Việt Nam=cờ đỏ sao vàng | đài phun nước | cột cờ |
        lan can | mái tranh | cổng sắt | thảm cỏ | hàng rào | cổng chính""",
    "le_hoi": """kiệu rước | pháo hoa | sân khấu | khán giả=người xem,khán đài |
        cờ lễ hội | ghe ngo | người chèo thuyền=người chèo ghe | thuyền rồng |
        thuyền đua | quạt giấy | trống | chú rể | cô dâu | nến | gạo nếp |
        hoa anh đào | nhà sư | đồ cúng=lễ vật | mâm cỗ=mâm cúng |
        tượng Phật | nhang=hương | gà luộc | bánh trôi nước | múa lân=người múa lân |
        lân đỏ | lân vàng | đoàn người diễu hành | biểu ngữ | cờ Phật giáo |
        bánh trung thu | súng nước | người biểu diễn=người biểu diễn nghệ thuật |
        người tham gia lễ hội=người tham dự lễ hội,người tham dự |
        trang phục truyền thống=người mặc trang phục truyền thống |
        đèn lồng | đèn lồng đỏ | hoa tươi | hoa hồng | hoa ban | xôi |
        màn hình LED | biển hiệu lễ hội | ô dù=ô | đám đông=đám đông người |
        người dân tộc=người dân tộc thiểu số | trẻ em | hoa quả=trái cây""",
    "phong_canh": """núi đá vôi | đảo đá vôi=hòn đảo đá vôi | ruộng bậc thang |
        thác nước | bãi biển | biển=nước biển | cát trắng | thuyền đánh cá |
        thuyền du lịch=du thuyền | thuyền nhỏ | rừng ngập mặn | rừng nhiệt đới |
        rừng tràm | rừng cây | hang động | măng đá | nhũ đá | đồi cát |
        hoàng hôn=bầu trời hoàng hôn | sương mù | biển mây | đồi chè |
        ruộng lúa=cánh đồng lúa,đồng lúa | lúa chín | bờ sông | bờ biển |
        cây dừa=hàng dừa | sông Hương | sông Thu Bồn | sông Hồng |
        sông Mekong=sông Mê Kông,sông Cửu Long | Vịnh Hạ Long=Hạ Long Bay,Biển Hạ Long |
        Hồ Ba Bể=Ba Be lake | Hồ Tây | Thác Bản Giốc | Thác Pongour |
        Hang động Phong Nha=hang Phong Nha | đồi cát Mũi Né=đồi cát Mui Né |
        xuồng ba lá | đường mòn | cầu gỗ | kênh rạch | núi=núi non,núi đồi,đồi núi |
        bầu trời | thảm thực vật=thực vật | người tắm biển | người nông dân |
        người bán hàng | khách sạn | sóng | vách đá | đá vôi""",
    "thu_cong_my_nghe": """rối nước=con rối nước,múa rối nước |
        sân khấu rối nước=sân khấu múa rối nước | chiếu cói | thảm cói | giấy dó |
        cọ vẽ | bình gốm Chu Đậu | tác phẩm điêu khắc gỗ | tượng Phật Di Lặc |
        tranh sơn mài | người thợ=người nghệ nhân,nghệ nhân |
        khuôn đúc | đồng nóng chảy | đế gỗ | tranh Đông Hồ |
        bình gốm=bình gốm sứ | bình hoa | tranh lụa | khung dệt |
        bàn làm việc | sợi chỉ | khung tranh | mặt nạ dân gian | tranh thêu |
        thảm dệt | xưởng gốm | người điều khiển rối | bàn tay người thợ |
        giỏ mây tre đan | bình gốm Phù Lãng | người thợ gốm |
        bàn xoay gốm | đất sét | dụng cụ điêu khắc | hoa văn chạm khắc |
        họa tiết rồng | lụa | lá sen | hoa sen | gỗ | chữ Hán |
        tượng sư tử đá | cá chép | con trâu | rồng | bàn tay | khung gỗ |
        chén trà | ấm trà | đĩa sứ | sàn gỗ | nhà tranh | cây cọ""",
    "trang_phuc": """áo dài | áo dài trắng | áo dài đỏ | áo dài cách tân |
        áo dài nam | áo dài cưới | áo dài trẻ em | áo bà ba | áo yếm | áo tứ thân |
        nón lá | nón quai thao | dây chuyền bạc | vòng tay bạc |
        vòng cổ=vòng cổ bạc | túi xách | vải thổ cẩm=thổ cẩm |
        khăn thổ cẩm | váy thổ cẩm | túi thổ cẩm | họa tiết thổ cẩm |
        khăn rằn=khăn rằn Nam Bộ | khăn piêu=khăn piêu đầu,khăn piêu Thái |
        khăn đóng=khăn đóng Huế | khăn đội đầu | khuyên tai | thắt lưng |
        trang phục H'Mông=trang phục HMông | trang phục Ê Đê | trang phục Tày |
        trang phục Cơ Tu | trang phục Khmer | trang phục Nùng | trang phục Chăm |
        trang phục Dao=trang phục người Dao | trang phục La Chi |
        nữ sinh | bé gái | phụ nữ=người phụ nữ | người đàn ông |
        quần trắng | quần dài | quạt xếp | giày cao gót | áo khoác |
        áo dài gấm Huế | tua rua | họa tiết thêu | giỏ mây | người mẫu""",
}


def concepts() -> list[dict]:
    result = []
    for category, text in sorted(CATALOG.items()):
        for entry in text.split("|"):
            name, _, aliases = " ".join(entry.split()).partition("=")
            result.append({"name": name, "category": category,
                           "aliases": [name, *[a.strip() for a in aliases.split(",")
                                               if a.strip()]]})
    return result
