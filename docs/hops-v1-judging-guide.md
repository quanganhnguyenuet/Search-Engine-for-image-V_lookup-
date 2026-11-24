# Hướng dẫn chấm eval/hops-v1 (multi-hop và multi-intent)

Bộ 40 câu hỏi viết tay để đo hai khả năng mà silver-v1.1 không đo được: đi theo quan hệ trong
graph (multi-hop) và trả lời câu có nhiều vế (multi-intent). Câu viết theo kiểu người dùng thật
nói, không theo mẫu, nên kết quả phản ánh pipeline trên câu tự nhiên.

## 1. Bộ câu

File: [eval/hops-v1/queries.jsonl](../eval/hops-v1/queries.jsonl). Mỗi dòng có `qid`, `kind`,
`query`, `intents` (danh sách vế, chỉ có ở multi-intent) và `tests` (điều câu đó kiểm tra).

| Nhóm | qid | Nội dung |
|---|---|---|
| multi_hop | h01–h18 | "hãng làm X còn bán gì", "cùng hãng với X", "ngoài X ra hãng đó…", tên hãng + loại trừ ("trừ", "ngoài", "khác"), giới hạn loại hàng ("…cho ô tô", "…điện thoại nào") |
| multi_hop (mốc) | h19, h20 | Quan hệ chưa hỗ trợ: TREATS ("thuốc chữa cùng bệnh") và FITS ("phụ tùng lắp cho xe"). Dùng để biết điểm sàn khi graph không giúp được |
| multi_intent | m01–m20 | hai tên thật, tên thật + loại chung, ba vế, "hoặc", "với", "cùng với", "ngoài ra", hai loại hàng, loại trừ theo loại (m20) |

## 2. Chạy trang chấm

```bash
python scripts/build_eval_hops.py                    # chỉ cần khi chưa có pool (cần Qdrant + bge-m3)
python scripts/hops_judge_server.py --judge human-1  # http://127.0.0.1:8013
```

Pool nằm ở `data/processed/products-v1/eval/hops-v1/pool.jsonl` (không có trên git, vì `data/`
bị ignore). Trang cần ảnh corpus theo `image_root` trong `manifest.json`.

Pool gồm hợp top 10 của 4 cấu hình pipeline (mục 5), gom theo **sản phẩm** (canonical entity):
tổng 298 thẻ, trung bình khoảng 7,5 thẻ mỗi câu. Mỗi thẻ hiện tối đa 4 ảnh của sản phẩm đó.

## 3. Thao tác trên trang

- Cột trái: danh sách 40 câu và tiến độ. Câu đã lưu được đánh dấu.
- Đầu trang: câu hỏi, các vế (multi-intent), ghi chú `tests` và luật chấm tóm tắt.
- Mỗi thẻ sản phẩm có nút chấm:
  - multi-hop: **Sai / Gần đúng / Đúng**
  - multi-intent: **Sai / Gần đúng / Vế 1 / Vế 2 / (Vế 3)**
- **Còn lại → Sai**: gán Sai cho mọi thẻ chưa chấm (không đổi thẻ đã chấm).
- **Câu hỏi không hợp lý**: đánh dấu khi câu không có đáp án hợp lý trong corpus hoặc mơ hồ. Câu bị
  đánh dấu không được tính điểm.
- **Ghi chú**: ghi lý do cho trường hợp khó, sau này dùng để sửa luật.
- **Lưu và sang câu tiếp**: server chỉ nhận khi mọi thẻ của câu đều đã chấm, và mỗi thẻ "Vế k" phải
  có vế hợp lệ.

Để chấm không thiên lệch:
- Đừng mở mục "Hệ thống hiểu câu thế nào" trước khi chấm xong câu.
- Bỏ qua dòng "Tìm thấy bởi" (cấu hình nào tìm ra thẻ). Chấm theo câu hỏi, không theo hệ thống.
- Thứ tự thẻ trên trang không phải thứ hạng của một cấu hình cụ thể, nên không cần quan tâm.

## 4. Luật chấm

Chấm theo **sự thật**, không theo việc hệ thống có làm được hay không (đặc biệt với h19, h20, m20).

### 4.1 Multi-hop

| Điểm | Khi nào |
|---|---|
| **Đúng (2)** | Đúng thứ câu hỏi muốn: đúng hãng, đúng loại hàng nếu câu giới hạn loại, và không phải sản phẩm bị loại |
| **Gần đúng (1)** | Đúng hãng nhưng sai loại hàng câu giới hạn; hoặc liên quan gián tiếp (biến thể của chính sản phẩm gốc, phụ kiện của nó) |
| **Sai (0)** | Sai hãng, hoặc là sản phẩm câu hỏi loại trừ |

Trường hợp hay gặp:

- **Chính sản phẩm gốc** (ví dụ bugi Bosch Super Plus trong h01):
  - Chấm **Sai** nếu câu có ý "khác / ngoài / trừ / còn" (h01–h10, h12–h18 đều có).
  - Chấm **Gần đúng** nếu câu không có ý đó (h11 "sản phẩm cùng thương hiệu với…").
  - Điểm này quyết định có cần sửa luật: hiện pipeline chỉ loại sản phẩm gốc khi có từ phủ định
    rõ ("khác", "ngoài", "trừ"). Nếu bạn chấm Sai ở h01, chữ "còn" cũng phải kích hoạt loại trừ.
- **Biến thể của sản phẩm gốc** (cùng dòng, khác dung tích/màu/đời): Gần đúng.
- **Giới hạn loại hàng** ("cho ô tô" ở h02, "điện thoại" ở h07, "túi" ở h05, "máy công trình" ở
  h08, "máy ảnh" ở h17, "trang sức" ở h18, "xe" ở h14, "phụ tùng" ở h15): đúng hãng nhưng khác loại
  là Gần đúng.
- **Loại trừ theo loại** (h04 "không phải bộ đề", h13 "ngoài vợt tennis"): sản phẩm thuộc loại bị
  trừ là Sai; cùng hãng, loại khác là Đúng.
- **h19** (Famotidin, thuốc dạ dày / trào ngược): Đúng là thuốc khác chữa cùng nhóm bệnh, bất kể
  hãng. Famotidin của hãng khác thì Gần đúng.
- **h20** (Honda Air Blade): Đúng là phụ tùng lắp được cho Air Blade. Phụ tùng Honda cho xe khác là
  Gần đúng. Xe Air Blade nguyên chiếc là Sai.

### 4.2 Multi-intent

| Điểm | Khi nào |
|---|---|
| **Vế k (2)** | Sản phẩm đáp ứng vế thứ k của câu |
| **Gần đúng (1)** | Liên quan nhưng không thật sự đáp ứng vế nào |
| **Sai (0)** | Không liên quan |

Trường hợp hay gặp:

- **Vế có tên cụ thể** ("Kindle Paperwhite", "Samsung Jet 75"): đúng sản phẩm đó (kể cả biến thể
  dung lượng/màu) là Vế k. Mẫu khác cùng dòng (Kindle Oasis) hoặc phụ kiện (ốp Kindle) là Gần đúng.
- **Vế loại chung**, ghi "(bất kỳ)" ("tai nghe", "bàn phím", "săm xe đạp"): mọi sản phẩm đúng loại
  là Vế k, không cần đúng hãng.
- **Vế hãng + loại** ("vợt tennis Yonex", "máy in Canon"): đúng cả hai là Vế k. Đúng hãng sai loại
  (vợt cầu lông Yonex), hoặc đúng loại sai hãng, là Gần đúng.
- **Một sản phẩm khớp hai vế** (hiếm): chọn vế cụ thể hơn. Trang chỉ cho chọn một vế.
- **m13 "giá rẻ"**: hạng giá chỉ áp dụng cho thời trang, nên mọi điện thoại Vsmart là Vế 1 và mọi
  điện thoại Redmi là Vế 2.
- **m06 "cho ô tô"**: bugi Bosch / nhớt Mobil dành cho xe máy là Gần đúng.
- **m20** (một vế, "trang sức PNJ trừ nhẫn"): trang sức PNJ không phải nhẫn là Vế 1; nhẫn PNJ là
  Sai; trang sức hãng khác là Gần đúng.

### 4.3 Ảnh có nhiều sản phẩm

Bạn chấm sản phẩm, không chấm ảnh. Khi tính điểm, một ảnh lấy điểm cao nhất trong các sản phẩm
trên ảnh, và được tính là đáp ứng mọi vế mà các sản phẩm của nó đáp ứng.

## 5. Kết quả chấm và cách tính điểm

Mỗi lần lưu ghi thêm một dòng vào [eval/hops-v1/judgments.jsonl](../eval/hops-v1/judgments.jsonl):

```json
{"qid": "m02", "grades": {"<entity_id>": {"grade": 2, "intent": 0}, "...": {"grade": 0, "intent": null}},
 "flag": null, "note": "", "judge": "human-1", "at": "2026-11-25T03:10:00+00:00"}
```

Chấm lại một câu sẽ ghi thêm dòng mới; dòng cuối của mỗi `qid` được dùng. Nhớ commit file này
nếu chấm trên một máy và tính điểm trên máy khác.

Chạy `python scripts/evaluate_hops.py` sau khi chấm. Nó tính điểm cho cả 4 cấu hình trên cùng
bản chấm:

| Cấu hình | Tắt gì | Trả lời câu hỏi |
|---|---|---|
| `current` | không tắt gì (mặc định của API) | mốc so sánh |
| `no_graph_prefetch` | kênh dense lọc theo tập đích của graph | "dense tập đích" có giúp không |
| `no_graph_boost` | điều chỉnh điểm theo graph (+seed, +hop, −anchor) | "điều chỉnh graph" có giúp không |
| `content_only` | cả hai, cùng boost danh mục và chia nhóm theo loại | toàn bộ phần graph + danh mục đóng góp bao nhiêu |

Các chỉ số, tính trên top 10:

| Nhóm | Chỉ số | Ý nghĩa |
|---|---|---|
| multi_hop | `p10` | tỉ lệ ảnh Đúng (2) |
| | `ndcg10` | thứ tự có tốt không; gain = 2^điểm − 1, nên Đúng = 3, Gần đúng = 1 |
| multi_intent | `p10` | tỉ lệ ảnh đáp ứng một vế nào đó |
| | `all_parts10` | 1 nếu mọi vế đều có ít nhất một ảnh, ngược lại 0 |
| | `balanced10` | mỗi vế được chia đều không: số ảnh của vế, tối đa 10 / số vế, chia cho 10 / số vế, rồi lấy trung bình các vế |

Với mỗi cấu hình khác `current`, script in `delta_vs_current` và khoảng tin cậy 95 % (bootstrap
ghép cặp theo câu, 10.000 lần, seed 7). Nếu khoảng tin cậy chứa 0 thì chưa đủ bằng chứng là
thành phần đó có tác dụng. Với khoảng 20 câu mỗi nhóm, chỉ hiệu ứng lớn mới hiện rõ.

## 6. Lưu ý

- Ước lượng thời gian: 298 thẻ, khoảng 1 đến 1,5 giờ.
- Pool đóng băng tại thời điểm build. Nếu đổi pipeline rồi build lại pool, các sản phẩm mới chưa
  được chấm, nên script coi là Sai (0). Khi đó cần chấm bổ sung trước khi so sánh.
- h19, h20 và m20 dự kiến điểm thấp ở mọi cấu hình. Chúng là mốc, không phải lỗi cần sửa ngay.
