# Plan triển khai V‑Lookup cho domain sản phẩm (products-v1)

Phiên bản 2026-11-22 (bản 4). Các quyết định nền:

1. Repo chuyển **100%** sang domain sản phẩm. Baseline văn hóa (code, ảnh, embedding, bộ eval) đã gỡ khỏi repo; code cũ chỉ còn trong lịch sử git (nhánh `feat/text-search-baseline`).
2. **Không gọi LLM lúc truy vấn.** Logic câu hỏi (multi-intent, multi-hop) lấy từ knowledge graph. LLM chỉ dùng offline để dựng graph và sinh câu eval.
3. Dataset candidate **không có cột mô tả**, nên phải sinh mô tả (VLM caption, cộng OCR và page context) trước khi embedding.
4. **Không dùng vector ảnh** trong đợt này. Text search chạy hoàn toàn trên text. Image → image làm ở đợt sau.

Bản 3 đưa vào các phát hiện từ POC 500 dòng của [docs/product-entity-search-redesign-2026-11-21.md](docs/product-entity-search-redesign-2026-11-21.md) (mục 3.4).

Bản 4 chốt thêm (2026-11-22):

5. **Graph lưu trong Neo4j** (thay Postgres). Neo4j là nơi lưu chính và nơi duyệt cạnh; lúc truy vấn API không gọi Neo4j mà dùng snapshot nạp vào RAM (mục 4.1).
6. **Không tải bù ảnh lỗi.** 1.411 dòng tải lỗi do CDN chặn bot/captcha, 51 dòng ngoài `review.sqlite3`; bỏ cả 1.462 dòng. 52 entity không còn ảnh nào bị loại khỏi index và eval. Sau khi gộp ảnh gần trùng (P0), corpus chốt: **30.198 ảnh, 2.304 entity**, fingerprint trong `locks/products-v1-corpus.json`.
7. **Ngân sách latency: P95 ≤ 300 ms** end-to-end, concurrency 1 (định nghĩa ở mục 5.3).
8. **Dev/test chia 40/60 theo entity.**
9. **Nhóm ưu tiên:** trang sức và quần áo cao cấp, điện thoại, đồ công nghệ, phụ tùng phương tiện giao thông. Corpus vẫn giữ mọi entity; các nhóm này được lấy mẫu dày hơn trong eval và báo cáo riêng (mục 5.1).
10. Định nghĩa query, Recall@10 và latency viết lại ở mục 5 để không lặp lỗi của bộ eval cũ (mục 3.1).

## 1. Mục tiêu

Xây search engine ảnh cho domain sản phẩm, chạy trên Qdrant. Hệ thống xử lý được câu **single intent**, **multi-intent** và **multi-hop** bằng **một đường xử lý duy nhất**: không router, không LLM lúc truy vấn. Mọi quyết định thiết kế được chốt bằng số đo trên bộ eval riêng, với Recall@10 định nghĩa cố định.

Không nằm trong phạm vi đợt này:
- image → image (tìm bằng ảnh upload hoặc theo `asset_id`);
- LLM rewrite / planner / rerank lúc truy vấn;
- scene graph trong ảnh (quan hệ không gian giữa các vật);
- truy vấn video;
- agent truy xuất nhiều vòng.

## 2. Dữ liệu đầu vào

Nguồn: các dòng `approve = yes` trong `candidate_images_part_01_sorted_approved.csv` và `candidate_images_part_04.csv` (thư mục Handoff gán nhãn).

| | part_01 | part_04 | Tổng |
|---|---|---|---|
| Ảnh yes | 28.519 | 3.818 | **32.337** |
| Entity | 2.096 | 260 | **2.356** (không trùng giữa hai file) |
| Ảnh / entity (trung vị) | 12 | 13 | tối đa 54 |

- Entity là sản phẩm có hãng/mẫu, thuốc, món ăn, phụ tùng, đồ thủ công, thiết bị y tế. Ví dụ: "Chuột Logitech MX Master 3", "Vendal (morphin)", "Ca-lăng Ford EcoSport".
- 8 cột gốc: Entity_ID, Tên entity, URL ảnh, rộng, cao, URL trang nguồn, Tiêu đề nguồn, Tên miền. **Không có mô tả, category, hãng hay thuộc tính.**
- 454 tên có phần trong ngoặc (alias/hoạt chất), 658 tên có mã/số. 166 URL ảnh thuộc nhiều entity, nên ảnh là đa nhãn.
- Nhãn yes là nhãn người duyệt ở mức entity, nên ground truth cho truy vấn theo entity/hãng/loại suy ra tự động được.
- Nhãn yes **vẫn nhiễu**: POC xem khoảng 250 ảnh thấy ~10% (ước lượng thô, chưa phải nhãn) là ảnh sai entity do trùng tên ("Lọc gió Vaico" ra lọc muội khí thải) hoặc ảnh ngữ cảnh không thấy sản phẩm (đèn Schréder ra ảnh sân vận động). Ground truth tự động vì thế có trần dưới 100%.
- part_01 còn 62.652 dòng `no` (dùng làm hard negative cho đánh giá, mục 5) và 8.872 dòng trống (bỏ, không vào index, không làm nhãn).

### Tài nguyên có sẵn từ pipeline gán nhãn (không tốn quota)

| Tài nguyên | Đã có | Vị trí |
|---|---|---|
| File ảnh yes | 30.875 / 32.337 dòng (95,5%), gộp thành **30.695 ảnh khác nội dung** (8,4 GB), rồi **30.198 ảnh** sau khi gộp ảnh gần trùng. Phần thiếu không tải bù (quyết định 6) | `LabelPortable/CodexReview/assets/`, map URL → file qua `review.sqlite3` (`items.data.image_url` → `items.evidence.asset_path`) |
| OCR (RapidOCR) | Pipeline có 23.952 ảnh có file OCR (78%), 13.596 (44%) đọc ra chữ. Sau OCR bù ở P0: **100% có file, 17.373 / 30.198 (58%) đọc ra chữ**; nhiễu, có `score` | `CodexReview/agy/*/ocr/*.json` + `data/processed/products-v1/ocr/` |
| Page context | 55.122 trang: title, h1, description, `products` (JSON-LD); 24.304 / 30.875 dòng gắn được trang | `CodexReview/pages/*.json`; chỉ khoảng **17%** dòng có brand trong JSON-LD, và giá trị có khi là tên loại hàng chứ không phải hãng |

Đo bằng `build_corpus_manifest.py` (P0) trên cả hai part, ngày 2026-11-21. Số 32.337 / 2.356 khớp CSV. Có 1.462 dòng không có ảnh: 1.411 tải lỗi vì CDN chặn bot/captcha (nhiều nhất `bizweb.dktcdn.net`, `cdn.tgdd.vn`, `*.hstatic.net`, `cdn.pnj.io`, `m.media-amazon.com`) và 51 dòng không có trong `review.sqlite3`. Hệ quả là 52 entity không còn ảnh nào (ví dụ "Bánh chocopie", "Asus ROG Phone 7", "PNJ nhẫn cưới"). Các dòng và entity này vẫn được ghi trong `data/processed/products-v1/missing.jsonl` và `entities.jsonl` (`assets = 0`), nhưng không vào index và không làm ground truth.

Ước lượng thô theo từ khóa trong tên (chưa phải nhãn; category thật đến từ graph ở P2): trong 2.304 entity có ảnh, khoảng 246 entity thuộc nhóm ưu tiên. Cụ thể: trang sức/thời trang ~107 entity (~2.0k ảnh), phụ tùng xe ~82 (~0.9k ảnh), công nghệ ~46 (~0.8k ảnh), điện thoại ~20 (~0.4k ảnh).

Lưu ý: tên file trong `assets/` **không** phải sha256 nội dung. Manifest tính `asset_id = sha256(bytes)` từ chính file ảnh (khớp 100% với `evidence.image_sha256` trong `review.sqlite3`). Cùng một ảnh có thể thuộc nhiều entity (165 trường hợp) nên `entity_ids` là danh sách.

### Nguồn text cho từng ảnh

Vì không có cột mô tả, text dùng để index được ghép từ bốn nguồn, mỗi nguồn ghi rõ `source`:

| Nguồn | Mức | Độ phủ | Ghi chú |
|---|---|---|---|
| Tên entity + alias + sự kiện graph đã duyệt | Entity | 100% | Lõi của entity card |
| Page context (title, h1, JSON-LD) | Ảnh (theo trang nguồn) | ~79% dòng gắn được trang | Miễn phí; brand chỉ ~17% và nhiễu, graph (P2) quyết định có dùng hay không |
| OCR | Ảnh | 58% có chữ (lọc theo `score`) | Tên hãng, mã mẫu, hàm lượng thuốc in trên bao bì; nhiễu nên phải lọc |
| VLM caption | Ảnh | 100% (sau P3) | Thuộc tính thị giác: loại vật, màu, chất liệu, bối cảnh |

## 3. Bài học từ baseline cũ (đã gỡ khỏi repo)

Baseline văn hóa (15.4k ảnh, SigLIP2) có ba lớp rẽ nhánh: scope guard bằng danh sách từ khóa, exact lookup trên title/tag, rồi classifier tuyến tính quyết định có gọi LLM planner. Planner sinh **một** `visual_query` + **một** `metadata_query`. Retrieval là NumPy hybrid 3 kênh (cosine ảnh, cosine metadata, lexical), gộp bằng RRF, có thể LLM rerank. Toàn bộ đã gỡ ở commit `e4ca439`.

### 3.1 Số đo cũ và cách đọc

| Route (text → image, 500 câu silver) | Recall@10 | Trần | HitRate@10 |
|---|---|---|---|
| local (lexical/hybrid) | 0.4298 | 0.4667 | 0.984 |
| semantic (chỉ vector SigLIP2) | 0.0853 | 0.4667 | 0.688 |

- Recall chia cho **toàn bộ** positive (trung vị 31.5), nên trần chỉ 0.47. Bộ eval mới dùng `min(|Rel|, 10)`.
- Con số local bị **vòng tròn**: ground truth sinh từ tag, và search local cũng chấm điểm trên chính tag đó.
- **"500 query" thực chất là 250 intent × 2 biến thể template** ("X" và "Tôi muốn tìm ảnh có X."). Trung bình tính trên 500 dòng nên mỗi intent bị đếm hai lần và CI hẹp giả tạo.
- **Latency đo trên query lặp:** mỗi query được gửi hai lần (`immediate_repeat`, để dò cache), và nhóm latency không lọc theo lần gửi. Benchmark riêng còn xoay vòng `queries[i % len(queries)]` qua 200 request, nên trúng planner cache và P95 đẹp giả.
- Kênh text → ảnh qua SigLIP2 rất yếu với tiếng Việt và giới hạn 64 token. Đây là lý do đợt này dùng text encoder riêng, không dùng vector ảnh cho text search.
- Nhánh LLM chưa bao giờ được đo trên bộ 500 câu.

### 3.2 Nguyên tắc giữ lại (viết lại từ đầu, không mang code)

- RRF nhiều kênh → `prefetch` + RRF trong Qdrant.
- Loại trừ theo tên → trừ điểm anchor trong formula.
- Hỏi lại khi tên mơ hồ (thiếu dấu, nhiều nghĩa) → tra alias trong graph: alias khớp ≥ 2 entity thì trả các lựa chọn, không cần LLM.
- Chuẩn hóa tiếng Việt: [text.py](packages/retrieval/src/visualseek_retrieval/text.py) (`surface`, `normalize`, `accent_key`), phần duy nhất còn giữ trong repo.
- Quy trình đo: fingerprint artifact, dev/held-out, báo cáo trần recall, ghi giới hạn. Bỏ hẳn: biến thể song song cho một intent, gửi lặp query, xoay vòng query khi đo latency.

### 3.3 Trạng thái repo hiện tại

| Phần | Có gì |
|---|---|
| `services/api` | FastAPI, chỉ có `/api/v1/health`; config `database_url`, `qdrant_url` |
| `packages/retrieval` | `text.py` |
| `apps/web` | Khung Next.js, trang tạm |
| `compose.yaml` | web, api, Postgres, Qdrant `v1.13.4`. Ở P2, Postgres được thay bằng Neo4j (`DATABASE_URL` trong config API bỏ theo). Ở P4, Qdrant local nâng lên đúng bản của cluster Cloud |
| `scripts/qdrant_setup.py` | Chưa commit; viết cho artifact cũ (bắt buộc vector ảnh), phải viết lại ở P4 |
| Qdrant Cloud | Kiểm ngày 2026-11-22: cluster **v1.20.2**, region `ap-northeast-2` (Seoul), `QDRANT_URL` / `QDRANT_API_KEY` trong `.env` dùng được. Round-trip từ máy dev ~92 ms khi giữ kết nối, ~290 ms cho request đầu (TLS). Có hai collection cũ: `product_images_poc` (478 điểm) và `product_images_v1` (27.886 điểm). Cả hai chỉ có vector `image` SigLIP2 768 chiều, payload `image_id` / `entity_id`, không có `doc` / `sparse`, nên không dùng lại được |

### 3.4 Quan hệ với tài liệu redesign (POC 500 dòng)

[docs/product-entity-search-redesign-2026-11-21.md](docs/product-entity-search-redesign-2026-11-21.md) được viết từ một POC trên máy cá nhân (500 dòng đầu, vector SigLIP2). Nó là tài liệu tham khảo; plan này là bản chốt. Khác biệt:

| Mục | Doc redesign | Plan này |
|---|---|---|
| Vector ảnh trong index | Có (`image` SigLIP2 từ P1) | Không, để đợt sau |
| LLM lúc truy vấn | Planner LLM (có short-circuit) và verify bằng VLM | Không; graph + rule từ gợi quan hệ |
| Nơi lưu graph | `nodes.jsonl` / `edges.jsonl` | Neo4j; API nạp snapshot vào RAM khi khởi động |
| Nguồn ảnh | Tải lại từ URL (~11% lỗi) | Thư mục Handoff (95% có sẵn), không tải bù |

Đã đưa vào plan từ doc đó: nhãn yes nhiễu ~10% (mục 2, rủi ro), `no` làm hard negative, chia dev/test theo entity, loại ảnh gần trùng bằng perceptual hash, và hai trường `image_role` / `shows_entity` trong caption (mục 4.2). Collection POC `product_images_poc` trên Qdrant Cloud không tương thích schema này (xem P4).

## 4. Kiến trúc mục tiêu

### 4.1 Knowledge graph

Schema quan hệ cố định:

```
Image  -DEPICTS->        Entity
Entity -MADE_BY->        Brand
Entity -IS_A->           ProductType -IN-> Category (2 cấp)
Entity -HAS_INGREDIENT-> Ingredient
Entity -FITS->           Vehicle/Model
Entity -ORIGIN->         Place
Entity -VARIANT_OF->     Entity
Brand  -COUNTRY->        Country
```

- Mỗi cạnh có `source` (jsonld / name / title / ocr / caption / llm_knowledge), `confidence`, `reviewed`.
- Cạnh chỉ dựa vào kiến thức LLM gắn `unverified`, không dùng làm ground truth.
- Vật phụ phát hiện trong caption chỉ lưu thành tag trong payload, không thành node.
- Graph thay hoàn toàn vai trò của LLM planner cũ: quan hệ đến từ cạnh đã duyệt, không đến từ suy luận lúc truy vấn.
- Taxonomy `Category` phải có sẵn các nhóm ưu tiên (quyết định 9), để eval lấy mẫu và báo cáo theo nhóm.

**Lưu trữ: Neo4j.** Graph nhỏ (~2.3k entity cộng vài nghìn node Brand/Type/Ingredient...), nên Neo4j được chọn vì tiện làm việc, không phải vì tải:
- Neo4j là nơi lưu chính. Node có `id` ổn định; cạnh mang thuộc tính `source`, `confidence`, `reviewed`, `reviewer`.
- Cypher dùng cho ba việc: hàng đợi duyệt cạnh (`reviewed = false`, sắp theo `confidence`), sinh path multi-hop cho bộ eval (chỉ đi trên cạnh `reviewed = true`), và kiểm tra tính nhất quán (entity thiếu `IS_A`, Brand trùng tên).
- Chạy Neo4j Community bằng Docker trong `compose.yaml`, pin chính xác phiên bản. Không cần plugin GDS.
- **Lúc truy vấn không gọi Neo4j.** Khi khởi động, API export cạnh `reviewed = true` thành snapshot (ma trận kề `scipy.sparse` cộng bảng id), kèm fingerprint = sha256 của danh sách cạnh đã sắp. PPR chạy bằng power iteration trong RAM, mục tiêu < 5 ms; `networkx.pagerank` chỉ dùng làm oracle trong test. Response và báo cáo eval ghi fingerprint snapshot, nên mỗi số đo gắn với đúng một phiên bản graph.

### 4.2 Mô tả ảnh và text để index

**VLM caption** (P3) sinh hai trường riêng, để lỗi bịa thuộc tính không lẫn với nhận dạng:

| Trường | Prompt | Mục đích |
|---|---|---|
| `visual_caption` | **Không** đưa tên entity; chỉ tả cái thấy: loại vật, màu, chất liệu, chữ/nhãn, số lượng, bối cảnh | Phục vụ câu "mô tả không nêu tên"; tránh VLM tả theo kiến thức về sản phẩm |
| `identified_caption` | Có tên entity + tiêu đề trang | Câu có tên, alias, biến thể |

`identified_caption` kèm hai trường phân loại (offline, cho cả 30.2k ảnh):
- `image_role ∈ {product_shot, in_use, context_scene, document, other}`;
- `shows_entity ∈ {yes, no, unsure}`: VLM được đưa tên entity làm giả thuyết và cho biết ảnh có thấy đúng sản phẩm đó không.

Cả hai lưu vào payload. Dùng để xếp `product_shot` lên trước và hạ điểm `context_scene` hoặc `shows_entity=no` (lọc mềm, không loại cứng); là giải pháp cho ~10% nhãn nhiễu. Độ chính xác của `shows_entity` đo bằng hard negative (mục 5), không tin mặc định.

**Text index cho mỗi ảnh** (`doc_text`) = entity card (tên, alias, sự kiện graph đã duyệt) + page title/h1 + OCR + `visual_caption` + `identified_caption`, mỗi phần có nhãn nguồn. Cache theo `hash(input + phiên bản prompt/model)`.

### 4.3 Qdrant

**Vai trò:** Qdrant là **index truy hồi ảnh**, và là thành phần duy nhất ngoài process API nằm trên đường truy vấn. Nó làm bốn việc trong **một request** `query_points` mỗi query:
1. tìm ứng viên song song trên dense `doc` và `sparse` BM25 (`prefetch`), rồi gộp bằng RRF;
2. cộng/trừ điểm theo tập entity từ S1/S2 (`formula`: boost seed và hop1/hop2, trừ anchor khi câu có "khác/ngoài/trừ");
3. lọc theo payload (`brand_id`, `type_id`, `category_id`) cho ô lọc ở web;
4. đa dạng hóa kết quả bằng `group_by entity_ids` hoặc MMR.

Qdrant **không** lưu graph, không quyết định quan hệ, không phải nơi lưu dữ liệu gốc. Nguồn sự thật là manifest P0, `doc_text` P3 và Neo4j P2; collection luôn dựng lại được từ chúng.

**Đủ điều kiện dùng:** cluster Cloud v1.20.2 đã có mọi tính năng plan cần (sparse IDF, prefetch + RRF, formula từ 1.14, MMR từ 1.15, group_by). Còn phải làm ở P4: pin `qdrant-client` cùng minor với server (chưa cài trong môi trường); nâng Qdrant local lên v1.20.2 để test parity; đo RAM thực tế trên free tier 1 GB.

**Ngân sách 300 ms:** mỗi query chỉ được **một** round-trip tới Qdrant (~92 ms đo từ máy dev). Vì vậy:
- spotting entity (S1) chạy hoàn toàn trong RAM: Aho-Corasick trên alias cộng ma trận dense của ~2.3k entity (~10 MB, NumPy), dùng lại vector query đã embed. Plan **bỏ collection `entities`** trên Qdrant;
- client giữ kết nối lâu dài (keep-alive), warmup lúc khởi động để không trả phí TLS trong lúc đo.

**Collection `products_v1_images`** (1 point / ảnh, ID điểm = `uuid5(asset_id)`):

| Vector | Nội dung |
|---|---|
| `doc` | Dense từ `doc_text`, text encoder đa ngôn ngữ (ứng viên: multilingual-e5, bge-m3, Qwen3-Embedding; chốt ở B2) |
| `sparse` | BM25, modifier IDF; token theo `normalize` / `accent_key` của [text.py](packages/retrieval/src/visualseek_retrieval/text.py) (có dấu + bỏ dấu) |

Không có vector ảnh trong đợt này. Khi làm image → image, thêm named vector `img` vào cùng collection bằng một lần ingest bổ sung, không phải dựng lại `doc`/`sparse`.

Payload có index: `entity_ids`, `brand_id`, `type_id`, `category_id`, `domain`. Ngoài ra payload còn `image_role`, `shows_entity` và `graph_fingerprint`.

Dung lượng free tier (1 GB RAM): 30.2k ảnh × 1 vector × 1024 chiều ≈ 124 MB float32, khoảng 32 MB nếu int8. Hai collection cũ đang chiếm thêm khoảng 86 MB vector cộng HNSW. Quantization chỉ bật khi đo thấy cần. Cluster free bị suspend sau 1 tuần không dùng, nên Qdrant luôn là index tái tạo được từ manifest.

### 4.4 Search: một đường cho mọi câu, không LLM

```
query
 → S1 Entity spotting (trong RAM): từ điển alias (Aho-Corasick) + dense entity  → seed S
      (alias khớp ≥ 2 entity và không phân biệt được → trả lựa chọn, dừng)
 → S2 Personalized PageRank trên snapshot graph trong RAM, trọng số cạnh theo từ gợi quan hệ
                                                                             → E_hop1, E_hop2
 → S3 Qdrant query_points (round-trip duy nhất ra ngoài process):
       prefetch: dense(doc) | sparse → RRF
       formula:  + w1·[entity ∈ S] + w2·[∈ E_hop1] + w3·[∈ E_hop2]
                 − w4·[entity ∈ S và câu có "khác/ngoài/trừ/không phải"]
       → group_by entity_ids (2–3 ảnh / entity) hoặc MMR
 → S4 (tùy chọn) reranker local trên top-50 → top-10
```

| Loại câu | Cách pipeline xử lý |
|---|---|
| Single: "Chuột Logitech MX Master 3" | S có 1 entity, không có từ gợi quan hệ → E rỗng → content search + boost entity |
| Single mô tả: "chuột không dây màu đen" | S rỗng → chỉ content search trên `doc` + `sparse` (caption gánh phần thuộc tính) |
| Multi-intent: "Kindle Paperwhite và chuột Logitech MX Master 3" | S có 2 entity → group_by giữ chỗ cho từng entity |
| Multi-hop: "sản phẩm khác của hãng làm tai nghe Kraken" | "hãng" kích hoạt MADE_BY → PPR lan Kraken → Razer → sản phẩm Razer; "khác" trừ điểm Kraken |

Scope: một câu nằm trong phạm vi khi spot được entity, hoặc điểm content vượt ngưỡng hiệu chỉnh trên dev. Không dùng danh sách từ khóa cấm/cho phép.

Điểm yếu đã biết:
- Từ gợi quan hệ và từ phủ định là một rule nhỏ, thay cho LLM.
- PPR không phân biệt chiều quan hệ hoàn toàn.
- Expansion có thể kéo entity lạ vào câu single.
- Thuộc tính thị giác mà caption bỏ sót thì không tìm được (không có vector ảnh để bù).

Mỗi điểm yếu đều có chỉ số đo riêng ở mục 5.

## 5. Đánh giá

### 5.1 Bộ eval (làm trước khi tinh chỉnh; chia dev/test, đóng băng test)

Số câu dưới đây là tổng; dev/test chia 40/60 theo entity (ví dụ 150 câu → 60 dev / 90 test).

| Loại | Số câu | Ground truth | Phụ thuộc |
|---|---|---|---|
| Single có tên (mức entity) | 150 | Ảnh `yes` của entity; ở test có người duyệt lại | Corpus đóng băng (P0) |
| Single có tên (hãng / loại) | 50 | Ảnh của mọi entity nối với hãng/loại đó qua cạnh đã duyệt | P2 |
| Single mô tả không nêu tên | 150 | Pooling top-10 các mốc B0–B5, người chấm (mục 5.2) | Câu: P0. Judgment: P6 |
| Multi-intent (k = 2 hoặc 3) | 150 | `Rel_i` cho từng intent | P0; intent theo hãng/loại thì cần P2 |
| Multi-hop (2 và 3 hop) | 150 | Path chạy được trên **cạnh đã duyệt**, không gồm anchor | P2 |
| Không có đáp án | 50 | Rỗng; đo khả năng từ chối | — |

**Luật về query (bắt buộc, kiểm bằng script khi build bộ eval):**
1. **Mỗi query là một mẫu đo**: một dòng, một `query_id`, trọng số 1 trong mọi trung bình và bootstrap.
2. **Không có biến thể song song** trong bộ chính. Paraphrase (agy hay người viết) **thay** câu template theo tỉ lệ 1 → 1, không thêm câu mới. Nếu cần đo độ bền với cách diễn đạt khác, dùng file riêng với báo cáo riêng, không bao giờ gộp vào bộ chính.
3. **Khử trùng trên toàn bộ dev, test và warmup:**
   - text sau `normalize` và sau `accent_key` ([text.py](packages/retrieval/src/visualseek_retrieval/text.py)) phải khác nhau từng đôi;
   - mỗi bộ (loại câu, tập entity anchor, đường quan hệ) có tối đa **một** query.
4. **Chia dev/test theo entity**, không theo ảnh: mọi entity xuất hiện trong Rel hay anchor của một câu test đều không xuất hiện ở dev. Câu nào vướng entity ở cả hai phía thì bỏ, không chuyển phía.
5. **Nhóm ưu tiên** (quyết định 9): mỗi loại (trừ "không có đáp án") có ≥ 40% số câu với anchor hoặc Rel thuộc nhóm ưu tiên. Báo cáo riêng hai phần: nhóm ưu tiên và phần còn lại.

**Nguồn câu và chống rò rỉ:**
- Câu sinh từ template trên graph rồi người viết lại cho tự nhiên, cộng thêm một phần người tự viết.
- **Không sinh câu từ caption, OCR hay page text đã index** (rò rỉ đáp án). Người viết câu "mô tả không nêu tên" nhìn ảnh, không đọc caption.
- Phần "mô tả không nêu tên" là bắt buộc, để không lặp lại lỗi vòng tròn của benchmark cũ.

**Ground truth:**
- Rel chỉ gồm `asset_id` có trong corpus đóng băng (quyết định 6). Ảnh gần trùng của cùng entity đã gộp trước khi đóng băng (P0).
- **Rel của test được người duyệt lại:** ảnh `yes` không thấy đúng sản phẩm thì bị loại khỏi Rel (ước tính ~1–2k ảnh, dùng giao diện kiểu `codex_review.html`). Dev dùng nhãn `yes` gốc và báo cáo trần.
- **Hard negative:** với các entity trong dev, lấy một mẫu ảnh `no` của chính entity đó (nhiều ảnh `no` có thể đã có file trong `CodexReview/assets`) đưa vào một collection eval riêng, không vào index chính.

### 5.2 Recall@10 và các chỉ số chất lượng

**Ký hiệu:**
- `R10` là **10 `asset_id` khác nhau đầu tiên** theo thứ tự trả về, sau khi khử trùng; `group_by` trên mảng `entity_ids` có thể trả một ảnh ở nhiều group. Trả ít hơn 10 thì các chỗ thiếu tính là trượt.
- `Rel` là tập `asset_id` đóng băng trong file ground truth, và `|Rel| ≥ 1` với mọi câu có đáp án.

```
Single, Multi-hop:
  Recall@10(q) = |R10 ∩ Rel| / min(|Rel|, 10)                       ∈ [0, 1]
  (Multi-hop: Rel = ảnh của tập entity đích T, KHÔNG gồm ảnh của anchor)

Multi-intent (k intent, k ≤ 3):
  s = ⌊10 / k⌋                     # cố định theo k, không phụ thuộc hệ thống
  Recall@10(q) = (1/k) · Σ_i  min(|R10 ∩ Rel_i|, s) / min(|Rel_i|, s)   ∈ [0, 1]
  (tử số bị chặn ở s: một intent không bù được cho intent khác)

Điểm của một loại = trung bình cộng Recall@10(q) trên các query của loại đó (macro),
  kèm n và CI 95% bootstrap percentile (10.000 lần lấy mẫu lại theo query, seed cố định).
So sánh hai hệ thống: paired bootstrap của Δ = Recall_B(q) − Recall_A(q) trên cùng tập query.
```

Ví dụ:
- `|Rel| = 4`, trúng 3 → 0,75.
- `|Rel| = 30`, trúng 7 → 0,70.
- Multi-intent k = 2 (s = 5): intent 1 có `|Rel| = 12`, trúng 8 → min(8,5)/5 = 1; intent 2 có `|Rel| = 3`, trúng 1 → 1/3. Recall = (1 + 0,333)/2 = 0,667.

**Trường hợp biên:**
- **Câu không có đáp án** không tính Recall. Đo **Rejection rate** = % câu không đáp án mà hệ thống trả `out_of_scope` với 0 kết quả.
- **False rejection** = % câu có đáp án bị từ chối. Các câu này có Recall = 0 và vẫn nằm trong mẫu số.
- **Hệ thống hỏi lại** (alias mơ hồ) trên câu có đáp án thì Recall = 0, và câu đó được đếm vào `clarification_rate`. Bộ chính không chứa câu cố ý mơ hồ.
- **"Single mô tả" (pooling):**
  - câu đóng băng ở P1a, còn judgment tạo ở P6;
  - pool dev là hợp top-10 của các mốc B0–B5, chấm dần qua từng mốc;
  - pool test lập **một lần** từ top-10 của mọi cấu hình ứng viên cuối, chấm xong mới chạy test;
  - ảnh chưa chấm tính là không liên quan;
  - báo cáo kèm độ sâu pool và số ảnh đã chấm. Recall của loại này là tương đối theo pool, nên báo cáo thêm nDCG@10.

**Chỉ số phụ:**
- Multi-intent: `AllCovered@10` = 1 nếu mọi intent có ≥ 1 ảnh đúng trong R10.
- Multi-hop:
  - `EntityCoverage@10` = số entity đích khác nhau trong R10 / min(|T|, 10);
  - `AnchorLeak@10` = tỉ lệ ảnh của anchor trong R10;
  - chẩn đoán theo tầng: anchor linking Acc@1, set precision/recall của tập entity đích, và recall khi đưa sẵn tập đích chuẩn (oracle).
- Hard negative: `NegLeak@10` = tỉ lệ ảnh `no` của chính entity lọt R10 khi truy vấn theo tên entity (collection eval riêng). Chỉ số này cũng dùng để đo độ chính xác của `shows_entity`.
- Chất lượng caption: tỉ lệ thuộc tính sai trên mẫu 200 ảnh do người chấm, báo riêng cho `visual_caption` và `identified_caption`.
- Kèm HitRate@10 và nDCG@10. Báo cáo riêng từng loại và từng nhóm (ưu tiên / còn lại), không gộp thành một con số.

### 5.3 Latency

**Đơn vị đo:** một request ứng với một query của bộ eval (mục 5.1), **gửi đúng một lần**. Số request bằng số query. Không gửi lặp, không xoay vòng `% len`, không có pha "immediate repeat".

**Giao thức:**
1. Khởi động API, rồi gửi **warmup** từ một file riêng (20 câu, không trùng bộ eval theo luật khử trùng). Warmup không vào thống kê. Request đầu tiên sau khi khởi động được ghi riêng là `cold_start_ms`.
2. Chạy toàn bộ query một lượt, **tuần tự (concurrency 1)**, thứ tự xáo trộn bằng seed cố định. Chất lượng và latency lấy từ **cùng lượt chạy này**.
3. **Cache:** response có cờ cho mọi cache tầng ứng dụng (embedding query, kết quả). Nếu có bất kỳ cache hit nào thì lượt chạy bị coi là không hợp lệ. Vì query đã khử trùng nên số cache hit đúng phải bằng 0.
4. Lỗi và timeout (timeout = 2 s) không bị loại bỏ trong im lặng: báo `error_count`, và lượt chạy có lỗi bị đánh dấu `complete = false`.

**Hai số cho mỗi request:**
- `client_ms` là thời gian HTTP thực đo từ client, từ lúc gửi đến khi parse xong JSON. Đây là **số chính để xét ngân sách**.
- `server_ms` = `timings.total`, kèm từng stage: `embed_query`, `s1_spotting`, `s2_ppr`, `qdrant`, `s4_rerank`, `serialize`.

**Báo cáo:** n, P50, P95, P99, max, dùng `numpy.percentile(..., method="linear")`, cho tổng, từng loại câu và từng stage.

**Môi trường ghi kèm:** CPU/GPU của bước embed query, Qdrant local hay Cloud (cùng region), RTT Qdrant đo bằng ping `GET /` trước lượt chạy, phiên bản model và collection, fingerprint artifact và graph.

**Ngân sách (quyết định 7):** `client_ms` P95 ≤ **300 ms**, với API chạy trên máy dev và Qdrant Cloud Seoul. Phân bổ dự kiến, kiểm lại ở P6:

| Stage | Dự kiến | Ghi chú |
|---|---|---|
| `embed_query` | ≤ 60 ms | Text encoder trên CPU; encoder chậm hơn mức này bị loại ở B2 dù chất lượng tốt |
| `s1_spotting` + `s2_ppr` | ≤ 10 ms | Trong RAM |
| `qdrant` | ≤ 150 ms | Một round-trip (~92 ms RTT) cộng thời gian server |
| HTTP, serialize, còn lại | ≤ 50 ms | |

Reranker (B5) chỉ được giữ nếu lượt chạy có reranker vẫn đạt P95 ≤ 300 ms. Trên CPU, khả năng cao là không đạt.

## 6. Lộ trình triển khai

Thời gian là ước tính cho một người làm cùng agent; đo lại sau P0.

### P0. Chốt corpus — ✅ xong 2026-11-22
- ✅ `scripts/build_corpus_manifest.py`: lọc yes, map URL → file qua `review.sqlite3`, tính sha256 từ bytes, gộp ảnh trùng nội dung (`entity_ids[]`), gắn OCR + page context đã cache. Chỉ đọc Handoff, không copy ảnh; manifest ghi `image_root` + đường dẫn tương đối. Mỗi source ghi `content_id` (sha256 của file gốc).
- ✅ Ảnh thiếu: **không tải bù** (quyết định 6). 52 entity không có ảnh bị loại khỏi index và eval.
- ✅ **Gộp ảnh gần trùng** bằng `scripts/find_near_duplicates.py`. Mục tiêu là "cùng một bức ảnh" (khác kích thước, độ nén, nền), không phải "ảnh sản phẩm giống nhau".
  - **Tiêu chí:** cùng tập `entity_ids`; pHash Hamming ≤ 6; tỉ lệ khung lệch ≤ 3%; MAE 16×16 ≤ 8; **p99 độ lệch từng pixel ở 64×64 ≤ 16**. Nhóm theo kiểu sao: mỗi thành viên được so trực tiếp với ảnh đại diện lớn nhất, không bắc cầu.
  - **Vì sao chặt:** soát bằng mắt hơn 200 cặp quanh mọi ngưỡng. Ảnh nền trắng của biến thể màu, góc chụp khác hoặc hộp thuốc khác tên thường có pHash trùng hoặc rất gần, MAE thấp: ví Samsonite xám/xanh, Entecavir/Acyclovir, Lostad T25/T50. Hai lọ thuốc dùng chung mẫu ảnh stock ("Perindopril 4mg"/"Klonopin 2mg") vượt qua mọi phép so pixel. Vì vậy **không gộp khác entity**: gộp nhầm trong cùng entity chỉ mất một ảnh, còn gộp khác entity làm sai nhãn. Trùng tuyệt đối khác entity vẫn gộp theo sha256.
  - **Kết quả:** 497 ảnh gộp vào 478 ảnh đại diện (nhóm lớn nhất 4). Mẫu ngẫu nhiên 18 cặp: 18/18 là cùng một bức ảnh. `phash` gắn vào mọi asset; thành viên đã gộp nằm trong `merged` của ảnh đại diện, kèm `hamming`, `mae`.
- ✅ **OCR bù** bằng `scripts/backfill_ocr.py` (RapidOCR 1.4.4, cùng tham số với pipeline gán nhãn: cạnh dài 960 px, không dùng angle classifier): 6.638 ảnh, 0 lỗi, 4.012 ảnh có chữ. Kết quả nằm ở `data/processed/products-v1/ocr/<asset_id>.json`.
- ✅ **Đóng băng corpus:** [locks/products-v1-corpus.json](locks/products-v1-corpus.json) (tracked trong git) ghi counts, checks, checksum từng file output, fingerprint CSV nguồn, sha256 của file near-dup và thư mục OCR bù. `corpus_fingerprint = 861911cb10dd5651e2b2297585230bf8dfe72a2265820011914791f4777e3455`. Mọi bước sau tham chiếu fingerprint này.

| | Trước P0 hoàn tất | Corpus đóng băng |
|---|---|---|
| Ảnh (asset) | 30.695 | **30.198** |
| Entity có ảnh | 2.304 | 2.304 |
| Ảnh có file OCR | 23.952 (78%) | 30.198 (100%) |
| Ảnh OCR ra chữ | 13.596 (44%) | 17.373 (58%) |
| Ảnh thuộc nhiều entity | 165 | 164 |

- **Output:** `data/processed/products-v1/` gồm `{assets,entities,missing}.jsonl`, `manifest.json`, `near_duplicates.json`, `ocr/`, `cache/image_features.npz`. `data/` không nằm trong git.
- **Dựng lại ở máy khác:** cần Handoff, hai CSV, `near_duplicates.json` và thư mục `ocr/`. Hai thứ sau nên được chép kèm, vì chạy lại OCR với phiên bản onnxruntime khác có thể ra chữ khác, làm lệch lock. Lệnh:
  `python scripts/build_corpus_manifest.py --handoff-root … --csv … --csv … --near-duplicates …/near_duplicates.json --ocr-dir …/ocr --check-lock locks/products-v1-corpus.json`
  Lệnh này thất bại nếu bất kỳ file output nào khác lock.
- **Gate:** ✅ số dòng và số entity khớp CSV (32.337 dòng, 2.356 entity); mọi dòng yes thuộc đúng một trong hai: nguồn của asset hoặc `missing.jsonl`; mọi ảnh đã gộp được tính đúng; sha256 khớp `review.sqlite3`; corpus đã khóa.

### P1. Bộ eval (3–4 ngày, chia hai đợt quanh P2)
- `scripts/build_eval_set.py`: template → paraphrase 1 → 1 bằng agy (50 câu/call, ~15 call) → người duyệt bằng giao diện kiểu `codex_review.html`. Script kiểm mọi luật query của mục 5.1 và dừng nếu vi phạm.
- **P1a** (sau khi corpus đóng băng, song song với P2):
  - viết và đóng băng câu Single có tên (mức entity), Single mô tả và câu không có đáp án;
  - chọn trước entity thuộc nhóm ưu tiên;
  - người duyệt Rel của test.
- **P1b** (sau P2): Single theo hãng/loại, Multi-intent, Multi-hop. Rel và path chỉ dùng cạnh đã duyệt.
- `scripts/evaluate_v2.py`: chỉ số mục 5.2, giao thức latency mục 5.3, bootstrap CI và paired bootstrap.
- **Gate:** test set đóng băng, có fingerprint (query, Rel, corpus, graph). Kiểm tự động: không trùng query, không entity nào nằm ở cả dev và test, mọi `asset_id` trong Rel có trong corpus.

### P2. Knowledge graph (3–5 ngày)
- Bước miễn phí: brand từ JSON-LD, hoạt chất/alias từ phần trong ngoặc, mã mẫu bằng regex (dùng lại `pc.codes` / `entity_need` của pipeline gán nhãn).
- Bước LLM (offline): agy trích xuất theo schema, 25 entity/call (~95 call). Thử 200 entity trước.
- Gộp node bằng chuỗi + embedding; người duyệt cặp mơ hồ.
- Duyệt 100% MADE_BY, HAS_INGREDIENT, FITS; Codex kiểm lại cạnh `confidence` thấp. Hàng đợi duyệt lấy bằng Cypher.
- Taxonomy Category có đủ bốn nhóm ưu tiên; gán `IS_A` / `IN` cho mọi entity.
- Hạ tầng:
  - thay Postgres bằng Neo4j Community trong `compose.yaml`, pin phiên bản;
  - script nạp idempotent từ file trích xuất (`MERGE` theo `id`);
  - bỏ `DATABASE_URL` trong config API.
- Output:
  - graph trong Neo4j;
  - `scripts/export_graph_snapshot.py` (cạnh đã duyệt → file snapshot + fingerprint);
  - `packages/retrieval/src/visualseek_retrieval/graph.py` (PPR bằng `scipy.sparse`, có test so với oracle `networkx`);
  - từ điển alias cho S1.
- **Gate:** độ chính xác cạnh trên mẫu ngẫu nhiên ≥ 300 cạnh, **cận dưới CI 95% ≥ 0,95**.

### P3. Mô tả ảnh và text embedding (3–5 ngày, máy GPU)
- `scripts/caption_images.py`: VLM mở (dòng Qwen3-VL instruct), sinh `visual_caption` và `identified_caption` kèm `image_role` / `shows_entity` (mục 4.2). Đo tốc độ trên 200 ảnh trước khi chạy 30.2k, cache theo hash.
- **Gate caption:** người chấm 200 ảnh; tỉ lệ thuộc tính sai dưới ngưỡng chốt trước khi chạy toàn bộ.
- `scripts/build_doc_texts.py`: ghép entity card + page context + OCR + caption thành `doc_text`, có nhãn nguồn.
- `scripts/embed_texts.py`: embed `doc_text` và tên/alias entity bằng các text encoder ứng viên, chạy trên máy GPU / Kaggle T4×2. Pin model revision, prefix query/passage, chính sách độ dài. Đo luôn thời gian embed **một câu query trên CPU** cho từng encoder, vì ngân sách stage này là ≤ 60 ms (mục 5.3).
- Kiểm license từng model (VLM và text encoder) trước khi chạy.

### P4. Qdrant (3–4 ngày)
- Cluster Cloud đã là v1.20.2 (mục 3.3). Nâng Qdrant local trong `compose.yaml` lên đúng `v1.20.2` để test parity chạy offline; pin `qdrant-client` cùng minor với server.
- Viết lại `scripts/qdrant_setup.py` cho artifact mới:
  - collection `products_v1_images` với `doc` dense, `sparse` IDF và payload index;
  - ingest idempotent, ID điểm = `uuid5(asset_id)`;
  - ghi fingerprint corpus, `doc_text` và graph vào payload hoặc metadata collection.
- `packages/retrieval/src/visualseek_retrieval/qdrant_store.py`: `query(prefetch, fusion, formula, group_by)` gói gọn trong **một** request `query_points`; client long-lived với keep-alive, timeout rõ ràng.
- Hai collection cũ `product_images_poc` (478 điểm) và `product_images_v1` (27.886 điểm) chỉ có vector `image` SigLIP2, nên không dùng lại được. Không đụng tới chúng cho đến khi parity xong; sau đó hỏi chủ repo trước khi xóa, để giải phóng RAM free tier.
- Đo RAM collection mới trên Cloud. Chỉ bật int8 quantization khi đo thấy thiếu.
- **Gate:**
  - parity: Qdrant `exact=true` khớp một oracle cosine NumPy nhỏ (viết trong test) trên cùng bộ query;
  - một request `query_points` đầy đủ (prefetch, RRF, formula, group_by) có P95 stage `qdrant` ≤ 150 ms trên bộ dev.

### P5. Search một đường (4–6 ngày)
- `services/api/src/visualseek_api/application/search.py` viết mới theo mục 4.4, endpoint `POST /api/v1/search/text`.
- Hỏi lại khi alias mơ hồ bằng tra graph; scope theo mục 4.4.
- Khi khởi động: nạp snapshot graph và ma trận entity vào RAM, mở client Qdrant, warmup. Đường truy vấn không gọi Neo4j.
- Response gồm:
  - ảnh, entity, nguồn text khớp, điểm theo kênh;
  - `timings` theo stage (mục 5.3) và cờ cache;
  - `out_of_scope`, `clarification`;
  - fingerprint artifact và graph.

### P6. Ablation (3–5 ngày)

| Mốc | Nội dung | Chỉ số chính |
|---|---|---|
| B0 | `sparse` trên tên + alias + page title + OCR (chưa caption, chưa dense) | Mọi loại; đây là baseline mới |
| B1 | + VLM caption trong `doc_text` | Single mô tả |
| B2 | + dense `doc`; so các text encoder ứng viên | Single mô tả, Single có tên; `embed_query` P95 ≤ 60 ms |
| B3 | + Spotting + group_by | Multi-intent, AllCovered |
| B4 | + PPR + formula boost + trừ anchor | Multi-hop, AnchorLeak; single không tụt (xem dưới) |
| B5 | + Reranker local trên top-50 | nDCG; chỉ giữ nếu P95 ≤ 300 ms |

- **Giữ mốc** khi Δ trên chỉ số chính ở dev, tính bằng paired bootstrap, có CI 95% nằm hoàn toàn trên 0, **và** P95 `client_ms` vẫn ≤ 300 ms.
- **"Single không tụt" ở B4:** Δ điểm ước lượng ≥ −0,01 **và** cận trên CI của Δ ≥ 0, tức không tụt có ý nghĩa thống kê. Không xét riêng điểm ước lượng, vì với khoảng 60 câu dev mỗi loại thì nhiễu lớn hơn 1 điểm.
- Mỗi mốc là một lượt chạy theo giao thức mục 5.3: mỗi query một lần, cùng lượt cho chất lượng và latency.
- Test chỉ chạy một lần ở cuối, trên cấu hình đã chốt.

### P7. Đưa vào dùng (2–3 ngày)
- Web dựng mới: ô tìm kiếm text, lọc theo category/brand/type từ graph, hiển thị lựa chọn khi alias mơ hồ.
- Serve ảnh từ thư mục corpus mới.
- Báo cáo cuối: số đo theo loại câu, P95, quota thực tế đã dùng.

### Sau đợt này
- **Image → image:** thêm named vector `img` (SigLIP2 hoặc Qwen3-VL-Embedding) vào collection `images`, endpoint tìm theo ảnh upload / `asset_id` có loại self-match. Có thể thử `img` như một kênh text → image nếu ablation chứng minh có lợi.
- LLM rewrite câu truy vấn: chỉ xét nếu B4/B5 chưa đủ ở multi-hop, và đo như một biến thể riêng.

## 7. Tối ưu quota

Giả định tài nguyên: agy (Gemini flash), Codex, OpenAI API, Kaggle T4×2, một máy GPU, Qdrant Cloud free.

1. **Miễn phí trước, LLM sau:** JSON-LD, phần trong ngoặc, regex, OCR có sẵn lấy tối đa thông tin trước khi gọi LLM.
2. **LLM theo entity, không theo ảnh:** 2.304 entity, 25 entity/call ≈ 93 call (thay vì ~31k call theo ảnh).
3. **Việc theo ảnh chạy local:** VLM caption, embedding, OCR, rerank trên GPU.
4. **Cache mọi output** theo hash(input + phiên bản prompt/model); chạy lại không tốn quota.
5. **Lặp trên lát 200 entity / 200 ảnh;** chạy toàn bộ khi đã qua gate.
6. **Không gọi LLM lúc truy vấn.** Mọi câu truy vấn khi eval và khi dùng thật đều 0 call.
7. **Đúng quota cho đúng việc:** agy cho trích xuất hàng loạt; Codex cho kiểm cạnh tranh chấp và viết code; OpenAI API chỉ khi cần, ưu tiên Batch API cho việc offline.

| Việc | Nguồn | Khối lượng ước tính |
|---|---|---|
| Graph | agy | ~95 call (+ thử 200 entity) |
| Paraphrase eval | agy | ~15 call |
| Kiểm cạnh tranh chấp | Codex | vài chục call |
| VLM caption 30.2k ảnh | GPU local / Kaggle | 0 API |
| Text embedding, OCR bổ sung, rerank | GPU local / Kaggle | 0 API |
| Truy vấn (eval và dùng thật) | — | 0 LLM |

## 8. Rủi ro

| Rủi ro | Cách kiểm soát |
|---|---|
| VLM bịa thuộc tính khi biết tên sản phẩm | Tách `visual_caption` (không tên) và `identified_caption`; gate chấm 200 ảnh ở P3 |
| Thuộc tính thị giác caption bỏ sót thì không tìm được (không có vector ảnh) | Đo riêng nhóm "mô tả không nêu tên"; nếu thấp, thử thêm `img` ở đợt sau |
| Caption 30.2k ảnh tốn thời gian GPU | Đo tốc độ trên 200 ảnh trước; cache; chạy theo lát |
| Text encoder chưa có số đo tiếng Việt cho domain này | B2 là phép thử thật; chọn theo dev |
| License model (VLM, text encoder, reranker) | Kiểm license trước P3 |
| Nhãn `yes` nhiễu ~10% (ước lượng thô): ground truth tự động có trần dưới 100% | Rel của test được người duyệt lại (mục 5.1); dev dùng nhãn gốc và báo cáo trần; `shows_entity` / `image_role`; NegLeak@10 |
| Chất lượng graph quyết định trần multi-hop | Duyệt 100% cạnh chính; ground truth chỉ dùng cạnh đã duyệt |
| Từ gợi quan hệ / từ loại trừ chỉ là rule nhỏ (thay LLM) | Đo AnchorLeak@10, anchor linking Acc@1; mở rộng danh sách từ dev, không từ test |
| Expansion làm tụt single | Ngưỡng trọng số quan hệ; gate "single không tụt > 1 điểm" |
| Cluster Qdrant free bị suspend/xóa | Index tái tạo từ manifest; script ingest idempotent |
| RTT Qdrant Cloud (~92 ms) ăn gần 1/3 ngân sách 300 ms | Mỗi query một round-trip; S1 và S2 chạy trong RAM; keep-alive và warmup; nếu vẫn vượt thì chuyển API sang chạy cùng region với cluster, hoặc dùng Qdrant self-host |
| Snapshot graph trong RAM lệch với Neo4j | Snapshot có fingerprint; response và báo cáo eval ghi fingerprint; đổi graph thì nạp lại snapshot, không sửa graph trong RAM |
| Bỏ ảnh tải lỗi gây lệch độ phủ (lỗi tập trung ở vài CDN như `cdn.tgdd.vn`, `cdn.pnj.io`, trùng nhóm điện thoại và trang sức) | Báo cáo số ảnh mỗi entity theo nhóm ưu tiên; entity nhóm ưu tiên có < 3 ảnh thì không làm anchor cho câu test |

## 9. Tham khảo

- [docs/product-entity-search-redesign-2026-11-21.md](docs/product-entity-search-redesign-2026-11-21.md): POC 500 dòng, đề xuất thay thế (xem mục 3.4)
- [Qwen3-VL-Embedding and Qwen3-VL-Reranker (arXiv 2601.04720)](https://arxiv.org/abs/2601.04720v1)
- [jina-embeddings-v4 (arXiv 2506.18902)](https://arxiv.org/abs/2506.18902)
- [HippoRAG 2 (arXiv 2502.14802)](https://arxiv.org/html/2502.14802) · [HippoRAG (arXiv 2405.14831)](https://arxiv.org/html/2405.14831v3) · [RAGU, so sánh độc lập (arXiv 2607.11683)](https://arxiv.org/pdf/2607.11683)
- [Qdrant 1.14: score boosting](https://qdrant.tech/blog/qdrant-1.14.x/) · [Qdrant 1.15: MMR](https://qdrant.tech/blog/qdrant-1.15.x/) · [Qdrant Cloud: tạo cluster](https://qdrant.tech/documentation/cloud/create-cluster)
- [Taobao BEQUE: LLM query rewriting (arXiv 2311.03758)](https://arxiv.org/abs/2311.03758v3) · [Taobao LEAPS (arXiv 2601.05513)](https://arxiv.org/html/2601.05513v2)
- [DoorDash: multi-source grounding cho query intent (arXiv 2603.01486)](https://arxiv.org/html/2603.01486v1)
- [ArtHopQA (AACL 2026)](https://aclanthology.org/2026.aacl-long.182.pdf) · [M3-VQA (arXiv 2604.25122)](https://arxiv.org/html/2604.25122)
