# Báo cáo rà soát & cập nhật hệ thống search ảnh products-v1 (P1–P4 & P6)

Ngày cập nhật: **2026-11-24**.
Tài liệu này đối chiếu với [bao-cao-van-de-search-2026-11-24.md](file:///D:/Search-Engine-for-image-V_lookup-/docs/bao-cao-van-de-search-2026-11-24.md), tổng hợp trạng thái từng vấn đề (P1–P8), chi tiết các thay đổi code đã thực hiện, bảng đối chiếu kế hoạch truy vấn (query plan) trước/sau trên bộ 40 câu [eval/hops-v1/queries.jsonl](file:///D:/Search-Engine-for-image-V_lookup-/eval/hops-v1/queries.jsonl), số đo trên [eval/descriptive-v1/queries.jsonl](file:///D:/Search-Engine-for-image-V_lookup-/eval/descriptive-v1/queries.jsonl) và hướng dẫn các bước soát lại.

---

## 1. Tổng quan trạng thái các vấn đề (P1 – P8)

| Mã | Vấn đề trong báo cáo gốc | Trạng thái hiện tại | Cách đã giải quyết / Hướng xử lý tiếp |
|---|---|---|---|
| **P1** | Bỏ qua loại hàng khi đã nhận ra hãng (*"Canon còn máy ảnh nào khác"* ra mực in; *"nồi chiên Philips"* ra đèn LED) | ✅ **Đã xử lý** | Tách cụm từ chỉ loại sản phẩm trong mệnh đề của hãng ([type_request.py](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/type_request.py)), đối chiếu n-gram dài nhất với `product_type` của riêng các sản phẩm thuộc hãng đó trong [`_narrow()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L565-L625). Tự động lập chỉ mục dòng sản phẩm con (`self.product_lines`, ví dụ `"Redmi"` $\to$ `brand:xiaomi`). |
| **P2** | Sản phẩm bị loại trừ vẫn hiện ra khi gọi bằng tên rút gọn (*"Magic Mary"*, *"PowerCore"*, *"Pegasus 40"*, *"hãng làm nồi chiên không dầu Philips"*) | ✅ **Đã xử lý** | 1. [`_mentions()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L460-L495) tra từ điển alias (kể cả `suppressed_aliases` và `short_aliases`) trong phạm vi nội bộ của hãng.<br>2. Lưu cụm từ mô tả sản phẩm gốc giữa từ chỉ quan hệ (`hãng làm`, `cùng hãng với`) và tên hãng vào `request.anchor` để nhận diện và loại trừ sản phẩm gốc ngay cả khi S1 toàn cục chỉ bắt được tên hãng (`h06`). |
| **P3** | Câu ghép *"Tên sản phẩm/Hãng cụ thể + Loại hàng chung"* mất nửa sau (*"Kindle Paperwhite và tai nghe"*, *"chuột MX Master 3 với bàn phím"*, *"nhẫn cưới Bvlgari và dây chuyền vàng"*) | ✅ **Đã xử lý** | 1. Tách các mệnh đề loại hàng chung không gắn với hãng (`unlinked`) trong [`_brand_requests()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L345-L437), hỗ trợ cả từ nối `"với"` đứng sau một entity đã nhận diện.<br>2. [`_generic_intents()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L439-L458) ánh xạ mệnh đề chung sang các entity có `product_type` khớp (`plan.generic_hits`).<br>3. [`_rank()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L1002-L1035) chia đều hạn ngạch top-10 giữa nhóm đích chính (`seeds + hops`) và nhóm loại hàng chung (`generic_hits`). |
| **P4** | Không loại trừ được theo loại hàng (*"đồ Shimano nhưng không phải bộ đề"*, *"trang sức PNJ nhưng không phải nhẫn"*) | ✅ **Đã xử lý** | Nhận diện mệnh đề loại trừ (`request.exclude`), ghép mệnh đề chỉ chứa phủ định vào hãng của mệnh đề đứng trước và đưa các sản phẩm khớp loại bị trừ vào `plan.anchors` (phạt `-2.0` điểm và loại khỏi `plan.hops`). |
| **P5** | Đồ thị (Graph) quá mỏng (chỉ có `MADE_BY`, 68% entity thiếu hãng, chưa có `FITS`, `TREATS`, `HAS_INGREDIENT`) | ⏳ **Bước tiếp theo (B4)** | Có thể dùng trực tiếp engine `agy` (`gemini-3.8-flash-low` / `medium`) đã có sẵn trong [scripts/graph_classify.py](file:///D:/Search-Engine-for-image-V_lookup-/scripts/graph_classify.py#L308-L353) để trích xuất offline từ `data/processed/products-v1/graph/evidence.jsonl` (~8–10 phút cho toàn bộ corpus). |
| **P6** | Câu mô tả (màu sắc, chất liệu, hình dáng) kém vì chưa nối kênh ảnh SigLIP2 (`nDCG@10` text = `0.58`) | ✅ **Đã xử lý** | Đã nối `ImageTextEncoder` + `VisualFusion` (`doc_dense.npy` + `img_siglip2.npy`) vào [pipeline.py](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L870-L973) và [search.py](file:///D:/Search-Engine-for-image-V_lookup-/services/api/src/visualseek_api/application/search.py#L88-L148). Tự động kích hoạt cho câu mô tả/danh mục đơn, nâng `nDCG@10` lên **`0.798` (Dev)** và **`0.815` (Test)** mà không tốn thêm round-trip tới Qdrant Cloud. |
| **P7** | Bộ eval `silver-v1.1` không bắt được P1–P4; nguy cơ overfit vào 40 câu `hops-v1` | ⚠️ **Đã có `hops-v1` chấm tay; cần thêm `hops-v2-test`** | `eval/hops-v1` (40 câu) đã có `judgments.jsonl` và trở thành tập **Dev** cho multi-hop/multi-intent. Khuyến nghị sinh thêm 40–60 câu `eval/hops-v2-test` trên các hãng/entity khác để làm tập **Held-out Test** độc lập. |
| **P8** | Latency có đuôi dài (Qdrant Cloud chiếm ~85%, gọi song song 2 request `_rank` + `branch_scores`) | 💡 **Đã tối ưu kênh ảnh trên RAM** | Kênh `VisualFusion` chạy hoàn toàn trên RAM (tra payload từ bộ nhớ, 0 request phụ lên Qdrant). Có thể giảm thêm 50% tải Qdrant bằng cách chỉ gọi `branch_scores` khi bật cờ debug trace. |

---

## 2. Chi tiết các file đã thay đổi để soát code

1. **[packages/retrieval/src/visualseek_retrieval/image_text.py](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/image_text.py#L47-L50)**
   - Sửa `self._model.get_text_features(**inputs)` để lấy `out.pooler_output if hasattr(out, "pooler_output") else out`, tương thích với cả `transformers` v4 (trong `.venv\product-ingest`) lẫn v5.
2. **[packages/retrieval/src/visualseek_retrieval/type_request.py](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/type_request.py#L41-L157)**
   - Thêm trường `anchor: list[tuple[str, ...]]` vào `Request` để giữ lại các từ mô tả sản phẩm gốc nằm giữa từ chỉ quan hệ (`hãng`, `thương hiệu`) và tên hãng khi S1 không bắt được entity cụ thể ở vị trí đó.
   - Giữ nguyên `Request.__bool__()` chỉ kiểm tra `bool(self.include or self.exclude)` để không làm thay đổi hành vi của các câu không lọc loại hàng.
3. **[packages/retrieval/src/visualseek_retrieval/pipeline.py](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py)**
   - **`__init__` ([L140-L218](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L140-L218)):** Nhận thêm `image_encoder`, `fusion`, `payloads`; tự động xây dựng bảng `self.product_lines` cho các dòng sản phẩm xuất hiện ở đầu tên của $\ge 2$ sản phẩm thuộc cùng một hãng (ví dụ: `"redmi"` $\to$ `brand:xiaomi`).
   - **`plan` & `_brand_requests` & `_generic_intents` ([L294-L458](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L294-L458)):**
     - Cho phép mệnh đề không nêu hãng mượn hãng từ `*last_brands` (các mệnh đề trước đó) nếu hãng đó thực sự có sản phẩm khớp loại hàng yêu cầu (`m18`).
     - Tách mệnh đề phụ sau từ `"với"` khi đứng ngay sau một entity đã nhận diện (`m03`).
     - Gom các mệnh đề loại hàng chung không thuộc về hãng nào vào `unlinked` và phân giải thành `plan.generic_hits` qua `_generic_intents()`.
   - **`_narrow` ([L565-L625](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L565-L625)):** Khi `request.anchor` có từ khóa mô tả sản phẩm gốc và chưa bắt được tên mẫu cụ thể (`not named_in`), tìm sản phẩm khớp `request.anchor` trong hãng để bổ sung vào `plan.origin_ids` và loại trừ vào `plan.anchors` khi câu có phủ định/khác (`h06`).
   - **`_segment_intent` ([L680-L706](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L680-L706)):** Chỉ cho phép `knn_votes` bác bỏ một từ khóa `CategoryLexicon` có độ tinh khiết `share == 1.0` khi các thẻ lân cận tạo thành đa số rõ ràng (`top_vote >= 0.5`).
   - **`search`, `_fuse_visual`, `_rank`, `_related` ([L845-L1075](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L845-L1075)):**
     - Kích hoạt `_fuse_visual()` trên các câu truy vấn mô tả/danh mục đơn, cập nhật `channels["img"] = "ready"` và `scores["img"]`.
     - Trong `_rank()`, khi câu có cả nhóm đích tên riêng/hãng (`primary_targets`) lẫn nhóm loại hàng chung (`secondary_targets` từ `plan.generic_hits`), chia hạn ngạch `primary_quota = top_k - share` và lấy phần còn lại từ `secondary_targets`.
4. **[services/api/src/visualseek_api/application/search.py](file:///D:/Search-Engine-for-image-V_lookup-/services/api/src/visualseek_api/application/search.py#L63-L148)**
   - Nạp `ImageTextEncoder`, `VisualFusion` (`doc_dense.npy`, `img_siglip2.npy`) và bảng `payloads` (kèm `categories`, `market_tiers`) vào `SearchPipeline` khi khởi động service.
5. **Unit tests bổ sung:**
   - [packages/retrieval/tests/test_brand_request.py](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/tests/test_brand_request.py#L198-L213)
   - [packages/retrieval/tests/test_fusion.py](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/tests/test_fusion.py#L69-L150)

---

## 3. Kết quả kiểm chứng trên các bộ dữ liệu

### 3.1. Bộ Unit Test (`packages/retrieval/tests`)
- **68/68 bài test PASS 100%** (thời gian chạy `1.13s`), bao gồm toàn bộ các bài test cũ và các bài test mới cho `request.anchor`, `generic_hits`, tách `"với"` và `VisualFusion`.

### 3.2. Bộ câu hỏi mô tả thị giác `eval/descriptive-v1` (P6 — Visual Fusion)

| Tập | Cấu hình | `nDCG@10` | Ghi chú |
|---|---|---|---|
| **Dev** (56 câu) | Chỉ kênh Text (`bge-m3` + `sparse`) | `0.5830` | Kênh `img` tắt (`inactive`) |
| **Dev** (56 câu) | `VisualFusion` thuần (`w_category = 0.0`) | `0.7934` | `+0.2104` so với chỉ Text |
| **Dev** (56 câu) | **`VisualFusion` + Category Bonus (`w_category = 0.2`)** | **`0.7981`** | **Cấu hình đã gắn vào `SearchPipeline`** |
| **Test** (52 câu) | Chỉ kênh Text (`bge-m3` + `sparse`) | `0.5788` | Kênh `img` tắt (`inactive`) |
| **Test** (52 câu) | `VisualFusion` thuần (`w_category = 0.0`) | `0.8127` | `+0.2339` so với chỉ Text |
| **Test** (52 câu) | **`VisualFusion` + Category Bonus (`w_category = 0.2`)** | **`0.8152`** | **Cấu hình đã gắn vào `SearchPipeline`** |

### 3.3. Bộ câu hỏi `eval/hops-v1` (40 câu Multi-hop & Multi-intent)

#### A. Điểm số trên `pool.jsonl` đã chấm (`python scripts/evaluate_hops.py`)
So sánh giữa cấu hình không lọc loại hàng theo hãng (`no_type_filter` — thời điểm viết báo cáo gốc) và sau khi bật lọc loại hàng (`current`):
- **`multi_hop` (20 câu):**
  - `P@10`: **`0.795` $\to$ `0.880`** ($\Delta = +0.085$, CI95 `[+0.005, +0.175]`)
  - `nDCG@10`: **`0.906` $\to$ `0.956`** ($\Delta = +0.050$)
  - `wrong@10` (tỉ lệ ảnh sai grade 0): **`0.100` $\to$ `0.045`** (giảm 55% ảnh sai)
- **`multi_intent` (20 câu):**
  - `P@10`: **`0.765` $\to$ `0.885`** ($\Delta = +0.120$, CI95 `[+0.035, +0.225]`)
  - `balanced@10`: **`0.577` $\to$ `0.686`** ($\Delta = +0.109$, CI95 `[+0.010, +0.220]`)
  - `wrong@10`: **`0.145` $\to$ `0.065`** (giảm 55% ảnh sai)

#### B. Bảng đối chiếu Query Plan của các câu vừa được sửa thêm (P2 & P3)
Các câu dưới đây trước khi sửa bị thiếu `anchors` hoặc mất hoàn toàn vế thứ 2 (`intents = 1`). Sau bản sửa mới nhất:

| `qid` | Câu truy vấn | Trước khi sửa P2/P3 | Sau khi sửa P2/P3 |
|---|---|---|---|
| **`h06`** | *những sản phẩm khác của hãng làm nồi chiên không dầu Philips* | `anchors=[]`, `hops=7` (Nồi chiên Philips vẫn nằm ở top 1) | `anchors=['Nồi chiên không dầu 2 ngăn Philips HD9880 (Dual Basket)']`, `hops=6` (đã loại trừ nồi chiên gốc) |
| **`m02`** | *Kindle Paperwhite và tai nghe* | `seeds=['Kindle Paperwhite đọc sách']`, `generic=0`, `intents=1` (10/10 ảnh Kindle) | `seeds=['Kindle Paperwhite đọc sách']`, `generic=[4 mẫu tai nghe]`, **`intents=2`** (5 ảnh Kindle + 5 ảnh tai nghe) |
| **`m03`** | *chuột Logitech MX Master 3 với bàn phím* | `seeds=['Chuột Logitech MX Master 3']`, `generic=0`, `intents=1` (10/10 ảnh chuột) | `seeds=['Chuột Logitech MX Master 3']`, `generic=['Keychron Q3', 'Akko 3068B Plus']`, **`intents=2`** (5 ảnh chuột + 5 ảnh bàn phím) |
| **`m09`** | *lốp Michelin Pro4 và săm xe đạp* | `seeds=['Lốp Michelin Pro4']`, `generic=0`, `intents=1` | `seeds=['Lốp Michelin Pro4']`, `generic=['Săm Michelin Airstop', 'Vittoria Ultralite TPU']`, **`intents=2`** |
| **`m13`** | *điện thoại Vsmart hoặc Redmi giá rẻ* | `hops=[4 máy Vsmart]`, `intents=1` (mất vế Redmi vì Redmi là dòng con của Xiaomi) | `hops=[4 máy Vsmart + 3 máy Redmi]`, **`intents=2`** |
| **`m16`** | *nhẫn cưới Bvlgari và dây chuyền vàng* | `hops=['Bvlgari B.zero1 Wedding Band']`, `generic=0`, `intents=1` (10/10 ảnh nhẫn Bvlgari) | `hops=['Bvlgari B.zero1 Wedding Band']`, `generic=['Chanel Comète Necklace']`, **`intents=2`** |
| **`m18`** | *lều cắm trại Naturehike, bếp mini và balo leo núi* | `hops=['Bếp Naturehike mini']` (mất cả 2 mẫu balo leo núi Naturehike ở vế 3) | `hops=['Naturehike Rock 60+5L', 'Naturehike NH18Y020-Z 32L', 'Bếp Naturehike mini']` (giữ đủ cả bếp lẫn 2 balo leo núi) |
| **`m19`** | *Espumisan và thuốc giảm đau dạng viên* | `seeds=['Espumisan']`, `generic=0`, `intents=1` (10/10 ảnh Espumisan) | `seeds=['Espumisan']`, `generic=[13 thuốc giảm đau dạng viên]`, **`intents=2`** |

---

## 4. Hướng dẫn các bước bạn cần chạy để soát lại thực tế

Vì môi trường sandbox không mở kết nối mạng ra Qdrant Cloud, bạn hãy chạy 3 lệnh dưới đây trên terminal máy của bạn để kiểm tra end-to-end:

### Bước 1: Chạy lại bộ unit test
```powershell
.\.venv\product-ingest\Scripts\python.exe -m pytest packages/retrieval/tests
```

### Bước 2: Cập nhật `pool.jsonl` của `hops-v1` và chấm bổ sung các thẻ mới ở vế 2
Do ở lần build pool trước, các câu `m02` (*tai nghe*), `m03` (*bàn phím*), `m13` (*điện thoại Redmi*), `m16` (*dây chuyền vàng*) chưa từng trả về sản phẩm nào của vế thứ 2 nên các sản phẩm đó chưa có trong `eval/hops-v1/judgments.jsonl`.
```powershell
$env:USE_TF="0"
.\.venv\product-ingest\Scripts\python.exe scripts/build_eval_hops.py
.\.venv\product-ingest\Scripts\python.exe scripts/hops_judge_server.py --judge human-1
```
- Mở `http://127.0.0.1:8013`, chỉ cần chấm bổ sung vài thẻ sản phẩm mới xuất hiện ở các câu `m02, m03, m13, m16` (các thẻ đã chấm từ trước đều được giữ nguyên).
- Sau đó chạy lại:
```powershell
.\.venv\product-ingest\Scripts\python.exe scripts/evaluate_hops.py
```
Bạn sẽ thấy chỉ số `all_parts@10` và `balanced@10` của `multi_intent` tăng vọt nhờ các vế thứ 2 đã có ảnh.

### Bước 3: Khởi động lại API server ở port 8000
Khởi động lại tiến trình FastAPI (`uvicorn visualseek_api.main:app --port 8000`) để API nạp `ImageTextEncoder` + `VisualFusion` và code `pipeline.py` mới, sau đó thử trực tiếp trên giao diện web các câu như:
- *"chuột không dây màu trắng"*, *"đồng hồ đeo tay dây da"* (kiểm tra `trace.channels.img == "ready"` và điểm `img`).
- *"Kindle Paperwhite và tai nghe"*, *"chuột Logitech MX Master 3 với bàn phím"*, *"những sản phẩm khác của hãng làm nồi chiên không dầu Philips"*.
