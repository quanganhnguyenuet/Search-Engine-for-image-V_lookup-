"""Prepare 200 draft evaluation requests; humans must confirm answerability and relevance."""

import argparse
import json
import unicodedata
from pathlib import Path

OBJECTS = ["người mặc áo dài", "chiếc nón lá", "tô phở bò", "bánh chưng", "bún bò Huế",
           "mâm bánh truyền thống", "đồ gốm", "sản phẩm mây tre", "tranh dân gian", "trống đồng",
           "nhà sàn", "ngôi chùa cổ", "cổng đình", "cầu gỗ", "thuyền đánh cá",
           "ruộng lúa", "thác nước", "bờ biển", "đèn lồng", "trang phục dân tộc"]
SCENES = ["người đang nấu ăn", "nghệ nhân đang nặn gốm", "người đang dệt vải",
          "đoàn người rước lễ", "múa lân ngoài đường", "chợ đông người",
          "thuyền di chuyển trên sông", "người đang cấy lúa", "người đang làm bánh",
          "lễ hội ngoài trời", "biểu diễn múa rối nước", "người bán hàng rong",
          "phố cổ vào ban đêm", "người mặc trang phục truyền thống biểu diễn",
          "sinh hoạt trong nhà sàn", "ngư dân trên bãi biển", "làng quê bên sông",
          "nghệ nhân đan giỏ", "chuẩn bị mâm cỗ ngày Tết", "ruộng bậc thang trên núi"]
ATTRIBUTES = ["người mặc áo dài đỏ đứng trước công trình cổ",
              "ruộng bậc thang xanh ở vùng núi", "tô phở có thịt bò và rau thơm",
              "thuyền trên sông giữa những núi đá vôi", "đèn lồng nhiều màu vào ban đêm",
              "bát bún có thịt nướng và rau sống", "ngôi chùa có mái cong và sân rộng",
              "người mặc áo dài trắng giữa cây xanh", "thác nước rộng có cây xanh xung quanh",
              "giỏ tre được đan bằng tay", "nhà sàn bằng gỗ trên sườn núi",
              "mâm cơm có nhiều món ăn truyền thống", "bãi biển với thuyền và cát trắng",
              "người mặc trang phục dân tộc nhiều màu", "đồ gốm có hoa văn màu xanh",
              "cổng đình chạm khắc có mái ngói", "lễ hội có cờ và nhiều người",
              "ruộng lúa vàng bên ngọn núi", "bánh chưng gói bằng lá xanh",
              "cảnh làng quê không có nhà cao tầng"]
SPELLING = ["áo bà ba", "đình làng", "gốm Bát Tràng", "đan lát", "múa rối nước",
            "bánh xèo", "vịnh Hạ Long", "trang phục H'Mông", "cầu tre", "lễ hội đua thuyền"]
NAMES = ["phố cổ Hội An", "thác Bản Giốc", "Văn Miếu Quốc Tử Giám", "chùa Một Cột",
         "cầu Rồng Đà Nẵng", "lăng Minh Mạng", "hồ Ba Bể", "hang Phong Nha",
         "ruộng bậc thang Mù Cang Chải", "chợ nổi Cái Răng"]
OUTSIDE = ["tàu vũ trụ trên sao Hỏa", "chim cánh cụt trên băng Nam Cực",
           "robot trên mặt trăng", "khủng long thật đang chạy trong thành phố",
           "người trượt tuyết ở dãy Alps", "tháp Eiffel ở Paris", "tượng Nữ thần Tự do",
           "kim tự tháp Ai Cập", "gấu Bắc Cực trên băng", "cầu Cổng Vàng ở San Francisco",
           "sân vận động bóng bầu dục Mỹ", "xe đua trên đường đua Monaco",
           "bên trong trạm vũ trụ quốc tế", "núi lửa trên sao Kim", "quái vật đang bay",
           "một bức ảnh hoàn toàn trống màu trắng", "logo công ty công nghệ ở Silicon Valley",
           "lâu đài băng ở Bắc Âu", "cực quang ở Iceland", "hẻm núi Grand Canyon"]


def unaccented(text):
    return "".join(char for char in unicodedata.normalize("NFD", text.replace("đ", "d"))
                   if not unicodedata.combining(char))


def draft_queries():
    rows = []
    for group, topics in (("objects", OBJECTS), ("scenes", SCENES),
                          ("attributes", ATTRIBUTES), ("spelling", SPELLING),
                          ("names", NAMES), ("outside", OUTSIDE)):
        for i, topic in enumerate(topics):
            variants = [topic, f"Tôi muốn tìm ảnh {topic}"]
            if group == "spelling":
                variants = [topic, unaccented(topic), f"Tìm ảnh {unaccented(topic)}"]
            elif group == "names":
                variants.append(f"Hình ảnh về {topic}")
            elif group == "outside":
                variants = [topic]
            for variant, query in enumerate(variants, 1):
                rows.append({"query_id": f"{group}-{i + 1:02d}-{variant}",
                             "intent_id": f"{group}-{i + 1:02d}",
                             "split": "development" if i % 2 == 0 else "held-out",
                             "group": group, "query": query, "answerable": None,
                             "draft": True})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/evaluation/queries-v1.jsonl"))
    args = parser.parse_args()
    rows = draft_queries()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Prepared {len(rows)} draft queries at {args.output}; "
          "confirm answerability before scoring")


if __name__ == "__main__":
    main()
