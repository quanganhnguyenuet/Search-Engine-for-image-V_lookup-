# Vận hành search sau khi triển khai fusion ảnh và metadata

Search hiện mặc định chạy local: `SEARCH_LLM_ENABLED=false`,
`SEARCH_LLM_RERANK=false`. Cả query nhiều điều kiện cũng không tự gọi OpenAI.
SigLIP2 query encoder vẫn chạy local để so query với vector ảnh/metadata. RAG và
`use_llm=true` trả lỗi có hướng dẫn khi LLM bị tắt. Nếu muốn bật lại planner, đặt
`SEARCH_LLM_ENABLED=true`; rerank còn cần `SEARCH_LLM_RERANK=true`. Các phần rerank
bên dưới mô tả chế độ opt-in. Restart backend sau khi đổi cấu hình.

Khi bật LLM, `SEARCH_ROUTING_MODE=auto` giữ lookup tên và mô tả thường ở local,
chỉ đưa reasoning/intent chưa chắc chắn vào planner. `always` ép dùng LLM cho mọi
query. Xem
[semantic query planning](semantic-query-planning.md). Các cụm `exclusions` của plan
được đối chiếu với tên/tag,
giữ phân biệt dấu tiếng Việt, và loại khỏi tập ứng viên trước khi fusion lấy top-k.
Bộ lọc này hoạt động cả khi rerank tắt. Điều kiện chỉ thấy trên pixel hoặc thiếu
trong tên/tag vẫn cần bước rerank phù hợp; bộ lọc tên không xác minh được pixel.

Đã triển khai bốn bước trong [chiến lược fusion](image-metadata-fusion-strategy.md),
tái sử dụng full index hiện có, không embedding lại corpus.

## Trạng thái local

Sau sửa dấu/tên ảnh, `.env` chọn artifact `culture-labels-v1`; xem
[vận hành nhãn tiếng Việt](vietnamese-label-repair.md). Pool cũ được giữ để đối chứng,
pool `fusion-pool-labels-v1.json` dùng dữ liệu mới.

`.env` đã cấu hình query roles, trọng số RRF 1/1/1, candidate depth 100 và
relevance gate `shadow`. Cấu hình mẫu dưới đây chạy local; để dùng cách hiểu bằng
LLM khi cần reasoning, đặt `SEARCH_LLM_ENABLED=true` và `SEARCH_LLM_RERANK=true`.
Đặt thêm `SEARCH_ROUTING_MODE=always` nếu muốn mọi query đi qua LLM;
mặc định `auto` chỉ gọi LLM khi cần reasoning.
Thay đổi có hiệu lực khi backend khởi động lại.

```dotenv
SEARCH_QUERY_ROLES=true
SEARCH_LLM_ENABLED=false
SEARCH_ROUTING_MODE=auto
SEARCH_IMAGE_WEIGHT=1.0
SEARCH_METADATA_WEIGHT=1.0
SEARCH_LEXICAL_WEIGHT=1.0
SEARCH_RRF_CONSTANT=60
SEARCH_CANDIDATE_DEPTH=100
SEARCH_LLM_RERANK=false
SEARCH_RERANK_CANDIDATES=32
SEARCH_RERANK_SCOPE=planned
SEARCH_RERANK_EVIDENCE=annotations
SEARCH_IMAGE_ROOT=data/raw/vietnamese-culture-vqa
SEARCH_RELEVANCE_MODE=shadow
```

Chạy backend, sau đó warm model/postings (không gọi LLM):

```bash
.venv/bin/python -m uvicorn visualseek_api.main:app \
  --app-dir services/api/src --host 127.0.0.1 --port 8000 --workers 1
```

```bash
curl -X POST http://127.0.0.1:8000/api/v1/search/warmup \
  -H 'Content-Type: application/json' -d '{"include_llm":false}'
```

`GET /api/v1/search/status` trả config fusion, shortlist, relevance mode và
`pipeline_signature`. API semantic và keyword baseline tiếp tục dùng để đối chứng.
`use_llm=false` tắt cả planner lẫn rerank, kể cả khi scope là `all`.

## Những thay đổi đã hoạt động

- Lexical dùng inverted postings, giữ scoring 3/2/1 và token semantics cũ. Warmup
  xây postings một lần. Các phép so dense dùng trực tiếp ma trận đã mmap, tránh
  copy hai ma trận full cho mỗi query không có category filter.
- Hybrid hỗ trợ trọng số và depth riêng từng channel ở tầng retrieval. Settings
  API dùng một depth chung. Khi có plan, image branch nhận visual query tích cực;
  metadata branch nhận metadata query tích cực. Không embed câu gốc chứa nội dung
  bị loại trừ. `SEARCH_QUERY_ROLES=false` dùng chung hai vector tích cực cho hai nhánh.
- Những positive keywords chứa cụm trong `exclusions` được bỏ khỏi lexical input;
  không dùng mô tả không đầy đủ để tự kết luận ảnh không chứa đối tượng bị loại trừ.
- Shortlist rerank mặc định 32 thay vì bị giới hạn 8; tăng tối thiểu lên `top_k`
  nếu request cần nhiều hơn. `planned` chỉ rerank query có planner; `all` cho phép
  dùng cùng cơ chế khi bật LLM. `use_llm=false` luôn tắt rerank. Exact entity lookup
  dùng nhánh nhanh trong chế độ local.
- API cung cấp channel scores/ranks, metadata completeness, thông tin gate,
  `abstained`, evidence type và số ứng viên rerank. UI có thông báo khi không có
  ứng viên đủ bằng chứng.

Rerank chú thích không chứng minh điều kiện đúng trên pixel. Để gửi thumbnail ảnh
ứng viên cho model kiểm tra, đặt `SEARCH_RERANK_EVIDENCE=pixels` và dùng model/API
hỗ trợ vision cùng Structured Outputs. Chế độ này gửi tối đa shortlist ảnh ra
provider, phát sinh input tokens/latency; chưa được gọi thật trong lượt triển khai.
Ảnh được đối chiếu SHA-256 với asset ID, kiểm tra đường dẫn local rồi resize tối đa
768 × 768 trước khi gửi. Đọc/lỗi model trả lỗi rõ ràng, không tự đổi về annotation.
Ngay cả vision rerank vẫn có thể sai, không phải bảo đảm mọi thuộc tính được xác minh.
Định dạng multimodal đối chiếu theo
[OpenAI Docs](https://developers.openai.com/api/docs/guides/images-vision?api-mode=chat).

## Chấm nhãn và so sánh chất lượng

Đã tạo bằng model thật, chạy local không gọi LLM:

- `data/evaluation/fusion-pool-labels-v1.json`: 200 query, đủ candidate union từ các nhánh.
- `data/evaluation/fusion-labels-repaired-v1.csv`: bảng chấm nhãn, chưa tự gán grade/answerable.
- Các mode: image-only, metadata-only, lexical-only, RRF 1/1/1 và weighted theo config.
  Hai mode RRF hiện giống nhau vì chưa có nhãn để chọn trọng số khác.

Tool thu thập bypass exact-title router và không chạy planner để tạo ablation có
kiểm soát. LLM rerank là mode tùy chọn khi collect bằng `--with-rerank`; chỉ bật khi
muốn gửi ứng viên ra provider. Artifact/config khác cần pool mới.

Có thể chấm development trước, held-out để sau:

```bash
.venv/bin/python scripts/evaluate_fusion.py export-labels \
  --pool data/evaluation/fusion-pool-labels-v1.json --split development \
  --output data/evaluation/fusion-development.csv
```

Mở ảnh theo `image_path` bên dưới `data/raw/vietnamese-culture-vqa/`, hoặc dùng
`imageUrl` trong pool với web local. Điền `grade`: 0 không phù hợp, 1 phù hợp một phần,
2 đáp ứng yêu cầu chính. Điền `answerable=true/false` cho mỗi query (ít nhất một dòng
trong query; nếu điền nhiều dòng phải nhất quán). Xem pixel theo toàn bộ yêu cầu,
không lấy description tự động làm ground truth.

```bash
.venv/bin/python scripts/evaluate_fusion.py import-labels \
  --pool data/evaluation/fusion-pool-labels-v1.json \
  --labels data/evaluation/fusion-development.csv --split development \
  --reviewer 'ten-nguoi-cham' --output data/evaluation/fusion-judged-dev-v1.json

.venv/bin/python scripts/evaluate_fusion.py score \
  --pool data/evaluation/fusion-judged-dev-v1.json --split development \
  --output data/evaluation/fusion-dev-report-v1.json

.venv/bin/python scripts/evaluate_fusion.py tune \
  --pool data/evaluation/fusion-judged-dev-v1.json \
  --output data/evaluation/fusion-tuned-v1.json
```

Tuning thử 27 bộ trọng số trên **development**, giữ nguyên candidate pool và dùng
thứ hạng đã lưu. `score --fusion-config data/evaluation/fusion-tuned-v1.json` replay
bộ tốt nhất. Tăng candidate depth quá phạm vi đã thu thập bị từ chối vì sẽ có ứng
viên chưa được chấm. Các công cụ từ chối tính metric trên nhãn thiếu.

Sau đó export/import nhãn `held-out` vào pool đã có development labels, dùng output
mới rồi đánh giá một lần trên held-out với config đã chọn. Metric gồm HitRate@10,
nDCG@10, Precision@10 (mẫu số cố định 10), outside false accept rate và answerable
empty rate. Không suy ra corpus Recall từ candidate pool chưa đầy đủ.

## Hiệu chỉnh và bật relevance gate

Chưa có nhãn thật nên gate đang báo `calibration_required`; chưa áp ngưỡng tùy ý
để loại query ngoài miền. Cơ chế đã có đủ shadow, enforce, kiểm tra artifact/config
fingerprint và thông báo abstention.

Sau khi chấm development (phải có query có đáp án và không có đáp án):

```bash
.venv/bin/python scripts/evaluate_fusion.py calibrate \
  --pool data/evaluation/fusion-judged-dev-v1.json \
  --fusion-config data/evaluation/fusion-tuned-v1.json \
  --output data/evaluation/relevance-policy-v1.json
```

Calibrator chọn ngưỡng image/metadata cosine từ development labels, có điều kiện
precision trên ứng viên được giữ và outside false accept rate. Đây là mục tiêu fit,
không phải bảo đảm chất lượng production. Nếu không tìm được policy hữu ích đáp ứng
mục tiêu, tool dừng và yêu cầu cải thiện retrieval/labels. Nhãn held-out không đi
vào tune hoặc calibrate. Description thiếu không tự được coi là ảnh không phù hợp.

Đánh giá policy trên pool có nhãn held-out và cùng bộ trọng số:

```bash
.venv/bin/python scripts/evaluate_fusion.py score \
  --pool data/evaluation/fusion-judged-all-v1.json --split held-out \
  --fusion-config data/evaluation/fusion-tuned-v1.json \
  --policy data/evaluation/relevance-policy-v1.json \
  --output data/evaluation/fusion-heldout-gated-v1.json
```

Đặt trọng số theo config đã chọn và đường dẫn policy vào `.env`, chạy `shadow`
để xem `would_reject`, rồi chỉ chuyển `SEARCH_RELEVANCE_MODE=enforce` sau khi đạt
tiêu chí chất lượng được duyệt:

```dotenv
SEARCH_RELEVANCE_POLICY=data/evaluation/relevance-policy-v1.json
SEARCH_RELEVANCE_MODE=enforce
```

Policy được bind với checksum các file index, model revision, fusion config và query
roles. Dùng nhầm policy làm enforce trả 503; shadow báo lỗi policy nhưng vẫn trả
ứng viên. Policy hiện được calibrate cho profile local không dùng planner, không
tự áp vào planned query hay exact entity lookup. API báo profile không áp dụng;
planned query có reranker kiểm tra riêng, chưa có calibrated gate riêng.
Docker dùng image root `/data/raw/vietnamese-culture-vqa`; policy cũng phải có đường
dẫn `/data/...` theo volume mount.

## Bằng chứng kiểm tra

`data/diagnostics/fusion-rollout-verification-2026-11-06.json` lưu phép đo 5 query,
mỗi query lặp lexical 5 lần, và smoke API thật với full 15.423 ảnh, encoder CPU,
không gọi LLM. Lexical cũ khoảng 53–57 ms, mới 0,09–0,22 ms, điểm số bằng nhau.
RRF trọng số mặc định khớp baseline trên 4 mô tả × 3 phạm vi category. Đây là stage
benchmark/smoke nhỏ, chưa phải SLA hoặc kết quả relevance.

Chạy hồi quy:

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check services packages scripts
npm --prefix apps/web run typecheck
```
