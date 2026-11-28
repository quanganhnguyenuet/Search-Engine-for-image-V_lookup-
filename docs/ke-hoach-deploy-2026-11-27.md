# Kế hoạch deploy V-Lookup (bản 27/11/2026)

Mốc: demo **01/12/2026** (thứ Ba). Thay cho [ke-hoach-deploy-demo-2026-11-26.md](ke-hoach-deploy-demo-2026-11-26.md).

## 1. Quyết định

Cập nhật tối 27/11: chỉ demo cho bài trình bày, **không cần chạy 24/7**.

| Cách demo | Khi nào dùng | Chạy gì |
| --- | --- | --- |
| **Trên chính laptop** (đơn giản nhất) | Chỉ người trình bày thao tác và chiếu màn hình | `.\scripts\start_demo.ps1`, mở `http://127.0.0.1:3000`. Không cần tunnel |
| **Laptop + Cloudflare Tunnel** | Người xem tự mở trên máy của họ | Thêm `-Tunnel quick` hoặc `-Tunnel named` |

Giữ Qdrant Cloud và Supabase. Cả hai cách đều cần mạng.

### Docker: không cần cho buổi demo

- Chạy trên laptop thì Docker không giải quyết vấn đề nào đang có, mà thêm một lớp có thể hỏng: máy ảo WSL2 tốn thêm
  bộ nhớ (commit memory đang là điểm yếu, xem mục 5), phải cấu hình GPU cho container, image torch bản CUDA rất lớn,
  và phải mount 8,3 GB ảnh từ ổ Windows. Đây là rủi ro đánh giá theo cấu hình, **chưa đo**.
- Dockerfile hiện tại chưa chạy được search:
  - `services/api/Dockerfile` không chép `scripts/` và `graph/`, không cài extra `encoder`;
  - `pillow`, `rapidocr-onnxruntime`, `python-multipart`, `google-genai`, `curl-cffi` chưa được khai báo là dependency;
  - API ghi vào `data/processed/products-v1/search`, `graph/` và `data/admin`, nên các thư mục này phải đọc-ghi được;
  - gắn nhãn CSV đang dùng `agy`, chỉ có trên laptop.
- Nếu bài trình bày **yêu cầu** có Docker thì đó là một hạng mục riêng (mục 8, bước 1–4), làm và kiểm tra tách khỏi
  đường chạy demo.

### Render: được, nhưng không khuyến nghị

- Chỉ web đặt được trên Render. Gói free có 512 MB RAM và 0,1 CPU, không có ổ đĩa bền
  ([Render free](https://render.com/docs/free)), không đủ cho API (hai model, ảnh, artifact).
- API vẫn ở laptop, nên **vẫn cần tunnel**. Web trên Render không bỏ được thành phần nào.
- `API_PROXY_URL` được đọc lúc build. Với quick tunnel, mỗi lần tunnel đổi URL phải build lại trên Render. Với named
  tunnel thì đã có URL cố định, không cần Render nữa.
- Service free ngủ sau 15 phút không có request, thức lại mất khoảng 1 phút. Ảnh phải đi thêm một chặng
  laptop → tunnel → Render → người xem.

Nếu vẫn dùng: Root Directory `apps/web`; build `npm ci && npm run build`; start `npm run start`; đặt
`API_PROXY_URL=<URL tunnel trỏ vào cổng 8000>`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`.
Khi tunnel trỏ thẳng vào API thì `/docs` cũng công khai. Cách này **chưa thử trên Render**.

Điểm đã lỗi thời trong plan cũ: demo ngày 24/12; đăng nhập Google (code hiện chỉ có email + mật khẩu + mã 6 số);
worker, Redis, Neo4j là thành phần bắt buộc (luồng search và admin hiện chạy trong API).

## 2. Kiến trúc khi mở qua tunnel

```text
Người dùng ──HTTPS──► Cloudflare ──tunnel──► cloudflared (laptop)
                                                  │
                                                  ▼
                                   web  next start  127.0.0.1:3000
                                                  │  rewrite /api/v1/*
                                                  ▼
                                   api  uvicorn     127.0.0.1:8000 ──gRPC──► Qdrant Cloud (Seoul)
                                                  │
                                                  └── bge-m3 + SigLIP2 (GPU 4 GB), ảnh và artifact trên đĩa

Trình duyệt ──► Supabase (đăng ký, đăng nhập)
```

- Chỉ **một** địa chỉ công khai. Web và API cùng origin, không cần CORS, không cần tunnel thứ hai.
- Chỉ đường `/api/v1/*` được chuyển tới API, nên `/docs` và `/openapi.json` của FastAPI không lộ ra ngoài.
- Không chuyển Qdrant về local trước demo. Lần đo 25/11 cho median phía client 319 ms; đổi lúc này phải chuyển dữ liệu
  và kiểm tra lại.

## 3. Chọn tunnel

| Phương án | Chi phí | URL | Ghi chú | Đánh giá |
| --- | --- | --- | --- | --- |
| **Cloudflare named tunnel** | Tiền domain | Cố định | Cần domain quản lý trên Cloudflare; đặt được Cache Rule cho ảnh | **Khuyến nghị** |
| Cloudflare quick tunnel | 0 | Đổi mỗi lần chạy | Tối đa 200 request đồng thời; Cloudflare nói chỉ dành cho thử nghiệm, không cam kết uptime | Dự phòng nếu không có domain |
| Tailscale Funnel | 0 (gói cá nhân, phi thương mại) | Cố định `*.ts.net` | Đang beta; có giới hạn băng thông không công bố | Rủi ro vì trang nhiều ảnh |
| ngrok free | 0 | Cố định | Giới hạn 1 GB tải ra mỗi tháng; chèn trang cảnh báo | Loại |

Đăng ký và quên mật khẩu dùng **mã 6 số**, email không chứa link. Vì vậy URL đổi (quick tunnel) không làm hỏng
đăng nhập. Vẫn cần thử lại ở mục 6.

Nguồn: [Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/),
[Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel),
[ngrok free plan](https://ngrok.com/docs/pricing-limits/free-plan-limits). Kiểm tra lại giới hạn trước khi chốt.

## 4. Việc phải làm

### P0: bắt buộc trước khi mở ra internet

| Việc | Trạng thái |
| --- | --- |
| P0.1 Commit và gắn tag `demo-rc1` | Xong (28/11, ở máy local) |
| P0.2 Nâng Next lên 16.4.6 | Xong; bản đang cài là 16.4.6 |
| P0.3 Web và API chung một origin | Xong |
| P0.4 `scripts/start_demo.ps1` | Xong; đã chạy thử bản sao trên cổng 3100 |
| P0.5 Luật an toàn | Script tự kiểm tra phần kiểm tra được; phần còn lại làm tay |

**P0.2.** `next@16.4.4` nằm trong dải bị `npm audit` cảnh báo (bảy advisory, mức "moderate"). Sau khi nâng, `npm audit
--omit=dev` báo 0 lỗ hổng. Dependency dev chưa xử lý.

**P0.3.** Đã làm:

- `next.config.ts` chuyển `/api/v1/*` tới `API_PROXY_URL` (mặc định `http://127.0.0.1:8000`).
- `proxy.ts` bỏ qua `api/`, để ảnh và `/health` không bị chuyển về `/login`.
- `proxyClientMaxBodySize: "12mb"`. Bản trước của kế hoạch này ghi rằng loại `api/` khỏi `matcher` là đủ; **điều đó
  sai**. Khi app có `proxy.ts`, Next vẫn cắt body ở 10 MB. Thử body 11 MB: lỗi 500 sau 30 giây; sau khi thêm cấu
  hình thì server nhận đủ.
- Bản production mặc định gọi cùng origin. `apps/web/.env.production.local` (không nằm trong git) ghi đè giá trị
  `localhost:8000` của `.env.local`.

**P0.4.** Script khởi động API (hoặc dùng API đang chạy), build, chạy web ở `127.0.0.1:3000`, tự kiểm tra rồi mới mở
tunnel. Nó dừng nếu: Next chưa đúng phiên bản; cổng 3000 đang bận; search không đòi đăng nhập hoặc chưa nạp; build còn
gọi `localhost:8000`.

**P0.5 Luật an toàn khi tunnel đang mở** (không áp dụng nếu chỉ demo trên laptop)

- `REQUIRE_LOGIN` luôn là `true`. Chỉ chạy `bench_latency.py` và `evaluate_*.py` (cần `false`) khi tunnel **đã tắt**.
- Không chạy `next dev` sau tunnel.
- Đăng ký đang mở: ai có URL cũng tạo được tài khoản và search. Tạo sẵn tài khoản demo, sau đó cân nhắc tắt
  "Allow new users to sign up" trên Supabase.
- Mật khẩu tài khoản admin phải mạnh và không dùng lại.
- `/api/v1/health` và `/api/v1/images/{asset_id}` là public. API chưa có rate limit, nên chỉ mở tunnel trong lúc
  tập dượt và demo.

### P1: nên làm nếu có người xem truy cập qua tunnel

Chỉ demo trên laptop thì bỏ qua P1.1 và P1.2.

**P1.1 Thumbnail.** Đây là rủi ro tốc độ lớn nhất khi chạy trên đường upload của laptop. API đang trả ảnh gốc: trung
bình khoảng 275 KB, trung vị 112 KB, p90 569 KB, lớn nhất 14,6 MB. Một trang 10 ảnh trung bình khoảng 2,75 MB, tức
khoảng 1,1 giây ở 20 Mbps cho **một** người, và tăng gần tuyến tính khi nhiều người tải cùng lúc.

- Thêm `GET /api/v1/images/{asset_id}?w=480`: thu nhỏ bằng Pillow, lưu cache vào `data/thumbs/`.
- Lưới kết quả dùng thumbnail; trang chi tiết dùng ảnh gốc.
- Khai báo `pillow` trong `services/api/pyproject.toml`.
- Đo dung lượng một trang và thời gian tải qua 4G trước và sau.

**P1.2 Cache ảnh ở Cloudflare** (chỉ named tunnel): Cache Rule cho `/api/v1/images/*`. URL không có đuôi file nên
Cloudflare không tự cache. API đã gửi `max-age=86400`. Lưu ý: ảnh bị ẩn hoặc xoá trong admin vẫn có thể còn trong
cache; purge nếu cần.

**P1.3 Đo tải.** `SearchService` chạy search trên **một** luồng, nên các request xếp hàng. Viết script nhỏ gửi 1, 5 và
10 request đồng thời. Ngưỡng đề xuất: p95 dưới 2 giây với 5 người. Nếu không đạt, giới hạn số người dùng thử cùng lúc
trong buổi demo.

## 5. Chuẩn bị laptop

| Việc | Ghi chú |
| --- | --- |
| Bộ nhớ trống | Lỗi segfault ngày 26/11 là do Windows thiếu **commit memory** khi nạp model (còn khoảng 7 GB lúc khởi động, còn khoảng 0,9 GB khi API đã chạy). Đã sửa bằng cách nạp hai model trước, nhưng biên vẫn hẹp. Đóng IDE, Docker Desktop, tab trình duyệt trước khi chạy; để pagefile do hệ thống quản lý |
| Chế độ ngủ | Đã tắt cả khi cắm sạc lẫn dùng pin. Kiểm tra thêm hành động khi gập máy |
| Windows Update | Tạm dừng đến hết 01/12 |
| Nguồn và mạng | Luôn cắm sạc; ưu tiên mạng dây; chuẩn bị 4G dự phòng và thử trước |
| Đường upload | Đo speed test ngay tại nơi sẽ chạy laptop |
| Dịch vụ ngoài | Mở dashboard Qdrant Cloud và Supabase, xác nhận project không bị tạm dừng |

**Backup** ra ổ khác (những thứ không nằm trong git):

- `data/processed/products-v1/search/` (khoảng 200 MB) và `data/processed/products-v1/manifest.json`
- `data/admin/` (có `actors.json`, ảnh upload, vector, trạng thái import)
- `.env`, `apps/web/.env.local`, `apps/web/.env.production.local` (cất ở nơi an toàn, không đưa lên cloud công khai)
- Ảnh corpus (8,3 GB), nếu chưa có bản thứ hai
- Một snapshot collection `products_v1_images` trên Qdrant Cloud

## 6. Nghiệm thu

Nếu có tunnel: làm bằng **điện thoại dùng 4G**, qua URL công khai. Nếu chỉ demo trên laptop: làm tại
`http://127.0.0.1:3000` và bỏ các ý về tunnel (cả trong mục 7).

- [ ] Chưa đăng nhập: vào `/` chuyển về `/login` đúng domain công khai, không phải `localhost`.
- [ ] `POST /api/v1/search/text` không có token trả 401.
- [ ] `/docs` không mở được.
- [ ] Đăng ký bằng email mới: nhận mã, xác nhận được (kiểm tra cả Spam).
- [ ] Quên mật khẩu: nhận mã, đặt mật khẩu mới, đăng nhập được.
- [ ] Tài khoản thường mở `/admin` bị chuyển về trang chủ; API admin trả 403.
- [ ] Search có kết quả và ảnh; xem chi tiết và graph quan hệ được. Thử cả truy vấn theo tên, theo mô tả và theo
      địa danh.
- [ ] Admin: upload một ảnh sát giới hạn 10 MiB, xem trước graph, rồi xoá ảnh thử.
- [ ] Đo tải (P1.3) đạt ngưỡng.
- [ ] Tắt hẳn rồi khởi động lại cả ba tiến trình theo script, từ đầu đến khi search được. Ghi lại thời gian.

Upload, ẩn ảnh, release và import **ghi vào Qdrant Cloud và dữ liệu thật**. Chỉ thử bằng dữ liệu biết cách hoàn tác.

## 7. Lịch và ngày demo

| Ngày | Việc | Xong khi |
| --- | --- | --- |
| 27/11 (T6) | Chốt domain và tunnel; P0.1, P0.2 | Có tag `demo-rc1`, build qua trên Next đã vá |
| 28/11 (T7) | P0.3, P0.4, cài tunnel | Điện thoại 4G đăng nhập và search được |
| 29/11 (CN) | P1.1, P1.2, P1.3; backup | Có số đo trước và sau; backup đã mở thử được |
| 30/11 (T2) | Chạy hết mục 6; tổng duyệt hai lần; quay video dự phòng | Checklist đạt; **tối 30/11 đóng băng** code và dữ liệu |
| 01/12 (T3) | Demo | |

Ngày demo, trước giờ trình bày 60 phút: khởi động lại máy → chỉ mở những gì cần → API → web → tunnel → search thử
5 truy vấn bằng điện thoại. Trong lúc demo không release, không import, không sửa code. Demo xong thì tắt tunnel.

Khi có sự cố:

| Sự cố | Xử lý |
| --- | --- |
| API sập khi nạp model | Đóng thêm ứng dụng, kiểm tra commit memory còn trống, chạy lại |
| Tunnel đứt hoặc hỏng | Chạy lại `cloudflared`; chuyển sang quick tunnel; cuối cùng demo trên `localhost:3000` |
| Mạng chính mất | Chuyển sang 4G (quick tunnel sẽ có URL mới nếu phải chạy lại) |
| Qdrant hoặc Supabase lỗi | Phát video dự phòng |
| Lỗi do thay đổi sát giờ | Quay về tag đã đóng băng, build lại |
| Dữ liệu sai sau release | Trỏ `SEARCH_ARTIFACT_DIR` về bản `search-prev-*` chỉ khôi phục được artifact, **không** khôi phục point trên Qdrant và `graph/`. Đóng băng dữ liệu là cách phòng chính |

## 8. Nếu sau này cần đóng container hoặc đưa lên máy chủ

Hiện không cần. Làm theo thứ tự:

1. Khai báo đủ dependency, khoá lại `uv.lock`, chọn bản torch cho đúng máy đích.
2. Sửa Dockerfile: có `scripts/`, `graph/`, extra `encoder`; thêm `output: "standalone"` cho web.
3. Tách dữ liệu ghi được (artifact, `graph/`, `data/admin`) thành volume đọc-ghi. Quyết định cách đưa thay đổi trong
   `graph/` về git khi release chạy trên máy chủ.
4. Đặt `PRODUCTS_IMAGE_ROOT`, `ADMIN_LLM_ENGINE=api`, `GEMINI_API_KEY`.
5. Đo thời gian embed query trên CPU **trước khi** chọn máy. Đo RAM thực tế trong container; con số khoảng 6 GB trên
   Windows chỉ là ước lượng từ commit memory. Không dùng lại cấu hình 8 vCPU / 32 GB của plan cũ khi chưa đo.
6. Chuyển Qdrant về cùng máy (mục 5 của plan cũ; số point phải lấy từ cluster hiện tại, không còn là 30.198).
7. Compose + Caddy, `/ready`, backup, giám sát, CI/CD: dùng lại mục 7–9 của plan cũ, bỏ những service không còn cần.

## 9. Cần chốt

1. **Người xem có tự truy cập không?** Nếu không: demo trên laptop, bỏ qua tunnel, P1.1 và P1.2.
2. Nếu có: đã có **domain** chưa (named hay quick tunnel), và khoảng bao nhiêu người cùng dùng?
3. Bài trình bày có **yêu cầu Docker** không?
