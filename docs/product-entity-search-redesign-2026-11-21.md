# Đề xuất luồng mới cho dataset entity/sản phẩm (CSV)

> **Trạng thái: tài liệu tham khảo, không phải kế hoạch chốt.** Kế hoạch đang theo là
> [PLAN_PRODUCTS_V1.md](../PLAN_PRODUCTS_V1.md). Hai tài liệu khác nhau ở: vector ảnh trong
> index (plan: không), planner/verify bằng LLM lúc truy vấn (plan: không), nơi lưu graph
> (plan: Postgres) và nguồn ảnh (plan: thư mục Handoff). Mục 3.4 của plan liệt kê những gì
> đã được đưa vào từ tài liệu này. Các liên kết tới `qdrant-cloud-setup.md` và
> `search-architecture-recall-review-2026-11-14.md` chỉ còn trên nhánh `feat/text-search-baseline`.

Ngày: 21/11/2026 · Input: `candidate_images_part_01_sorted_approved.csv`
Liên quan: [search-architecture-recall-review-2026-11-14.md](search-architecture-recall-review-2026-11-14.md)

Trạng thái bằng chứng: số liệu ở mục 1 đã chạy trên toàn CSV. Phần đánh giá ảnh dựa
trên 500 dòng `approve=yes` đầu tiên (90 entity). Đã tải 443 ảnh và xem trực tiếp
khoảng 250 ảnh qua contact sheet. Các tỉ lệ rút ra từ phần xem ảnh chỉ là **ước lượng
thô**, chưa phải nhãn.

---

## 1. Dataset thực tế

| Mục | Giá trị |
| --- | --- |
| Số dòng | 100.043 (đây là part 01, dự kiến tổng > 50k ảnh approved) |
| `approve` | yes 28.519 · no 62.652 · trống 8.872 |
| Entity | 2.365 tổng · 2.096 có ≥ 1 ảnh yes · 269 không có ảnh yes nào |
| Số ảnh yes / entity | trung bình 13,6 · trung vị 12 · p90 27 · max 73 |
| Cột | `Entity_ID, Tên entity, URL ảnh, Chiều rộng, Chiều cao, URL trang nguồn, Tiêu đề nguồn, Tên miền, approve` |
| Không có | description, category, brand, thuộc tính, quan hệ |
| Tên entity | 1.350/2.365 có dấu tiếng Việt; 432 có phần ngoặc mô tả loại, ví dụ "Vendal (morphin)", "Wellue DuoEK (máy đo ECG cầm tay)" |
| URL ảnh yes trùng | 100 |

**Domain:** mở hoàn toàn, gồm thuốc, phân bón/thuốc BVTV, thiết bị y tế (MRI, bơm
tiêm điện), phụ tùng ô tô/xe đạp, thời trang, đồ gia dụng IKEA, thức ăn chăn nuôi,
giống lợn, tiền xu, tem, xe khách, đồ chơi… Bài toán là **nhận diện thực thể
cấp instance** (hãng + model), không phải tìm cảnh theo mô tả như dataset văn hoá.

### Quan sát từ 500 ảnh

- **11,4% không tải được** (57/500): 15 lỗi 403 hotlink (IKEA, Walmart, Bulgari…),
  34 lỗi trả 200 nhưng không phải ảnh (HTML/AVIF), còn lại là timeout/502.
  → Ingest phải mirror ảnh về local/R2, không phụ thuộc URL gốc.
- **Chữ trên bao bì là tín hiệu mạnh nhất** với thuốc, phân bón, thức ăn chăn nuôi:
  chữ Latin, Cyrillic (Метронидазол, Синметон), tiếng Nhật (Midori).
  → Cần OCR.
- **Phân biệt mịn**: đèn ray Megaman và Flos Smile, các vành carbon, các balo trekking
  nhìn rất giống nhau. Embedding ảnh tổng quát dễ lẫn giữa các hãng.
- **Nhiều biến thể của cùng một entity**: màu (Tatonka Norix 44, Itoki Act), ảnh mặt
  trước/sau, ảnh gần như trùng nhau (#80/#81).
- **Nhiễu trong `approve=yes`**, ước lượng thô khoảng 10% số ảnh đã xem, gồm hai loại:
  - *Sai entity / trùng tên*: "Túi khô treo lưng Karst 30L" ra balo Nike ACG Karst và
    balo học sinh hiệu Karst; "IKEA MAJVIVA Curtains" ra vỏ chăn; "Đèn lồng đế nến"
    ra đèn lồng giấy đỏ; "Lọc gió Vaico" ra lọc muội khí thải.
  - *Ảnh ngữ cảnh, không thấy sản phẩm*: đèn Schréder OmniBlast ra ảnh sân vận động
    và hầm; dù lượn Niviuk Ikuma ra ảnh giải đấu; thuốc cản quang Gadopiclenol ra ảnh
    chụp MRI; lợn DanBred ra ảnh trang trại kèm lãnh đạo Hòa Phát; lốp WTB Vulpine ra
    ảnh nguyên chiếc xe đạp.
- **Quan hệ nằm trong tiêu đề nguồn**, không nằm trong cột: "Hòa Phát nhập khẩu heo
  giống DanBred", "Goal 2EC (Dow)", "Colflox 0.3% Pharmedic", "GE Signa Pioneer".
  Graph phải được **trích xuất**; CSV không có sẵn.

---

## 2. Những gì luồng hiện tại không còn đúng

| Thành phần | Vấn đề với dataset mới |
| --- | --- |
| `metadata_text` (keyword + tags + description) | Chỉ còn tên. Mọi ảnh của một entity có cùng vector metadata, nên kênh này thành tín hiệu cấp entity và không phân biệt được ảnh trong cùng entity |
| `search_scope.py`, prompt planner, enum 6 category, `label_overrides`, `culture_dataset` | Viết riêng cho văn hoá Việt Nam; gate scope sẽ chặn gần hết query mới |
| `IntentRouter` (`query_router_v1.json`) | Phân loại nhị phân, được train trên query văn hoá; mỗi route lại tạo một pipeline khác nhau |
| 3 route `lexical / hybrid / llm_hybrid` | Ba luồng, ba kiểu output, khó đánh giá chung |
| NumPy exact | **Không chậm ở 50k** (50k × 1152 float32 ≈ 230 MB, một phép matmul chỉ mất vài ms). Lý do nên chuyển là **tính năng**: lọc theo entity/brand/type lấy từ graph, hybrid sparse+dense phía server, gom nhóm theo entity, upsert tăng dần |

**Giữ lại được:** `text.py` (chuẩn hoá tiếng Việt, `accent_key`), logic so cụm từ trong
`EntityIndex` (dùng cho alias), `Siglip2Encoder`, pipeline export trên Kaggle và khung
đánh giá.

---

## 3. Vector DB: chọn Qdrant

Lý do chọn, theo đúng nhu cầu của dataset này:

- **Named vectors trên cùng một point**: `image`, `caption`, `sparse` (BM25 với IDF
  tính phía server) đều gắn với một ảnh.
- **Query API kết hợp prefetch + fusion** (RRF có trọng số hoặc DBSF) trong một request,
  thay cho `HybridRetriever.rank` tự viết.
- **Payload index và filter**: `entity_id ∈ {…}` lấy từ graph, `brand_id`, `type_ids`,
  `image_role`. Đây là cơ chế để kết quả multi-hop từ graph đi vào retrieval.
- **`query_points_groups(group_by="entity_id")`**: trả top entity, mỗi entity vài ảnh.
  Đây đúng là UX cần có khi mỗi entity có khoảng 13 ảnh gần giống nhau.
- Repo đã có [qdrant-cloud-setup.md](qdrant-cloud-setup.md); chạy local bằng Docker.

Không chọn:

- **pgvector**: dùng được ở 50k, nhưng hybrid sparse và group-by phải tự viết.
- **Milvus**: quá nặng so với quy mô này.

**Graph store:** graph có khoảng 2,4k entity, cộng brand, type và hoạt chất thì
khoảng < 20k node. Nên bắt đầu bằng **artifact có version** (`nodes.jsonl`,
`edges.jsonl`) nạp vào bộ nhớ của API (dict kề hoặc NetworkX); duyệt 1–3 hop mất
dưới 1 ms. Chỉ chuyển sang Neo4j khi cần biên tập graph qua UI, ghi đồng thời,
hoặc graph đã lên hàng triệu cạnh. Thêm một service DB lúc này chỉ tăng chi phí vận
hành mà chưa đem lại gì.

---

## 4. Luồng mới

### 4.1 Offline ingest

```text
CSV ──► (a) Mirror & validate ──► (b) VLM enrich (per image) ──► (d) Index Qdrant
   └──► (c) Entity & graph extraction (per entity) ─────────────┘
```

**(a) Mirror & validate**
- Tải ảnh, decode, tính sha256 và perceptual hash; loại bản trùng tuyệt đối và gần
  trùng; ghi danh sách lỗi.
- Dùng `approve=yes` cho index; giữ `no` làm **hard negative** cho đánh giá và hiệu
  chỉnh.

**(b) VLM enrich**: một lần gọi cho mỗi ảnh, output JSON có version
- `caption` (tiếng Việt và tiếng Anh)
- `visible_text` (OCR)
- `object_type`
- `image_role ∈ {product_shot, in_use, context_scene, document, other}`
- `shows_entity ∈ {yes, no, unsure}`, được đánh giá khi đưa tên entity làm giả thuyết

Ghi chú:
- Chạy trên Kaggle T4×2 bằng VLM mở (ví dụ Qwen2.5-VL 7B) hoặc qua API. Có thể dùng
  OCR chuyên dụng nếu VLM đọc chữ nhỏ yếu.
- Output này dùng để **index và lọc mềm**, không phải sự thật.
- Lấy một mẫu, so với nhãn `yes/no` để đo độ chính xác của `shows_entity`. Bộ 62k
  dòng `no` chính là tập kiểm định miễn phí.

**(c) Entity & graph extraction**: làm ở cấp entity, khoảng 2,4k lần gọi, rẻ
- Tách tên thành `brand`, `model`, `product_type`, `alias` (phần trong ngoặc,
  dạng không dấu, tên trên bao bì lấy từ OCR).
- Gom tiêu đề nguồn, rồi cho LLM trích xuất cạnh theo schema cố định, mỗi cạnh có
  `provenance` (tiêu đề hoặc ảnh làm bằng chứng) và `source ∈ {title, ocr, llm_knowledge}`.
- Loại cạnh gợi ý ban đầu:
  - `MADE_BY` (brand)
  - `IS_A` (taxonomy loại sản phẩm, có phân cấp)
  - `HAS_INGREDIENT` (thuốc)
  - `COMPATIBLE_WITH` / `PART_OF` (phụ tùng)
  - `IMPORTED_BY` / `DISTRIBUTED_BY`
  - `ALIAS_OF`
- **Quy tắc:** chỉ cạnh có bằng chứng (`title`/`ocr`) mới được dùng làm **filter
  cứng**. Cạnh `llm_knowledge` chỉ dùng để mở rộng mềm và phải hiển thị là "gợi ý".

**(d) Qdrant**

Collection `images` (mỗi point là một ảnh):

| Vector | Nội dung | Mục đích |
| --- | --- | --- |
| `image` | SigLIP2 image embedding (nên thử so400m-patch14-384 vì cần phân biệt mịn) | Query bằng ảnh, mô tả thị giác |
| `caption` | Embedding text đa ngôn ngữ (ví dụ BGE-M3) của caption + OCR | Mô tả bằng tiếng Việt, khác biệt theo từng ảnh |
| `sparse` | BM25 trên tên + alias + brand + OCR + caption, có bigram âm tiết | Tên riêng, mã model, chữ trên bao bì |

Payload: `entity_id, brand_id, type_ids[] (kèm tổ tiên), image_role, shows_entity,
domain, phash`.

Collection `entities`: vector dense và sparse của tên + alias, dùng cho entity linking.

### 4.2 Online: một pipeline duy nhất, không chia route

Chia route không tối ưu không phải vì tồn tại một bước quyết định, mà vì **mỗi route
chạy một pipeline khác và trả output khác**. Thiết kế mới: mọi query đi qua cùng các
stage và cùng một schema plan. Single intent chỉ là trường hợp `intents` có 1 phần tử.

```text
query/ảnh
  │
  ▼
① Entity linking ─ alias/sparse/dense trên collection `entities` (deterministic, ~ms)
  │
  ▼
② Planner ─ luôn trả về QueryPlan cùng schema:
  │    combination: single | all_of | any_of | separate_groups
  │    intents[]: { mentions[], target_type?, attributes[], relation_path[], exclusions[] }
  │  · Short-circuit: nếu query chỉ gồm entity đã link được, plan được dựng
  │    deterministic. Đây chỉ là tối ưu hoá: plan phải GIỐNG plan LLM sẽ trả
  │    (có test tương đương), không tạo luồng riêng.
  │  · Còn lại thì gọi LLM, với output là JSON schema, kèm cache.
  ▼
③ Graph resolve (theo từng intent) ─ duyệt relation_path → tập entity_id + đường đi giải thích
  │    ví dụ "thuốc nhỏ mắt cùng hoạt chất với Colflox":
  │    Colflox ─HAS_INGREDIENT→ ofloxacin ←HAS_INGREDIENT─ X, X IS_A thuốc nhỏ mắt, X ≠ Colflox
  ▼
④ Retrieve (theo từng intent, chạy song song) ─ Qdrant prefetch image/caption/sparse
  │    + filter entity_id ∈ tập từ graph (nếu có) / type_ids / image_role
  │    + fusion có trọng số, group_by entity_id
  ▼
⑤ Combine ─ separate_groups: mỗi intent một nhóm, có quota
  │          all_of: chấm từng điều kiện, lấy min / geometric mean, KHÔNG dùng max
  │          any_of: RRF hoặc xen kẽ giữa các intent
  ▼
⑥ Verify (tùy chọn) ─ VLM chấm top-N theo từng điều kiện: supported / contradicted / unknown
  ▼
Response ─ items[] + groups[] + explanation (đường đi trên graph, bằng chứng)
```

Các dạng query hệ thống phải xử lý, cũng là các nhóm trong tập đánh giá:

| Loại | Ví dụ | Kênh chính |
| --- | --- | --- |
| Tên entity | "Colflox 0.3%", "colflox" | Linking + sparse |
| Loại + thuộc tính | "ghế văn phòng lưới màu trắng" | caption + image, filter type |
| Chữ trên bao bì | "hộp thuốc có chữ Cefadroxil" | sparse trên OCR |
| 1-hop | "sản phẩm của Osprey" | graph `MADE_BY` → filter |
| Multi-hop | "giống lợn Hòa Phát nhập khẩu", "thuốc cùng hoạt chất với Colflox" | graph path → filter |
| Multi intent | "balo Tatonka và túi mỹ phẩm Osprey" | 2 intent, `separate_groups` |
| Query bằng ảnh | upload ảnh sản phẩm để biết là entity nào | image + group_by entity |

---

## 5. Đánh giá (làm trước khi đổi ranking)

Dataset này có **nhãn miễn phí** mà dataset văn hoá không có:

- **Truy vấn bằng ảnh, leave-one-out**: lấy 1 ảnh yes làm query; các ảnh yes còn lại
  của cùng entity là relevant. Đo Recall@10, mAP và entity Hit@1/@5.
- **Truy vấn bằng tên**: relevant là các ảnh yes của entity. Báo cáo Recall@10
  **kèm |R|**: trung vị |R| = 12 nên trần Recall@10 ≈ 0,83 ở trung vị và thấp hơn với
  entity nhiều ảnh. Báo cáo thêm entity-level Hit@k.
- **Hard negatives**: ảnh `no` của chính entity đó. Đo tỉ lệ ảnh `no` lọt top 10.
  Đây cũng là dữ liệu để hiệu chỉnh `shows_entity`.
- **Multi-hop và multi intent**: viết tay 50–100 query, ground truth sinh từ graph
  đã duyệt. Đo coverage từng intent và độ đúng của đường đi trên graph.
- Split dev/test theo **entity**, không theo ảnh, để tránh rò rỉ.

---

## 6. Lộ trình

| Pha | Việc | Xong khi |
| --- | --- | --- |
| P0 | Ingest CSV: mirror ảnh, dedupe, manifest; dựng bộ đánh giá leave-one-out + tên entity | Có manifest sạch, có số liệu baseline SigLIP2 hiện tại trên NumPy |
| P1 | Qdrant baseline: `image` + `sparse` (tên/alias), group_by entity; chạy song song với NumPy để đối chiếu | Kết quả trùng NumPy ở kênh dense; báo cáo hybrid > image-only |
| P2 | VLM enrich (caption, OCR, image_role, shows_entity) + vector `caption`; thử SigLIP2 so400m | Có số đo cải thiện; hard negative ít lọt top 10 hơn |
| P3 | Graph extraction + QueryPlan v2 + graph resolve + retrieve theo intent | Pass bộ multi-hop/multi intent; mỗi kết quả có đường đi giải thích |
| P4 | Verify/rerank bằng VLM cho query nhiều điều kiện; gỡ code văn hoá (scope, router v1, category enum) | Latency p95 nằm trong ngân sách; bỏ được code cũ |

---

## 7. Quyết định đã chốt (2026-11-21)

1. **Ảnh ngữ cảnh đúng entity: giữ.** Giữ trong index, gắn `image_role`, mặc định xếp
   `product_shot` lên trước. Ảnh sai entity (trùng tên) vẫn phải loại bằng `shows_entity`.
2. **8.872 dòng `approve` trống: bỏ**, không đưa vào index, không dùng làm nhãn.
3. **Cạnh `llm_knowledge`: hiển thị cho người dùng**, kèm nhãn nguồn (`title`, `ocr`,
   `llm_knowledge`) và mức tin cậy để phân biệt với cạnh có bằng chứng. Cạnh `llm_knowledge`
   không được dùng làm hard filter, chỉ dùng để gợi ý/mở rộng.
