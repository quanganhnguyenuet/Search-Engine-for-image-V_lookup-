# Báo cáo kiểm tra hệ thống search ảnh products-v1

Ngày đo: 2026-11-24. API chạy ở `127.0.0.1:8000`, collection `products_v1_images` (30,198 điểm), graph `graph-lite-v1`.
Tất cả số liệu dưới đây do tôi chạy lại trong phiên này, trừ khi ghi rõ "lấy từ run cũ".

## 1. Tóm tắt

- Trên bộ **silver-v1.1** (tự sinh từ template), Recall@10 đạt 0.95–1.0. Chạy lại hôm nay không ra 0.
- Bộ này gần như tự chấm cho mình, nên số cao không phản ánh chất lượng thật.
- Khi thử 40 câu viết tự nhiên (`eval/hops-v1`), tôi thấy 4 lỗi hành vi mà silver không bắt được: bỏ qua loại hàng, không loại trừ được sản phẩm đã nêu, mất nửa sau của câu ghép, không xử lý phủ định theo loại hàng.
- Graph hiện chỉ có quan hệ `MADE_BY`, và 68% entity không có hãng.
- Latency P95 khoảng 300 ms, Qdrant Cloud chiếm phần lớn.

## 2. Silver v1.1

- Định nghĩa: [silver-v1.1/manifest.json](../data/processed/products-v1/eval/silver-v1.1/manifest.json). Dẫn xuất từ silver-v1, Rel dựng lại sau khi duyệt 155 cặp entity (28 entity được gộp), bỏ 1 câu.
- Quy mô: dev 222 câu, test 327 câu. Chia theo cụm hãng, 40/60.
- Câu hỏi sinh từ template (`{name}`, `ảnh {name}`, "ngoài {name} thì hãng sản xuất còn làm gì khác", "đồ của thương hiệu {brand}"). Nhãn đúng lấy từ graph-lite và catalogue, chưa ai chấm tay.
- Chưa có: câu mô tả (cần chấm tay), 3-hop.

### Kết quả test, chạy lại hôm nay (327 câu, 0 lỗi, run hợp lệ)

| Loại | n | Recall@10 |
|---|---|---|
| single_named | 82 | 1.000 |
| single_brand | 31 | 0.948 |
| multi_intent | 90 | 1.000 |
| multi_hop | 95 | 0.989 |
| no_answer (tỉ lệ từ chối đúng) | 29 | 0.966 |

### Về con số "0"

Tôi không tìm thấy kết quả nào bằng 0 trong các run đã lưu. Hai khả năng:

- Đó là "recall delta 0.0" trong ghi chú B2 (category, connectors), nghĩa là không đổi so với trước.
- Nếu API tắt hoặc timeout lúc chạy `evaluate_v2.py`, mọi câu ra 0 và run bị đánh dấu `complete: false`.

Chưa xác định được trường hợp nào đúng. Cần bạn cho biết thấy số 0 ở đâu.

### Hạn chế của silver

- Câu hỏi dùng đúng tên trong catalogue.
- Rel của multi_hop là toàn bộ sản phẩm cùng hãng (trung vị 29 ảnh), nên silver thưởng việc rải kết quả ra mọi sản phẩm của hãng.
- Không có câu "cùng hãng nhưng chỉ lấy một loại hàng", câu trừ loại X, hay câu "tên cụ thể và loại hàng chung".

## 3. Graph được áp dụng thế nào

Dữ liệu ([graph.json](../data/processed/products-v1/search/graph.json)):

- 2,304 entity, 411 hãng, 738 cạnh, tất cả là `MADE_BY`. Chưa cạnh nào được duyệt tay (`reviewed: false`).
- 1,566 entity (68%) không có hãng. 259/411 hãng chỉ có 1 sản phẩm.
- Category (IS_A, Gemini gán cho 2,276 entity) nằm trong payload Qdrant, chỉ cộng điểm +0.2, không dùng như một bước nhảy trong graph.

Luồng xử lý ([pipeline.py:166-240](../packages/retrieval/src/visualseek_retrieval/pipeline.py#L166-L240)):

1. **S1 nhận diện** entity và hãng trong câu.
2. **S2 mở rộng**:
   - Có từ chỉ quan hệ và một entity X: lấy hãng của X, rồi mọi sản phẩm khác của hãng đó là đích.
   - Có "khác", "ngoài", "trừ": X thành anchor và bị trừ 2.0 điểm.
   - Chỉ nêu hãng: mọi sản phẩm của hãng là đích, cộng 0.5 điểm.
3. **Xếp hạng** ([qdrant_store.py:135-179](../packages/retrieval/src/visualseek_retrieval/qdrant_store.py#L135-L179)): RRF của dense, sparse và kênh dense lọc theo tập đích, cộng điểm graph và category. [`_rank`](../packages/retrieval/src/visualseek_retrieval/pipeline.py#L564-L567) chia top-10 theo entity, mỗi entity tối thiểu 2 ảnh.

## 4. Recall@10 và latency hiện tại

### Latency (silver-v1.1 test, 327 câu)

| | P50 | P95 | P99 | Max |
|---|---|---|---|---|
| Client | 174 ms | 298 ms | 398 ms | 1.75 s |
| Qdrant | 146 ms | 250 ms | 329 ms | 1.72 s |
| Encode câu hỏi | 23 ms | 78 ms | 95 ms | 105 ms |

Theo loại: multi_hop P95 343 ms, multi_intent 267 ms, single_brand 269 ms, single_named 206 ms, no_answer P50 28 ms.

### Các bộ eval khác

| Bộ | Chỉ số | Kết quả | Ghi chú |
|---|---|---|---|
| descriptive-v1 (chấm tay) | nDCG@10 | text 0.58, rerank bằng ảnh 0.80 | Lấy từ run cũ (`fusion-descriptive-v1.json`, tập dev 56 câu). Kênh ảnh chưa nối vào pipeline |
| category-v1 (tự viết) | P@10 | 0.806 | Lấy từ ghi chú B2, số lạc quan vì viết từ taxonomy |
| s1-names-v1, đo end-to-end hôm nay | Hit@10 | short 0.95–0.96, web_title 0.92–0.94 | Hit@1 0.86–0.92, P@10 0.67–0.77. Client P50 130 ms, P95 207 ms |

Bộ s1-names không phải bộ khó: text index chứa luôn tiêu đề trang. 3–6% câu có đáp án bị từ chối nhầm là ngoài phạm vi.

## 5. Các vấn đề đã chốt

Nguồn: 40 câu `eval/hops-v1` chạy qua API thật. Pool đã có ở `data/processed/products-v1/eval/hops-v1/pool.jsonl` nhưng **chưa có file judgments**, nên các nhận xét dưới đây dựa trên việc đọc kết quả top-10, chưa phải số đo.

| # | Vấn đề | Ví dụ | Nguyên nhân gốc |
|---|---|---|---|
| P1 | Bỏ qua loại hàng khi đã nhận ra hãng | "Canon còn máy ảnh nào khác…" ra mực in. "hãng của Redmi Note 11 còn điện thoại nào" ra củ sạc, cân. "máy in Canon" ra máy quay EOS C70. "nồi chiên Philips" ra đèn LED | Mọi sản phẩm của hãng cùng được +0.5. `_rank` luôn rải top-10 ra từ 5 sản phẩm. Category có trong payload nhưng không dùng để lọc tập đích |
| P2 | Sản phẩm bị loại trừ vẫn hiện ra | "lốp Schwalbe khác ngoài Magic Mary" vẫn có 4 ảnh Magic Mary. Tương tự lọc dầu Bosch, Lite-Box, Pegasus 40, PowerCore | Tên ngắn không được nhận ra là entity (S1 với tên kiểu web-title chỉ khớp đúng 0.27–0.37), nên không có anchor để trừ điểm |
| P3 | Câu "tên cụ thể và loại hàng chung" mất nửa sau | "Kindle Paperwhite và tai nghe" ra 10/10 ảnh Kindle. Tương tự "Espumisan và thuốc giảm đau", "chuột MX Master 3 với bàn phím" | Phần không có entity không được đổi thành ý định theo category |
| P4 | Không loại trừ được theo loại hàng | "đồ Shimano nhưng không phải bộ đề" trả về đúng bộ đề | Chưa xử lý phủ định nhắm vào category |
| P5 | Graph quá mỏng | "phụ tùng lắp được cho Honda Air Blade" chỉ đúng nhờ trùng chữ. "thuốc cùng bệnh với Famotidin" bị báo `unsupported_relation` | Chỉ có `MADE_BY`. Chưa có TREATS, FITS |
| P6 | Câu mô tả (màu, chất liệu) kém | nDCG@10 text 0.58 so với rerank bằng ảnh 0.80 | Kênh ảnh SigLIP2 chưa nối vào pipeline. Fusion có trọng số cũng chưa hơn rerank thuần trên dev |
| P7 | Bộ eval không bắt được P1–P4 | Silver ~1.0 trong khi các lỗi trên còn nguyên | Template và nhãn tự sinh |
| P8 | Latency có đuôi dài | Max 1.75 s. Qdrant chiếm khoảng 85% thời gian | Qdrant Cloud ở xa, lấy dư gấp 2 số nhóm khi xếp theo entity. Chưa tìm ra nguyên nhân cụ thể của các câu chậm nhất |

Một số trường hợp khác nên theo dõi: "giày thể thao nam và đồng hồ đeo tay" ra 8/10 ảnh đồng hồ Wahoo, "thức ăn cho mèo và đồ chơi cho chó" ra cả balo mèo.

## 6. Kế hoạch xử lý

Thứ tự theo tác động trên mỗi thay đổi nhỏ. Mỗi bước phải giữ silver không tụt và không làm descriptive-v1 giảm.

0. **Chấm tay hops-v1**, khoảng 1 giờ, bằng `scripts/hops_judge_server.py`. Dùng `scripts/evaluate_hops.py` để tính P@10 và nDCG@10. Từ đó hops-v1 là thước đo chính cho graph, silver chỉ còn là smoke test.
1. **Sửa P1**: lấy phần câu còn lại sau khi bỏ tên hãng, anchor và từ chỉ quan hệ, đưa qua `_segment_intent` ra category, giữ các đích thuộc category đó (nếu không còn đích nào thì giữ tất cả). Bỏ việc ép mỗi sản phẩm tối thiểu 2 ảnh.
2. **Sửa P2**: khi đã biết hãng, so phần câu còn lại với tên các sản phẩm của riêng hãng đó. Tập nhỏ nên khớp được chính xác, kể cả tên ngắn.
3. **Sửa P3, P4**: phần câu không có entity thành ý định theo category (đã có per-segment category). Phủ định nhắm vào category thì trừ điểm trên payload `categories`.
4. **Sửa P6**: nối kênh ảnh vào pipeline, chỉ bật khi câu không nêu entity. Cải thiện đã đo: +0.21 nDCG so với text. Cần đo thêm latency.
5. **Sửa P5**: trích hãng và quan hệ bằng LLM từ evidence pack (B4), duyệt trong Neo4j. Dài hạn.
6. **Sửa P8**: thử Qdrant local (đã có trong `compose.yaml`), giảm lượng lấy dư nhóm, tìm nguyên nhân các câu chậm trên 1 giây.

Khuyến nghị nếu deadline vẫn là 2026-11-25: làm 0, 1, 2 trước.

## 7. Điều chưa biết hoặc chưa kiểm tra

- Chưa xác định số "0" bạn thấy đến từ đâu.
- `eval/hops-v1` và `pool.jsonl` được tạo lúc 11:30 hôm nay; tôi không biết phiên nào tạo ra chúng.
- Các số của descriptive-v1 và category-v1 lấy từ run và ghi chú cũ, không chạy lại hôm nay.
- Đề xuất ở mục 6 chưa được thử. Số hạng mục "dự kiến sửa" là suy đoán từ việc đọc top-10.
- Silver-v1.1 test chỉ chạy một lần hôm nay, nên khoảng dao động giữa các lần chạy chưa đo.
- Port 8000 là API do bạn khởi động từ trước (PID 28788), tôi không đổi gì trên đó.
