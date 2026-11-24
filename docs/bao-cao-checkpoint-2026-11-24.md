# Báo cáo checkpoint 2026-11-24: hiểu loại hàng theo danh từ chính và graph đã duyệt

Phạm vi: 4 commit `2799055`, `5b2c525`, `2b5b350`, `901d68d` trên nhánh `chore/products-v1-cleanup`.
Mọi số liệu bên dưới do chạy lại trong phiên này trên code, graph (`25cb39ec`) và Qdrant hiện tại.
Điểm chấm tay hops-v1 (40 câu) và hops-v2-holdout (40 câu) là của người dùng.

---

## 1. Kết luận

1. **Các bản sửa tổng quát hoá được.** Trên bộ holdout (hãng và cách hỏi hops-v1 chưa từng thấy), điểm ngang bộ dev:
   multi_hop P@10 0.875 so với 0.885, multi_intent P@10 0.920 so với 0.880. Không thấy dấu hiệu overfit.
2. **Thay đổi lớn nhất là câu ghép.** Tỉ lệ câu có ảnh cho *mọi* vế (`all_parts@10`) tăng từ 0.60 lên 0.85 trên dev và đạt 0.90 trên holdout.
3. **Bộ lọc loại hàng (P1) và graph boost đều có tác dụng thật trên dữ liệu mới**: bỏ bất kỳ thành phần nào thì P@10 và Recall@10 giảm 0.13 đến 0.25, khoảng tin cậy 95% đều nằm dưới 0.
4. **Tìm thấy và đã sửa 1 lỗi thật** (mục 5.1): tên sản phẩm có dấu `&` hoặc `,` bị cắt thành nhiều vế, sinh ý định giả. Lỗi do P3 gây ra, silver dev bắt được, hops không bắt được.
5. **Chưa đo**: P6 (fusion ảnh) end-to-end, silver test, và latency dưới tải (mục 6).

---

## 2. Điểm trên hops (chấm tay theo sản phẩm)

| Chỉ số | Dev trước CP | **Dev sau CP** | **Holdout** |
|---|---|---|---|
| multi_hop P@10 | 0.880 | 0.885 | 0.875 |
| multi_hop nDCG@10 | 0.938 | 0.946 | 0.942 |
| multi_hop wrong@10 | 0.045 | 0.035 | 0.060 |
| multi_intent P@10 | 0.885 | 0.880 | 0.920 |
| multi_intent wrong@10 | 0.065 | 0.070 | 0.030 |
| multi_intent all_parts@10 | 0.600 | 0.850 | 0.900 |
| multi_intent balanced@10 | 0.686 | 0.818 | 0.860 |

Mỗi bộ có 20 câu mỗi loại nên khoảng tin cậy rộng (khoảng ±0.1). Chênh lệch dev và holdout nằm trong vùng nhiễu.
Riêng `wrong@10` của multi_hop trên holdout (0.060) cao hơn dev một chút, vẫn thấp (khoảng 0.6 ảnh sai mỗi top-10).

---

## 3. Recall@10 theo pool

Định nghĩa theo `PLAN_PRODUCTS_V1.md` mục 5.2: câu đơn và multi-hop chia cho `min(|Rel|, 10)`; multi-intent chia theo từng vế, mỗi vế tối đa `⌊10/k⌋` chỗ.
`Rel` ở đây là ảnh của các sản phẩm được chấm điểm 2 *trong pool*. Pool là hợp top-10 của 7 cấu hình, nên ảnh đúng mà chưa cấu hình nào tìm ra không có trong `Rel`.
**Đây là cận trên của recall thật, và chỉ so sánh công bằng giữa các hệ thống cùng pool.**

| | Dev hops-v1 | Holdout hops-v2 |
|---|---|---|
| multi_hop Recall@10 | **0.942** (CI95 0.853 đến 1.0; n=19, h19 không có ảnh đúng trong pool nên bị loại) | **0.943** (CI95 0.893 đến 0.984; n=20) |
| multi_intent Recall@10 | **0.950** (CI95 0.898 đến 0.990; n=20) | **0.957** (CI95 0.917 đến 0.990; n=20) |

**Trước và sau checkpoint trên dev** (cùng `Rel`, gộp ảnh đã chấm của pool cũ và mới):

| | Trước CP | Sau CP | Chênh lệch (CI95) |
|---|---|---|---|
| multi_hop | 0.937 | 0.942 | +0.005 (−0.016 đến +0.032): không đổi |
| multi_intent | 0.810 | 0.950 | **+0.141 (+0.041 đến +0.250)** |

### Đóng góp từng thành phần (độ lệch Recall@10 khi bỏ thành phần đó, so với cấu hình hiện tại)

| Bỏ | multi_hop dev | multi_hop holdout | multi_intent dev | multi_intent holdout |
|---|---|---|---|---|
| Bộ lọc loại hàng (P1) | −0.063 (CI chạm 0) | **−0.243** | **−0.239** | **−0.252** |
| Graph boost | **−0.247** | **−0.193** | **−0.156** | **−0.128** |
| Cả graph lẫn category (chỉ nội dung) | **−0.587** | **−0.568** | **−0.264** | **−0.257** |
| Cho phép mở rộng loại hàng sang category (`type_expand`) | 0.000 | **−0.079** | **−0.068** | **−0.109** |

In đậm là các chênh lệch có khoảng tin cậy 95% nằm hẳn dưới 0.
Quyết định `type_expand = False` (đã chốt từ trước) được holdout xác nhận: bật nó làm recall giảm có ý nghĩa.

Một cân nhắc: cấu hình `fill_any` (lấp đủ 10 ảnh bằng ảnh bất kỳ) cho Recall@10 cao hơn một chút ở multi_hop (+0.026 dev, +0.020 holdout, cận dưới CI bằng 0). Đây là cái giá của quyết định `fill_related_only`: hãng có ít hơn 10 ảnh đúng thì trả ít ảnh hơn 10 thay vì độn ảnh sai. P@10 không đổi giữa hai cấu hình.

---

## 4. Silver-v1.1 dev: kiểm tra hồi quy (222 câu, qua API thật)

Run hợp lệ, 0 lỗi, 0 cache hit. So với run `dev-b2-connectors` (trước P1, ghi trong `data/processed/products-v1/eval/runs/`).

| Loại | Trước | Sau | Ghi chú |
|---|---|---|---|
| multi_hop (n=54) | 0.993 | 0.937 | Xem bên dưới: 3 câu do Rel cũ của silver, không phải hồi quy |
| multi_intent (n=60) | 1.000 | 0.998 | 1 câu giảm nhẹ (lỗi ở mục 5.1) |
| single_named (n=68) | 0.985 | 0.978 | 1 câu giảm (lỗi ở mục 5.1) |
| single_brand (n=19) | 0.958 | 0.958 | không đổi |
| no_answer (tỉ lệ từ chối đúng, n=21) | 0.857 | **0.952** | tăng |

Hai câu single_named và multi_intent giảm nhẹ là lỗi thật ở mục 5.1, đã sửa: chạy lại silver dev sau khi sửa thì đúng hai câu này về 1.0 (single_named 0.985, multi_intent 1.000), các câu khác không đổi, p95 255.7 ms.

**Ba câu multi_hop giảm về 0 là hệ quả của lần duyệt graph, không phải lỗi.** Rel của silver dựng từ graph cũ, có cả các cạnh sai bạn đã gỡ hoặc chuyển:
- "…MAC Spice": Hauck Mac Baby từng bị gán hãng MAC, nay đã chuyển sang Hauck, nên MAC chỉ còn 1 sản phẩm và không còn "sản phẩm khác".
- "…Xi măng Fico Tây Ninh": 3 cạnh `brand:ninh` (xi măng, bánh trung thu, bún cá) đều là cạnh sai đã gỡ.
- "…Hauck Mac Baby Deluxe": hãng Hauck mới chỉ có 1 sản phẩm.

Bỏ 3 câu này thì multi_hop dev không đổi so với trước. Silver cần dựng lại Rel từ graph đã duyệt trước khi dùng tiếp cho multi_hop.

**Latency (client, ms):**

| | p50 | p95 | p99 |
|---|---|---|---|
| Trước | 156.7 | 258.0 | 313.4 |
| Sau | 179.2 | 270.3 | 381.2 |

p95 còn dưới ngân sách 300 ms nhưng p50 và p99 đều tăng. Chưa tách được nguyên nhân: giữa hai lần chạy có cả P1 đến P3, fusion ảnh và kênh theo nhóm danh mục thay đổi, và máy đang chạy thêm Docker và Neo4j. Ảnh chụp màn hình qua giao diện web cho thấy tổng 550 đến 680 ms, gồm cả thời gian trình duyệt và `branch_scores`.

---

## 5. Vấn đề còn tồn đọng

### 5.1 Tên sản phẩm có `&` hoặc `,` bị cắt thành nhiều vế (lỗi thật, đã sửa)

`connectors.segments` cắt câu ở `&` và dấu phẩy. `_brand_requests` rồi nhận diện từng mảnh riêng, nên entity bị cắt đôi không còn nhận ra, và các mảnh còn lại thành "vế loại hàng chung" (P3):

| Câu | Cắt thành | Hậu quả |
|---|---|---|
| bình nước lock**&**lock inox 600ml | `bình nước lock` / `lock inox 600ml` | thêm 1 ý định giả; top-10 còn 5 ảnh đúng và 5 ảnh bình nước/máy nước nóng khác (recall 1.0 xuống 0.5) |
| Indapamid DWP 2**,**5mg, Kệ vòng hoa viếng và Graber Solar Shades | `…dwp 2` / `5mg` / … | thêm ý định giả (4 ý định thay vì 3), recall 1.0 xuống 0.89 |

Trong graph có 37 entity có `&`, `,` hoặc `;` trong tên (ví dụ "Palăng cáp điện R&M Spacemaster", "Spiramycin 1,5 MIU"). Hops không có loại câu này nên không thấy lỗi; silver dev thấy 2 câu.
**Đã sửa:** `connectors.segments` nhận thêm các khoảng từ mà S1 đã nhận là entity hoặc hãng trên cả câu và không cắt ở connector hay dấu câu nằm bên trong khoảng đó. Cả `_brand_requests` lẫn `_category_intent` đều truyền khoảng này. Kiểm chứng: 54 plan (40 câu dev hops-v1 và 14 câu thử) không đổi; hai câu trên chỉ còn đúng số ý định; silver dev chạy lại qua API thật như đã nêu ở mục 4. Holdout chưa chạy lại sau thay đổi này.

### 5.2 Ba lỗi trên dev hops-v1, cố ý chưa sửa

Chưa sửa vì sửa theo từng câu dev là con đường dẫn tới overfit. Cần xem chúng có lặp lại ở holdout hay không (mình không xem kết quả từng câu holdout, theo quy tắc của bộ này).
- **h04** "đồ Shimano nhưng không phải bộ đề": bạn xếp tay đề vào "bộ đề"; danh từ tập hợp "bộ X" chưa được xử lý.
- **m14** "máy in Canon…": mực in Canon vào qua đường dự phòng theo category (tên danh mục là "Máy in, máy scan & mực").
- **m10** "đồ thờ cúng bằng đồng": vế loại hàng chung bỏ qua chất liệu, kéo cả đồ gốm và đất nung.

### 5.3 Giới hạn của graph (nguyên nhân gốc của nhiều heuristic)

Graph đã duyệt có 723 cạnh `MADE_BY` (tất cả `reviewed`) nhưng **1.581 / 2.304 entity chưa có hãng**, và chưa có quan hệ `FITS`, `TREATS`, `ORIGIN`. Câu hỏi quan hệ khác `MADE_BY` (h19, h20) vẫn chỉ được trả lời nhờ nội dung. Đây là việc của P5.

---

## 6. Chưa đo, cần nói rõ

- **P6 (fusion ảnh SigLIP2) chưa được đo end-to-end.** API đã bật fusion (silver chạy qua API nên đã có fusion), nhưng `build_pipeline` trong `scripts/evaluate_category.py` chưa truyền `image_encoder` và `fusion`, nên eval offline (hops, category-v1) không dùng fusion. Con số 0.793 (dev) là kết quả offline `tune_fusion` từ trước. Các số 0.798 và 0.815 trong `docs/bao-cao-cap-nhat-p1-p6-2026-11-24.md` không truy được nguồn và chưa được tái lập.
- **Silver test (327 câu) chưa chạy**, theo nguyên tắc chỉ chạy một lần ở cuối trên cấu hình đã chốt.
- **descriptive-v1** không chạy lại. category-v1 chỉ chạy lại để kiểm tra kênh theo nhóm danh mục: 63 câu một danh mục và 30 câu nhiều danh mục, điểm không đổi.
- **Latency dưới tải** chưa đo; con số ở mục 4 là chạy tuần tự từng câu.

---

## 7. Khuyến nghị

1. **Dựng lại Rel của silver từ graph đã duyệt** để silver dev trở lại dùng được làm smoke test.
2. **Nối fusion vào `build_pipeline`** rồi đo P6 trên descriptive, category-v1 và silver (mục 6).
3. **P5**: làm dày graph (hãng cho 1.581 entity chưa có, rồi `FITS`/`TREATS`), duyệt trong Neo4j. Khi có Rel đầy đủ từ graph thì Recall@10 của hops mới tính đúng nghĩa, không còn là cận trên theo pool.
4. **P8**: giảm latency (chỉ gọi `branch_scores` khi bật trace).
5. Chạy lại holdout ở checkpoint tiếp theo (thay đổi segments chưa được đo trên holdout) và chỉ đọc số tổng hợp.

---

## 8. Cách tái lập

```powershell
# hops: tạo pool, chấm ở :8013, rồi
python scripts/build_eval_hops.py [--set hops-v2-holdout]
python scripts/hops_judge_server.py --judge <tên> [--set hops-v2-holdout]
python scripts/evaluate_hops.py [--set hops-v2-holdout]
python scripts/recall_hops.py [--set hops-v2-holdout] [--before-pool <pool cũ>]

# silver dev (cần API chạy ở :8000)
python scripts/evaluate_v2.py run --split dev --eval data/processed/products-v1/eval/silver-v1.1 \
    --label <nhãn> --out data/processed/products-v1/eval/runs/<tên>.json
```

File kết quả silver của checkpoint: `data/processed/products-v1/eval/runs/dev-checkpoint-2026-11-24.json`.
Pool dev trước checkpoint (để so sánh): `data/processed/products-v1/eval/hops-v1/pool-2026-11-24-before-checkpoint.jsonl`.
Phần `data/` không nằm trong git.
