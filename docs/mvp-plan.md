# VisualSeek MVP Plan

> Cập nhật triển khai: theo yêu cầu bổ sung, đã có script Kaggle tạo SigLIP2 artifact,
> exact cosine retrieval chạy CPU và RAG tùy chọn qua Ollama. Hướng dẫn tại
> [kaggle-embedding-rag.md](kaggle-embedding-rag.md). Exact search hiện là baseline
> đối chứng; Qdrant/HNSW và benchmark SLO vẫn là giai đoạn tiếp theo. Phần RAG mở rộng
> phạm vi MVP ban đầu và có latency budget riêng cho generation.

## 1. Mục tiêu tài liệu

Tài liệu này xác định một MVP duy nhất, có thể chạy được trên một máy có NVIDIA
RTX 3050, lưu trữ được dữ liệu thật và hỗ trợ tìm kiếm ảnh bằng ảnh hoặc văn bản.

MVP không cố gắng giải quyết toàn bộ bài toán OCR, logo detection, GraphRAG, VLM,
reranking học máy hay production scaling. Mục tiêu của nó là tạo ra một baseline có
thể đo lường, từ đó mọi cải tiến sau này đều phải chứng minh được mức tăng chất lượng
hoặc giảm latency.

Kết quả cuối cùng của MVP phải trả lời được bốn câu hỏi:

1. Hệ thống có ingest, lưu và khôi phục ảnh ổn định không?
2. Người dùng có thể tìm ảnh bằng một ảnh khác hoặc một câu mô tả không?
3. Recall@10 và latency thực tế trên RTX 3050 là bao nhiêu?
4. Kiến trúc có cho phép thay model, re-index và bổ sung OCR/graph mà không viết lại
   toàn bộ hệ thống không?

## 2. Dự án này giống RAG ở đâu?

VisualSeek và RAG có cùng một retrieval pipeline, nhưng khác nhau ở đầu ra cuối cùng.

| RAG truyền thống | VisualSeek MVP |
| --- | --- |
| Tài liệu | Ảnh và metadata |
| Chunk tài liệu | Ảnh hoặc region/crop của ảnh |
| Text embedding | Image/text embedding chung không gian vector |
| Vector database | Qdrant |
| Retrieve top-K chunk | Retrieve top-K asset |
| Rerank context | Rerank asset, chưa nằm trong MVP |
| LLM sinh câu trả lời | Trả danh sách kết quả tìm kiếm |

Vì MVP chưa có generator, tên chính xác của nó là **multimodal retrieval system**, chưa
phải một hệ thống RAG hoàn chỉnh.

Nó trở thành multimodal RAG khi bổ sung bước sau:

```text
query -> retrieve ảnh/metadata liên quan -> tạo evidence context -> VLM/LLM trả lời
```

Ví dụ:

- "Logo trong ảnh thuộc thương hiệu nào và bằng chứng là gì?"
- "Cửa hàng này đã thay đổi biển hiệu từ thời điểm nào?"
- "Các kết quả tìm được có điểm chung gì?"

Generator chỉ nên được thêm sau khi retrieval đủ tốt. Generator không thể sửa một tập
candidate sai và còn làm tăng latency, chi phí cũng như nguy cơ hallucination.

## 3. Graph có thể áp dụng như thế nào?

Graph phù hợp khi sản phẩm cần trả lời về **quan hệ giữa các thực thể**, thay vì chỉ trả
về độ giống giữa hai vector.

### 3.1 Graph dữ liệu đề xuất

```text
(Asset)-[:HAS_REGION]->(Region)
(Asset)-[:DEPICTS]->(Entity)
(Entity)-[:LOCATED_AT]->(Location)
(Asset)-[:OBSERVED_AT]->(Location)
(Asset)-[:VERSION_OF]->(Asset)
(Asset)-[:DERIVED_FROM]->(Asset)
(Entity)-[:SAME_AS]->(Entity)
(Asset)-[:EMBEDDED_BY]->(ModelVersion)
```

Ví dụ graph giúp xử lý:

- Nhiều ảnh storefront cùng thuộc một chi nhánh.
- Một thương hiệu có nhiều logo qua các thời kỳ.
- Một ảnh là bản crop hoặc bản chỉnh sửa của ảnh khác.
- Một địa điểm thay đổi biển hiệu theo thời gian.
- Search tìm thấy region logo, sau đó graph mở rộng sang entity, location và lịch sử.

### 3.2 Quyết định cho MVP

MVP **không triển khai Neo4j hay graph database riêng**. Các node và edge cần thiết được
biểu diễn trước bằng bảng quan hệ trong PostgreSQL:

- `assets`
- `asset_relations`
- `entities`
- `asset_entities`
- `locations`
- `observations`

Lý do:

- Baseline chưa có truy vấn graph đủ phức tạp để biện minh thêm một database.
- PostgreSQL đã là source of truth và dễ backup, transaction, migration.
- Qdrant chịu trách nhiệm candidate retrieval; PostgreSQL chịu trách nhiệm identity và
  relationship lookup.

Chỉ cân nhắc graph database khi có một trong các điều kiện sau:

- Truy vấn thường xuyên đi qua từ ba hop trở lên.
- Entity resolution và visual lineage trở thành use case chính.
- Một truy vấn cần khám phá hàng nghìn quan hệ động.
- PostgreSQL recursive CTE hoặc join trở thành bottleneck đã được đo lường.

Graph không thay vector search. Luồng đúng là:

```text
vector search lấy candidate -> graph mở rộng/kiểm chứng quan hệ -> rerank/aggregate
```

## 4. Định nghĩa MVP

### 4.1 User stories bắt buộc

1. Người dùng upload một ảnh vào hệ thống.
2. Hệ thống lưu ảnh gốc vào object storage và metadata vào PostgreSQL.
3. Hệ thống normalize ảnh, tạo embedding và index vector vào Qdrant.
4. Người dùng gửi một ảnh query và nhận top 10 ảnh gần nhất.
5. Người dùng gửi một câu mô tả và nhận top 10 ảnh phù hợp.
6. Người dùng mở được thumbnail hoặc ảnh của từng kết quả.
7. Người vận hành có thể xem trạng thái `pending`, `processing`, `ready` hoặc `failed`.
8. Người vận hành có thể re-index asset khi đổi model.
9. Hệ thống có script đánh giá Recall@10 và benchmark P50/P90/P95.

### 4.2 Ngoài phạm vi MVP

- OCR và lexical/hybrid search.
- Tự động detect logo hoặc object crop.
- Neural reranker hoặc VLM reranker.
- Sinh câu trả lời bằng LLM/VLM.
- Graph database riêng.
- Learned entity resolution.
- Authentication, billing và multitenancy hoàn chỉnh.
- Kubernetes, autoscaling và distributed training.
- Video search và real-time camera ingestion.
- Fine-tune model embedding.

Các mục này không bị loại bỏ khỏi roadmap; chúng chỉ không được phép làm chậm việc đo
baseline đầu tiên.

## 5. Chỉ tiêu chấp nhận MVP

### 5.1 Phạm vi benchmark

- Model được load sẵn, không tính cold start.
- Không tính thời gian upload qua Internet.
- Input query đã được API nhận đầy đủ.
- Dataset benchmark ban đầu tối đa 100.000 ảnh.
- Benchmark trên đúng máy RTX 3050 dự kiến triển khai.
- Báo cáo concurrency 1 và 4; không chỉ đo một request đơn lẻ.

### 5.2 Quality gates

| Metric | Điều kiện MVP |
| --- | --- |
| ANN Recall@10 so với exact search | `>= 0.98` |
| Relevance HitRate@10 trên tập nhãn MVP | `>= 0.85` |
| Search-core P90, warm, concurrency 1 | `<= 100 ms` |
| Retrieval-only P90 | `<= 30 ms` |
| Tỉ lệ ingest thành công | `>= 99%` trên tập test hợp lệ |
| Re-index consistency | Không mất asset; số point `ready` khớp Postgres |

`ANN Recall@10` đo chất lượng của HNSW so với brute-force exact neighbors.
`Relevance HitRate@10` đo xem ít nhất một kết quả đúng có nằm trong top 10 hay không.
Hai metric này không được dùng thay thế cho nhau.

Nếu một query có nhiều đáp án đúng, evaluation runner phải báo thêm relevance
`Recall@10`:

```text
Recall@10 = số relevant assets xuất hiện trong top 10 / tổng số relevant assets
```

Ngưỡng relevance là ngưỡng khởi đầu cho tập dữ liệu MVP, không phải cam kết chất lượng
cho mọi domain. Mỗi domain mới cần một evaluation slice riêng.

## 6. Kiến trúc MVP

```text
                              INGESTION

Browser -> FastAPI -> MinIO/R2 ------------------------------+
                   -> PostgreSQL asset + job                 |
                                |                            |
                                v                            |
                         Indexing worker                      |
                                |                            |
                   normalize -> inference service            |
                                |                            |
                                v                            |
                              Qdrant                          |
                                                             |
                               SEARCH                        |
                                                             |
Browser -> FastAPI -> inference service -> Qdrant top-K -----+
                   -> PostgreSQL metadata -> signed image URL
```

### 6.1 Service tối thiểu

| Service | Trách nhiệm |
| --- | --- |
| `web` | Upload, image/text query, render top 10 và trạng thái asset |
| `api` | HTTP contract, validation, orchestration và signed URL |
| `indexer` | Poll job từ Postgres, preprocess, embed và upsert Qdrant |
| `inference` | Giữ đúng một bản model trong GPU memory |
| `postgres` | Source of truth cho asset, job, model version và relation |
| `qdrant` | Vector index có thể rebuild |
| `object-storage` | Ảnh gốc, ảnh normalized và thumbnail |

MVP dùng PostgreSQL job table thay vì thêm Redis/Kafka. Worker lấy job bằng
`SELECT ... FOR UPDATE SKIP LOCKED`. Cách này giữ job bền vững khi process bị restart
và giảm một service chưa cần thiết.

Inference phải là một process riêng và chỉ load model một lần. Không load model trong
mỗi FastAPI worker vì RTX 3050 có VRAM hạn chế.

## 7. Lựa chọn kỹ thuật baseline

### 7.1 Model

- Model: `google/siglip2-base-patch16-224`.
- Inference đầu tiên: PyTorch FP16 với `torch.inference_mode()`.
- Normalize vector trước khi ghi Qdrant.
- Text và image dùng đúng processor, tokenizer và checkpoint version.
- Chỉ chuyển ONNX/TensorRT sau khi đã có correctness baseline.

Model artifact phải được pin bằng tên checkpoint và revision/commit, không dùng một
floating `latest` không thể tái lập.

### 7.2 Preprocessing

Baseline chỉ thực hiện:

1. Kiểm tra magic bytes và MIME type.
2. Giới hạn kích thước file và số pixel để tránh decompression bomb.
3. Áp dụng EXIF orientation.
4. Chuyển RGB/sRGB.
5. Resize theo processor của model.
6. Tạo thumbnail phục vụ UI.

Không tự crop logo, không OCR và không sinh caption trong MVP.

### 7.3 Qdrant

- Một collection theo embedding model version, ví dụ
  `visual_assets_siglip2_base_224_v1`.
- Cosine similarity.
- Một point cho một asset trong MVP.
- Payload chỉ chứa dữ liệu cần filter hoặc hydrate nhanh:
  `asset_id`, `status`, `content_type`, `created_at`, `model_version`.
- Bắt đầu với `m=16`, `ef_construct=128`.
- Sweep `hnsw_ef` ở `32, 64, 96, 128` bằng evaluation runner.

Không hardcode vector dimension trong nhiều module. Embedding adapter phải công bố
dimension và collection factory sử dụng giá trị đó.

### 7.4 Source of truth

- Object storage là source of truth của binary image.
- PostgreSQL là source of truth của metadata và processing state.
- Qdrant là derived index và luôn phải rebuild được.

Không ghi ảnh binary vào PostgreSQL hoặc Qdrant.

## 8. Data model tối thiểu

### 8.1 `assets`

| Field | Ý nghĩa |
| --- | --- |
| `id` | UUID ổn định dùng xuyên suốt hệ thống |
| `object_key` | Key ảnh gốc trong object storage |
| `thumbnail_key` | Key thumbnail |
| `sha256` | Exact duplicate detection và idempotency |
| `content_type` | MIME type đã xác minh |
| `width`, `height` | Kích thước ảnh |
| `status` | `pending/processing/ready/failed` |
| `model_version` | Phiên bản embedding đã index |
| `created_at`, `updated_at` | Audit timestamps |
| `error_code` | Lỗi xử lý có cấu trúc |

### 8.2 `index_jobs`

| Field | Ý nghĩa |
| --- | --- |
| `id` | Job UUID |
| `asset_id` | Asset cần index |
| `target_model_version` | Model đích |
| `status` | `pending/running/succeeded/failed` |
| `attempts` | Số lần retry |
| `available_at` | Backoff/retry scheduling |
| `locked_at`, `worker_id` | Job ownership |
| `last_error` | Diagnostic cuối cùng |

### 8.3 Chuẩn bị cho graph, chưa dùng trong MVP

`asset_relations` có thể được migration sẵn nhưng chưa cần UI/API:

| Field | Ý nghĩa |
| --- | --- |
| `source_asset_id` | Node nguồn |
| `relation_type` | `derived_from/version_of/similar_to` |
| `target_asset_id` | Node đích |
| `confidence` | Độ chắc chắn của relation |
| `provenance` | Human, rule hay model nào tạo edge |

## 9. API contract dự kiến

### 9.1 Asset APIs

```text
POST   /api/v1/assets
GET    /api/v1/assets/{asset_id}
GET    /api/v1/assets/{asset_id}/content
POST   /api/v1/assets/{asset_id}/reindex
```

`POST /assets` trả `202 Accepted` cùng `asset_id` và trạng thái `pending`. Upload trùng
`sha256` phải idempotent hoặc trả về asset hiện có theo policy đã chọn.

### 9.2 Search APIs

```text
POST /api/v1/search/image
POST /api/v1/search/text
```

Response tối thiểu:

```json
{
  "query_id": "uuid",
  "model_version": "siglip2-base-224-v1",
  "took_ms": 42.7,
  "results": [
    {
      "asset_id": "uuid",
      "score": 0.82,
      "rank": 1,
      "thumbnail_url": "signed-url"
    }
  ]
}
```

Không trả embedding ra public API.

## 10. Evaluation dataset và benchmark

### 10.1 Dataset tối thiểu

- 5.000-20.000 ảnh cho functional MVP; scale test sinh thêm đến 100.000 point.
- Ít nhất 200 query được gán nhãn; mục tiêu tốt hơn là 500 query.
- Có cả image query và Vietnamese/English text query.
- Có hard negatives: cùng màu, cùng bố cục, cùng loại vật thể nhưng sai asset.
- Train/tune queries phải tách khỏi final evaluation queries.

File judgment có thể dùng JSONL:

```json
{"query_id":"q1","query_type":"image","query":"queries/q1.jpg","relevant_asset_ids":["a1","a7"]}
{"query_id":"q2","query_type":"text","query":"quán cà phê có biển hiệu màu đỏ","relevant_asset_ids":["a9"]}
```

### 10.2 Báo cáo bắt buộc

- ANN Recall@1/10.
- Relevance HitRate@1/10.
- Relevance Recall@10 khi có nhiều relevant assets.
- MRR@10.
- P50/P90/P95 end-to-end server latency.
- Latency breakdown: preprocess, inference, Qdrant, hydration.
- Throughput tại concurrency 1 và 4.
- GPU VRAM/utilization và Qdrant CPU/RAM.

Mỗi thay đổi model hoặc HNSW config phải tạo một report có version, không ghi đè report
cũ.

## 11. Kế hoạch triển khai

### Milestone 0 - Khóa benchmark contract

Deliverables:

- Chốt query types và định nghĩa metric.
- Chuẩn bị dataset/judgment format.
- Thêm cấu hình model version và collection name.
- Ghi nhận GPU/VRAM, CPU và RAM của máy benchmark.

Definition of done:

- Có thể chạy một command evaluation rỗng hoặc fixture nhỏ và tạo report JSON.

### Milestone 1 - Storage vertical slice

Deliverables:

- PostgreSQL migrations cho `assets` và `index_jobs`.
- S3-compatible storage adapter cho MinIO/R2.
- Upload API, validation, SHA-256 và thumbnail.
- Asset status API.

Definition of done:

- Upload ảnh, restart API, vẫn đọc được metadata và binary.
- File lỗi hoặc quá lớn bị từ chối có error code rõ ràng.

### Milestone 2 - Embedding và indexing

Deliverables:

- SigLIP2 text/image embedding adapter.
- Một GPU inference process giữ model resident.
- Qdrant collection factory và adapter.
- Index worker có retry và idempotent upsert.
- Re-index theo `model_version`.

Definition of done:

- Asset đi từ `pending` tới `ready`.
- Cùng asset/job retry không tạo point trùng.
- Có thể rebuild collection từ Postgres + object storage.

### Milestone 3 - Search product slice

Deliverables:

- Image search API.
- Text search API.
- Metadata hydration và signed thumbnail URLs.
- UI upload/query và grid top 10.
- Structured timing log cho từng stage.

Definition of done:

- Một người dùng có thể hoàn thành demo ingest và hai loại search từ browser.
- Search không đọc ảnh corpus từ object storage trong critical path.

### Milestone 4 - Evaluation và performance gate

Deliverables:

- Exact-search oracle.
- ANN/relevance evaluator.
- Load-test script với input thật.
- Sweep HNSW `hnsw_ef`.
- Baseline report được lưu theo model/config/git commit.

Definition of done:

- Đạt quality gates hoặc có report chỉ rõ stage nào chưa đạt.
- P90 được đo sau warm-up, không suy đoán từ request đơn lẻ.

### Milestone 5 - MVP hardening

Deliverables:

- Graceful shutdown và readiness check cho inference/indexer.
- Retry/backoff và dead-job inspection.
- Backup/restore test cho PostgreSQL và object storage.
- Reconciliation job kiểm tra Postgres `ready` so với Qdrant points.
- Giới hạn upload, timeout và request ID xuyên suốt pipeline.

Definition of done:

- Restart từng service không làm mất asset hoặc job.
- Demo và benchmark có thể chạy lại từ README mà không cần thao tác ẩn.

## 12. Mapping vào repository hiện tại

```text
services/api/
  HTTP routes, schemas, application orchestration

services/indexer/                 # thêm mới
  durable indexing worker

services/inference/               # thêm mới hoặc entrypoint dùng package embedding
  single GPU model server

packages/preprocessing/
  image validation and normalization implementation

packages/embedding/
  SigLIP2 adapter, model metadata and normalization

packages/retrieval/
  Qdrant adapter, collection setup and search

packages/evaluation/
  exact oracle, retrieval metrics and report writer

services/api/src/.../infrastructure/
  PostgreSQL and S3-compatible adapters

apps/web/
  upload, query and results UI only
```

Contracts hiện có cần được mở rộng bằng `model_version`, embedding dimension, query
modality, timing và processing status; vendor SDK vẫn phải nằm trong adapter.

## 13. Roadmap sau MVP

### V1.1 - Hybrid logo/place search

- PP-OCRv5 mobile Latin.
- PostgreSQL `pg_trgm` hoặc sparse BM25.
- User-selected crop trước, automatic detector sau.
- Global + region vectors.
- RRF fusion và group theo asset.

### V1.2 - Entity và temporal layer

- `entities`, `locations`, `observations` và relation APIs.
- Entity aggregation thay vì chỉ asset ranking.
- Visual history và version/change detection.
- Graph traversal vẫn chạy trên PostgreSQL trước.

### V1.3 - Learned reranking

- Thu thập relevant/irrelevant feedback.
- Hard-negative mining.
- Reranker nhẹ bằng Logistic Regression hoặc LightGBM.
- A/B evaluation theo query slice.

### V2 - Multimodal GraphRAG

- Retrieve asset/region candidates.
- Mở rộng evidence qua entity graph.
- VLM tạo mô tả có dẫn chứng từ asset IDs.
- LLM tổng hợp câu trả lời và từ chối khi evidence không đủ.
- Chỉ thêm graph database nếu benchmark quan hệ chứng minh cần thiết.

## 14. Rủi ro chính và cách kiểm soát

| Rủi ro | Cách kiểm soát |
| --- | --- |
| RTX 3050 không đạt P90 | Giữ một model process, FP16, warm model; profile trước ONNX/TensorRT |
| Recall tốt trên demo nhưng kém domain thật | Tạo hard negatives và evaluation slice theo domain |
| Đổi model làm mất khả năng tái lập | Pin checkpoint revision và version collection |
| Qdrant và Postgres lệch trạng thái | Idempotent upsert và reconciliation job |
| Upload làm nghẽn search | Ingest bất đồng bộ; online inference có ưu tiên cao hơn batch index |
| Scope phình sang OCR/graph/VLM | Tuân thủ danh sách ngoài phạm vi cho tới khi baseline report hoàn tất |
| Duplicate ảnh làm metric sai | SHA-256 cho exact duplicate; perceptual dedup thêm sau MVP |

## 15. Demo chấp nhận cuối cùng

Một demo MVP hoàn chỉnh phải thực hiện được tuần tự:

1. Khởi động stack bằng Docker Compose.
2. Upload một thư mục ảnh qua UI hoặc CLI.
3. Quan sát asset chuyển từ `pending` sang `ready`.
4. Search bằng một ảnh chưa dùng làm corpus input.
5. Search bằng một câu tiếng Việt.
6. Mở được top 10 thumbnails và asset metadata.
7. Chạy evaluation command và sinh report Recall/HitRate/MRR.
8. Chạy load test và sinh report P50/P90/P95.
9. Xóa rồi rebuild Qdrant collection từ source of truth mà không upload lại ảnh.

MVP được xem là hoàn thành khi demo này chạy lặp lại được và đạt các quality gates ở
Mục 5. Sau thời điểm đó, OCR, crop, hybrid search, graph và RAG mới được thêm như những
thí nghiệm có baseline để so sánh.
