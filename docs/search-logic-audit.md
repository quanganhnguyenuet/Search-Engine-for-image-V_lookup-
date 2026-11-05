# Rà soát logic relevance, suy luận và single/multi intent

Ngày: 05/11/2026. Phạm vi: audit, không thay ranking, cấu hình hoặc artifact.

> Cập nhật sau audit: đã thêm routing tự động để query tên/tags và mô tả thông thường
> bỏ qua LLM. Các quan sát bên dưới ghi nhận luồng trước thay đổi routing. Các vấn
> đề về kiểm chứng điều kiện, phủ định và chia nhóm multi intent vẫn chưa được giải
> quyết chỉ bằng routing; xem [luồng hiện tại](contextual-image-search.md).
>
> Cập nhật ranking single intent: nhánh lexical đã ưu tiên tên khớp và tên biến thể
> bắt đầu bằng cụm query, rồi mới tới nhắc tới trong tên và tags. Query bánh chưng
> không còn xếp các ảnh lễ hội chỉ có tag lên đầu trong kiểm tra full corpus.
> Đây là sửa ranking theo metadata, không thay thế kiểm chứng chi tiết bằng pixel.

## Kết luận

GPT-4o mini thường giữ được ý chính trong các query đã kiểm tra, nhưng pipeline
chưa biến ý nghĩa đó thành điều kiện nghiệm thu cho từng ảnh. Hiện trạng là tìm
ứng viên gần về chủ đề, chưa phải constraint-aware image search. Single intent đơn
giản có kết quả đúng ở đầu; nhiều thuộc tính, số lượng, quan hệ và phủ định chưa
được bảo đảm. Multi intent chưa được biểu diễn hoặc thực thi thành các nhánh riêng.

Đây là kết luận từ đọc code, tám query thật và bốn thử nghiệm rerank trên shortlist,
không phải metric chất lượng trên tập nhãn đại diện.

## Cách kiểm tra và bằng chứng

- API thực tế `127.0.0.1:8000` báo full index 15.423 ảnh, metadata sẵn sàng,
  GPT-4o mini. `.env` hiện đặt `SEARCH_LLM_RERANK=false`.
- Gửi tám query qua `/api/v1/search/text`, `top_k=24`, `use_llm=true`, không category
  filter, bao gồm đối tượng, hành động, nhiều thuộc tính, vai trò, phủ định,
  nhiều intent độc lập và ngoài miền.
- So sánh thứ hạng image/metadata/lexical bằng encoder và full artifact local.
- Gọi `select` riêng trên top 8 ở bốn case; không bật rerank cho server người dùng.
- Xem trực tiếp ba ảnh gốc để đối chiếu annotation với pixel, trong đó có ảnh
  Hội An áo dài trắng/đội nón và ảnh hai người đang vẽ bình gốm.
- Dữ liệu local: `data/diagnostics/search-logic-audit/live-api.json` và
  `data/diagnostics/search-logic-audit/ablation.json`. Không chứa API key.

| Trường hợp | LLM diễn giải | Kết quả quan sát |
| --- | --- | --- |
| Đĩa bánh đậu xanh cạnh tách trà | Giữ bánh, trà và quan hệ cạnh nhau | Bốn kết quả đầu có title bánh đậu xanh; rank 5 đã sang bánh gai. Không nên coi cả top-K đều đạt đủ điều kiện |
| Phụ nữ mặc áo bà ba múc nước từ chum bằng gáo dừa | Giữ được các thành phần chính | Top 1 khớp chú thích; rank 2 là người cầm sào, rank 3 là nhuộm vải |
| Áo dài đỏ trước Hội An, không đội nón | Có đỏ, Hội An và `exclusions=["nón"]` | Rank 2 là áo dài trắng; xem pixel thấy còn đội nón. Caption có màu trắng nhưng không ghi nón |
| Hai người làm gốm trên bàn xoay, một người hướng dẫn | Giữ vai trò trong visual/metadata query | Top 1 là hai người vẽ bình, không có bàn xoay trong ảnh đã xem; ứng viên có annotation đúng nằm rank 7 |
| Hai nhóm riêng: bánh đậu xanh kèm trà và nghệ nhân làm gốm | Tóm tắt được hai nhóm | Response vẫn một list; top 10 có 9 ảnh category ẩm thực và 1 thủ công; không phân nhóm hoặc bảo đảm mỗi intent |
| Áo dài trắng, không có áo dài đỏ | Có `exclusions=["áo dài đỏ"]` | Đồng thời đưa `"không có áo dài đỏ"` vào positive keywords; backend tách từ và có thể cộng điểm cho `đỏ`. Danh sách phần lớn là áo dài trắng, nhưng logic loại trừ chưa được thực thi |
| Tàu vũ trụ trên sao Hỏa | Diễn giải đúng mục tiêu ngoài miền | Vẫn trả 24 ảnh: Hồ Tây, pháo hoa, chùa, núi… |
| Người lớn dạy trẻ em làm gốm | Có người lớn, trẻ em, hành động | Top đầu là người lớn làm gốm, chưa có bằng chứng đáp ứng trẻ em hoặc quan hệ dạy học |

## 1. LLM đang hiểu đến đâu?

`QueryUnderstanding.plan` chủ yếu rewrite, dịch và trích keywords. Schema chỉ có
`intent`, `visual_query`, `metadata_query`, `keywords`, `exclusions`.
Chưa có `intents[]`, điều kiện bắt buộc/tùy chọn, binding màu → đối tượng, lượng từ,
vai trò, nhóm kết quả, số lượng mỗi nhóm hoặc phép nối AND/OR.

Trong tám query này, phần diễn giải thường đúng ý chính; case phủ định cho thấy
parser vẫn có lỗi đưa cụm phủ định vào positive keywords. JSON hợp lệ không tự
chứng minh diễn giải hay suy luận đúng. [OpenAI Docs](https://developers.openai.com/api/docs/guides/structured-outputs)
cũng nêu Structured Outputs vẫn có thể chứa sai sót nội dung.

Không nên kết luận GPT-4o mini nói chung không có khả năng suy luận. Kết luận hẹp
ở đây: ứng dụng chưa yêu cầu và kiểm tra một quá trình suy luận theo từng điều kiện;
LLM cũng không xem pixel trong các call hiện tại.

## 2. Lỗi/gap chính của retrieval

### Không có bước loại ảnh không liên quan

`HybridRetriever.search` luôn cắt top `limit`, không có relevance gate hoặc chính
sách abstain. Ở UI `top_k=24`; nếu corpus có đủ ảnh, mặc định trả đủ 24 ngay cả khi
mọi ứng viên đều không đáp ứng. RRF score là thứ hạng tương đối, không phải xác suất
relevant hoặc căn cứ tự đặt một ngưỡng cosine chung cho mọi query.

### `exclusions` bị bỏ qua khi rerank tắt

`SearchService.text_search` chỉ truyền vectors và `plan.keywords` vào hybrid.
`plan.exclusions` chỉ nằm trong plan truyền cho bước LLM select tùy chọn; không có
hard/soft negative constraint ở retrieval hoặc kiểm chứng hậu xử lý mặc định.

### Mất cụm từ, quan hệ và binding đối tượng

Lexical dùng `set(normalize(" ".join(terms)).split())`, rồi cộng số token giao nhau.
Ví dụ `bánh đậu xanh` trở thành ba từ; `áo dài đỏ` không còn ràng buộc màu đỏ với áo
dài. Các từ `người`, `ảnh`, `có`, `không`, `trên` có thể tham gia tính điểm. Không có
phrase matching, phủ định, role binding hay kiểm tra đủ tất cả điều kiện.

Hai nhánh dense dùng `max` trên câu gốc và các rewrite: ảnh được lấy điểm từ rewrite
khớp nhất, không kiểm tra riêng từng điều kiện. Chia đoạn và mean-pool cũng không
biến embedding thành bộ thực thi phép AND giữa các mệnh đề.

### Fusion ưu tiên đồng thuận chủ đề hơn bằng chứng đáp ứng đầy đủ

Ba nhánh image, metadata và lexical được cộng RRF cùng trọng số, depth 100, chưa
được hiệu chỉnh bằng nhãn relevance. Một ảnh đúng chủ đề ở cả ba nhánh có thể vượt
ảnh đáp ứng chi tiết nhưng chỉ mạnh ở hai nhánh.

Case hai người/hướng dẫn/bàn xoay là ví dụ cụ thể:

- Ảnh `ceramic Hue`, ID `33c753d329fc45b149bd792e3480785515746e8af793a9d3e3f0d5cc230b14dc`,
  có đúng annotation hai người và hướng dẫn/học viên.
- Image rank **4**, lexical rank **1**, metadata rank **2.579**; fusion đẩy xuống
  rank **7**. Do metadata rank > 100, ứng viên mất toàn bộ phần điểm nhánh này.
- Ảnh hai người vẽ bình có image rank **6**, metadata rank **2**, lexical rank
  **39**, nên thắng về RRF dù không có đủ bàn xoay/hướng dẫn.

Điều này cho thấy cần đánh giá nhánh text-text và fusion, chưa đủ để tuyên bố đổi
embedding model là chắc chắn giải quyết được. SigLIP2 có mục tiêu image-text
retrieval; chất lượng metadata text-text + custom pooling trong domain này vẫn
cần đánh giá riêng. [Paper SigLIP2](https://arxiv.org/abs/2502.14786).

## 3. Chỉ bật rerank có đủ không?

Chưa đủ. Thử nghiệm trên top 8 cho thấy:

- Case bàn xoay: rerank đưa đúng ứng viên `ceramic Hue` lên đầu, nhưng còn giữ một
  ảnh hai thợ làm gốm mà chú thích chưa xác nhận bàn xoay/quan hệ hướng dẫn.
- Case áo dài đỏ/Hội An: chọn ảnh áo dài đỏ ở “phố cổ Việt Nam”, chưa chứng minh
  Hội An; reason còn khẳng định không đội nón dù caption không nói điều đó.
- Case sao Hỏa: trả cả tám ID với reason như **“Không liên quan đến tàu vũ trụ hay
  sao Hỏa.”** Schema/validator vẫn chấp nhận vì chỉ kiểm tra ID, số lượng, trùng
  lặp và độ dài, không có decision hoặc kết quả từng constraint.

Schema `suggestions=[{candidate_id,reason}]` không đủ để API phân biệt đánh giá
ứng viên với ảnh thực sự được chấp nhận. Bật flag hiện tại không phải cách sửa
relevance hoàn chỉnh. Shortlist tối đa 8 cũng có thể bỏ sót ảnh đúng nằm phía sau.

## 4. Single intent và multi intent

- **Single intent đơn giản:** có dấu hiệu tốt ở đầu danh sách, chưa có nhãn để gọi
  toàn bộ top-K đạt chất lượng. Ví dụ một nhu cầu tìm bánh và trà.
- **Single intent nhiều điều kiện:** một ảnh phải có áo dài đỏ + Hội An + không
  nón. Đây vẫn là một intent, chưa được enforce đầy đủ.
- **Single intent có quan hệ:** hai người, một người dạy người còn lại. Tìm cùng
  chủ đề gốm chưa đủ; cần bằng chứng về số lượng và vai trò.
- **Multi intent độc lập:** hai nhóm bánh/trà và nghệ nhân gốm. Cần tách nhánh,
  retrieve từng nhánh và phân bổ/nhóm kết quả. Code hiện chưa có cấu trúc này.

Không nên gọi mọi câu có nhiều đối tượng hoặc chữ “và” là multi intent. Phải phân
biệt một cảnh kết hợp nhiều điều kiện, nhiều lựa chọn OR, và nhiều yêu cầu lấy ảnh
độc lập.

## 5. Thứ tự sửa đề xuất

1. **Intent/constraint contract:** tách intent, chế độ all/any/separate groups,
   các entity/attribute/relation, must/should/must_not và quantity. Validate positive
   keywords không lẫn điều kiện loại trừ. Giữ câu gốc và giải thích ngắn cho UI.
2. **Retrieve riêng từng intent:** candidate union đủ rộng từ các nhánh, bảo toàn
   cụm từ, giảm từ chung; không dùng fusion như bước xác nhận đã đáp ứng. Đo lại
   text-text và trọng số/cutoff bằng nhãn trước khi đổi model/index.
3. **Judge theo từng điều kiện:** output `supported/contradicted/unknown`, quote
   bằng chứng từ metadata và decision rõ. API quyết định nhận/loại theo policy;
   không coi “không được nhắc tới” là “không xuất hiện”. Thiếu bằng chứng phải nói
   chưa xác nhận, hoặc dùng bước kiểm chứng pixel cho chi tiết thị giác cần thiết.
4. **Return/abstain đúng:** không bù đủ top_k bằng ảnh sai; tách ảnh đáp ứng và khớp
   một phần nếu sản phẩm muốn gợi ý mở rộng. Với multi intent, ghi rõ group và
   không để một nhóm chiếm gần hết kết quả.
5. **Nghiệm thu bằng nhãn:** Precision@5/@10, full-constraint satisfaction, tỉ lệ
   vi phạm phủ định, coverage từng intent, false-positive ngoài miền; thêm nDCG và
   latency. HitRate@10 đơn lẻ có thể che chín kết quả sai trong top 10.

Ưu tiên parser + kiểm chứng điều kiện + abstain trước. Chưa có bằng chứng Qdrant
hoặc index lớn hơn sẽ sửa được lỗi relevance/intent đang thấy trong exact search.
