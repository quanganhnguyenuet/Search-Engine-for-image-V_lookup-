# VietNam Culture — tìm kiếm ảnh bằng từ khóa

## Chạy giao diện

Yêu cầu Node.js 22+ và dataset đã được tải theo [culture-dataset.md](culture-dataset.md).

```bash
cd apps/web
npm ci
npm run dev
```

Truy cập <http://localhost:3000>. Bản này chạy độc lập, không cần khởi động API Python,
PostgreSQL, Qdrant hay GPU. Build production bằng `npm run build`, chạy bằng
`npm run start`. Khi triển khai production phải giữ thư mục `data` trên ổ đĩa bền vững.

Với Docker Compose, service web đã mount `./data:/data:ro`:

```bash
docker compose up --build --no-deps web
```

## Dữ liệu được dùng

Server đọc và gộp hai manifest `processed/culture-search/assets.jsonl` và
`processed/culture-search-sample/assets.jsonl`, loại trùng theo `asset_id` và chỉ giữ
ảnh đang tồn tại trên đĩa. File `selected_annotations.jsonl` không được dùng làm danh
sách ảnh vì chứa cả annotation của ảnh chưa tải.

Mặc định data nằm ở `../../data`, tính từ working directory `apps/web`.
Có thể cấu hình bằng biến môi trường của process Next.js:

```bash
CULTURE_DATA_DIR=/absolute/path/to/data npm run dev
```

`CULTURE_IMAGE_ROOT` tùy chọn chỉ định thư mục chứa `images/`. Nếu không có, server
thử `data/raw/vietnamese-culture-vqa`, sau đó `image_root` trong `report.json`.
Các biến này chỉ dùng phía server. Với host development có thể đặt chúng trong
`apps/web/.env.local` (không commit).

Metadata được cache trong bộ nhớ, tự tải lại khi manifest thay đổi. Khi chỉ thay ảnh
hoặc `report.json`, restart server để làm mới cache. Không sao chép dataset vào Git
hoặc bundle JavaScript của client.

## Hành vi tìm kiếm và UX

- Tìm trong keyword, tags, description, category và cultural context.
- Không phân biệt hoa/thường hoặc dấu tiếng Việt, bao gồm `đ` và `d`.
- Tất cả từ trong query phải xuất hiện; tên/keyword được ưu tiên hơn tags và mô tả.
- Bộ lọc theo 6 chủ đề, hiển thị số kết quả tương ứng với query hiện tại.
- Phân trang 24 ảnh; ảnh thu nhỏ được resize/cache qua Next.js Image, lazy-load.
- Query, chủ đề và trang được lưu trong URL; hỗ trợ chia sẻ, refresh, Back/Forward.
- Dialog chi tiết hỗ trợ Escape, keyboard focus và nhấn tag để tìm tiếp.
- Có skeleton loading, trạng thái không có kết quả, lỗi tải, retry và ảnh lỗi.
- Tên/mô tả phản ánh annotation gốc, có thể sai hoặc chưa có dấu; không tự sửa nhãn.

Đây là lexical search trên corpus local, chưa phải semantic/vector search và chưa có
upload qua UI. Dữ liệu đã tải là lớp lưu trữ hiện tại; MinIO/R2 và PostgreSQL sẽ được
tích hợp khi triển khai ingestion theo MVP plan.

## Endpoints

```text
GET /api/culture?q=ao%20dai&category=trang_phuc&page=1
GET /api/culture/images/{sha256_asset_id}
```

API trả metadata hiển thị, counts, số trang và URL ảnh; không trả đường dẫn tuyệt đối.
Ảnh chỉ được truy cập bằng ID tồn tại trong manifest, với kiểm tra realpath nằm trong
image root; không cho truyền filesystem path từ request.

## Kiểm tra

```bash
cd apps/web
npm run typecheck
npm run test:search
npm run build
```

`test:search` là integration test dùng dataset local: kiểm tra tìm có/không dấu, lọc,
phân trang, loại trùng, tra ảnh và query không có kết quả.

Khi web server đang chạy, `npm run test:http` kiểm tra thêm HTML, search API, ảnh gốc,
thumbnail qua Next.js Image và validation qua HTTP. Có thể đổi địa chỉ bằng
`TEST_BASE_URL=http://127.0.0.1:3000`.
