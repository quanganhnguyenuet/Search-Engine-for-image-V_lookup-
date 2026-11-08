# Kịch bản thuyết trình VisualSeek

**Thời lượng:** khoảng 8 phút; có hướng dẫn rút xuống 5 phút và mở rộng lên 10 phút.

**Phạm vi:** giới thiệu kiến trúc thử nghiệm, phần đã triển khai, mục tiêu đánh giá, phương pháp so sánh và hướng nghiên cứu, ứng dụng tương lai.

**Thông điệp chính:** VisualSeek đã có hệ thống tìm kiếm hoạt động. Bước tiếp theo là chứng minh ảnh trả về đáp ứng đúng yêu cầu, với thời gian và chi phí hợp lý.

Các đoạn “Lời thoại” là phần để nói. Các đoạn còn lại là hướng dẫn chuẩn bị. Thời lượng là dự kiến; cần đọc thử để điều chỉnh theo tốc độ nói thực tế.

## Slide 1 — VisualSeek: Tìm ảnh theo nhu cầu bằng ngôn ngữ tự nhiên

**Thời gian:** 35 giây · **Mốc kết thúc:** 00:35

**Trên slide:** tên dự án; miền văn hóa Việt Nam; một truy vấn mẫu và ảnh minh họa.

**Lời thoại:**

> Em xin giới thiệu VisualSeek, dự án tìm kiếm ảnh với miền thử nghiệm là văn hóa Việt Nam.
>
> Ví dụ, người dùng cần “ảnh người lớn hướng dẫn trẻ làm gốm, ưu tiên góc toàn cảnh”. Họ có thể không biết tên ảnh hoặc tên làng nghề để tìm bằng từ khóa.
>
> Bài toán là đưa lên đầu những ảnh thực sự đáp ứng nhu cầu đó. Sau đây em sẽ trình bày kiến trúc thử nghiệm, phần đã làm được, cách đánh giá và hướng phát triển.

**Chuyển ý:** “Trước hết, hệ thống xử lý dữ liệu và truy vấn như thế nào?”

## Slide 2 — Kiến trúc thử nghiệm: Chuẩn bị offline, tìm kiếm online

**Thời gian:** 65 giây · **Mốc kết thúc:** 01:40

**Trên slide:** sơ đồ hai phần offline và online.

```text
OFFLINE
Ảnh + tên, tags, mô tả
        ↓ Chuẩn hóa
SigLIP2 → vector ảnh + vector metadata
        ↓
Artifact vector và metadata liên kết bằng asset ID

ONLINE
Truy vấn → Router
  ├─ Tên/tag rõ → Lookup local
  ├─ Mô tả thông thường → Hybrid local
  └─ Yêu cầu phức tạp → LLM planner → Hybrid → Rerank tùy chọn
                                              ↓
                                         Danh sách ảnh
```

**Lời thoại:**

> Kiến trúc gồm hai phần. Ở bước offline, ảnh được chuẩn hóa rồi đưa qua SigLIP2 để tạo vector ảnh. Tên, tags và mô tả được mã hóa thành vector metadata riêng. Hai loại vector cùng liên kết với một asset.
>
> Khi nhận truy vấn, router chọn luồng xử lý. Tên hoặc tag rõ ràng đi qua lookup local. Mô tả thông thường dùng hybrid, kết hợp độ tương đồng với ảnh, metadata và đối sánh từ khóa. RRF hợp nhất thứ hạng của ba kênh này.
>
> Với yêu cầu cần suy luận, LLM lập kế hoạch gồm điều kiện bắt buộc, ưu tiên và loại trừ. Hệ thống tìm ứng viên rồi có thể rerank. Nếu truy vấn nhập nhằng, hệ thống có thể hỏi lại trước khi tìm.
>
> Backend vector hiện dùng NumPy exact search. Thiết kế này cho phép tách từng thành phần để đo đóng góp của chúng.

**Lưu ý:** Qdrant chưa nằm trong luồng tìm kiếm hiện tại. Không trình bày Qdrant hoặc object storage như thành phần đã tích hợp.

## Slide 3 — Những gì đã làm được

**Thời gian:** 55 giây · **Mốc kết thúc:** 02:35

**Trên slide:** ba khối “Dữ liệu”, “Tìm kiếm”, “Ứng dụng và đánh giá”.

- 15.423 asset thuộc 6 nhóm văn hóa; 15.411 asset được phép tìm kiếm.
- Hai loại vector: ảnh và metadata, mỗi loại 768 chiều.
- Lookup, semantic, hybrid; router, planner và rerank tùy chọn.
- Web, API và công cụ chuẩn bị đánh giá.

**Lời thoại:**

> Theo báo cáo kiểm tra hiện có, corpus gồm 15.423 asset thuộc sáu nhóm văn hóa. Trong đó, 15.411 asset được phép tìm kiếm; 12 asset được loại khỏi phạm vi tìm kiếm sau rà soát dữ liệu.
>
> Em đã có quy trình chuẩn bị dữ liệu, tạo vector ảnh và metadata 768 chiều, cùng giao diện web và API. Hệ thống có các chế độ tìm kiếm để đối chiếu và công cụ tạo tập ứng viên, xuất bảng chấm nhãn, tính chỉ số.
>
> Nhánh local đã được chạy bằng model thật. Planner và rerank đã có trong code, nhưng lần kiểm tra LLM gần nhất gặp lỗi xác thực. Vì vậy, chất lượng của nhánh này vẫn cần được xác nhận bằng thực nghiệm với provider hoạt động.

**Lưu ý:** Đây là bằng chứng triển khai và vận hành thử, chưa phải kết luận về độ chính xác tổng thể.

## Slide 4 — Khoảng trống: Đúng chủ đề chưa đủ để đúng yêu cầu

**Thời gian:** 55 giây · **Mốc kết thúc:** 03:30

**Trên slide:** truy vấn làm gốm được tách thành các điều kiện.

| ID | Điều kiện |
| --- | --- |
| R1 | Có người lớn và trẻ nhỏ |
| R2 | Có hoạt động làm gốm |
| R3 | Người lớn đang hướng dẫn trẻ |
| P1 | Ưu tiên góc toàn cảnh |

**Lời thoại:**

> Với ví dụ ban đầu, một ảnh có đồ gốm có thể đúng chủ đề nhưng vẫn thiếu người lớn, trẻ nhỏ hoặc hành động hướng dẫn.
>
> Vector tổng thể có thể bỏ sót số lượng, quan hệ và điều kiện phủ định. Rerank bằng annotation cũng chỉ biết những gì được ghi trong chú thích. Chú thích không nhắc tới một chi tiết chưa đủ để kết luận chi tiết đó không có trong ảnh.
>
> Planner hiện đã biểu diễn điều kiện bằng các ID như R1, R2. Đây là nền tảng cho bước kiểm chứng, chưa chứng minh từng ảnh thỏa điều kiện. Bộ 200 query cũng chưa có nhãn xác nhận, nên hiện chưa thể kết luận hybrid tốt hơn các baseline.

**Chuyển ý:** “Để kiểm tra điều đó, em đề xuất đo chất lượng ở những tầng sau.”

## Slide 5 — Các chỉ số dự án hướng tới

**Thời gian:** 70 giây · **Mốc kết thúc:** 04:40

**Trên slide:** bảng chỉ số ngắn gọn.

| Mục tiêu | Chỉ số dự kiến |
| --- | --- |
| Đưa ảnh phù hợp lên đầu | nDCG@10 — chỉ số chất lượng chính |
| Đánh giá kết quả đầu | Precision@10, HitRate@10 |
| Đáp ứng yêu cầu phức tạp | Tỷ lệ ảnh thỏa toàn bộ điều kiện bắt buộc trong top K |
| Xử lý trường hợp không có đáp án | Tỷ lệ trả ảnh khi không có đáp án; tỷ lệ trả rỗng khi có đáp án |
| Thời gian và tài nguyên | Latency p50/p95, chi phí/query, số lần gọi LLM |

**Lời thoại:**

> Em đề xuất nDCG@10 làm chỉ số chính vì nó đánh giá cả mức phù hợp lẫn thứ tự: ảnh phù hợp hơn cần xuất hiện sớm hơn. Precision@10 cho biết tỷ lệ ảnh đúng trong mười kết quả; HitRate@10 cho biết có ít nhất một ảnh đúng hay không.
>
> Với truy vấn phức tạp, cần chấm thêm tỷ lệ ảnh thỏa toàn bộ điều kiện bắt buộc. Hệ thống cũng cần được đo khả năng từ chối khi không có đáp án và tránh trả rỗng khi có đáp án.
>
> Về hiệu năng, em sẽ báo cáo p50, p95, số lần gọi LLM và chi phí mỗi query. p95 phản ánh mức thời gian mà khoảng 95 phần trăm request trong tập đo không vượt quá.
>
> Recall trên toàn corpus chỉ nên công bố khi có tập đáp án đủ đầy đủ. Điểm cosine không được xem là xác suất ảnh đúng.

**Lưu ý:** Chỉ số điều kiện là phần đánh giá đề xuất, cần bổ sung nhãn và công cụ. Chưa chốt ngưỡng chất lượng hoặc độ trễ tuyệt đối khi chưa có baseline.

## Slide 6 — Thiết kế đo đạc và so sánh

**Thời gian:** 80 giây · **Mốc kết thúc:** 06:00

**Trên slide:** quy trình “Query → Chấm nhãn → Development → Held-out”, kèm các phương pháp đối chứng.

**Các nhóm query:**

- Tên chính xác và mô tả trực tiếp.
- Nhiều điều kiện, số lượng, quan hệ và phủ định.
- Nhập nhằng hoặc không có đáp án trong corpus.

**Các phương pháp so sánh:** lexical, image-only, metadata-only, hybrid, hybrid + planner, hybrid + planner + rerank.

**Lời thoại:**

> Trước hết, em chia truy vấn thành các nhóm từ đơn giản đến phức tạp, có cả nhập nhằng và không có đáp án.
>
> Ứng viên được gộp từ các phương pháp để người chấm xem ảnh trực tiếp. Nhãn gồm ba mức: sai, phù hợp một phần và đáp ứng yêu cầu chính. Với query khó, chấm thêm từng điều kiện. Một phần dữ liệu nên có hai người chấm độc lập để rà soát bất đồng.
>
> Tập development dùng để chọn trọng số và ngưỡng. Sau đó khóa cấu hình rồi đánh giá trên held-out. Những câu diễn đạt lại cùng một ý định phải nằm cùng split để hạn chế rò rỉ.
>
> Em sẽ so từng kênh riêng với hybrid, rồi bổ sung planner và rerank. Các lần chạy dùng cùng corpus, query, top K và công bố ngân sách ứng viên. Độ trễ cần tách cold với warm, cache hit với miss.
>
> Kết quả được báo cáo theo từng nhóm query, kèm khoảng tin cậy, để biết thành phần nào tạo ra cải thiện và chi phí đi kèm.

**Lưu ý:** Candidate pool chưa đầy đủ không cho phép suy ra Recall trên toàn corpus. Khi kiểm tra riêng annotation và pixel rerank, nên giữ nguyên shortlist để tách tác động của loại bằng chứng.

## Slide 7 — Hướng nghiên cứu: Kiểm chứng và tìm kiếm thích ứng

**Thời gian:** 75 giây · **Mốc kết thúc:** 07:15

**Trên slide:** ba hướng, mỗi hướng có câu hỏi và phép đo.

| Hướng | Câu hỏi thực nghiệm | Phép đo chính |
| --- | --- | --- |
| Kiểm chứng điều kiện bằng pixel | Có cải thiện so với annotation trên cùng shortlist? | Tỷ lệ thỏa toàn bộ điều kiện; nDCG; độ trễ, chi phí |
| Định tuyến thích ứng | Có giữ gần chất lượng always-LLM với ít lời gọi hơn? | Chất lượng, lỗi routing, số lần gọi, chi phí |
| Ảnh tham chiếu + yêu cầu thay đổi | Có giữ đúng đặc điểm cần giữ và đổi đúng đặc điểm được yêu cầu? | Ranking và mức đáp ứng điều kiện trên benchmark mới |

**Lời thoại:**

> Em đề xuất ba hướng nghiên cứu. Thứ nhất là kiểm chứng từng điều kiện bằng pixel. Trên cùng một shortlist, em so với rerank bằng annotation để xem ảnh trực tiếp có giúp xử lý quan hệ, số lượng và phủ định tốt hơn không.
>
> Thứ hai là định tuyến thích ứng. So sánh local-only, always-LLM và auto sẽ trả lời liệu có giữ gần chất lượng của luồng luôn dùng LLM với ít lời gọi hơn hay không.
>
> Thứ ba là tìm bằng ảnh tham chiếu kèm yêu cầu thay đổi, chẳng hạn “giống ảnh áo dài này nhưng màu xanh”. Đây là bài toán Composed Image Retrieval đã có hướng nghiên cứu và benchmark để tham khảo.
>
> Em muốn ưu tiên kiểm chứng điều kiện trước vì sát với giới hạn hiện tại. Đóng góp tiềm năng là phương pháp và benchmark cho truy vấn văn hóa tiếng Việt; tính mới cần được xác định sau khi khảo sát và có kết quả đối chứng.

**Tài liệu tham khảo:**

- [Winoground](https://huggingface.co/datasets/facebook/winoground): tham khảo thiết kế đánh giá khả năng hiểu tổ hợp giữa ngôn ngữ và hình ảnh.
- [SEARLE / Zero-Shot Composed Image Retrieval with Textual Inversion](https://arxiv.org/abs/2303.15247): tham khảo bài toán ảnh tham chiếu kết hợp mô tả thay đổi và benchmark CIRCO.

**Lưu ý:** Các hướng trên là đề xuất thực nghiệm, chưa phải kết quả hoặc tuyên bố đóng góp mới đã được chứng minh.

## Slide 8 — Ứng dụng tương lai và lộ trình

**Thời gian:** 45 giây · **Mốc kết thúc:** 08:00

**Trên slide:** ba use case và lộ trình ngắn.

- **Bài giảng, trưng bày số:** tìm bộ ảnh đủ các bước làm gốm, hạn chế trùng lặp.
- **Tìm ảnh có tương tác:** ảnh tham chiếu + “đổi sang màu xanh, có toàn thân”.
- **Tra cứu văn hóa qua hội thoại:** hỏi lại tên gọi hoặc ý định chưa rõ rồi thu hẹp kết quả.

```text
Hoàn thiện benchmark
  → Xác định lỗi chính
  → Thử kiểm chứng pixel và routing
  → Mở rộng ảnh + văn bản
  → Tối ưu hạ tầng khi quy mô tăng
```

**Lời thoại:**

> Từ nền tảng này, VisualSeek có thể hỗ trợ soạn bài giảng hoặc trưng bày số, chẳng hạn tìm bộ ảnh đủ các bước làm gốm và hạn chế trùng lặp. Người dùng cũng có thể tìm bằng ảnh kèm yêu cầu thay đổi, hoặc hội thoại để làm rõ tên gọi văn hóa.
>
> Em đề xuất hoàn thiện benchmark trước, xác định lỗi chính rồi mới chọn cách cải thiện và mở rộng hạ tầng.
>
> Mục tiêu tiếp theo là đo được mức đáp ứng yêu cầu của ảnh, từ đó quyết định nên đầu tư vào retrieval, kiểm chứng hay tương tác. Em mong nhận góp ý về thiết kế đánh giá và hướng nghiên cứu nên ưu tiên.

## Điều chỉnh theo thời lượng

### Bản 5 phút

Giữ trọng tâm vào hệ thống hiện tại, cách chứng minh chất lượng và một hướng nghiên cứu ưu tiên.

| Phần | Thời gian | Cách nói |
| --- | --- | --- |
| Slide 1 | 25 giây | Giới thiệu dự án và một ví dụ |
| Slide 2 | 45 giây | Nêu offline/online và ba nhánh; bỏ chi tiết RRF |
| Slide 3–4 | 60 giây | Gộp kết quả triển khai và giới hạn; chỉ đọc số liệu chính |
| Slide 5 | 50 giây | Tập trung nDCG, mức thỏa điều kiện và p95/chi phí |
| Slide 6 | 60 giây | Nói cách chấm, split và đối chứng |
| Slide 7 | 35 giây | Chỉ nói hướng kiểm chứng bằng pixel |
| Slide 8 | 25 giây | Một use case và lộ trình |
| **Tổng** | **300 giây** | **5 phút** |

### Bản 10 phút

Giữ bài nói 8 phút và thêm 2 phút phân tích một ví dụ sau slide 6:

1. **30 giây:** nhắc truy vấn và các điều kiện R1–R3.
2. **45 giây:** đối chiếu ảnh từ hai phương pháp, chỉ rõ điều kiện thỏa hoặc thiếu.
3. **30 giây:** minh họa cách chấm 0/1/2 và nhãn theo điều kiện.
4. **15 giây:** nối sang câu hỏi liệu pixel rerank có sửa được lỗi này với chi phí hợp lý không.

Chỉ dùng ảnh và kết quả thực tế đã kiểm tra. Nếu chưa có dữ liệu đối chứng, ghi rõ “ví dụ minh họa cách đánh giá”, không gán số đo giả cho dự án.

## Câu trả lời ngắn cho phần hỏi đáp

**Hybrid đã tốt hơn semantic chưa?**

> Hiện chưa có nhãn xác nhận để kết luận. Em đã có công cụ tạo pool và so sánh; bước tiếp theo là chấm nhãn và đánh giá trên held-out.

**Vì sao cần LLM khi đã có embedding?**

> LLM được dùng để biểu diễn ý định, điều kiện và nhập nhằng. Mức hữu ích của planner vẫn phải được đo so với baseline local, cùng độ trễ và chi phí.

**Rerank có bảo đảm mọi điều kiện đều đúng không?**

> Chưa. Annotation có thể thiếu chi tiết; vision model cũng có thể sai. Cần đối chiếu với nhãn người chấm trên ảnh, đặc biệt ở điều kiện quan hệ, số lượng và phủ định.

**Điểm nghiên cứu tiềm năng là gì?**

> Em hướng tới truy vấn văn hóa tiếng Việt có nhiều điều kiện, với benchmark và phương pháp kiểm chứng, định tuyến phù hợp. Cần khảo sát thêm và có kết quả đối chứng để xác định đóng góp mới.

**Khi nào cần chuyển sang vector database?**

> Khi phép đo cho thấy exact search hoặc cách quản lý index hiện tại không đáp ứng quy mô và tải dự kiến. Khi thử ANN, em sẽ đo cả chất lượng retrieval và độ trùng với exact baseline.

## Nguồn và phạm vi số liệu

- [Tổng quan dự án](../../README.md).
- [Kiểm thử và phân tích luồng ngày 08/11/2026](../search-update-test-and-flow-2026-11-08.md): nguồn số liệu corpus, vector và trạng thái kiểm tra local/LLM.
- [Semantic query planning](../semantic-query-planning.md): router, planner, điều kiện có ID và giới hạn hiện tại.
- [Fusion và quy trình đánh giá](../search-fusion-setup.md): candidate pool, chấm nhãn, split và chỉ số đã hỗ trợ.

Kịch bản dựa trên tài liệu hiện có; không chạy lại benchmark trong lần soạn này. Trước khi thuyết trình, cập nhật slide 3 nếu dữ liệu, cấu hình hoặc trạng thái LLM thay đổi. Các chỉ số mục tiêu và hướng nghiên cứu là đề xuất, không phải số đo đã đạt.
