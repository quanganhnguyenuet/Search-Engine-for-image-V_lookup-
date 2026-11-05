# Kế hoạch V1: tìm ảnh bằng mô tả của người dùng

Ngày rà soát: 05/11/2026. Trạng thái: **embedding full corpus đã hoàn tất; chưa nghiệm thu relevance/latency**.

> Cập nhật phạm vi theo yêu cầu đã duyệt tiếp theo: kết hợp embedding tags/description
> với image embedding và LLM để hiểu ngữ cảnh người dùng. Đã bổ sung hybrid RRF,
> query planning qua Qwen/Ollama, rerank tùy chọn và API/UI contextual search.
> Chi tiết triển khai, cách chạy và giới hạn tại [contextual-image-search.md](contextual-image-search.md).
> Mục 2–3 ghi nhận thời điểm rà soát ban đầu; mục 4 và ngân sách latency phía dưới
> là baseline ban đầu, được điều chỉnh cho phạm vi có LLM ở tài liệu triển khai.
> Chưa có nhãn relevance người thật, nên chưa tuyên bố vượt quality gates.
>
> Cập nhật tiếp theo: provider LLM đã chuyển sang OpenAI GPT-4o mini cho query
> planning, rerank và RAG. SigLIP2/artifact không đổi; không cần chạy lại embedding.
> Cần `OPENAI_API_KEY` phía backend. Qdrant mới có cấu hình, chưa có adapter retrieval.
>
> Đã hoàn tất 15.423 image vectors và 15.423 metadata vectors; đối soát đủ ID, không
> có ảnh lỗi, kiểm tra artifact/ZIP và chạy hybrid smoke thành công. `.env` local
> đã trỏ sang full index. 12 ảnh thiếu chú thích dùng tên/chủ đề làm metadata text.

Trong tài liệu này, “text to image” được hiểu là **text-to-image retrieval**: nhập
yêu cầu bằng văn bản để tìm ảnh có sẵn trong thư viện. Sinh ảnh mới bằng diffusion
không thuộc phạm vi. Đây là cách hiểu dựa trên yêu cầu “search dựa trên request”.

## 1. Kết quả sản phẩm cần đạt

Người dùng nhập một mô tả tiếng Việt, có thể chọn chủ đề, và nhận tối đa 24 ảnh
được xếp hạng theo mức phù hợp trong corpus văn hóa Việt Nam hiện có.

Ví dụ yêu cầu để kiểm thử:

- “Người mặc áo dài màu đỏ đứng trước công trình cổ.”
- “Ruộng bậc thang xanh ở vùng núi.”
- “Một tô phở có thịt bò và rau thơm.”
- “Lễ hội đông người ngoài trời.”

Các ví dụ là mục tiêu cần đánh giá trên pixel ảnh, chưa phải khả năng đã được chứng
minh. Tên địa danh, màu sắc, số lượng, phủ định và quan hệ giữa đối tượng có thể khó
với embedding; không tuyên bố bộ lọc chính xác cho các thuộc tính chưa có nhãn.
Corpus V1 chỉ có sáu chủ đề văn hóa Việt Nam; không hứa tìm được mọi ảnh ngoài miền.

## 2. Hiện tại đã có gì?

| Thành phần | Bằng chứng trong repo/local | Mức hoàn thành |
| --- | --- | --- |
| Corpus và metadata | `data/processed/culture-search/report.json`: 15.719 ảnh được chọn, 15.423 asset duy nhất, loại 296 ảnh trùng, sáu chủ đề | Đã chuẩn bị corpus; báo cáo decode riêng mới kiểm tra 299 ảnh |
| Keyword search | `apps/web/lib/culture-data.ts`: tìm title/tags/description/context, bỏ dấu, lọc chủ đề, phân trang | Đã có; mọi token phải xuất hiện nên không tương đương hiểu yêu cầu tự nhiên |
| Encoder text và ảnh | `packages/embedding/src/visualseek_embedding/siglip2.py`: SigLIP2, revision cố định, vector L2, text tối đa 64 token | Có implementation thật; chưa đo relevance tiếng Việt |
| Export/import embedding | `scripts/kaggle_embed.py`, `scripts/import_embeddings.py`: batch, resume, checksum, metadata và vector artifact | Đã có, tái sử dụng được |
| Search semantic | `services/api/src/visualseek_api/application/search.py`: text vector → exact cosine NumPy → metadata | Đã có, không phụ thuộc Qdrant |
| API | `POST /api/v1/search/semantic`, `GET /api/v1/search/status` | Có validation, filter category và timing |
| Web | `apps/web/components/culture-explorer.tsx`, proxy `/api/culture/ai` | Mặc định semantic; query rỗng duyệt corpus bằng luồng keyword; có grid, chi tiết, URL state, loading/error |
| Cache/runtime | Encoder lazy-load; cache tối đa 128 text vector, CPU mặc định, một lock inference | Có baseline; chưa có warm readiness hoặc đo tải |
| RAG và image query | API và UI đã có, RAG qua Ollama với annotation làm bằng chứng | Có code; không cần phát triển thêm cho mục tiêu V1 |
| Kiểm thử | Unit/integration cho artifact, ranking, filter, API, cache; script smoke HTTP/AI | Có kiểm tra luồng; không thay thế đánh giá độ phù hợp |

**Khoảng cách quan trọng nhất:** artifact mặc định
`data/embeddings/culture-siglip2/manifest.json` chỉ có **120 vector**, scope
`limit_per_category=20`, tương đương khoảng **0,78%** của 15.423 asset. `.env` tại
thời điểm rà soát đang rỗng, nên backend dùng đường dẫn mặc định nếu process không
được cấp biến môi trường khác. Chưa xác nhận cấu hình của một server đang chạy.

`complete=true` của artifact này chỉ có nghĩa hoàn tất 120 ảnh được yêu cầu,
**không có nghĩa index đủ corpus**. Duyệt được 15.423 ảnh trên web không đồng nghĩa
semantic search đang tìm trong toàn bộ số ảnh đó.

Smoke report local cho demo 120 ảnh ghi P90 khoảng 86 ms trên bốn query CPU và nói
rõ chưa đo relevance. Không dùng con số này để nghiệm thu chất lượng hoặc latency
của full corpus.

Kiểm chứng trong lượt rà soát: đã load artifact mặc định bằng `ArtifactIndex`
thành công (checksum/shape/unit norm), xác nhận 120 vector × 768 chiều. Đã kiểm tra
`git diff --check`. Lượt chạy toàn bộ pytest không trả kết quả trong thời gian
rà soát và đã được dừng; chưa kết luận bộ test hiện tại pass. Không khởi chạy server
hay đo lại inference model trong lượt lập kế hoạch này.

## 3. Chưa có gì cho V1?

1. Index semantic đầy đủ, có đối soát ID với corpus và kiểm tra ảnh hiển thị được.
2. Bộ query tiếng Việt độc lập, nhãn relevance do người xem ảnh xác nhận và runner
   đánh giá HitRate@10, nDCG@10, chất lượng theo từng nhóm yêu cầu.
3. Báo cáo chất lượng cho câu mô tả nhiều thuộc tính, có/không dấu, tên riêng và
   truy vấn ngoài miền. Model trực tiếp encode text gốc; chưa có bước hiểu intent,
   dịch, query rewrite hay hybrid ranking.
4. Kiểm tra giới hạn token trước inference: hiện UI giới hạn 200 ký tự, API 1.000
   ký tự, encoder âm thầm truncate ở 64 token.
5. Readiness xác nhận encoder đã warm và index đủ corpus: `/health` chỉ là liveness;
   `/search/status` hiện chỉ kiểm tra index và trạng thái encoder đã load.
6. Benchmark full corpus với query cache hit/miss, warm/cold và concurrency 1/4.
7. Một giao diện tập trung vào text search, thông báo rõ độ phủ và giới hạn kết quả.
8. Quy trình nghiệm thu/rollback cho phiên bản search. `docs/architecture.md` và kế
   hoạch MVP cũ còn mô tả nhiều phần tương lai, không phải bằng chứng đã triển khai.

Qdrant, PostgreSQL, object storage có cấu hình/hạ tầng khung nhưng chưa nằm trong
search path hiện tại. Evaluation package mới có contracts, chưa có runner.

## 4. Quyết định cố định cho đợt triển khai này

| Hạng mục | Chốt cho V1 |
| --- | --- |
| Tính năng chính | Text tiếng Việt → ảnh có sẵn, tối đa 24 kết quả, category tùy chọn |
| Corpus | Snapshot 15.423 asset hiện tại, giữ ID SHA-256 và fingerprint |
| Model | Giữ `google/siglip2-base-patch16-224`, revision `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2` |
| Ranking | Exact cosine trên image embeddings; metadata chỉ hiển thị và filter category |
| Runtime | Query encoder local CPU, một API worker; export corpus bằng script hiện có trên Kaggle |
| Lưu trữ | Ảnh local + artifact có phiên bản; chưa bổ sung database/service mới |
| API | Giữ `/api/v1/search/semantic`; bổ sung contract/readiness, không tạo API song song |
| UX | Một ô mô tả và bộ lọc chủ đề; chuyển keyword thành công cụ đối chứng phụ; ẩn RAG/image upload khỏi luồng chính |
| Query | Trim, chuẩn hóa Unicode NFC, giữ dấu và thứ tự từ; tối đa 200 ký tự và không quá ngân sách 64 token tính cả special tokens |
| Query dài | Từ chối có thông báo rút gọn; không âm thầm cắt mất yêu cầu |
| Lỗi AI | Báo lỗi rõ ràng; không âm thầm thay bằng keyword |
| Điểm tương đồng | Chỉ là điểm xếp hạng, không hiển thị thành xác suất đúng |
| Ngoài miền | Nêu corpus và mô tả kết quả là “gần nhất”; chưa cam kết tự phát hiện không liên quan |

Chưa thêm LLM phân tích yêu cầu, translation, BM25/hybrid, reranker, OCR, graph,
fine-tune, upload/ingestion mới, image generation hoặc tối ưu ANN. Giữ code các
tính năng hiện có để tránh phá tương thích; không mở rộng chúng trong đợt này.
Nếu baseline không vượt cổng chất lượng, phải báo thất bại và trình đề xuất cải
thiện dựa trên lỗi đo được; không tự đổi model hay mở rộng phạm vi.

## 5. Thứ tự triển khai và đầu ra nghiệm thu

### P0 — Chốt snapshot và bộ đánh giá (1–2 ngày công)

- Ghi fingerprint, ID và số lượng theo category của corpus; tạo danh sách query
  độc lập với VQA answers. Không lấy nguyên mô tả ảnh làm toàn bộ tập query.
- Chuẩn bị 200 query: 40 đối tượng/tên gọi, 40 cảnh/hoạt động, 40 nhiều thuộc tính,
  30 biến thể có/không dấu, 30 tên riêng/địa danh, 20 ngoài miền hoặc không có đáp án.
- Chia development 100 và held-out 100, mỗi nửa giữ cùng tỷ lệ nhóm; các biến thể
  cùng ý định nằm cùng một nửa để tránh rò rỉ.
- Tạo công cụ xuất bảng query–candidate kèm ảnh để người đánh giá gán nhãn 0/1/2:
  không phù hợp / một phần / đáp ứng phần chính. Không tự coi category là đáp án.
- Sau P1, pool top 30 từ semantic và keyword, loại trùng và ẩn nguồn ranking trước
  khi chấm. Có thể bổ sung ảnh đúng đã biết; khóa nhãn trước đo held-out.

**Đầu ra:** query set phiên bản V1, quy tắc chấm, công cụ gán nhãn và chủ sở hữu nhãn.
Người duyệt sản phẩm cần tham gia xác nhận nhãn; thời gian này tách khỏi ngày công code.
Nhóm ngoài miền là tập chẩn đoán, không giả vờ có relevant asset.

### P1 — Index đủ corpus (0,5–1 ngày công, chưa tính chờ Kaggle)

- Tái sử dụng selection/export/import; full run với `limit_per_category=0`.
- Tạo artifact mới `data/embeddings/culture-siglip2-full-v1`, không ghi đè demo.
- Đối soát đúng tập 15.423 ID, revision, dimension 768, checksum, L2 normalization
  và khả năng giải mã/truy cập ảnh. Điều tra ảnh lỗi, không âm thầm bỏ khỏi corpus.
- Đổi cấu hình local sau khi kiểm tra xong; ghi đường dẫn artifact cũ để rollback.
- Thêm kiểm tra full coverage theo fingerprint/tập ID; không dựa riêng `complete`.

**Đạt khi:** 15.423/15.423 ID khớp, không ảnh lỗi chưa xử lý, full index load được.

### P2 — Hoàn thiện text search API/runtime (1 ngày công)

- Chuẩn hóa query và validation thống nhất UI/API/tokenizer theo mục 4.
- Warm encoder bằng query kiểm tra trước khi báo search ready; giữ một worker.
- Bổ sung trạng thái `index_ready`, `encoder_ready`, `indexed`, `corpus_total`,
  `coverage_complete`, model revision và artifact fingerprint. Index sẵn chưa đủ
  để trả readiness thành công; thiếu full corpus vẫn có thể chạy demo có cảnh báo.
- Giữ request `{query, category?, top_k?}` với `top_k` mặc định 24, giới hạn 1–50;
  giữ response hiện tại, bổ sung `query_id`, fingerprint và tổng thời gian xử lý.
- Contract rõ: `total` là số kết quả trả về, `libraryTotal` là số ảnh được index,
  `result_scope=top_k`; không có tổng số ảnh “đúng” hay phân trang toàn bộ semantic.
- Lỗi 422 cho query/category không hợp lệ, 503 cho index/model chưa sẵn sàng;
  timeout search riêng 10 giây, không dùng budget 150 giây của RAG cho text search.
- Log request ID, độ dài query, revision, timing và cache hit; không cần lưu nguyên
  nội dung query của người dùng để vận hành V1.

**Đạt khi:** request mới thực sự encode text, filter đúng, lỗi có thể xử lý được,
không truncate query và không fallback ngầm.

### P3 — Giao diện chỉ tập trung tìm ảnh (1 ngày công)

- Ô “Mô tả ảnh bạn muốn tìm”, ví dụ gợi ý, Enter/nút tìm, category tùy chọn.
- Trạng thái ban đầu duyệt corpus; khi có query chuyển sang semantic rõ ràng.
- Giữ URL query/category, Back/Forward, grid và chi tiết ảnh; không thêm chat UI.
- Hiển thị “24 ảnh gần nhất trong N ảnh đã index” phù hợp số lượng thực trả về;
  không gọi toàn bộ top-K là ảnh đã xác nhận đúng. Cảnh báo nếu index mới một phần.
- Có loading, retry, lỗi query dài, lỗi dịch vụ, category rỗng và ảnh không tải được.
- Rà soát hủy request khi đổi query; phản hồi cũ không được ghi đè kết quả query mới.
- Không hiển thị pagination semantic, điểm cosine dạng phần trăm hay suy luận rằng
  luôn có đáp án. Không đặt ngưỡng loại kết quả tùy ý từ `RAG_MIN_SCORE`.

**Đạt khi:** người dùng hoàn thành nhập mô tả → lọc → xem ảnh trên desktop/mobile,
không cần chọn công nghệ hoặc khởi động Ollama.

### P4 — Đánh giá và benchmark (1–2 ngày công, sau khi có nhãn)

- Viết runner trong `packages/evaluation`, lưu report có corpus/model/query/label
  fingerprint, config runtime, hardware và timestamp.
- Báo HitRate@10 trên query có đáp án; relevant là nhãn 2. nDCG@10 dùng cả nhãn
  0/1/2; báo riêng từng nhóm và từng category, cả development và held-out.
- Không báo corpus Recall@10 từ nhãn pool chưa đầy đủ. Report phải nêu số candidate
  đã chấm và giới hạn của pooling; ứng viên chưa chấm cần hoàn tất trước so sánh.
- So sánh semantic với keyword hiện có trên cùng corpus, category và query. Không
  dùng test doubles hoặc smoke report cũ làm bằng chứng relevance.
- Đo 200 request cho mỗi cấu hình concurrency 1/4 và cache hit/miss, warm model;
  báo P50/P90/P95, lỗi và timeout. Đo cold start riêng; đo thêm qua web proxy.
- Kiểm tra các query ngoài miền bằng bảng top-K để thấy giới hạn; không tính
  HitRate cho query không có đáp án và không tuyên bố đã có rejection classifier.

**Cổng nghiệm thu đề xuất cần duyệt cùng kế hoạch:**

| Tiêu chí | Điều kiện |
| --- | --- |
| Corpus coverage | Đúng 15.423 asset của snapshot đã chốt |
| Held-out HitRate@10 | ≥ 0,85 trên 90 query có đáp án; báo cả tử số/mẫu số |
| Held-out nhóm nhiều thuộc tính | HitRate@10 ≥ 0,70 trên 20 query |
| Held-out nDCG@10 | ≥ 0,65 và không thấp hơn keyword baseline trên cùng tập |
| Warm API P90, concurrency 1, cache miss | ≤ 500 ms trên máy CPU thực tế được ghi trong report |
| Warm API P95, concurrency 4, cache miss | ≤ 2 giây trên cùng máy |
| Độ ổn định | Không 5xx/timeout trong các lượt benchmark hợp lệ |
| Ảnh kết quả | ID duy nhất; URL ảnh hợp lệ; filter category chính xác |

Các ngưỡng là mục tiêu nghiệm thu, chưa phải kết quả hiện tại. Nếu không đạt, báo
query lỗi và breakdown latency, sửa lỗi trong phạm vi đã duyệt rồi đo lại. Nếu cần
thay cách ranking/model, trình thay đổi kế hoạch trước khi triển khai tiếp.

### P5 — Bàn giao V1 (0,5 ngày công)

- Test regression API/keyword và HTTP thật của text search; không bắt RAG chạy để
  nghiệm thu V1. Chạy typecheck/build và bộ kiểm thử liên quan.
- Cập nhật README, architecture và hướng dẫn chạy text search không có Ollama;
  phân biệt rõ corpus local với corpus đã index.
- Ghi artifact/config đã nghiệm thu, report chất lượng/tốc độ và các query còn yếu.
- Kiểm tra rollback về artifact cũ bằng config + restart, không xóa dữ liệu.

**Đầu ra cuối:** một luồng text → ảnh chạy trên full corpus, báo cáo nghiệm thu có
thể tái lập, và hướng dẫn vận hành. Chưa bao gồm public deployment.

## 6. Phạm vi duyệt và cách triển khai

Ước lượng tổng **5–8 ngày công kỹ thuật**, cộng thời gian chạy Kaggle và người chấm
nhãn. Đây là ước lượng có phụ thuộc, không phải lịch giao hàng đã cam kết.

Duyệt tài liệu này nghĩa là duyệt mục tiêu retrieval, phạm vi và quyết định ở mục
4, trình tự P0 → P5 và các ngưỡng mục 5. Các giai đoạn là checkpoint kiểm chứng,
không cần xin lại xác nhận cho từng thay đổi trong phạm vi đã được duyệt. Không tự
đăng dữ liệu lên tài khoản ngoài hoặc public deploy từ việc duyệt kế hoạch code.

Kế hoạch này là tài liệu ưu tiên cho đợt text search V1. `mvp-plan.md` giữ vai trò
roadmap dài hạn; các hạng mục ingestion, Qdrant, storage và RAG trong đó không trở
thành yêu cầu của đợt này. Nếu thay corpus, model hoặc tiêu chí nghiệm thu, phải
cập nhật phiên bản kế hoạch và nêu tác động trước khi thực hiện.
