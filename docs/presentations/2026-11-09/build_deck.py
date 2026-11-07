"""Build editable PPTX, matching PDF, speaker notes and review images.

Run from the repo root with python-pptx, reportlab and Pillow installed.
The same layout primitives generate both formats; source data stays local.
"""
from __future__ import annotations

import io
import json
import math
import textwrap
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ASSETS = HERE / "assets"
EVIDENCE = HERE / "evidence"
W, H = 1280, 720
P = 0.75
BG, INK, MUTED = "F4F2EC", "142B31", "52686D"
TEAL, ORANGE, LINE, WHITE = "087F73", "C45C32", "D9E0DA", "FFFFFF"
PALE, DARK = "E3EEE8", "102A30"
FONT = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
pdfmetrics.registerFont(TTFont("Noto", FONT))
pdfmetrics.registerFont(TTFont("Noto-Bold", FONT_BOLD))
pdfmetrics.registerFont(TTFont("Fallback", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("Fallback-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))


def font_runs(content, bold=False):
    primary = "Noto-Bold" if bold else "Noto"
    fallback = "Fallback-Bold" if bold else "Fallback"
    supported = pdfmetrics.getFont(primary).face.charWidths
    runs = []
    for char in content:
        font = primary if ord(char) in supported else fallback
        if runs and runs[-1][0] == font:
            runs[-1] = (font, runs[-1][1] + char)
        else:
            runs.append((font, char))
    return runs


def text_width(content, size, bold=False):
    return sum(pdfmetrics.stringWidth(s, f, size) for f,s in font_runs(content,bold))

SOURCES = {
    "ir": "https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-unranked-retrieval-sets-1.html",
    "rank": "https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-ranked-retrieval-results-1.html",
    "qdrant": "https://qdrant.tech/documentation/operations/optimize/",
    "agents": "https://www.anthropic.com/research/building-effective-agents",
    "dataset": "https://www.kaggle.com/datasets/blackcubo/vietnamese-culture-vqa-dataset",
}

SLIDES = [
    {
        "title": "Tìm ảnh theo nhu cầu,\nkhông chỉ theo tên.", "time": 40, "short": 25,
        "notes": "VisualSeek là dự án tìm kiếm ảnh trên corpus văn hóa Việt Nam. Mục tiêu là cho phép người dùng tìm bằng tên, mô tả tự nhiên hoặc ảnh tham chiếu. Ví dụ, một người chuẩn bị bài giảng có thể cần ảnh nghệ nhân hướng dẫn trẻ làm gốm nhưng không biết tên ảnh hay làng nghề. Cái khó nằm ở khoảng cách giữa một ảnh cùng chủ đề và một ảnh thực sự đáp ứng yêu cầu. Khi thêm màu sắc, số lượng, quan hệ hoặc điều kiện loại trừ, chỉ tìm ảnh giống về ngữ nghĩa là chưa đủ. Hôm nay em muốn trình bày phần đã làm được và xin góp ý về cách đánh giá cùng hướng phát triển.",
        "sources": ["README.md", "docs/contextual-image-search.md", SOURCES["dataset"]],
    },
    {
        "title": "Pipeline đã chạy trên toàn bộ corpus", "time": 60, "short": 35,
        "notes": "Hiện dữ liệu có sáu nhóm văn hóa gồm ẩm thực, kiến trúc, phong cảnh, lễ hội, trang phục và thủ công mỹ nghệ. Sau khử trùng lặp bằng hash, corpus có 15.423 asset. Index hiện cho phép tìm kiếm 15.411 asset; 12 asset được cách ly do vấn đề dữ liệu đã xác nhận. Em đã có quy trình chuẩn bị dữ liệu, tạo hai loại vector ảnh và metadata bằng SigLIP2 768 chiều, chạy embedding trên Kaggle và lưu artifact trên Hugging Face. Phía ứng dụng đã có giao diện, API và các chế độ keyword, semantic, hybrid. Router, cache, planner và reranker tùy chọn cũng đã có. Đây là bằng chứng hệ thống hoạt động; còn kết luận về chất lượng tổng thể cần tập đánh giá có nhãn.",
        "sources": ["data/processed/culture-search/report.json", "data/embeddings/culture-labels-v1/manifest.json", "data/embeddings/culture-labels-v1/label-repair-report.json"],
    },
    {
        "title": "Chọn luồng xử lý theo truy vấn", "time": 65, "short": 40,
        "notes": "Khi người dùng nhập truy vấn, hệ thống định tuyến trước. Với tên hoặc tag rõ ràng, lookup local có thể trả kết quả mà không cần encoder hay LLM. Mô tả thông thường đi qua hybrid local. Hybrid kết hợp ba kênh: query so với vector ảnh, query so với vector metadata và đối sánh từ khóa. RRF hợp nhất thứ hạng của các kênh thay vì cộng trực tiếp các score khác thang đo. Với yêu cầu cần suy luận, nhánh LLM lập kế hoạch rồi dùng cùng retriever; rerank chỉ chạy khi được bật và có ứng viên. Tên không dấu nhập nhằng có thể được hỏi lại trước retrieval. Backend hiện vẫn là NumPy exact trên vector local. Hugging Face phân phối artifact; Qdrant chưa nằm trong search path. Cấu hình trên slide là cấu hình API được đọc khi chụp minh họa.",
        "sources": ["services/api/src/visualseek_api/application/search.py", "packages/retrieval/src/visualseek_retrieval/hybrid.py", "evidence/status.json"],
    },
    {
        "title": "Kết quả quan sát ≠ benchmark chất lượng", "time": 60, "short": 40,
        "notes": "Đây là ba ảnh chụp từ giao diện hiện tại, kèm response API được lưu để đối chiếu. Bánh chưng minh họa tìm theo tên, ruộng bậc thang minh họa mô tả trực tiếp, còn áo dài đỏ không đội nón minh họa yêu cầu nhiều điều kiện. Chúng ta cần mở ảnh và kiểm tra từng điều kiện thay vì chỉ đọc tên hoặc score. Bộ lọc loại trừ hiện dựa vào tên và tag; khi rerank dùng annotation, model cũng chỉ biết nội dung có trong chú thích. Có hỗ trợ gửi pixel cho model phù hợp, nhưng em chưa dùng kết quả đó để khẳng định toàn bộ điều kiện được xác minh. Bộ đánh giá có 200 truy vấn, song chưa có nhãn relevance và answerable được xác nhận. Vì vậy em chưa công bố recall và chưa kết luận hybrid tốt hơn semantic. Nếu một lần gọi LLM thất bại, ảnh local dự phòng sẽ được ghi rõ trên slide và trong evidence.",
        "sources": ["evidence/capture-summary.json", "data/evaluation/fusion-pool-labels-v1.json", "docs/search-fusion-setup.md"],
    },
    {
        "title": "Đo đúng chỉ số, ở đúng tầng", "time": 90, "short": 55,
        "notes": "Latency trả lời người dùng chờ bao lâu. p50 là phân vị giữa, p95 là mức mà khoảng 95 phần trăm request không vượt quá trong tập đo. Em sẽ đo cả API lẫn trải nghiệm hiển thị ảnh, và tách thời gian routing, encoding, retrieval, planning, rerank. Recall trả lời hệ thống thu hồi được bao nhiêu trong tổng số ảnh đúng. Ví dụ có 20 ảnh đúng trong corpus, top 10 lấy được 6 ảnh đúng: Recall@10 là 6 chia 20 bằng 30 phần trăm; Precision@10 là 6 chia 10 bằng 60 phần trăm. Ví dụ này chỉ minh họa, không phải kết quả dự án. Precision và nDCG hữu ích cho danh sách cuối mà người dùng thấy. Recall thường được đo offline với tập đáp án đủ tin cậy; không tính được recall thật cho mỗi query mới chỉ từ score. Khi thử ANN, ta còn đo độ trùng top-K với exact search. ANN recall tốt chỉ cho biết giữ được hàng xóm vector, không bảo đảm ảnh đúng theo yêu cầu. HitRate chỉ cần có một ảnh đúng, không thay thế recall. Runner hiện có HitRate, Precision, nDCG và chỉ số từ chối trên pool; pool chưa đầy đủ không cho phép công bố corpus recall.",
        "sources": [SOURCES["ir"], SOURCES["rank"], "packages/evaluation/src/visualseek_evaluation/relevance.py"],
    },
    {
        "title": "Tối ưu từ phần đang tốn tài nguyên", "time": 70, "short": 45,
        "notes": "Số đo local cho thấy ảnh gốc chiếm khoảng 6,51 GiB, còn hai ma trận vector float32 khoảng 90,4 MiB. Hai con số này chỉ là các thành phần cụ thể, chưa phải toàn bộ dung lượng dự án. Còn có metadata, model cache, checkpoint và các bản artifact. Storage nên được tách thành ảnh gốc theo hash, thumbnail cho hiển thị, metadata và vector liên kết bằng asset ID. Khi triển khai có thể dùng object storage và CDN; phần này hiện chưa được tích hợp. Về latency, router và cache đã có, bước tiếp theo là warmup và profiling để xác định thời gian đang nằm ở encoder, retrieval hay LLM. Cần tách cold với warm, cache hit với miss và các mức đồng thời. Chỉ thử ANN hoặc quantization khi có nhu cầu đã đo được, so với exact baseline. Nén biểu diễn vector có thể giảm bộ nhớ phần đó nhưng không đồng nghĩa tổng disk giảm cùng tỷ lệ, đặc biệt nếu vẫn giữ bản float32 để rescore.",
        "sources": ["evidence/facts.json", "scripts/benchmark_text_search.py", SOURCES["qdrant"]],
    },
    {
        "title": "Agent search: tìm, kiểm tra, rồi quyết định", "time": 60, "short": 40,
        "notes": "Hệ thống hiện có router, planner, retrieval và rerank theo luồng định sẵn. Agent search là hướng để model quyết định bước tiếp theo dựa trên kết quả thực tế: đổi truy vấn, tìm tiếp, kiểm tra ảnh hoặc hỏi người dùng. Ví dụ tìm riêng ảnh làm gốm và ảnh dệt thổ cẩm cần hai nhóm kết quả; người lớn hướng dẫn trẻ làm gốm là một cảnh có nhiều điều kiện, không phải hai nhu cầu độc lập. Một thử nghiệm nhỏ có thể cho phép tối đa hai vòng retrieval trong corpus, có điều kiện dừng và hỏi rõ khi nhập nhằng. Agent không thay thế index và cũng không tự sửa được nhãn sai hoặc ảnh thiếu. Em muốn đánh giá mức đáp ứng điều kiện, coverage từng nhóm và chất lượng ranking, đồng thời ghi latency, số lần gọi model và chi phí. Chỉ mở rộng nếu phần chất lượng tăng thêm xứng đáng với chi phí và thời gian chờ.",
        "sources": [SOURCES["agents"], "services/api/src/visualseek_api/application/search.py"],
    },
    {
        "title": "Xin góp ý để chọn bước tiếp theo", "time": 35, "short": 20,
        "notes": "Em đề xuất ưu tiên hoàn thiện benchmark trước, dùng nhãn để xác định lỗi chất lượng chính rồi mới chọn cách sửa. Sau đó em sẽ tối ưu storage và latency trong giới hạn chất lượng đã đo, và thử agent trên nhóm truy vấn khó. Em muốn xin góp ý ba điểm: bộ truy vấn và cách chấm đã phản ánh đúng bài toán chưa; nên ưu tiên metadata, retrieval hay kiểm chứng điều kiện bằng pixel; và trường hợp nào cần agent để tạo giá trị rõ ràng. Các tiêu chí và ngưỡng latency hoặc recall sẽ được chốt dựa trên baseline cùng yêu cầu sử dụng, thay vì đặt một con số chưa có căn cứ.",
        "sources": ["docs/search-fusion-setup.md", "packages/evaluation/src/visualseek_evaluation/relevance.py"],
    },
]


def collect_facts():
    config = {}
    for line in (ROOT / ".env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key in {"EMBEDDING_ARTIFACT_DIR", "SEARCH_LLM_ENABLED", "SEARCH_LLM_RERANK", "SEARCH_ROUTING_MODE", "SEARCH_RERANK_EVIDENCE", "SEARCH_RELEVANCE_MODE"}:
            config[key] = value
    artifact = ROOT / config["EMBEDDING_ARTIFACT_DIR"]
    rows = [json.loads(s) for s in (artifact / "assets.jsonl").read_text().splitlines() if s]
    image_files = [p for p in (ROOT / "data/raw/vietnamese-culture-vqa").rglob("*") if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}]
    pool = json.loads((ROOT / "data/evaluation/fusion-pool-labels-v1.json").read_text())
    facts = {"measured_at": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(), "config": config,
             "artifact": str(artifact.relative_to(ROOT)), "assets": len(rows),
             "searchable": sum(not r.get("search_excluded") for r in rows),
             "excluded": sum(bool(r.get("search_excluded")) for r in rows),
             "dimension": json.loads((artifact / "manifest.json").read_text())["dimension"],
             "raw_image_files": len(image_files), "raw_image_bytes": sum(p.stat().st_size for p in image_files),
             "vector_bytes": sum((artifact / n).stat().st_size for n in ("vectors.npy", "metadata_vectors.npy")),
             "metadata_bytes": (artifact / "assets.jsonl").stat().st_size,
             "evaluation_queries": len(pool["samples"]),
             "answerable_confirmed": sum(type(s["answerable"]) is bool for s in pool["samples"]),
             "judgments_confirmed": sum(type(v) is int for s in pool["samples"] for v in s["judgments"].values()),
             "storage_scope": "logical file bytes; not allocated disk, RAM, whole repo, backup or model cache"}
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2))
    return facts, {r["asset_id"]: r for r in rows}


class Deck:
    def __init__(self):
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(13.333333), Inches(7.5)
        self.prs.core_properties.title = "VisualSeek — Hiện trạng, đánh giá và hướng phát triển"
        self.prs.core_properties.subject = "General idea / Mentor review / 09.11.2026"
        self.prs.core_properties.author = "VisualSeek"
        self.prs.core_properties.language = "vi-VN"
        self.pdf = canvas.Canvas(str(HERE / "VisualSeek-General-Idea.pdf"), pagesize=(W * P, H * P))
        self.pdf.setTitle(self.prs.core_properties.title)
        self.pdf.setAuthor("VisualSeek")
        self.boxes = []

    def new(self, number, dark=False):
        self.slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        self.number = number
        self.dark = dark
        self.rect(0, 0, W, H, DARK if dark else BG)
        self.text("VISUALSEEK", 52, 28, 250, 18, bold=True, color="A6CEC1" if dark else TEAL)
        self.text("GENERAL IDEA  /  09.11.2026", 890, 30, 338, 14, color="A7BDBD" if dark else MUTED)
        self.line(52, 668, 1228, 668, "3D565A" if dark else LINE)
        self.text("Hiện trạng → Đánh giá → Hướng phát triển", 52, 681, 900, 13, color="A7BDBD" if dark else MUTED)
        self.text(f"{number:02d} / 08", 1150, 681, 90, 13, color="A7BDBD" if dark else MUTED)

    def rect(self, x, y, w, h, fill, radius=0, stroke=None):
        shape = self.slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
                                           Inches(x / 96), Inches(y / 96), Inches(w / 96), Inches(h / 96))
        shape.fill.solid(); shape.fill.fore_color.rgb = RGBColor.from_string(fill)
        shape.line.fill.background() if not stroke else None
        if stroke: shape.line.color.rgb = RGBColor.from_string(stroke)
        if radius:
            shape.adjustments[0] = min(0.12, radius / min(w, h))
        self.pdf.setFillColor(colors.HexColor("#" + fill))
        self.pdf.setStrokeColor(colors.HexColor("#" + (stroke or fill)))
        if radius: self.pdf.roundRect(x * P, (H-y-h) * P, w * P, h * P, radius * P, fill=1, stroke=int(bool(stroke)))
        else: self.pdf.rect(x * P, (H-y-h) * P, w * P, h * P, fill=1, stroke=int(bool(stroke)))

    def line(self, x1, y1, x2, y2, color=LINE, width=1.5):
        from pptx.enum.shapes import MSO_CONNECTOR
        s = self.slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1/96), Inches(y1/96), Inches(x2/96), Inches(y2/96))
        s.line.color.rgb = RGBColor.from_string(color); s.line.width = Pt(width * P)
        self.pdf.setStrokeColor(colors.HexColor("#" + color)); self.pdf.setLineWidth(width*P)
        self.pdf.line(x1*P,(H-y1)*P,x2*P,(H-y2)*P)

    def arrow(self, x1, y1, x2, y2, color=TEAL):
        self.line(x1,y1,x2,y2,color,2)
        angle = math.atan2(y2-y1,x2-x1)
        for turn in (-0.55,0.55):
            self.line(x2,y2,x2-10*math.cos(angle+turn),y2-10*math.sin(angle+turn),color,2)

    def text(self, content, x, y, w, size=26, color=INK, bold=False, leading=1.32):
        name = "Noto-Bold" if bold else "Noto"
        lines = []
        for paragraph in str(content).split("\n"):
            line = ""
            for word in paragraph.split():
                candidate = (line + " " + word).strip()
                if line and text_width(candidate,size,bold) > w-3:
                    lines.append(line); line = word
                else: line = candidate
            lines.append(line)
        for i, line in enumerate(lines):
            yy = y+i*size*leading
            box = self.slide.shapes.add_textbox(Inches(x/96), Inches(yy/96), Inches((w+4)/96), Inches(size*1.48/96))
            tf = box.text_frame
            tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
            tf.word_wrap = False
            p = tf.paragraphs[0]
            p.space_before = p.space_after = Pt(0)
            offset = 0
            for font, segment in font_runs(line,bold):
                run = p.add_run(); run.text = segment
                run.font.name = "Noto Sans" if font.startswith("Noto") else "DejaVu Sans"
                run.font.size = Pt(size * P); run.font.bold = bold
                run.font.color.rgb = RGBColor.from_string(color)
                self.pdf.setFont(font,size*P); self.pdf.setFillColor(colors.HexColor("#"+color))
                self.pdf.drawString((x+offset)*P,(H-yy-size*1.06)*P,segment)
                offset += pdfmetrics.stringWidth(segment,font,size)
            self.boxes.append({"slide":self.number,"text":line,"x":x,"y":yy,"w":w,"h":size*1.48})
        return y+len(lines)*size*leading

    def photo(self, path, x,y,w,h):
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im = ImageOps.fit(im, (int(w*2),int(h*2)), method=Image.Resampling.LANCZOS)
            data=io.BytesIO(); im.save(data,format="JPEG",quality=90); data.seek(0)
            self.slide.shapes.add_picture(data,Inches(x/96),Inches(y/96),width=Inches(w/96),height=Inches(h/96))
            data.seek(0); self.pdf.drawImage(ImageReader(data),x*P,(H-y-h)*P,w*P,h*P)

    def contain(self,path,x,y,w,h):
        with Image.open(path) as im: iw,ih=im.size
        scale=min(w/iw,h/ih); ww,hh=iw*scale,ih*scale
        self.photo(path,x+(w-ww)/2,y+(h-hh)/2,ww,hh)

    def heading(self, kicker,title,subtitle=None):
        self.text(kicker.upper(),52,90,1176,15,TEAL,True)
        self.text(title,52,121,1176,39,INK,True)
        if subtitle:self.text(subtitle,52,181,1176,21,MUTED)

    def card(self,x,y,w,h,label,body,accent=TEAL):
        self.rect(x,y,w,h,WHITE,12)
        self.rect(x,y,5,h,accent)
        yy=self.text(label,x+22,y+19,w-44,26,accent,True)
        self.text(body,x+22,yy+13,w-44,23,INK)

    def finish(self,meta):
        self.slide.notes_slide.notes_text_frame.text = (
            f"{meta['title'].replace(chr(10),' ')} — {meta['time']} giây (bản 5 phút: {meta['short']} giây)\n\n"
            + meta["notes"] + "\n\nNguồn / đối chiếu:\n"+"\n".join(meta["sources"]))
        self.pdf.showPage()

    def save(self):
        self.prs.save(HERE / "VisualSeek-General-Idea.pptx")
        self.pdf.save()
        (EVIDENCE/"layout-boxes.json").write_text(json.dumps(self.boxes,ensure_ascii=False,indent=2))


def main():
    ASSETS.mkdir(exist_ok=True)
    facts,rows=collect_facts()
    summaries=json.loads((EVIDENCE/"capture-summary.json").read_text())
    captures=[json.loads((EVIDENCE/f"{s['slug']}.json").read_text()) for s in summaries]
    def asset_photo(i):
        items=captures[i]["response"].get("items",[])
        if not items: return asset_photo(0)
        return ROOT/"data/raw/vietnamese-culture-vqa"/rows[items[0]["id"]]["image_path"]
    d=Deck()

    # 1: editorial cover, three real corpus images.
    d.new(1,True)
    d.text("TÌM KIẾM ẢNH VĂN HÓA VIỆT NAM",52,104,660,17,"A6CEC1",True)
    d.text("Tìm ảnh theo nhu cầu,\nkhông chỉ theo tên.",52,151,730,53,WHITE,True,1.22)
    d.text("Tên  /  Mô tả tự nhiên  /  Ảnh tham chiếu",55,312,670,23,"A7BDBD")
    d.rect(52,385,695,150,"1D3C41",14)
    d.text("“Nghệ nhân hướng dẫn trẻ làm gốm”",75,405,643,28,WHITE,True)
    d.text("Đúng chủ đề → Đúng thuộc tính → Đúng quan hệ",75,475,640,21,"A6CEC1")
    d.text("Đã có pipeline • Cần benchmark • Xin góp ý hướng phát triển",52,579,720,20,"A7BDBD")
    for i,(xx,yy,ww,hh) in enumerate([(806,100,180,220),(1002,100,226,360),(806,338,180,272)]):
        d.photo(asset_photo(i),xx,yy,ww,hh)
    d.text("Ảnh từ corpus hiện tại",1002,481,226,15,"A7BDBD")
    d.finish(SLIDES[0])

    # 2: scope and implementation proof.
    d.new(2); d.heading("01 / Hiện trạng","Pipeline đã chạy trên toàn bộ corpus")
    for x,num,label in [(52,"15.423","asset sau khử trùng lặp"),(452,"15.411","asset được tìm kiếm"),(852,"768","chiều / vector SigLIP2")]:
        d.rect(x,211,376,140,WHITE,12); d.text(num,x+24,225,330,54,TEAL,True); d.text(label,x+24,300,330,21,MUTED)
    d.card(52,380,568,179,"Dữ liệu & embedding","6 nhóm văn hóa • hash khử trùng lặp\nVector ảnh + metadata • Kaggle → HF")
    d.card(644,380,584,179,"Ứng dụng & retrieval","Web + API • keyword / semantic / hybrid\nRouter, cache, planner / rerank tùy chọn")
    d.rect(52,582,1176,55,PALE,8)
    d.text("12 asset cách ly. Pipeline hoạt động; chất lượng tổng thể cần benchmark có nhãn.",70,594,1140,21,TEAL,True)
    d.finish(SLIDES[1])

    # 3: correct branch topology: lexical bypasses dense retrieval.
    d.new(3); d.heading("02 / Kiến trúc","Chọn luồng xử lý theo truy vấn")
    d.rect(52,267,152,76,INK,10); d.text("Truy vấn",69,285,125,25,WHITE,True)
    d.arrow(204,305,239,305); d.rect(239,267,140,76,PALE,10); d.text("Router",266,285,110,25,TEAL,True)
    for yy,label,fill in [(210,"Tên / tag → Lookup local",WHITE),(320,"Mô tả → Hybrid local",WHITE),(430,"Suy luận → LLM lập plan",WHITE)]:
        d.rect(428,yy,361,76,fill,10); d.text(label,448,yy+22,323,22,INK,True)
    d.line(401,248,401,468,TEAL,2); d.line(379,305,401,305,TEAL,2)
    for yy in [248,358,468]: d.arrow(401,yy,428,yy)
    d.rect(846,320,382,130,PALE,10)
    d.text("Ảnh + Metadata + Từ khóa",866,338,340,24,TEAL,True)
    d.text("Hợp nhất thứ hạng RRF",866,392,340,24,INK)
    d.arrow(789,358,846,358); d.line(808,468,1037,468,TEAL,2); d.arrow(1037,468,1037,450)
    d.rect(846,210,382,76,WHITE,10); d.text("Kết quả lookup trực tiếp",866,232,340,22,INK)
    d.arrow(789,248,846,248)
    d.rect(846,510,382,70,INK,10); d.text("Rerank* → Kết quả",868,530,338,24,WHITE,True)
    d.arrow(1170,450,1170,510)
    d.text("*Chỉ nhánh LLM khi được bật; có thể hỏi rõ trước retrieval.",52,544,735,19,MUTED)
    d.text("NumPy exact local • HF lưu artifact • Qdrant chưa phục vụ search",52,601,1176,23,TEAL,True)
    d.finish(SLIDES[2])

    # 4: real screenshots with route labels, no invented judgments.
    d.new(4); d.heading("03 / Minh họa hiện tại","Kết quả quan sát ≠ benchmark chất lượng")
    labels=[("TÊN RÕ RÀNG","bánh chưng"),("MÔ TẢ TRỰC TIẾP","ruộng bậc thang xanh ở vùng núi"),("NHIỀU ĐIỀU KIỆN","áo dài đỏ, không đội nón")]
    for i,(label,query) in enumerate(labels):
        x=52+i*400; s=summaries[i]
        d.rect(x,206,376,355,WHITE,12)
        d.text(label,x+15,220,346,14,TEAL,True)
        d.text(query,x+15,249,346,22,INK,True)
        p=ASSETS/f"{s['slug']}-first.png"
        if p.exists():d.contain(p,x+15,317,346,191)
        else:d.contain(ASSETS/f"{s['slug']}-results.png",x+15,317,346,191)
        route=s.get("route","?"); calls=s.get("llm_calls",0)
        d.text(f"{route} · {calls} lần gọi LLM",x+15,526,346,17,MUTED)
        if s.get("local_fallback"):d.text("Local dự phòng — xem evidence",x+15,548,346,13,ORANGE)
    d.rect(52,580,1176,62,PALE,8)
    d.text(f"{facts['evaluation_queries']} query đã tạo • Chưa chấm nhãn • Chưa công bố recall",70,594,1140,26,TEAL,True)
    d.finish(SLIDES[3])

    # 5: compact metric map + explicit illustrative arithmetic.
    d.new(5); d.heading("04 / Đánh giá","Đo đúng chỉ số, ở đúng tầng")
    col=[52,290,824]; widths=[238,534,404]
    for x,ww,t in zip(col,widths,["CHỈ SỐ","CÂU HỎI CẦN TRẢ LỜI","DÙNG KHI NÀO?"]):
        d.rect(x,204,ww,43,INK);d.text(t,x+13,214,ww-22,16,WHITE,True)
    metric_rows=[
        ("Latency p50 / p95","Request thường / chậm mất bao lâu?","Benchmark & vận hành"),
        ("Recall@K","Thu hồi bao nhiêu trong tất cả ảnh đúng?","Chọn tập ứng viên"),
        ("Precision@K","Bao nhiêu ảnh trả về đáp ứng yêu cầu?","Danh sách cuối"),
        ("nDCG@K","Ảnh phù hợp hơn có đứng cao hơn?","Fusion & rerank"),
        ("ANN Recall@K","Giữ bao nhiêu top-K của exact search?","Thử ANN / nén vector"),
    ]
    for i,row in enumerate(metric_rows):
        yy=247+i*52
        for j,(x,ww,t) in enumerate(zip(col,widths,row)):
            d.rect(x,yy,ww,52,WHITE if i%2==0 else "EDEFE8")
            d.text(t,x+13,yy+13,ww-25,19,TEAL if j==0 else INK,j==0)
    d.rect(52,529,1176,68,PALE,8)
    d.text("Ví dụ minh họa: 20 ảnh đúng, top 10 có 6 ảnh đúng",70,537,1140,22,INK,True)
    d.text("Recall@10 = 6/20 = 30%     |     Precision@10 = 6/10 = 60%",70,568,1140,20,TEAL,True)
    d.text("HitRate ≠ Recall. ANN recall ≠ relevance. Pool chưa đầy đủ ≠ corpus ground truth.",52,618,1176,19,MUTED)
    d.finish(SLIDES[4])

    # 6: storage facts separated from intended changes.
    d.new(6); d.heading("05 / Tối ưu hệ thống","Tối ưu từ phần đang tốn tài nguyên")
    gib=facts['raw_image_bytes']/1024**3; mib=facts['vector_bytes']/1024**2
    d.rect(52,211,352,310,INK,12)
    d.text(f"{gib:.2f}".replace('.',','),75,227,306,64,WHITE,True);d.text("GiB ảnh gốc",75,309,306,25,"A6CEC1")
    d.text(f"{facts['raw_image_files']:,} file".replace(',','.'),75,348,306,20,"A7BDBD")
    d.line(75,393,381,393,"3D565A")
    d.text(f"{mib:.1f} MiB".replace('.',','),75,411,306,38,WHITE,True)
    d.text("hai ma trận vector float32",75,465,306,20,"A7BDBD")
    d.card(428,211,800,144,"Storage","Ảnh theo hash + thumbnail → object storage / CDN\nMetadata, vector và phiên bản liên kết bằng asset ID")
    d.card(428,377,800,144,"Latency","Đo routing → encode → retrieval → LLM / rerank\nWarmup & cache đã có nền tảng; ANN khi có nút thắt")
    d.rect(52,548,1176,90,PALE,8)
    d.text("Đo cold / warm • cache hit / miss • concurrency • API và hiển thị ảnh",70,558,1140,23,TEAL,True)
    d.text("Thử ANN / quantization: so tốc độ, RAM/disk và chất lượng với exact baseline.",70,598,1140,20,INK)
    d.finish(SLIDES[5])

    # 7: bounded agent proposal, distinct from current orchestration.
    d.new(7); d.heading("06 / Hướng mở rộng","Agent search: tìm, kiểm tra, rồi quyết định")
    d.text("HIỆN CÓ: router + planner + retrieval + rerank theo luồng định sẵn",52,195,1176,22,MUTED)
    stages=[("Hiểu yêu cầu",52),("Chọn công cụ tìm",352),("Kiểm tra bằng chứng",652),("Trả / hỏi rõ",952)]
    for i,(label,x) in enumerate(stages):
        d.rect(x,260,276,85,INK if i==3 else WHITE,12)
        d.text(label,x+16,284,244,22,WHITE if i==3 else TEAL,True)
        if i<3:d.arrow(x+276,303,x+300,303)
    d.line(790,345,790,388,TEAL,2);d.line(790,388,490,388,TEAL,2);d.arrow(490,388,490,345)
    d.text("Chưa đủ bằng chứng → đổi query / tìm lại",405,402,630,20,TEAL,True)
    d.card(52,459,568,144,"Thử nghiệm nhỏ","Query khó • Trong corpus • Tối đa 2 vòng retrieval")
    d.card(644,459,584,144,"Giữ nếu có giá trị","Đáp ứng điều kiện ↑  /  latency & chi phí chấp nhận được")
    d.text("Hai nhóm độc lập ≠ một cảnh nhiều điều kiện. Agent vẫn dùng retriever và dữ liệu hiện có.",52,615,1176,19,MUTED)
    d.finish(SLIDES[6])

    # 8: feedback prompts and ordered roadmap.
    d.new(8,True)
    d.text("07 / THẢO LUẬN",52,96,1176,16,"A6CEC1",True)
    d.text("Xin góp ý để chọn bước tiếp theo",52,133,1176,43,WHITE,True)
    questions=[("01","Bộ query và cách chấm relevance đã đúng bài toán?"),
               ("02","Ưu tiên metadata, retrieval hay kiểm chứng bằng ảnh?"),
               ("03","Trường hợp nào cần agent search để tạo giá trị rõ?")]
    for i,(num,q) in enumerate(questions):
        yy=237+i*90
        d.text(num,52,yy,65,30,"A6CEC1",True);d.text(q,138,yy+3,1090,29,WHITE)
        d.line(138,yy+65,1228,yy+65,"3D565A")
    d.rect(52,540,1176,87,"1D3C41",12)
    d.text("Benchmark có nhãn → Sửa lỗi chất lượng → Tối ưu hệ thống → Thử agent",75,555,1130,26,"A6CEC1",True)
    d.text("Chốt ngưỡng chất lượng và latency dựa trên baseline + nhu cầu sử dụng.",75,598,1130,19,"A7BDBD")
    d.finish(SLIDES[7]);d.save()
    write_notes(facts,summaries)
    print(json.dumps({"slides":8,"timing_seconds":sum(s['time'] for s in SLIDES),"short_seconds":sum(s['short'] for s in SLIDES),"files":["VisualSeek-General-Idea.pptx","VisualSeek-General-Idea.pdf","Loi-thoai-va-Hoi-dap.md","Loi-thoai-va-Hoi-dap.pdf"]},ensure_ascii=False))


def write_notes(facts,summaries):
    qa=[
        ("Vì sao dùng hybrid?", "Ba kênh cung cấp tín hiệu bổ sung: pixel, mô tả và tên/từ khóa. Đây là giả thuyết cần ablation trên cùng tập query có nhãn; hiện chưa chứng minh hybrid vượt baseline."),
        ("Vì sao chưa có recall?", "200 query và candidate pool đã được tạo nhưng chưa chấm grade 0/1/2 và answerable. Hơn nữa pooled judgments chưa bảo đảm tìm hết ảnh đúng trong corpus. Có thể báo recall trên tập đáp án giới hạn đã xác nhận nếu mô tả rõ phạm vi; chưa gọi đó là corpus recall."),
        ("Có nên báo HitRate trước?", "Sau khi chấm nhãn có thể báo HitRate@10, Precision@10 và nDCG@10 trên judged pool. HitRate chỉ cho biết có ít nhất một ảnh đúng, nên cần đi cùng precision và mức đáp ứng toàn bộ điều kiện."),
        ("Exact search có nghĩa recall 100% không?", "Exact cho top-K chính xác theo vector và metric được chọn; không bảo đảm semantic relevance. ANN recall so với exact là chỉ số kỹ thuật khác recall theo tập ảnh đúng do con người xác nhận."),
        ("Vì sao không chuyển Qdrant ngay?", "Exact NumPy hiện là baseline trên khoảng 15 nghìn asset. Cần đo nút thắt retrieval và tải trước; vector DB hữu ích cho index, cập nhật và vận hành, nhưng không tự sửa nhãn sai, biểu diễn kém hay điều kiện thị giác bị bỏ sót."),
        ("Storage nên tối ưu chỗ nào?", "Đo riêng ảnh gốc, thumbnail, metadata, vector, model cache, checkpoint và artifact trùng phiên bản. Ảnh gốc local khoảng 6,51 GiB; hai ma trận vector khoảng 90,4 MiB; assets.jsonl hiện khoảng 46 MiB. Quản lý vòng đời và tránh bản sao thừa trước khi kết luận cần nén vector."),
        ("Nén int8 có giảm tổng dung lượng bốn lần?", "Chỉ có thể nói về kích thước biểu diễn vector so với float32 trong điều kiện cụ thể. Tổng dung lượng còn originals, metadata, graph index, backup; nếu giữ float32 để rescore thì tổng disk không giảm cùng tỷ lệ. Phải kiểm tra hỗ trợ dtype: loader hiện yêu cầu float32."),
        ("Latency đang là bao nhiêu?", "Ba lần capture là ví dụ có cấu hình và timing, không phải benchmark tải. Không dùng chúng làm p95/SLO. Cần warmup, tách cache hit/miss và từng route, chạy nhiều query ở concurrency 1/4, ghi lỗi và throughput. Đo API và hiển thị ảnh riêng."),
        ("Làm sao đánh giá phủ định và không có đáp án?", "Chấm bằng pixel cho toàn bộ điều kiện; theo dõi tỉ lệ vi phạm phủ định, false accept ngoài miền và empty rate trên query có đáp án. Không xem thiếu mô tả là bằng chứng không có đối tượng. Relevance gate hiện ở shadow, chưa tự loại chỉ dựa vào ngưỡng chưa hiệu chỉnh."),
        ("Agent khác planner hiện có thế nào?", "Planner hiện tạo plan rồi đi qua luồng cố định. Agent đề xuất chọn hành động tiếp dựa trên kết quả công cụ, có thể tìm lại/kiểm tra/hỏi rõ. Giới hạn thử nghiệm là hai vòng retrieval, trong corpus, có điều kiện dừng; chưa có vòng agent này trong production code."),
        ("Agent có bắt buộc gọi nhiều agent con?", "Không. Thử một agent có bộ công cụ tối thiểu trước. Chỉ thêm thành phần khi chứng minh được cải thiện trên nhóm query khó so với pipeline hiện tại cùng ngân sách."),
        ("Thí nghiệm tiếp theo cụ thể là gì?", "Chấm development trước, chốt config fusion/relevance gate trên development, sau đó đánh giá held-out. So keyword, image-only, metadata-only, RRF và rerank trên cùng artifact. Ghi pipeline signature để tránh dùng nhãn/ngưỡng cho pipeline khác."),
    ]
    md=["# VisualSeek — Lời thoại và hỏi đáp", "", "Buổi góp ý: thứ Hai 09/11/2026. Bộ chính: 8 slide / 8 phút; bản ngắn: 5 phút.","", "## Cách sử dụng", "", "Mở PowerPoint ở Presenter View để xem lời thoại đã nhúng. PDF slide dùng khi cần giữ bố cục; PDF tài liệu này dành cho luyện nói và hỏi đáp. Slide 4 dùng ảnh chụp UI thật; ảnh full và response nằm trong assets/ và evidence/.","", "## Phân bổ thời gian", "", "| Slide | Nội dung | Bản 8 phút | Bản 5 phút |", "|---|---|---:|---:|"]
    for i,s in enumerate(SLIDES,1):md.append(f"| {i} | {s['title'].replace(chr(10),' ')} | {s['time']} giây | {s['short']} giây |")
    md += ["", "Bản 10 phút: giữ bài nói 8 phút, dành thêm 2 phút cho kế hoạch benchmark và trao đổi. Mốc cộng dồn bản chính: 0:40, 1:40, 2:45, 3:45, 5:15, 6:25, 7:25, 8:00.", ""]
    for i,s in enumerate(SLIDES,1):
        md += [f"## Slide {i} — {s['title'].replace(chr(10),' ')}", "", f"Thời lượng: {s['time']} giây; bản ngắn {s['short']} giây.","",s['notes'],"", "Nguồn / đối chiếu:",""]+[f"- {x}" for x in s['sources']]+[""]
    md += ["## Minh họa đã chụp", "", "Cấu hình API thật tại thời điểm capture lưu ở evidence/status.json; cấu hình file .env được đọc lại trong evidence/facts.json, chỉ gồm các trường được cho phép, không có khóa bí mật.", ""]
    for s in summaries:
        md += [f"- **{s['query']}**: route `{s['route']}`, {s['llm_calls']} lần gọi LLM; {s['total']} kết quả. Local dự phòng: {s['local_fallback']}. Top 3: {', '.join(s['top_titles'])}."]
    md += ["", "Ảnh trên slide 4 là ảnh chụp nguyên card đầu tiên trong UI, không phải ảnh tự chọn để đại diện cho top-1. Đây là kiểm tra chức năng và quan sát trực quan; chưa có nhãn độc lập để tính chất lượng. Timing trong response là thời gian của lần capture, không phải benchmark.", "", "## Kế hoạch benchmark để thảo luận", "", "1. Chấm ảnh theo pixel bằng grade 0/1/2, xác nhận answerable; không coi mô tả tự động là ground truth. Rà soát một phần nhãn bởi người thứ hai và giải quyết bất đồng.", "2. Dùng development để tune trọng số/gate; giữ held-out cho đánh giá sau khi khóa cấu hình. Ghi corpus, revision, pipeline signature, model và top-K.", "3. So keyword, image-only, metadata-only, RRF và rerank trên cùng query. Report theo nhóm tên, mô tả, thuộc tính, phủ định, nhập nhằng, nhiều nhóm độc lập và ngoài miền. Bổ sung query khó còn thiếu trong bộ 200 query.", "4. Dùng HitRate/Precision/nDCG trên judged pool; ghi scope rõ. Chỉ báo corpus Recall@K khi có tập đáp án đủ đầy. Không coi ứng viên chưa chấm là không liên quan.", "5. Latency: dùng benchmark_text_search.py với local/auto/forced-LLM, concurrency 1/4 và ít nhất 200 request mỗi cấu hình; tách route, cold/warm, cache hit/miss. Ghi lỗi, throughput và p50/p95; UI tải ảnh đo riêng.", "6. Storage: báo logical disk bytes và RAM riêng. Đo từng thành phần và theo dõi phiên bản/model cache. Thử ANN/quantization với exact oracle, sau đó kiểm tra cả ANN recall lẫn relevance.", "7. Agent: thử tối đa hai vòng retrieval trên cùng nhóm query khó, có ngân sách lời gọi/thời gian; đo full-constraint satisfaction, coverage theo nhóm, ranking, latency và chi phí. Lưu cả ca không cải thiện. Chưa đặt mục tiêu số trước baseline.", "", "## Câu hỏi có thể gặp", ""]
    for q,a in qa:md += [f"### {q}","",a,""]
    md += ["## Kiểm tra trước buổi trình bày", "", "- Mở PPTX trên máy trình chiếu, kiểm tra font Noto Sans; dùng PDF nếu máy thay font.", "- Bật Presenter View; thử màn hình ngoài, tỉ lệ 16:9 và khả năng đọc slide 4/5.", "- Luyện một lượt 8 phút và một lượt 5 phút theo bảng; không đọc toàn bộ tài liệu hỏi đáp.", "- Bản chính dùng ảnh chụp nên có thể trình bày offline. Nếu muốn demo thêm, warmup API và chuẩn bị ảnh dự phòng.", "- Không nói ‘recall cao’, ‘đã hiểu mọi điều kiện’ hoặc ‘Qdrant đang chạy search’ khi chưa có bằng chứng.", "", "## Nguồn kỹ thuật", ""]+[f"- {k}: {v}" for k,v in SOURCES.items()]
    content="\n".join(md)+"\n"
    (HERE/"Loi-thoai-va-Hoi-dap.md").write_text(content)
    (HERE/"slide-content.json").write_text(json.dumps(SLIDES,ensure_ascii=False,indent=2))
    style=ParagraphStyle("body",fontName="Noto",fontSize=10,leading=16,textColor=colors.HexColor("#"+INK),spaceAfter=8)
    h1=ParagraphStyle("heading1",parent=style,fontName="Noto-Bold",fontSize=20,leading=27,spaceBefore=12,spaceAfter=14)
    h2=ParagraphStyle("heading2",parent=style,fontName="Noto-Bold",fontSize=14,leading=21,spaceBefore=13,spaceAfter=10)
    story=[]
    for line in content.splitlines():
        if not line.strip() or line.startswith("|---"):continue
        st=h1 if line.startswith("# ") else h2 if line.startswith("## ") or line.startswith("### ") else style
        txt=line.lstrip("# ") if line.startswith("#") else line
        txt=txt.replace("**","").replace("`","")
        # Keep long URLs and source paths wrappable on the notes page.
        rendered = "".join(escape(segment) if font.startswith("Noto") else f'<font name="{font}">{escape(segment)}</font>' for font,segment in font_runs(txt))
        story.append(Paragraph(rendered,st))
    def footer(c,doc):
        c.setFont("Noto",8);c.setFillColor(colors.HexColor("#"+MUTED));c.drawString(42,25,"VISUALSEEK / LỜI THOẠI VÀ HỎI ĐÁP / 09.11.2026");c.drawRightString(552,25,str(doc.page))
    SimpleDocTemplate(str(HERE/"Loi-thoai-va-Hoi-dap.pdf"),pagesize=(595,842),rightMargin=42,leftMargin=42,topMargin=40,bottomMargin=46,title="VisualSeek — Lời thoại và hỏi đáp").build(story,onFirstPage=footer,onLaterPages=footer)


if __name__=="__main__":main()
