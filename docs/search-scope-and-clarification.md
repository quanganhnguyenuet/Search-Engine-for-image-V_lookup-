# Giới hạn phạm vi và hỏi lại khi tìm ảnh

Phạm vi sản phẩm là ảnh văn hóa/cảnh quan Việt Nam trong sáu nhóm: ẩm thực,
kiến trúc, phong cảnh, lễ hội, trang phục và thủ công mỹ nghệ.

**Ba kết quả trước retrieval**

- `in_scope`: tiếp tục lookup hoặc hybrid theo định tuyến hiện có.
- `out_of_scope`: trả `items=[]`, `scope_rejected=true`,
  `abstention_reason=out_of_scope`, thông báo phạm vi và các truy vấn thay thế.
- `unclear`: không trả ảnh; hỏi một câu cụ thể hoặc đưa gợi ý để người dùng chọn.

Response từ chối/hỏi lại có HTTP 200 vì đây là kết quả nghiệp vụ. Request sai
schema vẫn trả 422; lỗi provider vẫn trả lỗi rõ ràng, không tự trả ảnh gần nhất.

**Policy local và LLM**

`application/search_scope.py` định nghĩa phạm vi, ví dụ và các nhóm đối tượng
không hỗ trợ. Policy dùng cụm từ và tên có trong corpus, không dùng cosine/RRF
làm xác suất hoặc tự đặt ngưỡng relevance. Các trường hợp rõ ràng như tàu vũ trụ,
sao Hỏa, địa danh nước ngoài được cấu hình, sản phẩm công nghệ hoặc viết code
bị chặn trước encoder/planner, kể cả khi người dùng chọn category trong scope.

Đối tượng chưa được nhận diện không được mặc định đi vào vector search: auto
chuyển sang planner nếu LLM được bật; nếu không dùng LLM thì hỏi rõ đối tượng.
Phủ định/lựa chọn liên quan đến đối tượng ngoài scope cần diễn giải thay vì
chặn chỉ vì thấy từ khóa. Ví dụ `áo dài, không có tàu vũ trụ` được chuyển sang
planner/hỏi lại; `áo dài trên sao Hỏa` bị từ chối.

Local policy là bộ quy tắc phạm vi có thể rà soát và mở rộng, không phải bộ hiểu
ngữ nghĩa tổng quát. Một đối tượng hỗ trợ xuất hiện trong query chưa chứng minh
mọi địa danh/thuộc tính mới trong câu đều thuộc scope. Nhánh LLM kiểm tra toàn
yêu cầu; cần tiếp tục bổ sung paraphrase và đối chứng để đo sai sót của auto/local.

Planner bắt buộc trả trường `scope` và `scope_reason`. Scope thiếu hoặc sai được
yêu cầu sửa một lần; vẫn sai thì dừng. `out_of_scope` phải có lý do, không có
positive embedding queries/keywords và không giả làm câu hỏi clarification.
LLM không được bỏ một điều kiện ngoài scope để biến yêu cầu thành trong scope.
Các gợi ý thay thế là lựa chọn mới, chỉ chạy khi người dùng bấm chọn.

Guard áp dụng cho truy vấn **text** ở `/search/text`, `/search/semantic` và
`/search/rag`; semantic baseline không gọi LLM. Chưa thêm bộ xác minh scope pixel
cho ảnh người dùng upload. Keyword search vẫn chỉ lookup metadata trong corpus.

Scope guard chạy độc lập với relevance gate hiện có. Nó chặn đối tượng ngoài
miền, không chứng minh rằng query trong miền chắc chắn có ảnh đáp ứng mọi điều
kiện. Relevance policy vẫn cần nhãn và hiệu chỉnh riêng trước khi bật enforce.

**Vòng hỏi–đáp giữ ngữ cảnh**

API trả `clarification_context` cùng câu hỏi, tối đa bốn lượt. Khi trả lời, gửi
nguyên context này và đặt `query` là câu trả lời mới:

```json
{
  "query": "phố cổ",
  "clarification_context": [
    {
      "query": "Tìm pho ban đêm, không có người trong ảnh",
      "question": "Bạn muốn món phở hay phố cổ?"
    }
  ]
}
```

Planner đọc yêu cầu ban đầu, câu hỏi đã hỏi và câu trả lời mới; giữ các điều kiện
ban đêm/không có người trừ khi người dùng thay đổi. Đây là hướng dẫn/payload
được kiểm thử, chưa phải bảo đảm suy luận đúng cho mọi câu.

Cache plan bao gồm query, category và toàn bộ context. Hai cuộc hỏi–đáp có cùng
câu trả lời ngắn không được dùng chung plan nếu lịch sử khác nhau. Sau giới hạn
bốn lượt, hệ thống yêu cầu một mô tả đầy đủ mới, không âm thầm cắt bỏ điều kiện cũ.
Nếu gửi câu trả lời tiếp nối khi LLM không được dùng, hệ thống yêu cầu nhập lại
yêu cầu đầy đủ thay vì embed riêng câu trả lời ngắn.

Lookup một tên không dấu nhập nhằng vẫn có thể hỏi và xử lý các lựa chọn tên
độc lập ngay tại local; không bắt buộc gọi LLM cho `pho` → `phở`/`phố`.

**Giao diện**

- Có lựa chọn **Nhờ AI phân tích yêu cầu và hỏi lại khi cần**, tương ứng
  `use_llm=true` cho tìm theo yêu cầu; vẫn cần bật LLM và credential hợp lệ ở backend.
- Câu hỏi có nút gợi ý và ô trả lời tự do với nút **Tiếp tục tìm kiếm**.
- Gợi ý clarification và câu trả lời giữ context; thay category/phân trang cũng
  giữ context. Một tìm kiếm mới hoặc gợi ý sau từ chối scope bắt đầu yêu cầu mới.
- Lịch sử trình duyệt lưu context trong history state. URL riêng chỉ có query
  mới nhất; chia sẻ URL không chia sẻ toàn bộ cuộc hỏi–đáp.
- Từ chối scope có thông báo riêng và gợi ý khám phá. Không hiển thị dòng “ảnh
  gần nhất” cho một response không chạy retrieval.

**Kiểm chứng ngày 08/11/2026**

Các bài mới trong `services/api/tests/test_search_scope.py` kiểm tra chặn trước
encoder/planner/retriever, mixed query, dấu/không dấu, `use_llm` auto/false/true,
scope độc lập với relevance policy, semantic/RAG không đi vòng guard, validation
scope, clarification có context, cache tách lịch sử và giới hạn số lượt.

`apps/web/tests/search-clarification.test.cjs` kiểm tra proxy giữ nguyên history,
cờ ép planner và structured response; semantic không nhận các control hội thoại.
`npm run test:search` chạy cả ba file kiểm thử frontend.

Smoke trên index thật `culture-labels-v1` có 21 trường hợp qua FastAPI TestClient:
15 lượt ngoài scope trên ba endpoint bị chặn không encode; hai query chưa rõ
được hỏi lại local; bốn query hợp lệ vẫn lookup/hybrid hoặc hỏi rõ tên.
Kết quả ở `data/diagnostics/scope-clarification-rollout-2026-11-08.json`.

Production frontend build và TypeScript đã đạt. Lượt gọi provider thật cho yêu
cầu chưa rõ trả HTTP 401, `openai_authentication_failed`; vì thế chưa xác nhận
được chất lượng hỏi lại/hiểu scope của model thật trong môi trường hiện tại.
Kiểm thử orchestration dùng provider giả lập và không dùng API key thật.
