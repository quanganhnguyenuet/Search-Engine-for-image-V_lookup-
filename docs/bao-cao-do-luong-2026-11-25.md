# Báo cáo đo lường 25/11/2026: chất lượng truy xuất, các luồng và độ trễ (tài liệu hỗ trợ làm slide)

**Trạng thái mã khi đo:** nhánh `chore/products-v1-cleanup`, HEAD `e5ef0af` cộng các thay đổi chưa commit trong ngày (bản vá lúc 12:09 lưu ở `data/processed/products-v1/eval/runs/checkpoint-2026-11-25/uncommitted.patch`; sau đó còn thêm vài script đo và một sửa nhỏ ở bước warmup của API, xem mục 8).
**Thời điểm đo:** 12:09 đến 12:45 ngày 25/11. **Máy:** Windows, GPU 4 GB (CUDA), Qdrant Cloud ở Seoul qua gRPC.
**So sánh với:** báo cáo checkpoint tối qua `docs/bao-cao-checkpoint-2026-11-24.md`.

> Cách đọc: mỗi mục có "thông điệp" (câu để làm tiêu đề slide), bảng số, và hình trong `docs/figures/2026-11-25/`. Mọi số trong hình lấy từ cùng các file kết quả với báo cáo này (`data.json` ở cùng thư mục).

---

## 0. Gợi ý bố cục slide

| # | Thông điệp (tiêu đề slide) | Hình / bảng | Mục |
|---|---|---|---|
| 1 | Tìm ảnh sản phẩm bằng tiếng Việt: một đường xử lý, không gọi LLM lúc truy vấn | (tự dựng) | 1 |
| 2 | Kho dữ liệu: 2.304 sản phẩm, 30.198 ảnh, 410 hãng, 21 nhóm ngành | bảng mục 2 | 2 |
| 3 | Luồng S1 → S2 → S3 → S4 và khi nào dùng vector ảnh | sơ đồ ở `docs/bao-cao-luong-hoat-dong-search-2026-11-24.md` | 3 |
| 4 | Đo bằng 5 bộ khác nhau, mỗi bộ có điểm yếu riêng | bảng mục 4 | 4 |
| 5 | Câu đã biết đáp án: Recall@10 từ 0,94 đến 1,00 | `01_silver_recall.png` | 5.1 |
| 6 | Chấm tay: bộ chưa từng thấy ngang bộ dev | `02_hops_dev_vs_holdout.png` | 5.2 |
| 7 | Hiểu loại hàng: P@10 0,79 lên 0,88 | `04_category.png` | 5.3 |
| 8 | Vector ảnh nâng nDCG@10 từ 0,58 lên 0,79 | `05_descriptive.png` | 5.4 |
| 9 | "Đặc sản Bến Tre": Recall@10 0,15 lên 0,98 | `03_place_flow.png`, `ui_01_place_bentre.png` | 5.5 |
| 10 | Giao diện: bấm vào ảnh xem cosine, mô tả, đồ thị quan hệ | `ui_02_image_detail.png`, `ui_03_relation_graph.png` | 6 |
| 11 | Độ trễ theo luồng và thời gian đi đâu | `06_latency_by_flow.png`, `07_latency_stages.png` | 7.2 |
| 12 | Tối ưu trong ngày: 844 ms xuống 319 ms | `09_optimisation.png` | 7.3 |
| 13 | Nút cổ chai là mạng tới Qdrant; so với tối qua | `10_qdrant_stage_by_run.png`, bảng mục 7.4 | 7.4 |
| 14 | Dưới tải: trần khoảng 2 đến 3 câu mỗi giây | `08_load.png` | 7.5 |
| 15 | Giới hạn và rủi ro (nói thẳng) | mục 9 | 9 |
| 16 | Bước tiếp theo | mục 10 | 10 |

---

## 1. Tóm tắt điều hành

1. **Chất lượng giữ nguyên so với tối qua.** Recall@10 trên bộ hops (chấm tay) không đổi: chênh lệch đúng 0,000 (CI [0, 0]) ở cả dev và holdout. Silver dev giống hệt tối qua; silver test (chạy một lần) cho 1,000 / 0,948 / 1,000 / 0,970 và từ chối đúng 0,966.
2. **Bộ chưa từng thấy ngang bộ dev.** Holdout: multi-hop P@10 0,875 và Recall@10 0,943; multi-intent P@10 0,930 và Recall@10 0,957. Dev: 0,885 / 0,944 và 0,895 / 0,965.
3. **Hai luồng được cải thiện rõ trong ngày.** Câu theo địa danh ("đặc sản Bến Tre"): Recall@10 từ 0,15 lên 0,98. Hiểu loại hàng: P@10 từ 0,79 lên 0,88 (63 câu), và 0,53 lên 0,80 cho câu "A và B".
4. **Độ trễ tối qua và hôm nay không so trực tiếp được.** Hôm nay p50 là 292 đến 482 ms, p95 là 615 đến 1.989 ms tùy lần đo, tối qua p50 179 ms và p95 270 ms. Phần chênh nằm ở bước gọi Qdrant từ xa (p50 133 ms lên 247 đến 374 ms), vì đường mạng tới Seoul chậm và giật hơn. Phần mã trong ngày đã giảm độ trễ trung vị từ 844 ms xuống 319 ms (cùng kịch bản, cùng ngày).
5. **Trần thông lượng khoảng 2 đến 3 câu mỗi giây,** vì API xử lý từng câu một.
6. **Việc còn nợ:** chấm 6 sản phẩm mới ở câu h19, dựng lại nhãn silver từ graph đã duyệt, chạy Qdrant cục bộ để bỏ phần mạng, commit rồi chạy lại holdout theo quy tắc của bộ này.

---

## 2. Kho dữ liệu (để nói domain)

Kho ảnh sản phẩm tiếng Việt nhiều ngành, không có một domain chính.

| | Số lượng |
|---|---|
| Sản phẩm (entity) | 2.304 |
| Ảnh | 30.198 |
| Hãng | 410 (265 hãng chỉ có 1 sản phẩm) |
| Ảnh có gắn hãng | khoảng 40% |
| Trang nguồn | hơn 10.000 website |

Nhóm lớn nhất theo số ảnh: thực phẩm và đồ uống 12%, thuốc và dược phẩm 11%, thời trang 9%, ô tô và phụ tùng 8%, nhà cửa 7%, công nghệ, xe đạp, văn hóa và thủ công mỗi nhóm khoảng 6%. Hãng nhiều ảnh nhất: Shimano (307), Nike, Bosch, SRAM, Samsung, Canon.

Quan hệ trong graph: 723 cạnh `MADE_BY` (đã duyệt), 2.276 `IS_A`, 2.434 `TREATS`, 752 `HAS_INGREDIENT`, 687 `ORIGIN`, 278 `FITS`. `ORIGIN` và `TREATS` còn nhiễu (audit B4), nên không dùng làm ví dụ "cùng công dụng" hay "cùng xuất xứ" khi demo.

---

## 3. Luồng truy xuất (nhắc ngắn)

Một đường xử lý duy nhất, không router, không LLM lúc truy vấn: **S1** nhận diện sản phẩm và hãng trong câu, **S2** mở rộng qua graph và nhận loại hàng, **S3** truy xuất hybrid (dense bge-m3 + sparse) trên Qdrant, **S4** trộn vector ảnh SigLIP2 cho câu mô tả thị giác. Chi tiết và bảng "khi nào dùng luồng nào" ở `docs/bao-cao-luong-hoat-dong-search-2026-11-24.md`. Luồng mới trong ngày: **câu theo địa danh** (mục 5.5).

---

## 4. Các bộ đo và độ tin cậy của từng bộ

| Bộ đo | Số câu | Nhãn | Điểm yếu cần nói khi trình bày |
|---|---|---|---|
| silver-v1.1 (dev, test) | 222, 327 | tự động từ catalogue và graph | Gần như vòng tròn: câu dùng tên trong catalogue. Nhãn multi-hop dựng từ graph cũ nên có câu lỗi thời. |
| hops-v1 (dev), hops-v2 (holdout) | 40 + 40 | chấm tay theo sản phẩm trên pool | 20 câu mỗi loại nên CI rộng (khoảng ±0,1). Recall tính trên pool nên là cận trên. Holdout chỉ chạy ở checkpoint, chỉ đọc số tổng hợp. |
| descriptive-v1 | 114 | chấm tay | Chấm một lần, 5 câu bị đánh dấu `bad_query`. |
| category-v1 | 63 + 30 | dựng từ taxonomy | Hơi lạc quan vì dựng khớp với từ điển loại hàng. |
| Luồng địa danh (mới) | 6 địa danh | lấy từ chính cạnh `ORIGIN` | Kiểm tra nhất quán với graph, chưa phải chấm tay. |

---

## 5. Chất lượng theo luồng

### 5.1 Câu đã biết đáp án (silver-v1.1, qua API thật)

**Thông điệp:** các luồng đơn giản đạt gần 1,0, câu quan hệ 0,94 đến 0,97. Hình `01_silver_recall.png`.

| Loại câu | Dev (222) | Test (327, một lần) | Tối qua (dev, sau sửa lỗi tách vế) |
|---|---|---|---|
| Theo tên sản phẩm | 0,985 | **1,000** | 0,985 |
| Theo hãng | 0,958 | 0,948 | 0,958 |
| Nhiều ý (A và B) | 1,000 | **1,000** | 1,000 |
| Quan hệ (sản phẩm khác của hãng) | 0,937 | 0,970 | 0,937 |
| Từ chối câu ngoài phạm vi (đúng) | 0,952 | 0,966 | 0,952 |

Cả hai lần chạy: 0 lỗi, 0 cache hit, hợp lệ.

**Về multi-hop.** Test 0,970 thấp hơn lần test cũ (0,989) một chút (−0,020, CI [−0,05, 0,0]). Nguyên nhân là **nhãn lỗi thời**: trong 95 câu có 2 câu tụt, và cả hai đều có đích mà cạnh `MADE_BY` tới hãng của câu gốc không còn trong graph đã duyệt. Loại các câu có nhãn lỗi thời (tiêu chí chỉ dựa vào nhãn và graph, không dựa vào kết quả): **dev 0,992 (loại 3 câu), test 0,989 (loại 6 câu)**, tức bằng lần trước. Không có hồi quy của pipeline.

### 5.2 Chấm tay (hops): dev và holdout

**Thông điệp:** bộ hãng và cách hỏi chưa từng thấy cho điểm ngang bộ dev. Hình `02_hops_dev_vs_holdout.png`.

| | Dev hops-v1 | Holdout hops-v2 | Tối qua (dev / holdout) |
|---|---|---|---|
| multi-hop P@10 | 0,885 (chặn dưới) | 0,875 | 0,885 / 0,875 |
| multi-hop wrong@10 | 0,055 | 0,060 | 0,035 / 0,060 |
| multi-hop Recall@10 | **0,944** (CI 0,86 đến 1,0) | **0,943** (CI 0,893 đến 0,984) | 0,942 / 0,943 |
| multi-intent P@10 | 0,895 | **0,930** | 0,880 / 0,920 |
| multi-intent wrong@10 | 0,055 | 0,020 | 0,070 / 0,030 |
| multi-intent all_parts@10 (đủ mọi vế) | 0,850 | 0,900 | 0,850 / 0,900 |
| multi-intent balanced@10 | 0,833 | 0,860 | 0,818 / 0,860 |
| multi-intent Recall@10 | **0,965** (CI 0,93 đến 0,994) | **0,957** (CI 0,917 đến 0,99) | 0,950 / 0,957 |

**Điều cần nói rõ:**
- **Recall không đổi so với pool trước:** dev so với pool sáng nay, holdout so với pool tối qua, đều chênh 0,000 (CI [0, 0]). Dev so với pool trước checkpoint 24/11: multi-intent 0,807 lên 0,965 (+0,158, CI [+0,058, +0,265]), multi-hop 0,937 lên 0,944 (+0,005, không có ý nghĩa).
- **Dev có 6 sản phẩm chưa chấm** (cùng 1 câu, h19, đã biết từ sáng nay). Công cụ tính chúng là sai, nên P@10 multi-hop dev nằm giữa **0,885 (chặn dưới) và 0,935 (chặn trên)**, chỉ tính các slot đã chấm là 0,932. Số chính thức cần bạn chấm trên `hops_judge_server`.
- **Holdout: 0 sản phẩm chưa chấm,** nên các số holdout là chính thức. Chỉ đọc số tổng hợp, không xem từng câu.
- nDCG@10 multi-hop (mẫu số lý tưởng cố định): dev 0,907 (thấp hơn 0,946 tối qua vì h19 có sản phẩm chưa chấm, tính 0), holdout 0,933 (0,942 ở lần chạy tối qua, chênh −0,009 trong vùng nhiễu).
- **Quy tắc holdout:** chạy trên mã chưa commit, ghi lại bản vá để tái lập. Nên commit rồi chạy lại ở checkpoint tới.

### 5.3 Hiểu loại hàng (category-v1)

**Thông điệp:** nhận ra loại hàng giúp cả câu đơn lẫn câu "A và B". Hình `04_category.png`.

| | Tắt | Bật |
|---|---|---|
| Câu một loại hàng (63): P@10 chung | 0,786 | **0,875** |
| P@10 loại hàng / hạng thời trang | 0,815 / 0,350 | 0,895 / 0,575 |
| Số câu nhận ra loại hàng | | 55/63 (53 trước khi đọc mô tả taxonomy) |
| Câu "A và B" (30): có ảnh cho mọi vế | 0,533 | **0,800** |
| Chia đều các vế | 0,530 | 0,837 (0,820 trước khi đọc mô tả) |

Thay đổi trong ngày: từ điển loại hàng đọc thêm các cụm trong `description` của taxonomy (thêm 109 cue, không cue nào bị mất hay đổi loại). Kiểm tra trên API thật: 59 câu một loại hàng P@10 0,892 lên 0,895; 22 câu đời thường ngắn (chọn từ các cue mới nên chỉ để kiểm tra cue bật) có cue 7 lên 19 và P@10 0,814 lên 0,855. Ví dụ: "nước ngọt" nhận ra `food.drink`, ảnh thuộc nhóm này trong top-10 từ 5 lên 9. Còn sót: lon bia vẫn xuất hiện vì kênh ảnh chiếm 90% trọng số (xem mục 9).

### 5.4 Mô tả thị giác (descriptive-v1, vector ảnh)

**Thông điệp:** trộn vector ảnh nâng điểm câu có màu, chất liệu, hình dáng. Hình `05_descriptive.png`.

| nDCG@10 (chấm tay) | Chỉ văn bản | Văn bản + ảnh | Chênh (CI 95%) |
|---|---|---|---|
| Dev (56 câu), đo hôm nay | 0,576 | **0,792** | +0,216 (+0,155 đến +0,280), thắng 38, thua 8 |
| Tối qua (dev) | 0,580 | 0,796 | +0,216 |
| Test (52 câu), đo tối qua, một lần | 0,579 | 0,788 | +0,209 (+0,133 đến +0,286) |

Khác tối qua −0,004 (nhiễu). 53/56 câu dùng fusion (55/56 tối qua). Test không chạy lại để giữ nguyên nguyên tắc "một lần".

### 5.5 Câu theo địa danh (luồng mới)

**Thông điệp:** nhận tên địa danh trong câu và đi thẳng vào graph. Hình `03_place_flow.png`, ảnh `ui_01_place_bentre.png`.

Trước đây "đặc sản bến tre" chỉ là tìm theo nội dung (hạng 1 là "Mẹt tre"). Nay nhận địa danh Việt Nam trong câu (bỏ quốc gia và các nơi nước ngoài như Cuba, USA, Milan), lấy các sản phẩm có `ORIGIN` tại đó, thu hẹp theo loại hàng ("đặc sản" → thực phẩm), và chỉ trả đúng các sản phẩm của địa danh, không thêm cho đủ 10 ảnh.

| Địa danh (số sản phẩm thực phẩm) | Recall@10 đường graph | Recall@10 tìm theo nội dung |
|---|---|---|
| Bến Tre (4) | 1,00 | 0,10 |
| Đà Lạt (5) | 0,90 (9 ảnh) | 0,00 |
| Hà Nội (10) | 1,00 | 0,30 |
| Huế (4) | 1,00 | 0,20 |
| Sài Gòn (5) | 1,00 | 0,30 |
| Tây Bắc (4) | 1,00 | 0,00 |
| **Trung bình** | **0,983** (precision 1,00) | **0,15** |

Điều kiện kích hoạt chặt: phần còn lại của câu ngoài địa danh phải chỉ là từ chỉ loại hàng hoặc từ đệm. Phiên bản đầu kích hoạt cho mọi câu có địa danh làm đổi 26/44 câu eval có địa danh (ví dụ "dự báo thời tiết Đà Nẵng ngày mai" thành trả sản phẩm); bản đã siết giữ nguyên 43/44 câu, chỉ câu mục tiêu đổi. Đáp án lấy từ chính cạnh `ORIGIN`, nên đây là kiểm tra nhất quán chứ chưa phải chấm tay. Bến Tre trong kho chỉ có 6 sản phẩm.

---

## 6. Giao diện (thêm trong ngày)

**Thông điệp:** xem được vì sao một ảnh được xếp hạng và nó nối với gì trong graph. Hình `ui_02_image_detail.png`, `ui_03_relation_graph.png`.

- **Bấm vào ảnh** mở hộp thoại: câu truy vấn, cosine "câu ↔ mô tả" (bge-m3) và "câu ↔ ảnh" (SigLIP2), điểm xếp hạng chi tiết, mô tả đã embed (kèm chữ OCR), link trang nguồn, đồ thị lân cận của sản phẩm. Phím ← → chuyển ảnh, Esc đóng.
- **Đồ thị quan hệ của cả câu** dưới lưới ảnh: thực thể gốc (vòng đôi, nhãn GỐC) → hãng/loại hàng/địa danh ← sản phẩm kết quả, kéo nút được, bấm nút xem thuộc tính, nguồn và độ tin cậy của cạnh. Có bảng quan hệ dạng văn bản.
- **Chip** "Địa danh: Bến Tre" cho câu địa danh.
- Badge cam "Insight" ở góc ảnh chụp là của chế độ dev của Next.js, không có ở bản production.

---

## 7. Độ trễ

### 7.1 Điều kiện đo (đọc trước khi dùng số)

- API với `qdrant_transport = grpc`, bge-m3 và SigLIP2 chạy `cuda`, khởi động mất 31 giây khi cache đĩa ấm (nạp artifact 27,9 s). Yêu cầu đầu tiên sau warmup: 315 đến 415 ms.
- **Đường mạng tới Qdrant Cloud ở Seoul giật:** RTT trung vị đo bằng 20 mẫu: 141 ms (12:33), 130 ms (12:37), 195 ms (12:43). Có lúc p90 lên 1,5 giây và tối đa 2,4 giây (12:26). Khoảng 11:55 có đợt mọi cuộc gọi tới Qdrant quá hạn 10 giây trong khoảng 35 giây rồi tự hồi phục.
- Các lần đo silver đặt timeout 10 giây (mặc định 2 giây) để một cú giật mạng không bị tính là lỗi của hệ thống.
- Một người dùng, tuần tự, shuffle cố định, không cache ứng dụng.

### 7.2 Độ trễ theo luồng (phía người dùng, ms)

**Thông điệp:** p50 khoảng 290 ms, p95 khoảng 615 ms; câu bị từ chối chỉ mất 72 ms. Hình `06_latency_by_flow.png`, `07_latency_stages.png`.

Mẫu đại diện cho từng luồng (173 câu, 0 lỗi): p50 **292**, p95 **615**, p99 **1.058**, tối đa 1.372.

| Luồng | Số câu | p50 | p95 | p99 | Gọi Qdrant (trung vị) |
|---|---|---|---|---|---|
| Theo tên sản phẩm | 25 | 262 | 361 | 415 | 178 |
| Theo hãng | 19 | 341 | 1.159 | 1.329 | 261 |
| Nhiều ý (A và B) | 25 | 318 | 466 | 538 | 239 |
| Quan hệ (sản phẩm khác của hãng) | 25 | 429 | 684 | 787 | 384 |
| Ngoài phạm vi (từ chối) | 21 | 72 | 105 | 216 | 159 (chỉ các câu chưa bị từ chối) |
| Theo loại hàng | 25 | 275 | 458 | 504 | 168 |
| Mô tả thị giác (fusion ảnh) | 25 | 270 | 376 | 872 | 177 |
| Theo địa danh | 8 | 369 | 632 | 635 | 265 |

Các giai đoạn khác gần như cố định: mã hóa câu bge-m3 khoảng 41 đến 44 ms, S1 và S2 dưới 1 ms mỗi giai đoạn, SigLIP2 mã hóa câu 13 đến 15 ms, tính cosine cho giao diện 15 đến 22 ms. Ở mọi luồng, **gọi Qdrant chiếm phần lớn thời gian.**

### 7.3 Tối ưu trong ngày (cùng ngày, cùng kịch bản người dùng dừng 10 giây)

**Thông điệp:** giữ kết nối sống, dùng gRPC và gọn payload làm trung vị giảm hơn 60%. Hình `09_optimisation.png`.

| | Trước (REST) | Sau (gRPC) |
|---|---|---|
| Trung vị phía người dùng | 844 ms | **319 ms** |
| Câu chậm nhất trong 6 câu thử | 3.901 ms | 561 ms |
| Xếp hạng + trace gọi Qdrant, song song (trung vị) | 791 ms | 170 ms |

Nguyên nhân tìm ra: (1) kết nối đóng sau 5 giây không dùng, mỗi câu sau đó tốn thêm khoảng 380 ms bắt tay TCP + TLS (đã giữ kết nối 120 giây kèm heartbeat 15 giây); (2) vector 1024 chiều gửi dạng JSON khoảng 20 KB, gửi hai lần ở lượt trace (gRPC gửi nhị phân); (3) payload đầy đủ khoảng 0,8 KB mỗi ảnh cho 50 đến 60 ảnh (nay chỉ lấy 4 trường cần, phần còn lại lấy từ RAM); (4) React StrictMode ở chế độ dev gửi hai request giống nhau. Qdrant tự tính chỉ mất 2 đến 24 ms. gRPC trả cùng kết quả với REST (so theo điểm, vì Qdrant xếp điểm hòa không xác định). Kết quả 16 câu mốc không đổi.

### 7.4 Bảng chênh lệch độ trễ: tối qua và hôm nay

**Thông điệp:** độ trễ hôm nay cao hơn tối qua, chủ yếu vì đường mạng tới Qdrant. Hình `10_qdrant_stage_by_run.png`.

Cùng bộ silver-v1.1, chạy tuần tự qua API. Tối qua là lần chạy `dev-checkpoint-2026-11-24` (REST; trước khi sửa lỗi tách vế, p95 sau khi sửa là 255,7 ms); hôm nay là hai lần chạy cách nhau vài phút.

**(a) Phía người dùng, ms**

| | Tối qua (dev) | Hôm nay (dev) | Chênh | Hôm nay (test) | Chênh |
|---|---|---|---|---|---|
| p50 | 179,2 | 481,9 | +302,7 (×2,7) | 329,2 | +150,0 (×1,8) |
| p95 | 270,3 | 1.989,2 | +1.718,9 (×7,4) | 649,1 | +378,8 (×2,4) |
| p99 | 381,2 | 5.294,4 | +4.913,2 (×13,9) | 862,8 | +481,6 (×2,3) |
| Tối đa | 426,1 | 8.383,9 | +7.957,8 | 1.079,3 | +653,2 |

Hai lần hôm nay cách nhau vài phút nhưng khác nhau nhiều (p95 1.989 so với 649 ms), cho thấy phần biến động đến từ đường mạng chứ không từ mã.

**(b) Theo từng giai đoạn (phía server, ms; p50 / p95)**

| Giai đoạn | Tối qua | Hôm nay (dev) | Hôm nay (test) |
|---|---|---|---|
| Mã hóa câu (bge-m3) | 44,5 / 92,4 | 75,8 / 92,0 | 50,2 / 87,2 |
| S1 nhận diện | 0,6 / 1,1 | 0,9 / 1,8 | 0,6 / 1,6 |
| S2 mở rộng graph | 0,4 / 0,8 | 0,7 / 2,4 | 0,5 / 1,5 |
| SigLIP2 mã hóa câu (khi dùng fusion) | 16,8 | 21,0 | 27,3 |
| Tính cosine cho giao diện (mới) | không có | 27,9 / 31,4 | 18,2 / 30,6 |
| **Gọi Qdrant** | **133,0 / 241,5** | **374,1 / 1.464,4** | **246,9 / 533,7** |
| Gọi Qdrant và chờ trace | 133,0 / 241,5 | 405,4 / 1.949,9 | 254,1 / 573,7 |
| **Tổng phía server** | **174,7 / 265,9** | **476,8 / 1.982,7** | **324,8 / 644,9** |

Tỉ lệ câu có giai đoạn Qdrant quá 500 ms: tối qua 0%, hôm nay 31% (dev) và 6% (test); quá 1.000 ms: 0%, 9% và 0%.

**(c) Theo loại câu (p50 / p95, ms)**

| Loại | Tối qua (dev) | Hôm nay (dev) | Hôm nay (test) |
|---|---|---|---|
| Theo tên sản phẩm | 149 / 198 | 458 / 1.031 | 304 / 627 |
| Theo hãng | 196 / 264 | 621 / 4.511 | 356 / 645 |
| Nhiều ý | 192 / 244 | 488 / 1.390 | 333 / 628 |
| Quan hệ (multi-hop) | 202 / 304 | 627 / 2.072 | 391 / 705 |
| Ngoài phạm vi | 27 / 99 | 74 / 148 | 78 / 97 |

**(d) Gánh phần chênh p50 giữa tối qua và hôm nay (ước lượng từ trung vị từng giai đoạn)**

| Nguồn chênh | Lần dev (+302 ms) | Lần test (+150 ms) |
|---|---|---|
| Gọi Qdrant (mạng tới Seoul) | +241 ms (80%) | +114 ms (76%) |
| Tính cosine cho giao diện (tính năng mới) | +28 ms (9%) | +18 ms (12%) |
| Mã hóa câu bge-m3 | +31 ms (10%) | +6 ms (4%) |
| Chờ trace, S1, S2, SigLIP2 | phần còn lại | khoảng +12 ms |

**Cách đọc cho đúng:**
- Gọi Qdrant là phần tăng lớn nhất ở mọi lần đo, tuy hôm nay đã dùng gRPC và gọn payload, vốn nhanh hơn REST nếu cùng mạng. Điều đó cho thấy mạng chậm hơn tối qua. Bằng chứng độc lập: bộ đánh giá chạy trong tiến trình, cùng mã REST, p50 là **150 ms** tối qua và **444 đến 578 ms** hôm nay.
- Không có số RTT của tối qua nên chưa định lượng chính xác được phần mạng; con số ở bảng (d) là ước lượng.
- **Tính cosine cho giao diện tốn thêm 15 đến 28 ms** ở các câu không dùng fusion (một lần mã hóa SigLIP2 cho câu). Có thể chuyển sang tính khi bấm vào ảnh để bỏ chi phí này khỏi đường tìm kiếm.
- Mã hóa bge-m3 chênh nhỏ khi mạng êm (50 so với 45 ms ở lần test) và cao hơn ở lần dev (76 ms); chưa rõ nguyên nhân, có thể do GPU bận lúc đó.
- **Khi trình bày:** dùng số hôm nay kèm điều kiện mạng, không dùng p95 270 ms của tối qua làm mốc cho hệ thống hiện tại.

### 7.5 Nhịp người dùng và dưới tải

**Thông điệp:** một người dùng mất khoảng 320 ms mỗi câu; nhiều người cùng lúc thì xếp hàng. Hình `08_load.png`.

| Kịch bản | Kết quả |
|---|---|
| Người dùng đọc 10 giây rồi tìm tiếp (12 câu) | p50 **323 ms**, p95 875 ms, 0 lỗi (kết nối vẫn ấm nhờ heartbeat) |
| 1 client liên tục, 30 giây | 2,19 câu/giây, p50 349, p95 1.110 ms |
| 2 client | **2,82 câu/giây** (cao nhất), p50 621, p95 1.675 ms |
| 4 client | 2,26 câu/giây, p50 1.765, p95 2.334 ms |
| 8 client | 1,98 câu/giây, p50 3.738, p95 7.313 ms |

Mọi mức: 0 lỗi. Thông lượng không tăng quá khoảng 2,8 câu/giây vì API chỉ có **một luồng xử lý** và giữ nó cả lúc chờ mạng tới Qdrant. Muốn tăng thông lượng cần tách bước mã hóa (GPU) khỏi bước gọi Qdrant (chờ mạng).

---

## 8. Những gì đã đổi kể từ tối qua (để slide "đã làm gì")

1. **Graph:** gộp enrichment `TREATS`, `HAS_INGREDIENT`, `FITS`, `ORIGIN` kèm bộ lọc ngành (commit `27c0830`).
2. **Hiểu loại hàng:** đọc mô tả taxonomy (+109 cue, không cue nào bị mất).
3. **Luồng địa danh mới:** "đặc sản Bến Tre" đi qua graph (Việt Nam, chỉ trả đúng địa danh).
4. **Độ trễ:** gRPC thay REST (tự quay về REST nếu cổng 6334 không tới được), giữ kết nối và heartbeat, lấy payload gọn, bỏ request trùng ở dev. Sửa thêm: lỗi ở bước warmup chỉ ghi cảnh báo, không làm API không khởi động được (đã gặp thật lúc 12:26 khi Qdrant treo).
5. **Giao diện:** hộp thoại chi tiết ảnh (cosine, mô tả, đồ thị), đồ thị quan hệ cho cả câu, chip địa danh.
6. **Công cụ đo mới:** `scripts/evaluate_places.py`, `scripts/bench_latency.py`, `scripts/hops_coverage.py`, `scripts/make_report_figures.py`; thêm cờ `--configs`, `--out`, `--pool` cho các script hops (dựng pool chỉ cấu hình `current` mà không ghi đè pool đầy đủ).

---

## 9. Giới hạn và rủi ro (nên nói thẳng trong buổi trình bày)

- **Silver gần như vòng tròn** và nhãn multi-hop lỗi thời (6 trong 95 câu test, 3 trong 54 câu dev); cần dựng lại từ graph đã duyệt.
- **Hops: CI rộng** (20 câu mỗi loại). Recall tính trên pool là cận trên. Dev còn 6 sản phẩm chưa chấm.
- **Holdout chạy trên mã chưa commit**; nên commit rồi chạy lại ở checkpoint tới.
- **Mạng tới Qdrant Seoul là điểm yếu số một:** độ trễ đuôi cao và có lúc cuộc gọi quá hạn 10 giây (đã gặp nhiều lỗi 500 trong các đợt giật mạng và một lần API không khởi động được, trước khi sửa warmup).
- **Thông lượng thấp:** khoảng 2 đến 3 câu mỗi giây, một luồng xử lý.
- **Câu trừu tượng bằng tiếng Việt vẫn yếu:** "nước ngọt" còn lẫn lon bia vì kênh ảnh SigLIP2 chiếm 90% trọng số (tinh chỉnh trên câu "vật cụ thể + màu"), và `product_type` do Gemini gán không nằm trong văn bản được embed. Kho chỉ có 1 sản phẩm nước ngọt thật.
- **Dữ liệu:** 265/410 hãng chỉ có 1 sản phẩm nên "sản phẩm khác của hãng" trả rỗng (ví dụ Razer Kraken); `ORIGIN` và `TREATS` còn nhiễu; Bến Tre chỉ có 6 sản phẩm.
- **Luồng địa danh** chỉ kích hoạt khi câu gần như chỉ có địa danh và loại hàng; "bánh Bến Tre" (từ "bánh" chưa là cue của từ điển) vẫn đi đường tìm theo nội dung.

---

## 10. Bước tiếp theo

1. **Chạy Qdrant cục bộ** (collection chỉ 30 nghìn điểm, đã có `compose.yaml`): bỏ phần mạng, kỳ vọng p50 khoảng 170 đến 200 ms như tối qua.
2. **Tính cosine khi bấm vào ảnh** thay vì mỗi lần tìm: bớt 15 đến 28 ms.
3. **Tách mã hóa (GPU) khỏi gọi Qdrant** để tăng thông lượng.
4. **Chấm 6 sản phẩm mới ở h19**, dựng lại nhãn silver từ graph đã duyệt.
5. **Commit rồi chạy lại holdout** theo quy tắc của bộ này.
6. Cải thiện câu trừu tượng: đưa `product_type` vào văn bản embed hoặc giảm trọng số ảnh khi câu không có thuộc tính thị giác; chọn tham số trên `descriptive-v1` dev và một bộ câu đời thường mới, không dùng holdout.

---

## 11. Cách tái lập

```powershell
# Pha A: chạy trong tiến trình (tắt API trước: 16 GB RAM và GPU 4 GB không đủ cho cả hai)
python scripts/build_eval_hops.py --set hops-v1 --configs current --out <pool_current.jsonl>
python scripts/evaluate_hops.py --set hops-v1 --pool <pool_current.jsonl>
python scripts/recall_hops.py  --set hops-v1 --pool <pool_current.jsonl> --before-pool <pool cũ>
python scripts/hops_coverage.py --set hops-v1 --pool <pool_current.jsonl>   # độ phủ chấm, chặn dưới/trên
python scripts/evaluate_descriptive.py --split dev
python scripts/evaluate_category.py [--multi] [--no-descriptions]
python scripts/evaluate_places.py --out runs/places.json

# Pha B: qua API đang chạy ở :8000
python scripts/evaluate_v2.py run --split dev  --eval data/processed/products-v1/eval/silver-v1.1 --timeout 10 --out runs/silver_dev.json
python scripts/evaluate_v2.py run --split test --eval data/processed/products-v1/eval/silver-v1.1 --timeout 10 --out runs/silver_test.json
python scripts/bench_latency.py --out runs/latency.json
python scripts/make_report_figures.py            # hình và data.json cho slide
```

Kết quả gốc: `data/processed/products-v1/eval/runs/checkpoint-2026-11-25/` (không nằm trong git). Hình: `docs/figures/2026-11-25/` (10 biểu đồ, 3 ảnh giao diện, `data.json`).
