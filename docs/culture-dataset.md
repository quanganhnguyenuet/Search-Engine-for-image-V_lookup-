# Dữ liệu nền Vietnamese Culture Search

Nguồn: <https://www.kaggle.com/datasets/blackcubo/vietnamese-culture-vqa-dataset>,
version 1. Kaggle công bố giấy phép Apache 2.0; thông tin này được giữ trong manifest.

Đọc trực tiếp `annotations/vietnamese_vqa_dataset.json` cho kết quả 28.484 bản ghi.
File `dataset_statistics.json` đi kèm không khớp annotation và thiếu `am_thuc`.
Số lượng theo annotation trước khi đối chiếu tên file thật và loại trùng:

| Nhóm | Category | Số ảnh |
| --- | --- | ---: |
| Ẩm thực | `am_thuc` | 2.941 |
| Kiến trúc | `kien_truc` | 2.979 |
| Phong cảnh | `phong_canh` | 2.929 |
| Lễ hội | `le_hoi` | 2.387 |
| Trang phục | `trang_phuc` | 2.485 |
| Thủ công mỹ nghệ | `thu_cong_my_nghe` | 1.986 |
| Tổng | | 15.707 |

## Bộ đầy đủ đã lưu local

Đối chiếu ZIP version 1 cho thấy 6 thư mục thực tế có **15.719 ảnh**. Annotation có
120 đường dẫn dùng `H'Mông` trong khi tên file thật dùng `HMông`, cùng 12 ảnh không
có annotation. Pipeline đối chiếu Unicode NFC và dấu nháy để giữ annotation đúng ảnh;
ảnh không có annotation chỉ lấy keyword từ thư mục, không tự sinh mô tả hay câu trả lời.

| Nhóm | File ảnh gốc | Asset duy nhất sau loại trùng toàn bộ corpus |
| --- | ---: | ---: |
| Ẩm thực | 2.941 | 2.925 |
| Kiến trúc | 2.985 | 2.947 |
| Phong cảnh | 2.930 | 2.908 |
| Lễ hội | 2.390 | 2.356 |
| Trang phục | 2.485 | 2.365 |
| Thủ công mỹ nghệ | 1.988 | 1.922 |
| Tổng | **15.719** | **15.423** |

296 file trùng nội dung được giữ trên đĩa và ghi vào `duplicates.jsonl`; giao diện
hiển thị asset duy nhất. Báo cáo đầy đủ có `complete: true`, `failures: 0`.
Số asset theo nhóm phụ thuộc bản ghi được giữ khi ảnh trùng xuất hiện ở nhiều nhóm.

## Tải và chuẩn bị

Chạy từ thư mục gốc repo, dùng Python 3.12; công cụ chỉ dùng standard library:

```bash
PYTHONPATH=packages/preprocessing/src python3 -m visualseek_preprocessing.culture_dataset
```

Lệnh tải annotation và từng ảnh thuộc 6 category, không cần tải ZIP toàn bộ 12,05 GB.
File tải xong được cache; chạy lại để tiếp tục nếu mạng bị gián đoạn. File `.part`
chưa hoàn tất sẽ được tải lại. Version nguồn được cố định ở 1.

Để tải đầy đủ qua một ZIP chính thức (12.050.064.425 bytes), rồi chỉ trích ảnh của
6 nhóm đã chọn và tạo manifest đầy đủ:

```bash
PYTHONPATH=packages/preprocessing/src python3 -u -m visualseek_preprocessing.culture_dataset \
  --download-archive data/raw/archives/vietnamese-culture-vqa-v1.zip
```

Lệnh này không đặt `--limit-per-category`. ZIP được giữ trong `data/raw/archives/`
để có thể trích lại offline; các nhóm khác trong ZIP không được trích ra ổ đĩa.
Tải ZIP hỗ trợ tiếp tục file `.part` bằng HTTP Range khi máy chủ hỗ trợ. Không thay
đổi tên file ZIP giữa các lần tiếp tục. Khi gặp HTTP 429, lệnh dừng và giữ phần đã tải.
Kiểm tra `selected_images`, `unique_assets`, `duplicates` và `failures` trong report:
ảnh duy nhất + ảnh trùng + ảnh lỗi phải bằng số ảnh đã chọn.

Để thử tập nhỏ, chọn mẫu ổn định theo hash đường dẫn, tối đa 100 ảnh mỗi nhóm:

```bash
PYTHONPATH=packages/preprocessing/src python3 -m visualseek_preprocessing.culture_dataset \
  --limit-per-category 100 --output data/processed/culture-search-sample
```

Tạo lại manifest từ ảnh local, không gọi mạng:

```bash
PYTHONPATH=packages/preprocessing/src python3 -m visualseek_preprocessing.culture_dataset --offline
```

`--workers` điều chỉnh số lượt tải đồng thời (mặc định 12). Lỗi tải/thiếu ảnh được ghi
vào `failures.jsonl`; exit code là 1 nếu còn lỗi. Manifest chỉ chứa file tải thành công.
Mặc định các request cách nhau ít nhất 1 giây trên toàn bộ worker, có thể chỉnh bằng
`--request-interval`. Khi gặp HTTP 429, công cụ ngừng gửi request mới và ghi nhận phần
còn thiếu. Chờ Kaggle mở lại hạn mức rồi chạy lại; không có thời gian reset cố định
được xác nhận cho dataset này.

Nếu tải từng file bị giới hạn, tải ZIP bằng nút Download trên trang Kaggle, sau đó:

```bash
PYTHONPATH=packages/preprocessing/src python3 -m visualseek_preprocessing.culture_dataset \
  --archive /path/to/archive.zip
```

Lệnh này lấy đúng annotation và ảnh của 6 nhóm từ ZIP, không gọi mạng.
Kiểm tra `report.json` trước khi indexing; pipeline này chưa decode/normalize ảnh.
Indexer cần xác minh MIME, kích thước, EXIF và decode ảnh trước khi embedding.

## Đầu ra

- `data/raw/vietnamese-culture-vqa/vietnamese_vqa_dataset.json`: annotation nguyên bản.
- `data/raw/vietnamese-culture-vqa/images/<category>/<subject>/<filename>`: ảnh gốc.
- `data/processed/culture-search/assets.jsonl`: mỗi dòng một asset duy nhất theo SHA-256.
- `data/processed/culture-search/selected_annotations.jsonl`: toàn bộ annotation của 6 nhóm,
  bao gồm cả ảnh còn thiếu, phục vụ kiểm tra và tiếp tục tải.
- `data/processed/culture-search/duplicates.jsonl`: đường dẫn và category của ảnh trùng.
- `data/processed/culture-search/failures.jsonl`: ảnh không tải/đọc được.
- `data/processed/culture-search/report.json`: số lượng, category, split và đường dẫn gốc ảnh.
- `data/processed/culture-search/path_aliases.jsonl`: 120 ánh xạ đường dẫn annotation
  sang tên file thật, kèm quy tắc đối chiếu.
- `data/processed/culture-search/unannotated_images.jsonl`: 12 ảnh có thật nhưng thiếu
  annotation; asset có `source.annotation_status: missing`.

`image_path` trong manifest tương đối với `image_root` trong report. `asset_id` là SHA-256
toàn bộ file; không dùng `image_id` nguồn vì ID này lặp lại giữa các thư mục.
Mỗi asset có category, nhãn tiếng Việt, keyword, description, tags, cultural context,
questions và provenance (dataset/version/file). Khi trùng bytes, giữ bản ghi đầu theo
thứ tự hash đường dẫn ổn định; nhãn của bản trùng vẫn có trong `duplicates.jsonl`.
`selected_annotations.jsonl` chứa bản ghi đã đối chiếu đường dẫn và bản ghi tối thiểu
cho ảnh thiếu annotation. Annotation gốc trong `data/raw` được giữ nguyên.

Split tùy chỉnh theo hash nội dung: train 80%, validation 10%, test 10% (xấp xỉ).
Đây không phải split chính thức của dataset. Ảnh trùng bytes luôn chung split;
ảnh gần trùng, crop hay ảnh cùng địa điểm vẫn cần rà soát khi lập benchmark.

## Đưa vào luồng search cơ bản

```text
assets.jsonl + ảnh local
  -> validate/normalize -> lưu ảnh vào MinIO/R2 và metadata vào PostgreSQL
  -> image embedding -> Qdrant (asset_id, category, split, model_version)
  -> query ảnh/text -> embedding cùng model -> top-K -> metadata + thumbnail
```

Caption/tags có sẵn giúp hiển thị kết quả, kiểm tra chất lượng và tạo query đánh giá.
Baseline image embedding không cần gọi VLM để sinh lại caption. Nếu index thêm vector
text từ description, chỉ dùng description/tags của corpus; giữ câu hỏi/đáp án đánh giá
riêng để tránh lộ nhãn. Các câu hỏi VQA chung như “Đây là gì?” không đủ đặc trưng để
làm query retrieval; nên viết query mô tả cụ thể và kiểm tra thủ công relevant assets.

Giao diện VietNam Culture hiện tìm từ khóa trên manifest và phục vụ ảnh local; xem
[culture-search.md](culture-search.md). Sau khi manifest đầy đủ được cập nhật, làm mới
trang để giao diện đọc thêm ảnh; không phải build lại frontend. Storage adapter,
embedding và vector search vẫn là các phần cần triển khai theo MVP plan.

## Kiểm tra

```bash
PYTHONPATH=packages/preprocessing/src python3 -m unittest discover \
  -s packages/preprocessing/tests -v
```
