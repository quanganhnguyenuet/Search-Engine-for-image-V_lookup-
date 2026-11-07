# Kiểm tra data và search sau khi tải embedding từ Hugging Face

Ngày kiểm tra: 06/11/2026. Đã chạy encoder SigLIP2 thật trên CPU, offline,
gọi trực tiếp các hàm production `SearchService`. Không gọi LLM hoặc sửa `.env`.
Kết quả chi tiết: `data/diagnostics/embedding-search-review-2026-11-06.json`.

## Dữ liệu thực tế

| Thành phần | Kết quả kiểm tra |
| --- | --- |
| Corpus local | 15.423 asset duy nhất, đủ đường dẫn ảnh gốc |
| Index đang được `.env` chọn | `data/embeddings/culture-contextual-full-v1` |
| Image vectors của index đang chọn | 15.423 × 768, float32, L2, checksum hợp lệ |
| Metadata vectors của index đang chọn | 15.423 × 768, float32, L2, checksum hợp lệ |
| Coverage | Tập asset ID bằng chính xác manifest local |
| ZIP mới tải từ HF | Đủ 15.423 asset, schema v2, cùng revision model, checksum hợp lệ |
| HF Dataset commit của ZIP | `fd0958c045532607ae8d8e5beae94fd1d0e7cf2a` |
| Index mới đã import để kiểm tra | `data/embeddings/culture-siglip2-t4x2-v1` |

Hai index có cùng tập asset và cùng metadata theo asset ID, nhưng khác thứ tự hàng.
Không thay riêng một ma trận hoặc `assets.jsonl` giữa hai index. Import giữ nguyên
mapping vector–asset trong ZIP. Sau khi căn hàng theo asset ID, cosine trung bình
giữa image vectors cũ/mới là 0,9999955; giữa metadata vectors là 0,9999996.
Đây là hai bản embedding rất gần nhau, không phải thay model hoặc bổ sung corpus.
Sai khác nhỏ phù hợp với khác biệt tính toán CPU float32 / GPU float16, nhưng kiểm
tra này không tách riêng từng nguyên nhân sai khác.

`.env` vẫn chọn full index cũ. Cả full index cũ và index HF mới đều đã qua kiểm tra.
Các demo 120 ảnh không phải index đang được cấu hình. Có một artifact cũ tên
`culture-contextual-invalid-mask-v1` không hợp lệ về metadata preprocessing;
artifact này không được cấu hình làm index hiện tại.

## Query của user có được so với embedding ảnh không?

Có. Nhánh semantic đã thực hiện đúng chuỗi:

```text
POST /api/v1/search/semantic {query, top_k, category?}
→ SigLIP2 text encoder cùng model/revision với ảnh
→ chuẩn hóa L2
→ dot product với vectors.npy, tương đương cosine
→ xếp hạng top-K
→ assets.jsonl ánh xạ về ID và ảnh local
```

Request ảnh dùng image encoder thay text encoder, rồi tìm trong cùng image matrix.
Backend đọc vector local; Hugging Face là nơi lưu và phân phối artifact,
không phải dịch vụ thực hiện tìm kiếm mỗi request. Storage/retrieval hiện là NumPy,
chưa có adapter Qdrant/ANN trong search path.

## Các phần đã nâng cấp so với baseline

| Baseline | Hiện tại |
| --- | --- |
| Tìm keyword trong metadata | Vẫn giữ để đối chứng; có thêm semantic retrieval |
| Semantic: query vector so với image vectors | Giữ `/search/semantic` làm baseline |
| Metadata chỉ hiển thị | `/search/text` kết hợp image vectors, metadata vectors và lexical qua RRF |
| Mọi request cùng một luồng | Router: tên/tags → lexical; mô tả → local hybrid; logic/nhiều mệnh đề → LLM planning rồi hybrid |
| Phải encode lại query lặp | Có cache query vectors và query plans trong RAM |
| Index demo 120 ảnh | `.env` hiện chọn full corpus 15.423 ảnh |

Giao diện mặc định là `Tìm theo yêu cầu`, gọi `/search/text`; các tab `Ngữ nghĩa`
và `Từ khóa` vẫn cho phép đối chứng. Query khớp tên/tags có thể không chạy encoder;
muốn kiểm tra riêng query embedding → image embedding, dùng `/search/semantic`.

Notebook T4×2 và HF upload nâng cấp quy trình tạo/lưu artifact. Chúng không tự thay
thuật toán ranking, không fine-tune model và không chứng minh relevance tốt hơn.

## Kết quả chạy thật

Kiểm tra cả hai full index: bốn text query × hai chế độ = 16 lượt search,
và hai lượt truy vấn bằng một ảnh corpus. Các lượt đều hoàn tất.

- `bánh chưng`: semantic/hybrid đều trả ảnh tên `Banh chung Tet` ở đầu;
  contextual dùng nhánh lexical, không cần encode text cho request này.
- `người mặc áo dài đỏ trước phố cổ`: semantic/hybrid trả `Ao dai cach tân` ở đầu;
  đây là quan sát xếp hạng, chưa xác nhận ảnh đáp ứng mọi thuộc tính trong query.
- `green terraced rice fields in the mountains`: cả hai trả `Rice field Vietnam`
  ở đầu.
- `tàu vũ trụ trên sao Hỏa`: vẫn trả núi/cảnh/lễ hội; chưa biết từ chối query
  không có ảnh phù hợp.
- Query bằng chính một ảnh corpus: cả hai index tìm lại đúng asset ở rank 1;
  cosine khoảng 1,000000 với index cũ và 0,999991 với index HF.

Không gọi nhánh LLM trong lượt kiểm tra; contextual được đặt `use_llm=false`.
Không truy cập được server API/web đang chạy qua loopback trong sandbox, nên kết
quả này xác nhận data và production search service, không xác nhận cấu hình của
một process server đang chạy từ trước. Timing vài query không thay benchmark tải.

## Việc nên làm tiếp để cải thiện search

1. **Đánh giá trên cùng corpus trước khi tuyên bố tốt hơn baseline.** Đã có 200
   query và runner đánh giá; hiện chưa query nào có `answerable` được xác nhận.
   Cần chấm relevance 0/1/2, so semantic với hybrid và keyword bằng HitRate@10/nDCG@10.
2. **Từ chối kết quả không phù hợp.** Đo trên query có/không có đáp án để hiệu chỉnh
   relevance gate; không dùng một ngưỡng cosine/RRF tùy ý hoặc coi score là xác suất.
3. **Thực thi điều kiện và phủ định.** Planner có `exclusions`, nhưng retriever chưa
   nhận trường này để lọc ảnh. Điều kiện số lượng, màu sắc, quan hệ hoặc nhiều nhóm
   cần biểu diễn/truy xuất/kiểm chứng rõ ràng; embedding gần chủ đề chưa đủ bảo đảm.
4. **Rerank và kiểm tra bằng pixel khi cần.** Rerank hiện tắt và chỉ dựa annotation;
   muốn kiểm chứng thuộc tính thị giác cần nhãn đáng tin hoặc model xem ảnh.
   Đánh giá chất lượng/latency trước khi bật vào luồng mặc định.

Chưa cần thay model hoặc embedding lại corpus chỉ để có luồng text-to-image:
luồng này đã tồn tại và hoạt động trên full index.

Nếu chọn dùng bản HF vừa import, đặt trong `.env` rồi restart backend:

```dotenv
EMBEDDING_ARTIFACT_DIR=data/embeddings/culture-siglip2-t4x2-v1
```
