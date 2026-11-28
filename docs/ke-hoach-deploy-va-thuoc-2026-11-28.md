# Kế hoạch deploy và tập trung domain thuốc (bản 28/11/2026)

Mốc: demo **01/12/2026**. Bản này dùng tiếp [ke-hoach-deploy-2026-11-27.md](ke-hoach-deploy-2026-11-27.md) cho phần
chi tiết deploy (tunnel, luật an toàn, chuẩn bị laptop) và thêm hướng tập trung vào thuốc.

## 1. Quyết định

| Câu hỏi | Chọn | Lý do |
| --- | --- | --- |
| "Tập trung thuốc" nghĩa là gì trước demo | Thêm **chế độ "Thuốc"** (lọc theo loại hàng) và kịch bản demo về thuốc. Index chung giữ nguyên | Payload `category_l1s` đã có index trên Qdrant; lọc thêm không phải index lại, tắt đi là về như cũ |
| Có thu hẹp hẳn sản phẩm về thuốc không | Để sau 01/12 | Cần schema thuốc riêng và bộ đánh giá chấm tay, không kịp trong 2 ngày |
| Cách chạy demo | **Laptop**, tunnel là phương án thêm | Chưa chốt người xem có tự truy cập không. Nếu có: làm thêm mục 4.3 |
| Docker | Không | Như bản 27/11 mục 1 |
| Thêm model trước demo | Không | Mục 6 |

## 2. Hiện trạng (đo ngày 28/11)

**Deploy**

| Việc | Trạng thái |
| --- | --- |
| P0.1–P0.4 (tag `demo-rc1`, Next 16.4.6, chung origin, `start_demo.ps1`) | Xong |
| Lỗi "Áp dụng thay đổi" (`qdrant_sync_changes.py failed with exit code 1`) | Đã sửa: `admin_release.active_uploads()` bỏ sót ảnh có bản ghi `update`. Lần áp dụng 08:00 UTC thành công. **Chưa commit** |
| Search khi đã đăng nhập trên bản production | Chưa kiểm tra |
| Tunnel, thumbnail, đo tải, backup, nghiệm thu | Chưa làm |

Lần áp dụng 08:00 UTC đã đưa 4 sản phẩm từ file test vào tìm kiếm thật (Doublestar DH05, Caterpillar 994, Găng tay
võ, Đồng 1/4 cent 1942). Chưa có cách gỡ sản phẩm mới trong admin.

**Thuốc**

- 317/2.278 sản phẩm (14%), 3.222/30.198 ảnh (10,7%), trung vị 8 ảnh mỗi thuốc.
- Tân dược uống 198, tiêm 41, thực phẩm chức năng 30, dùng ngoài 26, đông dược 16, vắc-xin 5, thú y 1.
- Có hoạt chất: 297/317. Có công dụng (TREATS): 293/317. Có hãng: chỉ 23/317.
- Lỗi đã biết: từ vựng TREATS phân mảnh; xếp hạng "thuốc cùng bệnh" chưa tính số giá trị chung (Povidone đứng trên
  Spiramycin); 6 sản phẩm trong top-10 của h19 (Famotidin) chưa chấm.
- `candidate_images_part_04_yes.csv`: 1.654 dòng từ trang nhà thuốc (Long Châu, An Khang, Pharmacity…), 317 mã
  entity, trong đó 37 đã có trong đồ thị. Số thuốc mới (~280) là **ước đoán theo tên miền**, chưa gắn nhãn.

## 3. Lịch

| Ngày | Deploy | Thuốc | Xong khi |
| --- | --- | --- | --- |
| **28/11 tối** | D1 commit bản sửa; D2 kiểm tra search đã đăng nhập trên bản production; D3 backup + snapshot Qdrant | T1 chế độ "Thuốc" (API + UI) | Snapshot mở lại được; bật/tắt chế độ thuốc chạy trên bản production |
| **29/11** | D4 (nếu có tunnel) | T2 bộ truy vấn thuốc + chấm; T3 nhập thuốc từ part_04 (có cổng go/no-go) | Có điểm trước/sau; quyết định giữ hay bỏ dữ liệu nhập |
| **30/11** | D5 nghiệm thu, tổng duyệt 2 lần, quay video dự phòng | T4 kịch bản demo thuốc | **Tối 30/11 đóng băng**, tag `demo-rc2` |
| 01/12 | Demo (bản 27/11 mục 7) | | |

## 4. Deploy

**D1. Commit.** Bản sửa `active_uploads` + test. Không commit `apps/web/next-env.d.ts`. `graph/admin/*.jsonl` và
`graph/entity_categories.json` là dữ liệu của lần áp dụng 08:00, commit riêng. Push nhánh, chưa merge.

**D2. Search khi đã đăng nhập.** Chạy `scripts/start_demo.ps1`, đăng nhập bằng tài khoản thường và admin, search 5
truy vấn, mở `/admin`. Đây là đường duy nhất trong P0 chưa được kiểm tra.

**D3. Backup trước mọi lần ghi dữ liệu mới.** Bản 27/11 mục 5, cộng:

- snapshot collection `products_v1_images` trên Qdrant Cloud (bắt buộc trước T3: import ghi point mới, thư mục
  `search-prev-*` không khôi phục được Qdrant);
- xoá `data/processed/products-v1/search-release-20261128-074815` và `-075102` (thư mục tạm của hai lần áp dụng lỗi)
  sau khi backup.

**D4. Nếu người xem tự truy cập.** Làm theo bản 27/11 mục 3–4: tunnel, P1.1 thumbnail (rủi ro tốc độ lớn nhất),
P1.3 đo tải 1/5/10 request (p95 < 2 s với 5 người), luật an toàn P0.5. Nếu không, bỏ qua.

**D5. Nghiệm thu.** Checklist bản 27/11 mục 6, thêm: bật chế độ "Thuốc" và chạy kịch bản T4 trên bản production.
Khởi động lại máy rồi chạy từ đầu, ghi thời gian.

## 5. Thuốc

**T1. Chế độ "Thuốc"** (khoảng nửa ngày)

- API: thêm trường tuỳ chọn `category_l1: list[str] | None` vào `TextSearchRequest`, cạnh `brand_id`. Pipeline lọc
  Qdrant bằng `category_l1s`, giống bộ lọc hãng đang có.
- Entity được nhận diện trong câu nhưng nằm ngoài nhóm đang lọc thì bỏ, để không trả "no_target" giả.
- UI: một nút gạt "Chỉ thuốc & thiết bị y tế" (`drug` + `medical`) trên trang tìm kiếm, nhớ trong URL.
- Dòng cảnh báo khi bật: "Thông tin tham khảo, không thay thế tư vấn của bác sĩ/dược sĩ."
- Kiểm tra: test API; **khi tắt chế độ, kết quả silver-v1.1 và hops-v1 phải giữ nguyên** (mặc định là tắt).

**T2. Bộ truy vấn thuốc `eval/drug-v1`** (~30 câu, chấm tay)

| Kiểu | Ví dụ |
| --- | --- |
| Tên | "Nexium 40mg" |
| Hoạt chất | "thuốc chứa ofloxacin" |
| Bệnh / công dụng | "thuốc trị trào ngược dạ dày" |
| Dạng bào chế | "thuốc nhỏ mắt", "siro ho cho trẻ" |
| Nhiều bước | "thuốc cùng hoạt chất với Colflox", "thuốc khác chữa cùng bệnh với Famotidin" |
| Chữ trên hộp | "hộp có chữ Cefadroxil" |

Pool top-10 có và không bật chế độ thuốc, chấm trên trang judge như hops-v1. Chấm luôn 6 sản phẩm còn treo của h19.
Bộ này là dev; không tinh chỉnh trên hops-v2-holdout.

**T3. Nhập thuốc từ part_04** (tuỳ chọn, có cổng)

1. Tách các dòng có tên miền nhà thuốc ra file riêng (~1.654 dòng).
2. Nhập thử 100 dòng trước. Kiểm tra: không lỗi, so khớp hợp lý, ảnh đúng thuốc.
3. Nhập phần còn lại → duyệt nhóm "Cần so khớp" → áp dụng.
4. Chạy lại T2 và hops-v1.

**Cổng:** chỉ làm khi D3 đã có snapshot. Nếu đến **20:00 ngày 29/11** chưa áp dụng xong hoặc điểm T2/hops-v1 giảm thì
không đưa vào demo, khôi phục snapshot. Lý do: mỗi lần nhập và áp dụng ghi vào Qdrant Cloud, và lần chạy đầy đủ sau
khi sửa 6 lỗi import mới chỉ có 50 dòng.

**T4. Kịch bản demo** từ những câu T2 chạy đúng, theo thứ tự: tên → hoạt chất → bệnh → nhiều bước (xem đồ thị quan
hệ) → chế độ thuốc bật/tắt → admin sửa một quan hệ thuốc và xem trước đồ thị (**không bấm áp dụng khi demo**).

## 6. Bộ nhớ và model

Đo trên laptop (RTX 3050 4 GB, RAM 15,7 GB):

| | Giá trị |
| --- | --- |
| VRAM của API: bge-m3 fp16 (~1,1 GB) + SigLIP2-base fp16 (~0,75 GB) + CUDA context | 1,94 / 4 GB |
| RAM private của API | 6,7 GB |
| Commit memory còn trống (API đang chạy) | 7,4 GB |

- Trước demo không thêm model. Import dùng RapidOCR trên CPU và `agy` (bên ngoài), áp dụng thay đổi dùng lại bge-m3
  đang chạy. Không VRAM nào thêm.
- Search chạy trên **một** luồng GPU: nhiều người cùng search thì xếp hàng (đo ở P1.3).
- Không import hay áp dụng thay đổi trong lúc demo.

## 7. Sau 01/12

1. Schema thuốc: hàm lượng, dạng bào chế, số đăng ký (VD-/VN-…), kê đơn/không kê đơn, nhà sản xuất. Lấy từ OCR trên
   hộp và trang nguồn, đối chiếu cơ sở dữ liệu của Cục Quản lý Dược nếu cần.
2. Chuẩn hoá hoạt chất (alias, bỏ dạng muối) và TREATS về danh sách bệnh chuẩn; xếp hạng theo số giá trị chung.
3. Cạnh TREATS do LLM sinh: người duyệt (role `pharmacist`), hiển thị nhãn "tham khảo".
4. Thử `bge-reranker-v2-m3` (đã có trong cache HF, ~1,1 GB fp16): VRAM dự kiến ~3,1 GB + activation, batch 8–16,
   **đo** VRAM đỉnh và latency trên drug-v1 trước khi giữ. Nếu thiếu VRAM, chuyển vision tower của SigLIP2 (chỉ dùng
   khi upload/import) sang CPU.
5. Cách gỡ sản phẩm mới đã áp dụng nhầm trong admin.
6. Nếu chạy trên máy chủ: bản 27/11 mục 8; đo bge-m3 trên CPU trước khi chọn máy.

## 8. Cần chốt

1. Người xem có tự truy cập không? (quyết định có làm D4)
2. Có làm T3 (nhập thuốc từ part_04) trước demo không?
3. Giữ hay gỡ 4 sản phẩm từ file test đã áp dụng hôm nay?
