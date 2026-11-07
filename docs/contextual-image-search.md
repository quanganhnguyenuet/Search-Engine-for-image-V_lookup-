# Tìm ảnh theo mô tả, tags/description và LLM

Đã bổ sung inverted lexical index, weighted RRF, query roles, shortlist rerank rộng
hơn và relevance gate theo nhãn người chấm. Xem
[cấu hình và quy trình đánh giá mới](search-fusion-setup.md); các ví dụ bên dưới có
thể ghi lại cấu hình baseline trước rollout.

Rà soát relevance ngày 05/11/2026: xem [audit logic search và intent](search-logic-audit.md).
Full coverage xác nhận mọi asset đã có vector; chưa đồng nghĩa đáp ứng đủ các
thuộc tính, phủ định hoặc nhiều intent của truy vấn.

## Luồng đã triển khai

Khi `SEARCH_LLM_ENABLED=true` và `SEARCH_ROUTING_MODE=auto`, tên/tag và mô tả thường
chạy local, chỉ reasoning hoặc intent chưa chắc chắn được gửi LLM. Router là bộ
phân loại nhỏ học từ ví dụ, không gọi OpenAI để chọn đường. `always` vẫn có thể dùng
để đưa mọi query qua planner. Xem [semantic query planning](semantic-query-planning.md)
cho schema, clarification, cấu hình và các query kiểm tra.

```text
Yêu cầu tiếng Việt + bộ lọc chủ đề
  -> tên/tag rõ hoặc nhập nhằng theo index: lookup/hỏi lại local
  -> mô tả trực tiếp: hybrid ảnh + description + lexical local
  -> reasoning hoặc intent chưa chắc chắn: LLM lập kế hoạch ngữ nghĩa
     -> nhập nhằng: hỏi lại trước khi tìm
     -> rõ nghĩa: encode mô tả tích cực bằng SigLIP2 local
        -> hybrid ảnh/metadata/lexical + bộ lọc loại trừ
        -> tùy chọn LLM kiểm tra điều kiện và xếp lại shortlist
        -> ảnh có sẵn trong index
```

Plan tách điều kiện bắt buộc, ưu tiên, loại trừ và truy vấn tích cực. Chỉ encode
đối tượng muốn tìm, tránh câu gốc chứa đối tượng bị loại trừ kéo kết quả về đối tượng
đó. Bước rerank nhận nguyên request và mọi điều kiện trong plan, có thể trả ít hơn
`top_k` hoặc không có ảnh nếu thiếu bằng chứng. Không sinh ID ảnh hoặc đổi bộ lọc
category. Query và plan được cache theo cả query lẫn category; lỗi model trả lỗi rõ
ràng, không fallback ngầm sang local.

Khi `SEARCH_LLM_ENABLED=false` (mặc định thư viện), hoặc request đặt `use_llm=false`,
query đi qua lookup tên/tag và hybrid local. Tên không dấu có nhiều cách đọc được
đề xuất cách hiểu có dấu theo corpus. Lookup tên giữ dấu, ưu tiên tên chính xác,
biến thể tên rồi tag; không cần OpenAI key. `/search/semantic` và keyword search vẫn
là baseline riêng, không tự gọi LLM. `/search/rag` là tác vụ tạo câu trả lời từ bằng
chứng riêng và yêu cầu bật LLM.

Structured Outputs kiểm tra schema, refusal, finish reason và ứng viên hợp lệ;
không chứng minh suy luận đúng. Key chỉ ở backend, query và chú thích ứng viên được
gửi tới provider khi bật LLM. Mặc định rerank chỉ dựa annotation; vision rerank là
lựa chọn rõ ràng trong [hướng dẫn fusion](search-fusion-setup.md). Chất lượng tên/tag
và description ảnh hưởng trực tiếp đến kết quả. Cần chấm relevance trên model thật
để đánh giá suy luận, không dùng cosine/RRF như xác suất đúng.

## Artifact embedding

Schema v1 cũ vẫn dùng được cho semantic/image search. Contextual search yêu cầu v2:

| File | Nội dung |
| --- | --- |
| `vectors.npy` | Image vectors, float32, L2 |
| `metadata_vectors.npy` | Vector title/keyword + tags + description, float32, L2 |
| `assets.jsonl` | Metadata cùng thứ tự hàng với cả hai ma trận |
| `manifest.json` | Model revision, dimension, count, scope, checksums, metadata version |

Description dài được chia theo tokenizer thành các đoạn vừa 64 token, embed từng
đoạn rồi mean-pool và L2 normalize; không âm thầm bỏ phần cuối. Không embedding VQA
answers hoặc suy luận lịch sử/vùng miền không quan sát được trong ảnh.

Export offline cho phép ảnh corpus tối đa 72 MP (corpus hiện có 8 ảnh 36–67 MP).
Ảnh query do người dùng upload vẫn giới hạn 36 MP và 10 MiB như trước. Cách decode,
EXIF/RGB và resize của model không đổi; ảnh gốc/ID SHA-256 được giữ nguyên.

### Chạy ngay bằng artifact demo local

Đã tạo `data/embeddings/culture-contextual-demo-v1`, có 120 image vectors và 120
metadata vectors từ corpus local. Đây là demo, chưa phải index full 15.423 ảnh.
Checkout mới cần tự tạo bằng lệnh sau (dữ liệu/model không nằm trong Git):

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python scripts/enrich_embeddings.py \
  --source data/embeddings/culture-siglip2 \
  --output data/embeddings/culture-contextual-demo-v1 \
  --cache-dir data/models/huggingface --batch-size 16
```

Bỏ hai biến offline nếu cần tải model lần đầu. Giữ môi trường đã cài torch,
transformers theo [hướng dẫn runtime](kaggle-embedding-rag.md); tránh `uv sync` gỡ
dependency model được cài thủ công. Script enrich không encode lại pixel và có
checkpoint metadata, kiểm tra checksum khi resume.

Trong `.env` ở repo root:

```dotenv
EMBEDDING_ARTIFACT_DIR=data/embeddings/culture-contextual-demo-v1
EMBEDDING_CACHE_DIR=data/models/huggingface
EMBEDDING_DEVICE=cpu
EMBEDDING_CPU_THREADS=4
CORPUS_MANIFEST_PATH=data/processed/culture-search/assets.jsonl
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
OPENAI_BASE_URL=https://api.openai.com/v1
SEARCH_LLM_MODEL=gpt-4o-mini
SEARCH_LLM_TIMEOUT_SECONDS=90
SEARCH_LLM_ENABLED=true
SEARCH_LLM_RERANK=false
RAG_MODEL=gpt-4o-mini
```

Điền key thật ở máy của bạn; không cần khởi động Ollama. Nếu dùng API gateway được
cấp, đặt `OPENAI_BASE_URL` là URL gốc kết thúc bằng `/v1` (không gồm
`/chat/completions`); gateway cần hỗ trợ Structured Outputs cho model đã chọn.
Mỗi lệnh dưới đây chạy ở terminal riêng, tại repo root:

```bash
.venv/bin/python -m uvicorn visualseek_api.main:app \
  --app-dir services/api/src --host 127.0.0.1 --port 8000 --workers 1
```

```bash
npm --prefix apps/web run dev
```

Mở `http://localhost:3000`, chọn **Tìm theo yêu cầu**. Lần đầu SigLIP2 cần load model
local; thời gian gọi OpenAI tách riêng khỏi embedding/retrieval. Restart API sau khi
đổi `.env`; không cần thêm dependency Python vì adapter dùng `httpx` có sẵn.

Docker: `docker compose up --build`. Artifact directory trong container được
chọn bằng `DOCKER_EMBEDDING_ARTIFACT_DIR`, mặc định demo-v1 dưới `/data/embeddings`.

### Dùng GPT-5.6 Luna

```dotenv
SEARCH_LLM_MODEL=gpt-5.6-luna
RAG_MODEL=gpt-5.6-luna
OPENAI_REASONING_EFFORT=low
```

Adapter có nhánh tương thích cho model này: không gửi `temperature=0`, cấu hình
`reasoning_effort` và dành ít nhất 2.048 completion tokens cho cả reasoning và JSON.
Request cũ có `temperature=0` đã được kiểm tra thực tế và bị API từ chối HTTP 400
(`unsupported_value`, param `temperature`). GPT-4o mini giữ nguyên request cũ.
[Thông tin model chính thức](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

Đã chạy query thật với GPT-5.6 Luna và full index, trả plan hợp lệ và kết quả hybrid;
model planning khoảng 2,86 giây trong một lượt smoke. Đây chưa phải phép đo relevance
hoặc SLO. 72 test đã pass sau khi bổ sung kiểm tra nhánh tương thích.

Sau khi sửa `.env` hoặc adapter, restart API rồi gọi `POST /api/v1/search/warmup`
với `{"include_llm":true}`. `GET /search/status` chỉ đọc trạng thái, không chạy
warm-up. `model_loaded`/`encoder_ready` thuộc SigLIP2 local; tên trong `query_model`
chỉ là cấu hình. `query_model_last_success=true` mới ghi nhận một lần gọi LLM thành
công gần nhất, không phải phép đánh giá chất lượng suy luận hay intent.

### Full corpus: Kaggle hoặc CPU local

Bundle code Kaggle đã bao gồm helper metadata. Tái tạo code ZIP trước upload:

```bash
.venv/bin/python scripts/prepare_kaggle_bundle.py
```

Trong notebook cũ, thêm `--with-metadata` vào lệnh `kaggle_embed.py`, giữ
`--limit-per-category 0`, dùng output mới. ZIP sẽ có cả `metadata_vectors.npy`.
Download/import vẫn dùng `scripts/import_embeddings.py`; script hỗ trợ schema v1/v2.

Lệnh tương đương CPU local, có thể chạy lại cùng output để resume:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -u scripts/kaggle_embed.py \
  --input-root data/raw/vietnamese-culture-vqa \
  --manifest data/processed/culture-search/assets.jsonl \
  --output data/embeddings/culture-contextual-full-v1 \
  --cache-dir data/models/huggingface \
  --limit-per-category 0 --device cpu --batch-size 8 --with-metadata --zip
```

Không serve artifact đang build. Sau khi hoàn tất, kiểm tra `failures.jsonl`, hai
ma trận, revision và `/search/status`: `indexed=corpus_total=15423`,
`coverage_complete=true`, `metadata_ready=true`. `complete=true` riêng lẻ không đủ.
Sau đó đổi `.env` sang thư mục full và restart API. Rollback bằng đường dẫn demo cũ
và restart; không ghi đè/xóa artifact cũ.

Ngày 05/11/2026, job CPU full đã hoàn tất: **15.423 image vectors và 15.423 metadata
vectors**, mỗi vector 768 chiều, không có ảnh lỗi. Đã đối soát đúng tập ID corpus,
checksum/L2 normalization, đường dẫn ảnh và CRC của ZIP. Hybrid search trên full
index đã chạy thành công với query làm gốm và category thủ công mỹ nghệ, không gọi LLM.

`.env` local đã được chuyển sang `data/embeddings/culture-contextual-full-v1`;
API đang chạy cần restart để nạp index mới. Bản demo được giữ nguyên để rollback.
Các artifact/report local không nằm trong Git:

- `data/embeddings/culture-contextual-full-v1/culture-embeddings.zip`
- `data/embeddings/culture-contextual-full-v1/verification_report.json`
- `data/embeddings/culture-contextual-full-v1/full-contextual-smoke.json`
- Log: `data/exports/contextual-full-build.log`.

12 ảnh thiếu tags/description dùng tên/chủ đề để tạo metadata vector. Đợt này không
sinh caption mới từ pixel. Hoàn tất embedding/coverage chưa chứng minh đạt quality
gates relevance hoặc load-test SLO; vẫn cần bộ nhãn đánh giá.

## API

```text
POST /api/v1/search/text
GET  /api/v1/search/status
POST /api/v1/search/warmup
```

```json
{"query":"bánh chưng","category":"am_thuc","top_k":24}
```

Query: 1–1000 ký tự sau trim/NFC, top_k: 1–50. Category chỉ do client chọn.

| `use_llm` | Hành vi |
| --- | --- |
| Bỏ trường này hoặc `null` | Auto: tên/tags -> lexical; mô tả thường -> local hybrid; query có dấu hiệu logic -> LLM |
| `false` | Không gọi OpenAI, kể cả query phức tạp; tên/tags vẫn dùng tra cứu nhanh |
| `true` | Buộc dùng planner, kể cả tên ngắn; dùng để so sánh hoặc khi router nhận sai |

Các giá trị chỉ nhận boolean hoặc null. Endpoint
`/semantic` cũ giữ vai trò baseline chỉ dùng image vectors; câu vượt 64 token trả
422 thay vì bị cắt. `/text` chia đoạn câu dài nên không mất clause ở cuối.

Response giữ `items/total/page/pages/libraryTotal/counts` và bổ sung
`interpretation`, `query_model`, `llm_reranked`, `plan_cache_hit`, `score_type`,
`corpus_total`, `coverage_complete`, artifact fingerprints và timing từng bước.
`total` là số ảnh trả về, không phải tổng số ảnh đúng. Semantic/contextual không
phân trang toàn corpus. Có thể nhận ít hơn top_k khi filter ít ảnh hoặc bật rerank.

Response thêm `search_route=lexical|hybrid|llm_hybrid`, `routing_reason`, `llm_used`,
`llm_call_count` và `vector_cache_hit`. Query không dùng planner có `interpretation`
và `query_model` bằng null. `llm_call_count` đếm API call của request hiện tại, nên
plan đã cache có thể dùng nhánh `llm_hybrid` nhưng không phát sinh planning call mới.
`encoder_ready` và `query_model_last_success` có thể vẫn false sau query lexical;
đây là bình thường vì query đó không cần hai model.

Item có `match_kind=title_exact|title_variant|title_phrase|tag_exact|tag_variant`
ở nhánh lexical và `match_reason` mô tả bằng chứng tên/tag thực tế. Không gọi đó là
kết quả VLM xác nhận chủ thể chính; chất lượng vẫn phụ thuộc annotation.

Warm-up body `{"include_llm":true}` tải encoder, chạy inference mẫu và probe LLM.
Status phân biệt index/metadata, encoder warm và lần lập plan thành công gần nhất;
không coi model đã từng thành công là bảo đảm OpenAI API luôn khả dụng.
Status có `llm_provider=openai` và `openai_key_configured`, không trả giá trị key.
Để lấy endpoint/API key Qdrant, xem [hướng dẫn Qdrant](qdrant-cloud-setup.md).
Retrieval hiện vẫn là NumPy; cấu hình Qdrant chưa tự chuyển backend tìm kiếm.

```bash
curl -s http://127.0.0.1:8000/api/v1/search/warmup \
  -H 'Content-Type: application/json' -d '{"include_llm":true}'
```

Thiếu key trả `openai_key_missing`; key/quyền model không hợp lệ trả
`openai_authentication_failed`; quota/rate limit trả `openai_rate_limited`.

## Đánh giá

Runner đã có, **chưa có nhãn người thật nên chưa công bố HitRate/nDCG**. Chuẩn bị
query JSONL theo [query mẫu](text-search-evaluation-sample.jsonl), mở rộng lên 200
query như kế hoạch và giữ các biến thể cùng intent trong cùng split.

Đã có generator bộ nháp 200 query đúng tỷ lệ nhóm và split 100/100, giữ các biến
thể cùng intent ở một split. Đây là query soạn theo domain, chưa phải log người dùng
thật; `answerable=null` cho đến khi người chấm xác nhận. Không tự xem query ngoài
miền là chắc chắn không có đáp án trong corpus.

```bash
.venv/bin/python scripts/prepare_text_search_queries.py
```

File tạo tại `data/evaluation/queries-v1.jsonl`; script không ghi đè file đã có.

```bash
.venv/bin/python scripts/evaluate_text_search.py collect \
  --queries docs/text-search-evaluation-sample.jsonl \
  --output data/evaluation/contextual-pool.json --use-llm
```

Cần API và web đang chạy. Script lấy pool từ semantic, contextual và keyword trên
cùng query/category. Keyword API hiện trả 24 ảnh/trang, hai nhánh còn lại tối đa 30
trong lần collect; metric @10 vẫn dùng cùng cutoff. Không dùng query quá dài cho
semantic baseline. Nếu đổi ranking/model, collect và chấm lại candidate chưa có nhãn.

Người chấm xem `candidates` theo ID đã sắp xếp, không xem `rankings` hoặc `scores`;
điền toàn bộ `judgments` đang `null` thành 0/1/2 và xác nhận `answerable` theo pixel
ảnh. 2 = đáp ứng phần chính; 1 = một phần; 0 = không phù hợp. Pool chưa gán nhãn sẽ
bị từ chối chấm điểm, không tự coi candidate chưa biết là sai.

```bash
.venv/bin/python scripts/evaluate_text_search.py score \
  --pool data/evaluation/contextual-pool.json \
  --split held-out --output data/evaluation/contextual-report.json

.venv/bin/python scripts/benchmark_text_search.py \
  --queries docs/text-search-evaluation-sample.jsonl \
  --requests 200 --concurrency 1 --output data/evaluation/latency-c1.json
```

Lặp benchmark với concurrency 4. Thêm `--use-llm` để đo riêng đường có LLM; không
gộp với mục tiêu latency retrieval. Report ghi cold request, P50/P90/P95 warm,
cache hit, lỗi và fingerprint index. Bộ query lặp có thể hit plan cache; phải đọc
breakdown, không gọi mọi mẫu là cache miss. Không báo corpus Recall từ nhãn pool.

Thêm `--auto-llm` để đo routing tự động; không truyền flag nào giữ benchmark ở chế
độ `use_llm=false` như trước. `--auto-llm` và `--use-llm` loại trừ nhau.

Đã đo riêng query `bánh chưng` trên full index, API localhost, index đã sẵn sàng và
backend không có OpenAI key: 200 request ở concurrency 1 có HTTP P90 **3,07 ms**;
200 request ở concurrency 4 có P90 **11,30 ms**; không lỗi, không gọi LLM, encoder
vẫn chưa load sau nhánh exact. Request đầu xây lookup sau khi đã load index mất
khoảng 218 ms. Một query mô tả local lặp lại sau cache vector mất khoảng 190 ms.
Đây là benchmark query cụ thể trên máy local, không gồm mạng Internet/render ảnh
và không phải SLO cho mọi query. Report:
`data/diagnostics/fast-query-routing-benchmark.json`.

`npm run test:contextual` mặc định test đường auto không cần OpenAI key. Đặt
`TEST_CONTEXTUAL_LLM=1` để kiểm tra thêm planning thật, cần key và có API usage.
Sau thay đổi routing đã pass 101 test Python, Ruff, TypeScript typecheck, production
build Webpack và smoke test web production → API → ảnh thật với OpenAI key rỗng.

Sau sửa ranking single intent, query `bánh chưng` trên full corpus có top 5 đều là
ảnh ẩm thực với title `bánh chưng Tết`/`bánh chưng gù`, thay cho các ảnh lễ hội chỉ
khớp tag. Đã đối chiếu trực tiếp hai ảnh đầu: bánh chưng là chủ thể chính. Bản
không dấu trả cùng thứ tự; query `phở` không lấy `phố cổ` do mất dấu.

Đã pass 110 test Python và đo HTTP với `top_k=5`, không OpenAI key, encoder chưa
load: 200 request concurrency 1 có P90 **2,90 ms**, 200 request concurrency 4 có
P90 **7,43 ms**, không lỗi. Đây là đo query cụ thể trên localhost sau warm-up, không
phải benchmark relevance tổng quát hoặc thời gian render ảnh. Report:
`data/diagnostics/single-intent-ranking-regression.json` và
`data/diagnostics/single-intent-ranking-http-benchmark.json`.

Tham khảo API chính thức: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[SigLIP2](https://huggingface.co/docs/transformers/model_doc/siglip2).

## Kiểm chứng trong phiên triển khai

Các phép đo latency/model thật bên dưới là baseline Ollama trước khi đổi provider.
Sau migration OpenAI, kiểm thử dùng mock HTTP; cần điền API key để đo lại model thật.
Đã pass 70 test Python, Ruff và TypeScript typecheck sau migration, bao gồm bearer
authentication, schema, thiếu key, 401/403/429, refusal và phản hồi bị cắt/JSON lỗi.

- Pytest toàn repo đã pass sau khi bổ sung tests contextual, artifact v2 và metrics.
- Ruff, TypeScript typecheck và integration test keyword trên dữ liệu local đã pass.
- Production build bằng `npm run build -- --webpack` đã pass. Turbopack bị môi trường
  thực thi chặn bind socket nội bộ; đây là giới hạn môi trường của lượt build mặc định.
- `npm run test:contextual` kiểm tra HTTP thật: web production → API → Qwen/SigLIP2
  → URL ảnh. Kiểm tra cả cache plan, query rỗng và category sai.
- Query “ruộng bậc thang xanh ở vùng núi” có top-1 title `Rice field Vietnam` trong
  demo. Lần đầu khoảng 27,2 giây (LLM 19,1 giây, load/inference embedding 6,6 giây),
  lần lặp cache plan khoảng 0,39 giây. Đây là hai lần smoke, không phải benchmark SLO
  hoặc bằng chứng đạt relevance gates cho 200 query/full corpus.
- Đã kiểm tra bằng model thật: metadata encoder cho vector bằng query encoder khi
  input vừa một đoạn; có regression test cho padding và việc giữ token cuối câu dài.
  Artifact metadata dùng version `title-tags-description-chunk-mean-l2-v2` để từ chối
  bản thử nghiệm trước khi sửa attention mask.
