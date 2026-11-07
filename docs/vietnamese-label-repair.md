# Sửa dấu tiếng Việt và tên ảnh sai đối tượng

Ngày thực hiện: 06/11/2026. Trường hợp người dùng báo: `bánh chay` / `banh chay`
trả ảnh xương hoặc khớp gối.

## Nguyên nhân đã xác nhận

1. Thư mục `banh_chay` thực sự chứa nhiều đối tượng khác nhau, nhưng tất cả dùng
   tên thư mục làm keyword. Trong 57 asset có ảnh bánh, dụng cụ, 8 ảnh y khoa,
   bóng chày/phụ kiện và ảnh sản phẩm không xác định. Một số caption còn là câu
   fallback chung chung, không mô tả pixel. Sửa dấu trên một tên chung không thể
   sửa được kiểu lẫn dữ liệu này.
2. Keyword search FE dùng chuỗi bỏ dấu và substring, nên `phở` có thể khớp `phố`
   hoặc một phần của `phong`. Nhánh lexical hybrid cũng bỏ dấu mọi token.
3. Bộ khớp entity cũ xử lý từng từ: từ `chay` không có dấu bị coi là thiếu dấu,
   ngay trong query đã có dấu `bánh chay`, dẫn đến chấp nhận `bánh chày`.

## Sửa dữ liệu

- Bảng tên chuẩn có phạm vi category trong `visualseek_preprocessing/labels.py`.
  Ví dụ `ao dai cuới` → `Áo dài cưới`, `nha tho Đuc Ba` → `Nhà thờ Đức Bà`.
  Các tên chưa có căn cứ trong bảng được giữ lại; không chạy bộ đoán dấu tự do
  hoặc tự dịch tên tiếng Anh.
- Đã xem contact sheet của toàn bộ 57 ảnh `banh_chay`, ghi override theo SHA-256
  asset ID và đường dẫn. Đặt tên/chú thích bảo thủ theo đối tượng thấy được;
  không khẳng định tên một món bánh cụ thể nếu ảnh chưa đủ bằng chứng.
- 12 asset ra khỏi search: 8 ảnh y khoa, 2 quả bóng chày, 1 mũ lưỡi trai và 1 hũ
  sản phẩm chưa xác định. File gốc và vector vẫn được giữ cho đối soát; đây không
  phải thao tác xóa ảnh. Semantic, hybrid, entity và keyword đều bỏ qua asset có
  `search_excluded=true`. Sample manifest cũ không được thêm lại những ID này.
- Thêm sửa tên từng asset khi tags và description cùng xác nhận `tủ giày`, `kệ
  giày`, `giá để giày`, `keo dán gạch`... cho các nhóm tên nguồn nhập nhằng.
  Những trường hợp này ghi rõ `pixel_verified=false`, không giả vờ đã xem từng ảnh.
- Giữ keyword/tags/description/context gốc trong `label_original`, có revision và
  phương pháp sửa để truy vết. Không đưa VQA answers vào metadata encoder/export.

## Sửa cách khớp tiếng Việt

- Chuẩn hóa Unicode NFC; giữ âm chính, dấu thanh và chữ `đ` trong khóa có dấu.
- Query có dấu được so theo toàn cụm: `bánh chay` khác `bánh chày` và `bánh cháy`.
  Query hoàn toàn không dấu vẫn có thể tìm dạng có dấu. Field nguồn hoàn toàn
  ASCII cũ được hỗ trợ, nhưng không được xem là bằng chứng xác định một nghĩa mới.
- Chấp nhận cách đặt dấu tương đương `Hoà`/`Hòa`; hỗ trợ `H'Mông`/`H’Mông`/`HMông`.
- Chỉ khớp token/cụm từ nguyên vẹn, không khớp một phần ký tự.
- Inverted lexical index có khóa có dấu, khóa bỏ dấu và field masks để không cộng
  trùng điểm khi cùng một từ xuất hiện ở nhiều biến thể trong cùng field.
- Với tên không dấu có nhiều nghĩa, `/search/text` và keyword FE trả
  `clarification_required` + `suggested_queries`. Ví dụ `pho` có thể gợi ý `phở`,
  `phố` hoặc `bánh phô mai`; `keo` có thể gợi ý `kẹo` hoặc `keo dán`. Lựa chọn
  có đủ ngữ cảnh để không lặp lại vòng hỏi cùng một từ không dấu.
- Text gửi vào encoder vẫn giữ dấu; không encode text đã bỏ dấu. Pure semantic
  vẫn là baseline vector, không cam kết exact entity matching.

## Artifact và chạy lại

Artifact local mới: `data/embeddings/culture-labels-v1`.
Kết quả: chuẩn hóa 13.221 keyword, encode lại 13.222 metadata text thay đổi;
15.423 asset vẫn được lưu, 15.411 asset được tham gia search. Image vectors có
checksum giống hệt bản nguồn. Bản gốc và backup manifest được giữ để đối soát.

Script sửa `scripts/repair_culture_labels.py` tạo phiên bản mới, giữ byte của
`vectors.npy`, chỉ encode lại metadata text thay đổi, có shard để resume và kiểm
tra checkpoint/run identity. `manifest.json` và checksum được cập nhật sau khi
hoàn tất, ZIP mới nằm trong chính thư mục artifact.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python scripts/repair_culture_labels.py \
  --source data/embeddings/culture-contextual-full-v1 \
  --output data/embeddings/culture-labels-v1 \
  --apply-manifest data/processed/culture-search/assets.jsonl
```

Manifest phục vụ FE được cập nhật nguyên tử sau khi artifact hợp lệ, có file
`assets.before-label-repair-<sha>.jsonl` để khôi phục. Ảnh và asset ID không đổi.
`label-repair-report.json` trong artifact ghi danh sách before/after, số vector
metadata được encode lại và số asset searchable/excluded.

Sau rollout `.env` chọn `EMBEDDING_ARTIFACT_DIR=data/embeddings/culture-labels-v1`.
Restart backend để bỏ index/encoder cache cũ; FE dev tự nạp code và đọc lại manifest
theo mtime, có thể restart FE nếu cần. `/api/v1/search/status` cung cấp
`label_revision`, `indexed`, `searchable`, `excluded_from_search`.

Relevance policy/candidate pool cũ gắn với fingerprint cũ. Pool đánh giá sau sửa
tên được lưu riêng, không ghi đè nhãn hoặc baseline cũ. Nếu xuất lại trên Kaggle,
dùng code/selection ZIP mới và RUN_NAME mới; không tái sử dụng checkpoint của
metadata cũ. Bản local mới chưa tự upload đè artifact trên Hugging Face.

## Các query hồi quy

- `bánh chay`, `banh chay`: cùng tập ứng viên entity, không có 12 asset bị cách ly.
- `phở`, `phố`: không khớp nhầm tên nhau; `phở` không substring-match `Phong Nha`.
- `pho`: yêu cầu chọn nghĩa, không tự gộp các đối tượng khác nhau.
- `kẹo`, `keo dán gạch`: tách kẹo với keo.
- `áo dài cưới`, `ao dai cuoi`, `nhà thờ Đức Bà`: tìm được tên đã chuẩn hóa.

Smoke API thật với artifact mới xác nhận `bánh chay` và `banh chay` trả cùng 18
asset ID, không có asset nào trong nhóm 57 ảnh đã gán lại chủ đề. Đã kiểm tra từng
vector của 12 asset bị cách ly: semantic và hybrid đều không trả lại ID bị loại.
Chi tiết: `data/diagnostics/vietnamese-label-rollout-verification.json`.

Đây là kiểm tra tên, mapping và các lỗi dữ liệu đã xác nhận. Không suy ra toàn bộ
caption còn lại đã được xác minh bằng pixel hoặc mọi query semantic đều đúng.
