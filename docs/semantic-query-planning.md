# Hybrid search: tìm local trước, dùng LLM khi cần reasoning

Đặt trong `.env` và restart backend:

```dotenv
SEARCH_LLM_ENABLED=true
SEARCH_ROUTING_MODE=auto
SEARCH_LLM_RERANK=true
SEARCH_QUERY_ROLES=true
SEARCH_RERANK_EVIDENCE=annotations
```

Giữ nguyên `SEARCH_LLM_MODEL` đang dùng và API key hợp lệ ở backend.
Không cần tạo lại embedding corpus. Keyword search và semantic search là các
baseline riêng; thay đổi áp dụng cho `POST /api/v1/search/text`, tương ứng chế độ
**Tìm theo yêu cầu** trên FE.

## Ba nhánh trong chế độ auto

- Tên/tag khớp rõ: lookup index, không gọi encoder hoặc LLM.
- Mô tả trực tiếp: kết hợp embedding ảnh, embedding title/tags/description và
  từ khóa bằng RRF local. Không gọi planner hoặc rerank LLM, kể cả scope cũ là `all`.
- Yêu cầu reasoning hoặc intent chưa chắc chắn: LLM hiểu câu, tạo plan rồi tìm
  bằng cùng index ảnh/description; bật rerank để kiểm tra điều kiện trên shortlist.

Tên không dấu có nhiều nghĩa trong corpus (ví dụ `pho`) được hỏi lại ngay bằng
index, không cần LLM. Router học từ ví dụ gán nhãn bằng word/bigram/character
features và bộ phân loại tuyến tính nhỏ; **không phải một LLM hay bộ hiểu ngữ nghĩa
hoàn chỉnh**. Nó không tìm vài từ khóa cố định để bật OpenAI, và giữ khác biệt dấu
`trừ/trụ`, `những/nhưng`. Trường hợp sát ranh giới được chuyển sang LLM.

Đây là bộ định tuyến xấp xỉ, có thể gọi thừa hoặc bỏ sót reasoning trên câu mới.
`use_llm=true` ép dùng planner cho một request; `SEARCH_ROUTING_MODE=always` đưa
mọi query qua LLM. `use_llm=false` hoặc tắt `SEARCH_LLM_ENABLED` giữ toàn bộ local.

Ví dụ auto:

| Query | Nhánh dự kiến |
| --- | --- |
| `bánh chay` | Lookup tên/tag |
| `người mặc áo dài đỏ` | Hybrid local |
| `ruộng bậc thang xanh ở vùng núi` | Hybrid local |
| `pho` | Hỏi rõ nghĩa từ index |
| `mấy cái lăng ở Huế bỏ cái lăng của Bác ra nhé` | LLM + hybrid + rerank |
| `người lớn chỉ cho trẻ nhỏ cách nặn một chiếc bình` | LLM + hybrid + rerank |

## Quy trình trong nhánh LLM

1. Query cần reasoning (hoặc query bị ép dùng LLM) được gửi cùng category filter và danh mục tên ảnh
   có giới hạn (tối đa 512 tên/12.000 ký tự) để hiểu tên gọi gián tiếp theo corpus.
   Các tên không dấu có nhiều cách đọc trong corpus được cung cấp như ngữ cảnh;
   LLM quyết định hỏi lại theo ngữ nghĩa.
2. LLM trả kế hoạch JSON: `intent`, `requirements` (điều kiện bắt buộc, giữ phạm vi
   và/hoặc, số lượng, quan hệ), `preferences` (ưu tiên tùy chọn), `exclusions`,
   `visual_query` tiếng Anh, `metadata_query` tiếng Việt và `keywords` tích cực.
   Có thể hiểu tên gọi gián tiếp, lỗi gõ nhỏ và không dấu nhưng không được bịa danh tính.
3. Nếu có nhiều nghĩa làm thay đổi đối tượng, LLM trả `clarification_question` và
   `suggested_queries` nếu có lựa chọn phù hợp. Backend trả câu hỏi trước khi encode/retrieve; FE hiện
   lựa chọn để người dùng xác định ý định, không tự đoán rồi trả ảnh.
4. SigLIP2 chỉ encode mô tả đối tượng muốn tìm, không encode nguyên câu chứa tên
   bị loại trừ. Query ảnh và metadata có vai trò riêng. Fusion lọc tên/tag khớp
   `exclusions` trước khi xếp hạng và lấy shortlist. Nếu truy vấn tích cực còn chứa
   cụm bị loại trừ hoặc JSON không qua validation, yêu cầu model sửa một lần;
   nếu vẫn sai thì dừng trước encoder. Query embedding có thể để trống khi đang
   hỏi lại, vì chưa chạy retrieval cho đến khi xác định được ý định.
5. Nếu bật rerank, LLM đối chiếu ứng viên với điều kiện bắt buộc rồi mới dùng ưu
   tiên tùy chọn để sắp thứ tự. Có thể trả ít hơn `top_k` hoặc không có ảnh;
   không tự bổ sung ảnh đã bị loại. FE hiện cách hiểu, điều kiện và đối tượng loại trừ.

Hai lần gọi model thông thường cho query mới khi có ứng viên: lập kế hoạch rồi
rerank; có thể thêm một lần sửa truy vấn nếu kiểm tra phát hiện lỗi trong kế hoạch.
Cache kế hoạch tối đa 128 cặp `(query, category)`; query lặp lại vẫn rerank
nếu cấu hình bật. Bước clarification chỉ cần lập kế hoạch. Chi phí và độ trễ vì
vậy cao hơn luồng chỉ dùng local.

Nếu `SEARCH_LLM_ENABLED=false` hoặc request đặt `use_llm=false`, hệ thống dùng
lookup tên/tag và hybrid local, không gọi planner/rerank. `use_llm=true` không
vượt qua được cấu hình tắt LLM ở backend. Lỗi model trả lỗi rõ ràng.

## Điều kiện có ID trong query plan

`interpretation.requirements` và `interpretation.preferences` trả danh sách object
`{id, text}` thay cho danh sách chuỗi. Ví dụ với yêu cầu “Hai người làm gốm trên
bàn xoay, một người hướng dẫn, ưu tiên toàn cảnh”:

```json
{
  "requirements": [
    {"id": "R1", "text": "Có hai người trong cùng một ảnh"},
    {"id": "R2", "text": "Hai người đang làm gốm trên bàn xoay"},
    {"id": "R3", "text": "Một người hướng dẫn người còn lại"}
  ],
  "preferences": [
    {"id": "P1", "text": "Ảnh toàn cảnh"}
  ]
}
```

Mỗi plan có tối đa 8 requirements (`R1` đến `R8`) và 4 preferences (`P1` đến
`P4`); từng danh sách bắt đầu từ 1 và không trùng hoặc nhảy số. ID có phạm vi
trong một plan, giữ nguyên qua cache, response API và payload rerank; không phải
định danh toàn cục giữa các query khác nhau. `text` được trim, có 1–240 ký tự.
Danh sách rỗng được phép. Cấu trúc chuỗi cũ bị từ chối; nếu model trả sai, backend
yêu cầu sửa một lần theo cơ chế hiện có.

Planner tách điều kiện có thể đối chiếu độc lập, giữ màu gắn với đúng đối tượng,
số lượng và quan hệ theo yêu cầu gốc. Một lựa chọn “đỏ hoặc xanh” vẫn nằm trong
cùng một điều kiện; “ưu tiên toàn cảnh” thuộc preferences. Không tự đổi “hai người”
thành “đúng hai người” khi ngữ cảnh chưa yêu cầu giới hạn đó.

Frontend hiển thị `condition.text`. Reranker nhận đầy đủ `{id, text}`. Đây là bước
chuẩn hóa kế hoạch để có thể gắn bằng chứng theo điều kiện ở bước tiếp theo;
chưa bổ sung verifier, trạng thái đáp ứng hoặc bảo đảm từng ảnh thỏa yêu cầu.

## Kiểm tra

Các câu không cần từ khóa kích hoạt:

- `nơi yên nghỉ của các vua triều Nguyễn`
- `mấy cái lăng ở Huế bỏ cái lăng của Bác ra nhé`
- `người lớn chỉ cho trẻ nhỏ cách nặn một chiếc bình`
- `áo dài đỏ hoặc xanh trong cùng một ảnh có hai người ưu tiên toàn cảnh`
- `pho` để kiểm tra nhập nhằng nếu thiếu ngữ cảnh phân biệt phở/phố.

Response có `search_route=lexical|hybrid|llm_hybrid`, `routing_mode`, `routing_reason`,
`routing_classifier`, `routing_score` (không phải xác suất), `llm_used`, `llm_call_count`
và `timings.routing_ms`. Query local có `llm_call_count=0` kể cả khi cả hai cờ LLM
đều bật. Nhánh LLM còn có `interpretation` và `plan_cache_hit`.
`/api/v1/search/status` báo `query_routing=auto|always|local`.
Kiểm tra cả plan lẫn ảnh thực tế, không chỉ số lần gọi model.

Train lại bộ định tuyến bằng dữ liệu được đóng gói cùng API:

```bash
OPENBLAS_NUM_THREADS=4 .venv/bin/python scripts/train_query_router.py
```

Model dùng 986 mẫu train gồm câu gốc và bản không dấu/prefix, 40 mẫu validation
để chọn ranh giới và 20 mẫu test riêng. Các nhãn là bản nháp do tác giả soạn, chưa
phải tập benchmark người dùng thật. Report ở `data/evaluation/query-routing-report-v1.json`.
Không dùng các mẫu test để train. Cần thêm paraphrase thực tế và chấm độc lập để
đánh giá độ bao phủ; không suy từ tập nhỏ này ra độ chính xác production.

Smoke với index thực ghi ở `data/evaluation/auto-routing-smoke-v1.json`:
`bánh chay`, `người mặc áo dài đỏ`, `ruộng bậc thang xanh ở vùng núi`,
`nghệ nhân làm gốm` và `pho` đều không gọi planner/rerank. Lượt `nghệ nhân làm gốm`
khi model đã nạp mất khoảng 206 ms trên máy hiện tại; lần hybrid đầu còn cần nạp
SigLIP2 và xây postings nên khoảng 9 giây. Đây là smoke, chưa phải benchmark.
Gọi `/search/warmup` trước khi đo độ trễ để tách khởi động khỏi thời gian truy vấn.

## Giới hạn và đánh giá

JSON hợp lệ chưa chứng minh model hiểu đúng câu. Kiểm tra hồi quy dùng provider
giả lập để xác minh orchestration, điều kiện được chuyển đến selector, cache theo
ngữ cảnh, clarification trước retrieval và không đưa đối tượng loại trừ vào encoder.
Đánh giá chất lượng suy luận vẫn cần chạy model thật trên tập query được chấm nhãn.

Rerank annotation chỉ biết tên/tag/description, không thể xác minh chi tiết không
có trong chú thích. `SEARCH_RERANK_EVIDENCE=pixels` hỗ trợ đối chiếu thumbnail nếu
model đang dùng hỗ trợ vision; vẫn cần đánh giá lỗi. Không tự suy ra quan hệ hay
phủ định từ điểm cosine. Pipeline signature v4 tách các policy relevance cũ để
tránh áp dụng ngưỡng đã hiệu chỉnh cho cách encode query trước đây.

Prompt yêu cầu kết quả có cấu trúc và mục tiêu rõ ràng, không yêu cầu model xuất
chuỗi suy nghĩ. Tham khảo [OpenAI Docs về reasoning](https://developers.openai.com/api/docs/guides/reasoning-best-practices)
và [giới hạn Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
