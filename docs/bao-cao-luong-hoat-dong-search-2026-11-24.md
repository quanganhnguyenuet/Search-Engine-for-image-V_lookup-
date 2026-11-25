# Báo cáo luồng hoạt động hệ thống Search V_lookup (`products-v1`)

Ngày cập nhật: **2026-11-24**.  
Tài liệu này mô tả chi tiết kiến trúc lưu trữ vector (Qdrant Cloud & RAM local), luồng xử lý thống nhất 4 bước (**S1 $\to$ S2 $\to$ S3 $\to$ S4**) trong [`SearchPipeline`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py), cơ chế phối hợp giữa **Text Search**, **Graph Search**, **Category Prior** và **Image Vector Search (Visual Fusion)**, cũng như điều kiện kích hoạt của từng nhánh.

---

## 1. Kiến trúc lưu trữ dữ liệu & Vector (Qdrant Cloud vs. RAM Local)

Hệ thống làm việc trên tập dữ liệu **`products-v1`** gồm **30,198 ảnh sản phẩm** (`doc_texts.jsonl`), được đồng bộ giữa **Qdrant Cloud** và **bộ nhớ RAM của API Server** khi khởi động ([`SearchService.load()`](file:///D:/Search-Engine-for-image-V_lookup-/services/api/src/visualseek_api/application/search.py#L62-L142)).

### 1.1. Trên Qdrant Cloud (Collection `products_v1_images`)
Mỗi ảnh (`asset_id`) tương ứng với 1 point (`UUIDv5(asset_id)`), lưu trữ **cả 3 loại vector** cùng payload đã đánh chỉ mục ([`qdrant_store.py`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/qdrant_store.py#L13-L70), [`index_manifest.json`](file:///D:/Search-Engine-for-image-V_lookup-/data/processed/products-v1/search/index_manifest.json)):

| Tên trường / Vector | Kiểu / Kích thước | Mô hình / Thuật toán | Vai trò |
|---|---|---|---|
| **`doc`** | Dense `1024` chiều (`float32`, Cosine) | `BAAI/bge-m3` | Biểu diễn ngữ nghĩa văn bản của tài liệu ảnh (`page_title`, tên entity, OCR, caption). |
| **`sparse`** | Sparse Vector (`Modifier.IDF`) | Lexical BM25 / Token IDF ([`lexical.py`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/lexical.py)) | Khớp từ khóa chính xác (mã sản phẩm, tên riêng, thuật ngữ chuyên ngành). |
| **`img`** | Dense `768` chiều (`float16`, Cosine) | `google/siglip2-base-patch16-224` (Image Tower) | Biểu diễn đặc trưng thị giác trực tiếp từ pixel ảnh (màu sắc, chất liệu, kiểu dáng, họa tiết) cho đủ 30,198/30,198 points. |
| **Payload Indexes** | `KEYWORD` index | `entity_ids`, `brand_ids`, `categories`, `category_l1s`, `market_tiers`, `domain` | Phục vụ lọc tập đích Graph, lọc theo hãng, chia nhóm `group_by` và cộng điểm FormulaQuery trực tiếp trên Qdrant. |

### 1.2. Trên RAM của API Server (`data/processed/products-v1/search`)
Khi [`SearchService.load()`](file:///D:/Search-Engine-for-image-V_lookup-/services/api/src/visualseek_api/application/search.py#L62-L142) khởi chạy, các artifact sau được nạp một lần vào RAM:
1. **Đồ thị thực thể & thương hiệu (`graph.json`, `identity_review.json`, `short_aliases.json`):** Danh sách entity, brand, các cạnh `MADE_BY` đã qua kiểm duyệt, bảng gộp `SAME_AS` (`canonical`) và danh sách ảnh bị chặn (`blocked_assets`).
2. **Ma trận nhúng thẻ thực thể (`entity_dense.npy`):** Vector `bge-m3` của toàn bộ thẻ thực thể để phục vụ bước S1 Spotting và bầu chọn danh mục kNN (`knn_votes`).
3. **Từ điển & phân loại danh mục (`entity_categories.json`):** Phân loại danh mục (`category`, `category_l1`), phân khúc thời trang (`market_tier: luxury | premium | mass`) và cụm từ chỉ loại sản phẩm (`product_type`).
4. **Bộ mã hóa & Ma trận Visual Fusion ([`fusion.py`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/fusion.py#L155-L168)):**
   - `TextEncoder` (`bge-m3`) và `ImageTextEncoder` (Text Tower của `SigLIP2`).
   - Ma trận `doc_dense.npy` (`30198 × 1024`) và `img_siglip2.npy` (`30198 × 768`).
   - Bảng `payloads` trong bộ nhớ ([`asset_payloads()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/fusion.py#L121-L152)) chứa metadata của cả 30,198 ảnh để khi kênh ảnh trên RAM tìm ra ứng viên mới (không nằm trong tập trả về của Qdrant text), hệ thống vẫn lấy được đầy đủ payload và `categories` mà **không tốn thêm request phụ lên Qdrant Cloud**.

---

## 2. Sơ đồ luồng hoạt động tổng thể (S1 $\to$ S4)

Hệ thống không dùng LLM hay Router tách biệt ở thời điểm truy vấn. Mọi câu truy vấn đều chảy qua **một đường ống duy nhất** trong [`SearchPipeline.search()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L952-L1109):

```mermaid
flowchart TD
    Q["Câu truy vấn (Query)"] --> E1["Mã hóa Text Query bằng bge-m3 (vector 1024d)"]
    E1 --> S1["S1: Entity & Brand Spotting (spotting.py)"]
    
    S1 --> S2["S2: Lập kế hoạch truy vấn & Mở rộng Graph (pipeline.py: plan)"]
    S2 -->|Out of scope / Cần làm rõ / Không có đích| EARLY["Trả về rỗng kèm trạng thái (out_of_scope / clarification / no_target)"]
    
    S2 -->|Hợp lệ| CHECK_FUSION{"Thỏa điều kiện bật\nVisual Fusion (S4)?"}
    
    CHECK_FUSION -->|Có: Câu đơn mô tả / danh mục,\nkhông có Entity/Brand/Tier| E2["Mã hóa Query bằng SigLIP2 Text Tower (img_vector 768d)"]
    CHECK_FUSION -->|Không: Có Entity/Brand/Graph\nhoặc câu ghép nhiều vế| S3
    E2 --> S3
    
    subgraph S3_BLOCK ["S3: Truy xuất & Xếp hạng trên Qdrant (qdrant_store.py)"]
        S3["Tạo Request Prefetch (Dense + Sparse + Graph Prefetch + Category Group Prefetch)"]
        S3 --> QDRANT_MODE{"Câu truy vấn chia nhóm?\n(grouped == True)"}
        QDRANT_MODE -->|Không (grouped = False)| QP["query_points (limit = 50 nếu bật Fusion, ngược lại top_k = 10)"]
        QDRANT_MODE -->|Có (grouped = True)| QPG["query_points_groups (group_by entity_ids hoặc categories, chia đều quota)"]
        QP --> REL["Lọc _related() (loại ảnh lạc đề nếu có tập đích)"]
        QPG --> REL
    end
    
    REL --> BRANCH_S4{"use_fusion == True?"}
    BRANCH_S4 -->|Không| FINAL["Đóng gói Top-10 kết quả + Bảng điểm Trace (channels, scores, timings)"]
    
    subgraph S4_BLOCK ["S4: Visual Fusion trên RAM (fusion.py & _fuse_visual)"]
        BRANCH_S4 -->|Có| CAND["Hợp ứng viên: Top-50 Text từ Qdrant ∪ Top-25 Image ANN từ img_siglip2.npy"]
        CAND --> ZSCORE["Tính điểm: alpha * zscore(text_cos) + (1 - alpha) * zscore(img_cos) (alpha = 0.1)"]
        ZSCORE --> CAT_BONUS["Cộng điểm thưởng danh mục (+0.2 nếu khớp CategoryIntent)"]
        CAT_BONUS --> RERANK["Sắp xếp lại & Lấy Top-10"]
    end
    
    RERANK --> FINAL
```

---

## 3. Chi tiết từng giai đoạn trong Pipeline

### Giai đoạn S1: Nhận diện Thực thể & Thương hiệu ([`spotting.py`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/spotting.py))
1. **Khớp từ điển (Lexical Spotting):** Quét các n-gram trong câu truy vấn (ưu tiên khớp có dấu nếu câu có dấu, hoặc không dấu nếu câu không dấu) đối chiếu với tên chuẩn, `aliases`, `short_aliases` của các `Entity` và `Brand` trong đồ thị.
   - Bỏ qua các từ nằm trong `suppressed_aliases` ở mức toàn cục để tránh bắt nhầm từ đa nghĩa.
   - Giữ nguyên cụm tên sản phẩm có chứa ký tự `&` hoặc `,` (ví dụ: `"lock&lock"`) để không bị cắt nhầm ở các bước sau.
2. **So khớp ngữ nghĩa thẻ thực thể (`entity_scores`):** Nhân vô hướng vector câu truy vấn (`bge-m3`) với `entity_dense.npy`.
3. **Xử lý khi không khớp tên từ điển nào (`not entities and not brands`):**
   - **Nếu có từ khóa nhập nhằng (`spotting.ambiguous`):** Dừng và trả về yêu cầu làm rõ (`status = "clarification"`).
   - **Dense Seed Fallback:** Nếu thẻ thực thể gần nhất có điểm cosine $\ge 0.80$ (`dense_seed_min`) và bỏ xa thẻ thứ hai $\ge 0.03$ (`dense_seed_margin`), nhận thẻ đó làm `dense_seed = True`.
   - **Cổng Out-of-Scope (OOS Gate):** Nếu thẻ thực thể gần nhất có điểm cosine $< 0.48$ (`out_of_scope_max`), đánh dấu `out_of_scope = True` và dừng tìm kiếm (chặn các câu ngoài miền dữ liệu như *"lời bài hát Nối vòng tay lớn"*).

---

### Giai đoạn S2: Mở rộng Đồ thị (Graph), Lọc Loại hàng & Ý định Danh mục ([`pipeline.py: plan()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L250-L355))

Giai đoạn S2 chuyển kết quả của S1 thành đối tượng `Plan` gồm tập đích (`seeds`, `hops`, `generic_hits`), tập loại trừ (`anchors`) và thông tin danh mục (`category`):

1. **Phát hiện từ khóa quan hệ & phủ định:**
   - **Quan hệ hãng (`RELATION_ACCENTED` / `RELATION_FOLDED`):** *"hãng"*, *"thương hiệu"*, *"nhà sản xuất"*, *"brand"*, *"cùng hãng"*.
   - **Loại trừ / Khác (`connectors.has_negation` / `OTHER`):** *"trừ"*, *"không lấy"*, *"không phải"*, *"ngoài ... ra"*, *"khác"*.
2. **Mở rộng qua đồ thị `MADE_BY` (Multi-hop & Brand Expansion):**
   - **Truy vấn quan hệ từ sản phẩm gốc** (VD: *"những sản phẩm khác của hãng làm lốp Maxxis Receptor"* hoặc *"sản phẩm khác của hãng làm nồi chiên không dầu Philips"*):
     - Tìm `brand_id` của sản phẩm gốc (`origin_ids`). Nếu S1 chỉ bắt được tên hãng (*"Philips"*), hệ thống trích cụm từ nằm giữa từ chỉ quan hệ và tên hãng (`request.anchor = "nồi chiên không dầu"`) để tìm đúng sản phẩm gốc trong nội bộ hãng đó.
     - Lấy toàn bộ sản phẩm cùng hãng (`siblings`) đưa vào `plan.hops`, đồng thời đưa sản phẩm gốc vào `plan.anchors` (để loại trừ và phạt điểm).
   - **Truy vấn theo thương hiệu** (VD: *"nồi chiên Philips"*, *"trang sức PNJ nhưng không phải nhẫn"*):
     - Lấy danh sách sản phẩm thuộc hãng qua cạnh `MADE_BY`.
     - Tự động nhận diện **dòng sản phẩm con** (`self.product_lines`, VD: `"Redmi"` $\to$ `brand:xiaomi`).
3. **Thu hẹp theo loại sản phẩm yêu cầu ([`_narrow()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L659-L757) & [`type_request.py`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/type_request.py)):**
   - Tách các cụm từ chỉ loại hàng cần lấy (`include`) và cần loại trừ (`exclude`) trong từng mệnh đề của hãng.
   - Tra từ điển alias nội bộ của riêng hãng đó (`_mentions()`) để nhận ra các tên gọi rút gọn như *"Magic Mary"*, *"Vios"*, *"Pegasus 40"*.
   - Khớp n-gram dài nhất với `product_type` (ưu tiên danh từ chính đứng đầu `head`) của các sản phẩm thuộc hãng:
     - Giữ lại đúng các sản phẩm khớp loại yêu cầu trong `plan.hops`.
     - Các sản phẩm thuộc hãng nhưng khác loại hoặc nằm trong mệnh đề phủ định (`exclude`) được đẩy vào `plan.anchors` (bị loại khỏi tập đích và bị phạt `-2.0` điểm).
     - Nếu loại hàng có trong toàn bộ danh mục nhưng hãng được hỏi không sản xuất loại hàng đó (VD: *"điện thoại Samsung"* khi trong kho không có điện thoại Samsung), đánh dấu `status = "absent"` và `plan.no_target = True` (trả về 0 ảnh thay vì trả về tivi/tủ lạnh Samsung).
4. **Giải quyết câu ghép hỗn hợp "Tên cụ thể/Hãng + Loại hàng chung" ([`_generic_intents()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L500-L519)):**
   - Với các câu như *"Kindle Paperwhite và tai nghe"*, *"chuột Logitech MX Master 3 với bàn phím"*, *"nhẫn cưới Bvlgari và dây chuyền vàng"*:
     - Vế đầu tạo ra `seeds` hoặc `hops`.
     - Vế sau là mệnh đề loại hàng chung không thuộc về hãng ở vế đầu (`unlinked`), được hệ thống đối chiếu với `product_type` toàn danh mục để lấy danh sách entity khớp đưa vào `plan.generic_hits`, tăng số ý định `plan.intents` lên 2.
5. **Nhận diện Ý định Danh mục cho câu không nêu tên thực thể/hãng ([`_category_intent()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L759-L800)):**
   - Tách câu theo từ nối (`"và"`, `","`, `"với"`...) nếu có nhiều vế (VD: *"lốp xe máy, dầu nhớt và vali"*).
   - Kết hợp 2 nguồn cho từng vế:
     1. **Lexicon (`CategoryLexicon`):** Bắt cụm từ khóa danh mục và phân khúc (`luxury`, `premium`, `mass`).
     2. **kNN Votes (`knn_votes`):** Lấy các thẻ thực thể có điểm cosine `bge-m3` gần nhất để bầu chọn danh mục (và kiểm chứng chéo với Lexicon).
   - Nếu câu có $\ge 2$ danh mục khác nhau ở các vế, lưu danh sách vào `plan.category_groups` để chia đều kết quả ở bước S3.

---

### Giai đoạn S3: Truy xuất Hybrid & Chia nhóm trên Qdrant ([`qdrant_store.py`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/qdrant_store.py#L119-L190) & [`pipeline.py: _rank()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L1194-L1276))

1. **Các kênh Prefetch trên Qdrant (`depth = 100` mỗi kênh):**
   - **Kênh 1 — `dense`:** Tìm 100 điểm gần nhất trên vector `doc` (`bge-m3`).
   - **Kênh 2 — `sparse`:** Tìm 100 điểm khớp nhất trên vector từ khóa `sparse` (BM25/IDF).
   - **Kênh 3 — `graph_prefetch` (Chỉ bật khi có `seeds`, `hops` hoặc `generic_hits`):** Tìm 100 điểm trên vector `doc` nhưng có bộ lọc cứng `must: entity_ids IN targets`, đảm bảo mọi thực thể đích từ Graph đều có ứng viên dù nội dung text chung bị xếp thấp.
   - **Kênh 4 — `category_groups` Prefetch (Chỉ bật khi câu có $\ge 2$ nhóm danh mục):** Mỗi danh mục trong `plan.category_groups` được tạo 1 kênh prefetch riêng có filter `categories IN [group]` để không vế nào bị vector trung bình làm mất ứng viên.
2. **Hợp nhất RRF & Công thức điểm `FormulaQuery` trên Qdrant:**
   - Các kênh prefetch được gộp bằng **RRF (Reciprocal Rank Fusion)** lấy top `200` (`depth * 2`), tạo ra điểm nền `$score`.
   - Sau đó Qdrant tính điểm xếp hạng theo công thức tuyến tính:
     $$\text{Score}_{\text{Qdrant}} = \text{RRF} + w_{\text{seed}} \cdot \mathbb{I}(\text{seeds}) + w_{\text{hop}} \cdot \mathbb{I}(\text{hops} \cup \text{generic\_hits}) - w_{\text{anchor}} \cdot \mathbb{I}(\text{anchors}) + w_{\text{cat}} \cdot \mathbb{I}(\text{categories}) + w_{\text{tier}} \cdot \mathbb{I}(\text{tiers})$$
     Trong đó các trọng số mặc định ([`Settings`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L77-L115)):
     - $w_{\text{seed}} = +1.0$ (hoặc $+0.3$ nếu là `dense_seed`)
     - $w_{\text{hop}} = +1.0$ (khi chỉ có `hops`) hoặc $+0.5$ (khi đi kèm `seeds`)
     - $w_{\text{anchor}} = 2.0$ (phạt $-2.0$ điểm cho các thực thể bị loại trừ)
     - $w_{\text{cat}} = +0.2$, $w_{\text{tier}} = +0.2$
3. **Cơ chế truy vấn đơn (`grouped = False`) vs. Chia nhóm đa đích (`grouped = True`):**
   - **`grouped = False` (Câu tìm kiếm 1 thực thể đơn, hoặc câu mô tả / danh mục đơn):**
     - Gọi `client.query_points()`. Nếu chuẩn bị chạy Visual Fusion (S4), lấy `limit = 50` ứng viên (`text_candidates = 50`); ngược lại lấy `limit = 10` (`top_k`).
   - **`grouped = True` (Khi `plan.intents >= 2`, hoặc có `plan.hops`, `plan.generic_hits`, hoặc `len(plan.category_groups) >= 2`):**
     - Gọi `client.query_points_groups()` để tránh hiện tượng một sản phẩm có nhiều ảnh chiếm hết 10 vị trí đầu:
       - **Câu đa danh mục không nêu tên riêng** (VD: *"lốp xe máy, dầu nhớt và vali"*): Chia nhóm theo `group_by="categories"`, mỗi danh mục lấy $\lfloor 10 / k \rfloor$ ảnh.
       - **Câu hỗn hợp Tên cụ thể/Hãng + Loại hàng chung** (VD: *"Kindle Paperwhite và tai nghe"*): Chia hạn ngạch công bằng giữa nhóm đích chính (`primary_targets = seeds + hops`, lấy 5 ảnh) và nhóm loại hàng chung (`secondary_targets = generic_hits`, lấy 5 ảnh).
       - **Câu Multi-hop / Thương hiệu / Nhiều thực thể cụ thể:** Chia nhóm theo `group_by="entity_ids"`, gộp các nhóm trùng thực thể chuẩn (`canonical`) qua hàm [`merge_groups()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L1308-L1339) với số ảnh tối đa mỗi sản phẩm là $\max(2, \lceil 10 / \text{số sản phẩm đích} \rceil)$.
4. **Lọc chống độn ảnh sai ([`_related()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L1278-L1302)):**
   - Khi câu truy vấn đã có tập thực thể đích (`targets`) và bật `fill_related_only = True`:
     - Với câu hỏi về hãng hoặc quan hệ (`plan.hops` khác rỗng): **Chỉ giữ lại ảnh của đúng các thực thể trong tập đích** (nếu hãng chỉ có 4 ảnh đúng trong kho thì trả về đúng 4 ảnh, không độn ảnh của hãng khác vào).

---

### Giai đoạn S4: Visual Fusion — Phối hợp Vector Văn bản & Vector Ảnh ([`fusion.py`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/fusion.py) & [`_fuse_visual()`](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L1111-L1192))

Triết lý thiết kế của S4: **"Văn bản xác định đồ vật kết hợp với Vector ảnh quyết định ngoại hình (màu sắc, chất liệu, hình dáng)"**.

Khi `use_fusion == True`, hệ thống thực hiện 4 bước hoàn toàn trên RAM:
1. **Mã hóa truy vấn sang không gian ảnh:**
   - Dùng `ImageTextEncoder` (`google/siglip2-base-patch16-224` Text Tower) mã hóa câu truy vấn thành `img_vector` (`768` chiều, chuẩn hóa L2).
2. **Hợp nhất tập ứng viên (Candidate Pool Union):**
   - Lấy **Top-50** `asset_id` từ bước S3 Qdrant (`text_candidates = 50`).
   - Nhân ma trận `self.img @ img_vector` trên toàn bộ 30,198 ảnh để lấy **Top-25** `asset_id` có độ tương đồng thị giác cao nhất (`img_candidates = 25`).
   - Hợp hai tập lại (`candidates = text_top_50 ∪ img_top_25`, loại bỏ các ảnh nằm trong `blocked_assets`), thu được tối đa **75 ứng viên**. Nhờ bước hợp này, những ảnh có màu sắc/hình dáng rất khớp nhưng tiêu đề text không ghi chữ mô tả vẫn được kéo vào vòng chấm điểm.
3. **Chấm điểm đôi & Chuẩn hóa Z-Score (`fuse()`):**
   - Với mọi ứng viên $i \in \text{candidates}$, tính chính xác hai điểm tích vô hướng (Cosine Similarity):
     $$s_{\text{text}}(i) = \text{doc\_dense}[i] \cdot \text{text\_vector}, \qquad s_{\text{img}}(i) = \text{img\_siglip2}[i] \cdot \text{img\_vector}$$
   - Chuẩn hóa **Z-Score** từng kênh ngay trên tập ứng viên để đưa hai phân phối (`bge-m3` và `SigLIP2`) về cùng thang đo (trung bình $0$, độ lệch chuẩn $1$):
     $$z(s) = \frac{s - \mu(s)}{\sigma(s)}$$
   - Kết hợp tuyến tính theo trọng số $\alpha$ (`TUNED = FusionSettings(text_candidates=50, img_candidates=25, alpha_attribute=0.1, alpha_plain=0.1)`):
     $$\text{Score}_{\text{fused}}(i) = \alpha \cdot z(s_{\text{text}}(i)) + (1 - \alpha) \cdot z(s_{\text{img}}(i)), \quad (\alpha = 0.1)$$
     *(Kênh ảnh chiếm $90\%$ trọng số phân hạng trên tập ứng viên đã lọc, kênh dense text giữ $10\%$ để giữ ngữ nghĩa đồ vật và phá thế đồng hạng).*
4. **Cộng điểm thưởng Danh mục (Category Bonus) & Xếp hạng lại:**
   - Nếu ở bước S2 câu truy vấn nhận diện được danh mục (`boosts.categories`), mỗi ứng viên thuộc đúng danh mục đó được cộng thêm $+0.2$ (`w_category`):
     $$\text{Score}_{\text{final}}(i) = \text{Score}_{\text{fused}}(i) + \text{CategoryBonus}(i)$$
   - Sắp xếp giảm dần theo $\text{Score}_{\text{final}}$ và trả về Top-10 ảnh đầu tiên.

---

## 4. Bảng tổng hợp: Khi nào chạy luồng nào & Khi nào Vector Ảnh có tác dụng?

Điều kiện bật/tắt S4 Visual Fusion được kiểm tra tường minh tại [`pipeline.py` (dòng 1008–1022)](file:///D:/Search-Engine-for-image-V_lookup-/packages/retrieval/src/visualseek_retrieval/pipeline.py#L1008-L1022):

```python
use_fusion = (
    s.visual_fusion
    and self.fusion is not None
    and self.image_encoder is not None
    and one_part
    and not grouped
    and not plan.seeds
    and not all_hops
    and not plan.anchors
    and not plan.value_ids
    and not brand_id
    and not (plan.category and plan.category.tiers)
)
```

Dưới đây là bảng phân nhánh cho tất cả các dạng câu truy vấn trên V_lookup:

| Dạng câu truy vấn | Ví dụ thực tế | Các kênh hoạt động (`trace.channels`) | Kênh Vector Ảnh (`img`) | Lý do & Cơ chế phối hợp |
|---|---|---|---|---|
| **1. Câu mô tả thị giác (màu sắc, chất liệu, hình dáng, họa tiết)** | *"chuột máy tính màu trắng"*, *"đồng hồ đeo tay dây da"*, *"bàn gỗ màu nâu"*, *"áo họa tiết caro"* | `dense`, `sparse`, `category` (nếu có), **`img`** | ✅ **BẬT (`ready`)** | Không tìm thực thể cụ thể. Kênh text + category khoanh vùng đồ vật, kênh ảnh `SigLIP2` kéo ảnh khớp màu sắc/chất liệu lên đầu (`nDCG@10` tăng từ `0.58` $\to$ `0.815`). |
| **2. Câu tìm kiếm loại hàng / danh mục đơn (1 vế)** | *"máy lọc nước"*, *"balo leo núi"*, *"thuốc kháng sinh"* | `dense`, `sparse`, `category`, **`img`** | ✅ **BẬT (`ready`)** | Kênh `category` cộng `+0.2` cho đúng ngành hàng, kết hợp `VisualFusion` ưu tiên ảnh chụp sản phẩm rõ nét, đúng hình thái đồ vật. |
| **3. Câu tìm đích danh Tên sản phẩm cụ thể (`single_named`)** | *"Chuột Logitech MX Master 3"*, *"Panadol Extra"* | `dense`, `sparse`, `graph_prefetch`, `graph` | ❌ **TẮT (`inactive`)** | Đã bắt được `plan.seeds`. Cần chính xác đúng mã sản phẩm (`+1.0` seed boost + lọc `_related`). Vector ảnh không phân biệt được chữ/mã model trên vỏ hộp. |
| **4. Câu tìm theo Thương hiệu & Lọc loại hàng (P1)** | *"nồi chiên Philips"*, *"máy ảnh Canon"*, *"điện thoại Redmi"* | `dense`, `sparse`, `graph_prefetch`, `graph` | ❌ **TẮT (`inactive`)** | Có `plan.value_ids` & `plan.hops`. Đồ thị `MADE_BY` + bộ lọc `_narrow()` giữ lại đúng các mẫu thuộc hãng và chia nhóm `query_points_groups`. |
| **5. Câu truy vấn Quan hệ / Multi-hop / Loại trừ (P2, P4)** | *"sản phẩm khác của hãng làm nồi chiên không dầu Philips"*, *"trang sức PNJ nhưng không phải nhẫn"* | `dense`, `sparse`, `graph_prefetch`, `graph` | ❌ **TẮT (`inactive`)** | Có `plan.hops` và `plan.anchors`. Hệ thống cộng điểm cho `hops` (`+1.0`) và phạt/loại trừ `anchors` (`-2.0`). |
| **6. Câu ghép hỗn hợp: Thực thể/Hãng + Loại hàng chung (P3)** | *"Kindle Paperwhite và tai nghe"*, *"chuột MX Master 3 với bàn phím"* | `dense`, `sparse`, `graph_prefetch`, `graph` | ❌ **TẮT (`inactive`)** | `grouped = True` (`intents = 2`). Hệ thống chia quota Top-10 giữa `primary_targets` (`seeds/hops`) và `secondary_targets` (`generic_hits`). |
| **7. Câu ghép nhiều đối tượng / Đa danh mục** | *"bàn gỗ màu nâu và ghế nhựa trắng"*, *"lốp xe máy, dầu nhớt và vali"* | `dense`, `sparse`, `category` | ❌ **TẮT (`inactive`)** | `one_part = False` / `grouped = True`. Một vector ảnh SigLIP2 cho câu *"A và B"* sẽ bị lệch về 1 vế làm mất vế kia; do đó dùng `query_points_groups` theo `categories`. |
| **8. Câu tìm theo phân khúc thời trang (`market_tier`)** | *"giày hàng hiệu"*, *"túi xách cao cấp"* | `dense`, `sparse`, `category` | ❌ **TẮT (`inactive`)** | Có `plan.category.tiers`. Phân khúc `luxury/premium` là thuộc tính thương hiệu trong metadata (`w_tier = +0.2`), không suy đoán từ pixel ảnh. |

---

## 5. Cách kiểm tra và đọc kết quả trên Giao diện Web / API Trace

Khi gọi API `POST /api/v1/search` hoặc mở bảng **"Trace truy vấn"** trên giao diện Web ([`search-trace.tsx`](file:///D:/Search-Engine-for-image-V_lookup-/apps/web/components/search/search-trace.tsx)):

1. **Kiểm tra trạng thái các kênh (`trace.channels`):**
   - `dense`: `"ready"` (luôn chạy cho mọi câu hợp lệ).
   - `sparse`: `"ready"` (luôn chạy cho mọi câu hợp lệ).
   - `graph_prefetch`: `"ready"` khi câu có thực thể/hãng đích (`seeds`, `hops`, `generic_hits`); ngược lại là `"inactive"`.
   - `graph`: `"ready"` khi có cộng/trừ điểm thực thể đích hoặc loại trừ (`anchors`).
   - `category`: `"ready"` khi câu không nêu tên thực thể/hãng nhưng nhận diện được ngành hàng (`categories`) hoặc phân khúc (`tiers`).
   - `img`: **`"ready"` khi Visual Fusion (S4) được kích hoạt**; `"inactive"` khi câu thuộc nhóm Graph / Thực thể / Câu ghép nhiều vế.
2. **Đọc bảng "Điểm theo ảnh" (`results[i].scores`):**
   - Cột **Dense** & **Sparse**: Hiển thị điểm gốc và thứ hạng trong nhánh prefetch Qdrant (VD: `0.6412 (#3)`), hoặc `"Ngoài danh sách nhánh"` nếu ảnh đó do kênh Ảnh (`img`) kéo từ ngoài top-100 text vào.
   - Cột **Ảnh (`img`)**:
     - Khi kênh `img` là `"ready"`: Hiển thị điểm cosine `SigLIP2` trực tiếp giữa câu truy vấn và ảnh (VD: `0.1425`).
     - Khi kênh `img` là `"inactive"`: Hiển thị `"Chưa chạy"`.
   - Cột **Điểm cuối (`score`)**:
     - Khi chạy luồng Qdrant (`img = inactive`): Bằng `RRF + Điều chỉnh graph + Điều chỉnh loại hàng`.
     - Khi chạy luồng Visual Fusion (`img = ready`): Bằng `0.1 * zscore(text) + 0.9 * zscore(img) + Điều chỉnh loại hàng (+0.2)`.
3. **Đo thời gian thực thi (`timings` tính bằng ms):**
   - `embed_query`: Thời gian chạy `bge-m3` cho câu truy vấn.
   - `s1_spotting` & `s2_expand`: Thời gian nhận diện thực thể và lập kế hoạch trên đồ thị.
   - `embed_image`: Thời gian chạy `SigLIP2 Text Tower` (chỉ xuất hiện khi `use_fusion = True`).
   - `qdrant` & `qdrant_with_trace`: Thời gian truy vấn Qdrant Cloud.
   - `total`: Tổng thời gian xử lý toàn bộ truy vấn.
