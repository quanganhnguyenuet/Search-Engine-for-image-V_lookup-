# Kết hợp image embedding và metadata embedding trong VisualSeek

Đã triển khai các bước tối ưu bên dưới; xem
[cấu hình rollout, chấm nhãn và relevance gate](search-fusion-setup.md).
Phần còn lại ghi nhận kiểm tra trước triển khai và cơ sở của chiến lược.

Kiểm tra ngày 06/11/2026 trên full corpus local và artifact tải từ Hugging Face.
Đây là kết quả kiểm tra và chiến lược tối ưu; chưa thay đổi ranking production.
Số liệu chi tiết: `data/diagnostics/vector-fusion-review-2026-11-06.json`.

## Hai loại vector đang được lưu

| Thuộc tính | Image embedding | Metadata embedding |
| --- | --- | --- |
| File | `vectors.npy` | `metadata_vectors.npy` |
| Input | Pixel ảnh, EXIF orientation, RGB, resize theo processor | Keyword/tên ảnh + tags + description; thiếu hết thì category |
| Encoder | Image tower của SigLIP2 | Text tower của cùng SigLIP2 |
| Model | `google/siglip2-base-patch16-224` | Cùng model và revision |
| Revision | `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2` | Cùng revision |
| Shape | 15.423 × 768 | 15.423 × 768 |
| Dtype/normalization | float32, L2 | float32, L2 |
| Dung lượng ma trận | 45,18 MiB | 45,18 MiB |
| Mapping | Hàng i ứng với asset i trong `assets.jsonl` | Cùng hàng i, cùng asset |

`ArtifactIndex` đã kiểm tra checksum, shape, finite values, unit norms và unique IDs
cho cả index cấu hình `culture-contextual-full-v1` và index HF đã import
`culture-siglip2-t4x2-v1`. Hai ma trận có thể dùng kết hợp trong retrieval vì được
tạo bởi cùng model/revision và có mapping asset nhất quán, không chỉ vì cùng số chiều.

Metadata không phải description-only. Hiện có 12 asset thiếu description và tags,
nhưng tất cả đều có keyword và metadata vector. Các file asset được kiểm tra không
chứa `questions`. Hàm tạo metadata chỉ lấy keyword/tags/description, không lấy VQA
answers hoặc `cultural_context`.

Ngân sách thực tế là 63 content tokens và 1 special token mỗi đoạn. Median metadata
85 content tokens, P95 119, tối đa 189. Có **13.534/15.423 asset (87,75%)** cần nhiều
đoạn, nhiều nhất 3 đoạn. Code encode từng đoạn, chuẩn hóa L2, cộng các vector đoạn
rồi chuẩn hóa L2; tương đương hướng của trung bình các vector đoạn. Không cắt bỏ
description sau 64 token.

Tái tạo metadata vector bằng CPU cho ba mẫu: thiếu description, độ dài trung vị và
dài nhất. Cosine với vector local đều xấp xỉ 1; với vector HF tương ứng là
0,9999996–0,9999998. Kiểm tra mẫu xác nhận pipeline phù hợp, không thay thế việc
tái tạo/đối chiếu toàn bộ 15.423 vector.

## Cách search hiện đang kết hợp hai ma trận

`SearchService.text_search` định tuyến tên/tags sang `EntityIndex`; nhánh này có
thể trả kết quả trực tiếp mà không gọi model. Với nhánh hybrid:

1. Encode query gốc; nếu có LLM plan thì encode thêm `visual_query`, `metadata_query`.
2. Dùng tất cả query vectors cho cả image matrix và metadata matrix; với mỗi ảnh,
   lấy cosine lớn nhất qua các biến thể query trong từng nhánh.
3. Lexical scoring cộng số token query trùng trong tên/tags/description với trọng số
   trường tương ứng 3/2/1. Đây chưa phải BM25: không có IDF hoặc length normalization.
4. Mỗi nhánh góp top 100 vào RRF, hệ số rank 60 và trọng số ba nhánh bằng nhau.
5. Trả top-K; rerank tùy chọn hiện chỉ nhận shortlist tối đa 8 ảnh và dùng annotation.

Vì vậy việc kết hợp hai loại embedding **đã có**, chưa cần embedding lại corpus.
`/search/semantic` chỉ so query với image vectors; `/search/text` là nhánh hybrid.

Giữ hai ma trận riêng và hợp nhất thứ hạng lúc search cho phép điều chỉnh vai trò
của từng loại bằng chứng và dễ chạy đối chứng. Có thể thử gộp vector trong cùng
không gian, nhưng hiệu quả phải được đo; không thay thế hai ma trận hiện có chỉ
bằng một phép cộng mặc định.

Một lý do tránh cộng trực tiếp các điểm chưa hiệu chỉnh là khác biệt phân bố điểm:
query `bánh chưng` có top image cosine khoảng 0,205 và top metadata cosine khoảng
0,870; lexical dùng một thang điểm khác. Cùng là cosine không có nghĩa hai nhánh
có cùng độ tin cậy. RRF kết hợp thứ hạng thay vì cộng các thang điểm này.
[Bài gốc RRF](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf).

## Thứ tự tối ưu đề xuất

### 1. Tăng tốc lexical, giữ nguyên scoring

Phép đo offline trên 5 query, mỗi query lặp 3 lần, cùng CPU và ma trận đã load:

| Bước | Khoảng thời gian quan sát |
| --- | --- |
| Image matrix × query | 1,7–2,6 ms |
| Metadata matrix × query | 1,4–1,7 ms |
| Lexical hiện tại, median mỗi query | 58,8–61,8 ms |
| Lexical dùng inverted postings thử nghiệm | 0,06–0,20 ms |

Mỗi query hiện gọi `field.split()` và xét token trên toàn bộ 15.423 asset. Thử
nghiệm xây `token → (asset positions, summed field weights)` một lần, rồi cộng
điểm vào các asset có token; vector điểm lexical **bằng chính xác** code cũ trên
toàn bộ 5 query. Mảng postings khoảng 5,23 MiB, chưa tính Python/container overhead;
thời gian xây thử khoảng 257 ms, không gồm nạp model hoặc chuẩn hóa fields.

Triển khai có thể đặt postings trong `HybridRetriever`, gắn lifecycle với index,
giữ filter category và scoring 3/2/1. Kiểm thử cần đối chiếu cả token lặp, dấu tiếng
Việt, category rỗng và asset trùng token ở nhiều trường trước khi thay production.
Đây là tối ưu giữ scoring, tách riêng khỏi thử nghiệm BM25/thay ranking.

Số liệu trên chỉ đo các bước retrieval trong process, không gồm query encoder,
không phải end-to-end latency hoặc SLA. Trên corpus này nên xử lý bước lexical
trước khi cân nhắc chuyển sang ANN/Qdrant để tăng tốc.

### 2. Phân vai query và fusion có trọng số

Với query mô tả đơn giản, tiếp tục encode local và truy vấn hai nhánh. Với query có
planner, thử dùng mô tả thị giác cho image branch và mô tả/tên văn hóa cho metadata
branch, giữ query gốc làm bằng chứng với trọng số được đánh giá. Code hiện lấy max
qua cùng một tập query vectors cho cả hai nhánh, chưa phân vai theo trường.

Thử Weighted RRF:

```text
score(asset) = w_image / (60 + rank_image)
             + w_metadata / (60 + rank_metadata)
             + w_lexical / (60 + rank_lexical)
```

Chỉ cộng nhánh mà asset xuất hiện trong candidate list. Bắt đầu từ baseline
`w_image = w_metadata = w_lexical = 1`, rồi tune trên development labels:

- Tên riêng/đối tượng rõ: ưu tiên title/tag exact hoặc lexical nếu đánh giá xác nhận.
- Màu sắc/cảnh/hoạt động: thử tăng ảnh hưởng image branch.
- Địa danh/tên văn hóa/mô tả tiếng Việt: thử tăng metadata branch.

Các nhóm trên là giả thuyết cần đo, không phải quy tắc semantic luôn đúng. So thêm
score fusion đã hiệu chỉnh trên cùng labels; RRF không được bảo đảm luôn tối ưu.
[Nghiên cứu phân tích fusion](https://arxiv.org/abs/2210.11934) cho thấy cần đánh giá
tham số và cách hợp nhất trong ngữ cảnh cụ thể, không suy ra trọng số tốt nhất cho
corpus này từ kết quả trên dataset khác.

### 3. Candidate retrieval rồi rerank có kiểm chứng

Thử lấy khoảng 100 ứng viên mỗi dense branch và 50–100 lexical, hợp nhất theo ID,
sau đó chọn shortlist khoảng 20–50 để đánh giá reranking. Đây là tham số thử nghiệm;
shortlist lớn hơn cần đo token cost và latency. Shortlist tối đa 8 hiện tại có thể
bỏ sót ảnh trước khi reranker được xem.

Reranker dựa description phù hợp để đánh giá ngữ nghĩa chú thích. Để kiểm chứng
`áo đỏ`, `hai người`, `không đội nón`, cần bằng chứng pixel hoặc nhãn thị giác đáng
tin. Chú thích không nhắc đến nón không chứng minh ảnh không có nón.

Xử lý phủ định thành điều kiện rõ ràng, tách khỏi positive keywords; hiện
`QueryPlan.exclusions` chưa được truyền vào retriever để thực thi. Với nhiều intent,
cần truy xuất từng intent và phân nhóm thay vì lấy max cosine rồi coi mọi điều kiện
đã được thỏa mãn.

### 4. Giảm ảnh hưởng metadata yếu và query ngoài miền

Đánh dấu 12 asset chỉ có keyword để biết metadata evidence chưa đầy đủ; không bỏ
ảnh khỏi search. Khi thử điều chỉnh trọng số theo độ đầy đủ, phải đánh giá ảnh hưởng
recall và không biến thiếu description thành kết luận ảnh không liên quan.

Query ngoài miền `tàu vũ trụ trên sao Hỏa` có top metadata cosine khoảng **0,839**
với `lăng Hồ Chi Minh`, vẫn không chứng minh phù hợp. Đây là cảnh báo thực nghiệm
rằng không thể dùng một ngưỡng cosine metadata tùy ý làm xác suất relevance.
RRF cũng không tự cung cấp xác suất hoặc cơ chế từ chối.

Relevance gate cần được hiệu chỉnh trên query có/không có đáp án, có thể kết hợp
điểm riêng từng nhánh, chênh lệch top scores, mức khớp entity và kết quả reranker.
Chỉ trả `không có ảnh phù hợp` theo chính sách đã đo, không suy ra từ một số score
đơn lẻ. SigLIP2 hỗ trợ image-text retrieval; mức hiệu quả text-to-text trên metadata
tiếng Việt của corpus này vẫn cần đánh giá riêng.
[Model card SigLIP2](https://huggingface.co/google/siglip2-base-patch16-224).

### 5. Đánh giá trước khi thay cấu hình mặc định

Giữ cùng corpus, query và labels; chạy các cấu hình:

1. Image-only.
2. Metadata-only.
3. Lexical-only.
4. RRF hiện tại.
5. Weighted RRF hoặc score fusion đã tune trên development.
6. Cấu hình tốt nhất cộng reranker.

Chấm candidate theo pixel và yêu cầu query, không lấy chính description làm nhãn
đúng tự động. Tune trên development, báo chất lượng trên held-out; đo HitRate@10,
nDCG@10, Precision@K và tỷ lệ trả sai/từ chối sai với query ngoài miền. Không công bố
corpus Recall@K từ một pool nhãn chưa đầy đủ. Bộ 200 query hiện có chưa có
`answerable` được xác nhận; chưa thể chọn trọng số tối ưu hoặc công bố mức cải thiện.

Benchmark end-to-end riêng: cold/warm, cache hit/miss, LLM/no-LLM, concurrency 1/4,
báo P50/P95 và tỷ lệ lỗi. Warm model, lexical index và các cấu trúc phụ ở startup hoặc
warmup; cache query theo model/artifact version; chỉ gọi planner/reranker khi cần.

Kế hoạch này tái sử dụng image và metadata vectors hiện có. Lưu per-chunk metadata
vectors để hạn chế mất chi tiết do pooling, hoặc thử text encoder chuyên metadata,
là nhánh nghiên cứu tiếp theo nếu đánh giá chỉ ra điểm yếu tương ứng; cần query
encoder/schema tương thích và không thể so chéo tùy ý giữa hai model khác nhau.
