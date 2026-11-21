# Plan triển khai V‑Lookup cho domain sản phẩm (products-v1)

Phiên bản 2026-11-21 (bản 3). Bản này chốt thêm bốn quyết định:

1. Repo chuyển **100%** sang domain sản phẩm. Baseline văn hóa (code, ảnh, embedding, bộ eval) đã gỡ khỏi repo; code cũ chỉ còn trong lịch sử git (nhánh `feat/text-search-baseline`).
2. **Không gọi LLM lúc truy vấn.** Logic câu hỏi (multi-intent, multi-hop) lấy từ knowledge graph. LLM chỉ dùng offline để dựng graph và sinh câu eval.
3. Dataset candidate **không có cột mô tả**, nên phải sinh mô tả (VLM caption, cộng OCR và page context) trước khi embedding.
4. **Không dùng vector ảnh** trong đợt này. Text search chạy hoàn toàn trên text. Image → image làm ở đợt sau.

Bản 3 đưa vào các phát hiện từ POC 500 dòng của [docs/product-entity-search-redesign-2026-11-21.md](docs/product-entity-search-redesign-2026-11-21.md) (mục 3.4), không đổi bốn quyết định trên.

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
| File ảnh yes | 30.875 / 32.337 dòng (95,5%), gộp thành **30.695 ảnh khác nội dung** (8,4 GB) | `LabelPortable/CodexReview/assets/`, map URL → file qua `review.sqlite3` (`items.data.image_url` → `items.evidence.asset_path`) |
| OCR (RapidOCR) | 23.952 ảnh có file OCR (78%), nhưng chỉ **13.596 (44%) đọc ra chữ**; nhiễu, có `score` | `CodexReview/agy/*/ocr/*.json` |
| Page context | 55.122 trang: title, h1, description, `products` (JSON-LD); 24.304 / 30.875 dòng gắn được trang | `CodexReview/pages/*.json`; chỉ khoảng **17%** dòng có brand trong JSON-LD, và giá trị có khi là tên loại hàng chứ không phải hãng |

Đo bằng `build_corpus_manifest.py` (P0) trên cả hai part, ngày 2026-11-21. Số 32.337 / 2.356 khớp CSV. Có 1.462 dòng chưa có ảnh (1.411 chưa tải được, 51 không có trong `review.sqlite3`) và 52 entity chưa có ảnh nào; xem `data/processed/products-v1/missing.jsonl`.

Lưu ý: tên file trong `assets/` **không** phải sha256 nội dung. Manifest tính `asset_id = sha256(bytes)` từ chính file ảnh (khớp 100% với `evidence.image_sha256` trong `review.sqlite3`). Cùng một ảnh có thể thuộc nhiều entity (165 trường hợp) nên `entity_ids` là danh sách.

### Nguồn text cho từng ảnh

Vì không có cột mô tả, text dùng để index được ghép từ bốn nguồn, mỗi nguồn ghi rõ `source`:

| Nguồn | Mức | Độ phủ | Ghi chú |
|---|---|---|---|
| Tên entity + alias + sự kiện graph đã duyệt | Entity | 100% | Lõi của entity card |
| Page context (title, h1, JSON-LD) | Ảnh (theo trang nguồn) | ~79% dòng gắn được trang | Miễn phí; brand chỉ ~17% và nhiễu, graph (P2) quyết định có dùng hay không |
| OCR | Ảnh | 44% có chữ (lọc theo `score`) | Tên hãng, mã mẫu, hàm lượng thuốc in trên bao bì; nhiễu nên phải lọc |
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
- Kênh text → ảnh qua SigLIP2 rất yếu với tiếng Việt và giới hạn 64 token. Đây là lý do đợt này dùng text encoder riêng, không dùng vector ảnh cho text search.
- Nhánh LLM chưa bao giờ được đo trên bộ 500 câu.

### 3.2 Nguyên tắc giữ lại (viết lại từ đầu, không mang code)

- RRF nhiều kênh → `prefetch` + RRF trong Qdrant.
- Loại trừ theo tên → trừ điểm anchor trong formula.
- Hỏi lại khi tên mơ hồ (thiếu dấu, nhiều nghĩa) → tra alias trong graph: alias khớp ≥ 2 entity thì trả các lựa chọn, không cần LLM.
- Chuẩn hóa tiếng Việt: [text.py](packages/retrieval/src/visualseek_retrieval/text.py) (`surface`, `normalize`, `accent_key`), phần duy nhất còn giữ trong repo.
- Quy trình đo: fingerprint artifact, dev/held-out, báo cáo trần recall, ghi giới hạn.

### 3.3 Trạng thái repo hiện tại

| Phần | Có gì |
|---|---|
| `services/api` | FastAPI, chỉ có `/api/v1/health`; config `database_url`, `qdrant_url` |
| `packages/retrieval` | `text.py` |
| `apps/web` | Khung Next.js, trang tạm |
| `compose.yaml` | web, api, Postgres (cho graph), Qdrant `v1.13.4` (phải nâng ở P4) |
| `scripts/qdrant_setup.py` | Chưa commit; viết cho artifact cũ (bắt buộc vector ảnh), phải viết lại ở P4 |

### 3.4 Quan hệ với tài liệu redesign (POC 500 dòng)

[docs/product-entity-search-redesign-2026-11-21.md](docs/product-entity-search-redesign-2026-11-21.md) được viết từ một POC trên máy cá nhân (500 dòng đầu, vector SigLIP2). Nó là tài liệu tham khảo; plan này là bản chốt. Khác biệt:

| Mục | Doc redesign | Plan này |
|---|---|---|
| Vector ảnh trong index | Có (`image` SigLIP2 từ P1) | Không, để đợt sau |
| LLM lúc truy vấn | Planner LLM (có short-circuit) và verify bằng VLM | Không; graph + rule từ gợi quan hệ |
| Nơi lưu graph | `nodes.jsonl` / `edges.jsonl` | Postgres, nạp vào bộ nhớ khi chạy |
| Nguồn ảnh | Tải lại từ URL (~11% lỗi) | Thư mục Handoff (95% có sẵn), chỉ tải bù phần thiếu |

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
- Lưu Postgres (bảng `nodes`, `edges`), load vào bộ nhớ để duyệt (vài nghìn node).
- Graph thay hoàn toàn vai trò của LLM planner cũ: quan hệ đến từ cạnh đã duyệt, không đến từ suy luận lúc truy vấn.

### 4.2 Mô tả ảnh và text để index

**VLM caption** (P3) sinh hai trường riêng, để lỗi bịa thuộc tính không lẫn với nhận dạng:

| Trường | Prompt | Mục đích |
|---|---|---|
| `visual_caption` | **Không** đưa tên entity; chỉ tả cái thấy: loại vật, màu, chất liệu, chữ/nhãn, số lượng, bối cảnh | Phục vụ câu "mô tả không nêu tên"; tránh VLM tả theo kiến thức về sản phẩm |
| `identified_caption` | Có tên entity + tiêu đề trang | Câu có tên, alias, biến thể |

`identified_caption` kèm hai trường phân loại (offline, cho cả 30.9k ảnh):
- `image_role ∈ {product_shot, in_use, context_scene, document, other}`;
- `shows_entity ∈ {yes, no, unsure}`: VLM được đưa tên entity làm giả thuyết và cho biết ảnh có thấy đúng sản phẩm đó không.

Cả hai lưu vào payload. Dùng để xếp `product_shot` lên trước và hạ điểm `context_scene` hoặc `shows_entity=no` (lọc mềm, không loại cứng); là giải pháp cho ~10% nhãn nhiễu. Độ chính xác của `shows_entity` đo bằng hard negative (mục 5), không tin mặc định.

**Text index cho mỗi ảnh** (`doc_text`) = entity card (tên, alias, sự kiện graph đã duyệt) + page title/h1 + OCR + `visual_caption` + `identified_caption`, mỗi phần có nhãn nguồn. Cache theo `hash(input + phiên bản prompt/model)`.

### 4.3 Qdrant

**Collection `images`** (1 point / ảnh):

| Vector | Nội dung |
|---|---|
| `doc` | Dense từ `doc_text`, text encoder đa ngôn ngữ (ứng viên: multilingual-e5, bge-m3, Qwen3-Embedding; chốt ở B2) |
| `sparse` | BM25, modifier IDF; token theo `normalize` / `accent_key` của [text.py](packages/retrieval/src/visualseek_retrieval/text.py) (có dấu + bỏ dấu) |

Không có vector ảnh trong đợt này. Khi làm image → image, thêm named vector `img` vào cùng collection bằng một lần ingest bổ sung, không phải dựng lại `doc`/`sparse`.

Payload có index: `entity_ids`, `brand_id`, `type_id`, `category_id`, `domain`.

**Collection `entities`** (1 point / entity): tên + alias, dense + sparse, để spot entity trong câu.

Dung lượng free tier (1 GB RAM): 32k ảnh × 1 vector × 1024 chiều ≈ 131 MB float32, khoảng 33 MB nếu int8. Dư nhiều chỗ, nên quantization chỉ bật khi đo thấy cần. Cluster cần ≥ 1.15 (score boosting từ 1.14, MMR từ 1.15); `qdrant:v1.13.4` trong compose.yaml quá cũ. Cluster free bị suspend sau 1 tuần không dùng, nên Qdrant luôn là index tái tạo được từ manifest.

### 4.4 Search: một đường cho mọi câu, không LLM

```
query
 → S1 Entity spotting: từ điển alias (Aho-Corasick) + collection `entities`   → seed S
      (alias khớp ≥ 2 entity và không phân biệt được → trả lựa chọn, dừng)
 → S2 Personalized PageRank trên graph, trọng số cạnh theo từ gợi quan hệ trong câu
                                                                             → E_hop1, E_hop2
 → S3 Qdrant query_points:
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

| Loại | Số câu | Ground truth |
|---|---|---|
| Single có tên | 150 | Ảnh của entity / hãng / loại, suy từ nhãn yes |
| Single mô tả không nêu tên | 150 | Pooling top-10 nhiều hệ thống, người chấm |
| Multi-intent | 150 | `Rel_i` cho từng intent |
| Multi-hop (2 và 3 hop) | 150 | Path chạy được trên **cạnh đã duyệt**, không gồm anchor |
| Không có đáp án | 50 | Đo khả năng từ chối |

- Câu sinh từ template trên graph, người viết lại cho tự nhiên, cộng một phần người tự viết.
- **Chia dev/test theo entity**, không theo ảnh, để một entity không nằm ở cả hai phía.
- **Hard negative:** với các entity trong dev, lấy một mẫu ảnh `no` của chính entity đó (nhiều ảnh `no` có thể đã có file trong `CodexReview/assets`) đưa vào một collection eval riêng, không vào index chính.
- **Không sinh câu từ caption, OCR hay page text đã index** (rò rỉ đáp án). Người viết câu "mô tả không nêu tên" nhìn ảnh, không đọc caption.
- Phần "mô tả không nêu tên" là bắt buộc, để không lặp lại lỗi vòng tròn của benchmark cũ.

### 5.2 Chỉ số (R10 = top-10 trả về)

```
Single:       Recall@10 = |R10 ∩ Rel| / min(|Rel|, 10)

Multi-intent: Recall@10 = (1/k) · Σ_i |R10 ∩ Rel_i| / min(|Rel_i|, q_i)
              q_i = số slot chia cho intent i (⌊10/k⌋, phần dư cho intent điểm cao)
              phụ: AllCovered@10 = 1 nếu mọi intent có ≥ 1 ảnh đúng trong top-10

Multi-hop:    Rel = ảnh của tập entity đích, KHÔNG gồm anchor
              Recall@10 = |R10 ∩ Rel| / min(|Rel|, 10)
              phụ: EntityCoverage@10 = số entity đích khác nhau trong top-10 / min(|T|, 10)
                   AnchorLeak@10 = tỉ lệ ảnh anchor lọt vào top-10
```

- Hard negative: `NegLeak@10` = tỉ lệ ảnh `no` của chính entity lọt top-10 khi truy vấn theo tên entity (collection eval riêng). Cũng dùng để đo độ chính xác của `shows_entity`.
- Chẩn đoán multi-hop theo tầng: anchor linking Acc@1; set precision/recall của tập entity đích; recall khi đưa sẵn tập đích chuẩn (oracle).
- Chất lượng caption: tỉ lệ thuộc tính sai trên mẫu 200 ảnh do người chấm, báo riêng cho `visual_caption` và `identified_caption`.
- Kèm HitRate@10, nDCG@10, P95 latency.
- Báo cáo riêng từng loại, bootstrap CI, không gộp thành một con số.

## 6. Lộ trình triển khai

Thời gian là ước tính cho một người làm cùng agent; đo lại sau P0.

### P0. Chốt corpus (1–2 ngày, 0 API)
- ✅ `scripts/build_corpus_manifest.py` (đã xong): lọc yes, map URL → file qua `review.sqlite3`, tính sha256 từ bytes, gộp ảnh trùng nội dung (`entity_ids[]`), gắn OCR + page context đã cache. Chỉ đọc Handoff, không copy ảnh; manifest ghi `image_root` + đường dẫn tương đối.
- Ảnh còn thiếu (1.411 chưa tải + 51 dòng ngoài `review.sqlite3`): `retry_failed.sh` / `page_context.fetch_image`. OCR còn thiếu (khoảng 6.8k ảnh chưa có file OCR): RapidOCR local. Sau mỗi bước chạy lại script để cập nhật manifest.
- Còn lại: loại ảnh gần trùng bằng perceptual hash (hiện chỉ gộp trùng tuyệt đối theo sha256); gắn `phash` vào asset.
- Output: `data/processed/products-v1/{assets,entities,missing}.jsonl` + `manifest.json` (đếm, checksum, fingerprint CSV nguồn). `data/` không nằm trong git, nên máy khác phải dựng lại bằng script này từ Handoff.
- **Gate:** số dòng/entity khớp CSV, mọi dòng yes thuộc đúng một trong hai: nguồn của asset hoặc `missing.jsonl` (đạt: 32.337 dòng, 2.356 entity). Còn lại: quyết định giữ hay loại 52 entity chưa có ảnh sau khi tải lại.

### P1. Bộ eval (3–4 ngày, song song với P2)
- `scripts/build_eval_set.py`: template → paraphrase bằng agy (50 câu/call, ~15 call) → người duyệt bằng giao diện kiểu `codex_review.html`.
- `scripts/evaluate_v2.py`: các chỉ số mục 5.2 + bootstrap CI.
- **Gate:** test set đóng băng, có fingerprint.

### P2. Knowledge graph (3–5 ngày)
- Bước miễn phí: brand từ JSON-LD, hoạt chất/alias từ phần trong ngoặc, mã mẫu bằng regex (dùng lại `pc.codes` / `entity_need` của pipeline gán nhãn).
- Bước LLM (offline): agy trích xuất theo schema, 25 entity/call (~95 call). Thử 200 entity trước.
- Gộp node bằng chuỗi + embedding; người duyệt cặp mơ hồ.
- Duyệt 100% MADE_BY, HAS_INGREDIENT, FITS; Codex kiểm lại cạnh `confidence` thấp.
- Output: bảng `nodes` / `edges` trong Postgres + `packages/retrieval/src/visualseek_retrieval/graph.py` (PPR, networkx) + từ điển alias cho S1.
- **Gate:** độ chính xác cạnh trên mẫu ngẫu nhiên ≥ 95% (hoặc ngưỡng chốt).

### P3. Mô tả ảnh và text embedding (3–5 ngày, máy GPU)
- `scripts/caption_images.py`: VLM mở (dòng Qwen3-VL instruct), sinh `visual_caption` và `identified_caption` kèm `image_role` / `shows_entity` (mục 4.2). Đo tốc độ trên 200 ảnh trước khi chạy 30.9k, cache theo hash.
- **Gate caption:** người chấm 200 ảnh; tỉ lệ thuộc tính sai dưới ngưỡng chốt trước khi chạy toàn bộ.
- `scripts/build_doc_texts.py`: ghép entity card + page context + OCR + caption thành `doc_text`, có nhãn nguồn.
- `scripts/embed_texts.py`: embed `doc_text` và tên/alias entity bằng các text encoder ứng viên, chạy trên máy GPU / Kaggle T4×2. Pin model revision, prefix query/passage, chính sách độ dài.
- Kiểm license từng model (VLM và text encoder) trước khi chạy.

### P4. Qdrant (3–4 ngày)
- Nâng Qdrant trong `compose.yaml` lên một bản ≥ 1.15, pin chính xác; pin `qdrant-client` tương thích.
- Viết lại `scripts/qdrant_setup.py` cho artifact mới: `doc` dense + `sparse` IDF, collection `entities`, payload index; ingest idempotent, ID ổn định từ `asset_id`.
- `packages/retrieval/src/visualseek_retrieval/qdrant_store.py`: `query(prefetch, fusion, formula, group_by)`, client long-lived, timeout rõ ràng.
- Collection POC `product_images_poc` trên Qdrant Cloud (vector `image` SigLIP2, ID điểm theo sha1 của URL) không dùng lại được: tạo collection mới (tên có tiền tố `products_v1_`), ID điểm = `uuid5(asset_id)`; xóa collection POC sau khi parity xong.
- **Gate:** parity — Qdrant `exact=true` khớp một oracle cosine NumPy nhỏ (viết trong test) trên cùng bộ query.

### P5. Search một đường (4–6 ngày)
- `services/api/src/visualseek_api/application/search.py` viết mới theo mục 4.4, endpoint `POST /api/v1/search/text`.
- Hỏi lại khi alias mơ hồ bằng tra graph; scope theo mục 4.4.
- Response: ảnh, entity, nguồn text khớp, điểm theo kênh, timings, fingerprint artifact.

### P6. Ablation (3–5 ngày)

| Mốc | Nội dung | Chỉ số chính |
|---|---|---|
| B0 | `sparse` trên tên + alias + page title + OCR (chưa caption, chưa dense) | Mọi loại; đây là baseline mới |
| B1 | + VLM caption trong `doc_text` | Single mô tả |
| B2 | + dense `doc`; so các text encoder ứng viên | Single mô tả, Single có tên |
| B3 | + Spotting + group_by | Multi-intent, AllCovered |
| B4 | + PPR + formula boost + trừ anchor | Multi-hop, AnchorLeak; single không tụt > 1 điểm |
| B5 | + Reranker local trên top-50 | nDCG, P95 |

Giữ mốc khi mức tăng trên dev có CI không cắt qua 0. Test chỉ chạy một lần ở cuối.

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
2. **LLM theo entity, không theo ảnh:** 2.356 entity, 25 entity/call ≈ 95 call (thay vì ~32k call theo ảnh).
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
| VLM caption 30.9k ảnh | GPU local / Kaggle | 0 API |
| Text embedding, OCR bổ sung, rerank | GPU local / Kaggle | 0 API |
| Truy vấn (eval và dùng thật) | — | 0 LLM |

## 8. Rủi ro

| Rủi ro | Cách kiểm soát |
|---|---|
| VLM bịa thuộc tính khi biết tên sản phẩm | Tách `visual_caption` (không tên) và `identified_caption`; gate chấm 200 ảnh ở P3 |
| Thuộc tính thị giác caption bỏ sót thì không tìm được (không có vector ảnh) | Đo riêng nhóm "mô tả không nêu tên"; nếu thấp, thử thêm `img` ở đợt sau |
| Caption 30.9k ảnh tốn thời gian GPU | Đo tốc độ trên 200 ảnh trước; cache; chạy theo lát |
| Text encoder chưa có số đo tiếng Việt cho domain này | B2 là phép thử thật; chọn theo dev |
| License model (VLM, text encoder, reranker) | Kiểm license trước P3 |
| Nhãn `yes` nhiễu ~10% (ước lượng thô): ground truth tự động có trần dưới 100% | `shows_entity` / `image_role`; báo cáo trần; NegLeak@10; không coi mọi ảnh yes là đúng khi chấm |
| Chất lượng graph quyết định trần multi-hop | Duyệt 100% cạnh chính; ground truth chỉ dùng cạnh đã duyệt |
| Từ gợi quan hệ / từ loại trừ chỉ là rule nhỏ (thay LLM) | Đo AnchorLeak@10, anchor linking Acc@1; mở rộng danh sách từ dev, không từ test |
| Expansion làm tụt single | Ngưỡng trọng số quan hệ; gate "single không tụt > 1 điểm" |
| Cluster Qdrant free bị suspend/xóa | Index tái tạo từ manifest; script ingest idempotent |

## 9. Tham khảo

- [docs/product-entity-search-redesign-2026-11-21.md](docs/product-entity-search-redesign-2026-11-21.md): POC 500 dòng, đề xuất thay thế (xem mục 3.4)
- [Qwen3-VL-Embedding and Qwen3-VL-Reranker (arXiv 2601.04720)](https://arxiv.org/abs/2601.04720v1)
- [jina-embeddings-v4 (arXiv 2506.18902)](https://arxiv.org/abs/2506.18902)
- [HippoRAG 2 (arXiv 2502.14802)](https://arxiv.org/html/2502.14802) · [HippoRAG (arXiv 2405.14831)](https://arxiv.org/html/2405.14831v3) · [RAGU, so sánh độc lập (arXiv 2607.11683)](https://arxiv.org/pdf/2607.11683)
- [Qdrant 1.14: score boosting](https://qdrant.tech/blog/qdrant-1.14.x/) · [Qdrant 1.15: MMR](https://qdrant.tech/blog/qdrant-1.15.x/) · [Qdrant Cloud: tạo cluster](https://qdrant.tech/documentation/cloud/create-cluster)
- [Taobao BEQUE: LLM query rewriting (arXiv 2311.03758)](https://arxiv.org/abs/2311.03758v3) · [Taobao LEAPS (arXiv 2601.05513)](https://arxiv.org/html/2601.05513v2)
- [DoorDash: multi-source grounding cho query intent (arXiv 2603.01486)](https://arxiv.org/html/2603.01486v1)
- [ArtHopQA (AACL 2026)](https://aclanthology.org/2026.aacl-long.182.pdf) · [M3-VQA (arXiv 2604.25122)](https://arxiv.org/html/2604.25122)
