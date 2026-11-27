# Triển khai đăng ký / đăng nhập (từ 26/11, hạn 01/12/2026)

Xác nhận email bằng **mã 6 chữ số** do Supabase tạo và kiểm tra. Chạy trên máy dev (`http://localhost:3000` + API
`:8000`).

**Bắt buộc:**

- Đăng ký bằng email + mật khẩu → nhận mã qua email → nhập mã trên web → tài khoản được xác nhận.
- Đăng nhập, đăng xuất, đổi mật khẩu, quên mật khẩu (bằng mã).
- Chưa đăng nhập thì không search được (web chuyển về `/login`; API trả 401).

**Nếu còn thời gian:** phân quyền `admin`/`user`.

**Không làm:** Send Email Hook / Gmail API, đăng nhập Google, trang admin thật, deploy production, chuyển Qdrant về
Docker local.

---

## A. Cần chuẩn bị

- [x] Tài khoản Supabase (đăng nhập bằng GitHub được).
- [x] Một Gmail để gửi mail, đã bật **2-Step Verification** (nên dùng Gmail riêng cho hệ thống).
- [ ] Một email khác để thử đăng ký.
- [ ] Node 22+, npm 10+, `uv`, venv Python của dự án chạy được API như README.
- [ ] Luôn mở web bằng `http://localhost:3000` (không dùng `127.0.0.1:3000`): cookie của phiên đăng nhập gắn với đúng
      host này.
- [ ] Nhánh mới: `git checkout -b feat/auth-supabase`.

Web chỉ cần **Project URL** và **publishable key** (Dashboard → Connect, hoặc Project Settings → API Keys).
Secret key / service role key **không cần** và không bao giờ đặt trong web.

---

## B. Các bước

| # | Việc | Trạng thái / ngày |
| --- | --- | --- |
| 1 | Cấu hình Supabase | xong |
| 2 | Gmail SMTP (xong) + mẫu email chứa mã | 26/11 |
| 3 | Code web | xong 26/11, chờ thử với email thật |
| 4 | Code API + test | xong 26/11 |
| 5 | Thử toàn bộ luồng | 30/11 |
| 6 | (Nếu còn thời gian) Phân quyền admin | 30/11 |
| | Dự phòng | 01/12 |

### Bước 1. Supabase

1. Project region **Southeast Asia (Singapore)**.
2. **Project Settings → JWT Keys**: dùng asymmetric signing key (để API verify bằng JWKS). Kiểm tra
   `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json` trả về ít nhất một key.
3. **Authentication → Sessions**: JWT expiry = 900 giây.
4. **Authentication → URL Configuration**: Site URL `http://localhost:3000`.
5. **Authentication → Sign In / Providers → Email**: bật "Confirm email"; **Email OTP length** = 6; **Email OTP
   expiration** = 600 giây; **Minimum password length** = 8; bật **Require current password when updating** nếu có.
6. **Authentication → Hooks**: nếu đã bật hook **Send Email** thì tắt đi, nếu không Supabase sẽ không dùng SMTP.

### Bước 2. Gmail SMTP + mẫu email

1. Gmail hệ thống: bật 2-Step Verification, rồi tạo App Password tên `Supabase` tại
   https://myaccount.google.com/apppasswords (16 ký tự).
2. **Authentication → Emails → SMTP Settings**: host `smtp.gmail.com`, port `465`, username và sender là địa chỉ Gmail,
   password là App Password, sender name `V-Lookup`.
3. **Templates → Confirm signup** (subject: `{{ .Token }} là mã xác nhận V-Lookup`). Chỉ hiển thị mã, **không** có link:

   ```html
   <p>Mã xác nhận tài khoản V-Lookup của bạn:</p>
   <p style="font-size:28px;font-weight:bold;letter-spacing:4px">{{ .Token }}</p>
   <p>Mã có hiệu lực trong 10 phút. Nếu bạn không đăng ký, hãy bỏ qua email này.</p>
   ```

4. **Templates → Reset password** (subject: `{{ .Token }} là mã đặt lại mật khẩu V-Lookup`):

   ```html
   <p>Mã đặt lại mật khẩu V-Lookup của bạn:</p>
   <p style="font-size:28px;font-weight:bold;letter-spacing:4px">{{ .Token }}</p>
   <p>Mã có hiệu lực trong 10 phút. Nếu bạn không yêu cầu, hãy bỏ qua email này.</p>
   ```

### Bước 3. Web (`apps/web`) — đã viết

- `apps/web/.env.local` (đã được git bỏ qua): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`,
  `NEXT_PUBLIC_API_URL`.
- `proxy.ts` + `lib/supabase/`: làm mới phiên; chưa đăng nhập thì chuyển về `/login`.
- Trang: `/signup`, `/verify-email` (`verifyOtp` type `email`, gửi lại mã), `/login`, `/forgot-password`
  (mã type `recovery` → mật khẩu mới), `/account/password` (đổi mật khẩu).
- Header trang search: email, "Đổi mật khẩu", "Đăng xuất".
- Hằng số trong `lib/auth.ts` (độ dài mã, độ dài mật khẩu) phải khớp cấu hình trên dashboard.
- `lib/api.ts`: `authFetch` gắn token khi gọi search và graph.

### Bước 4. API (`services/api`) — đã làm

- `core/auth.py`: `current_user` kiểm tra chữ ký bằng JWKS (ES256/RS256), `audience`, `issuer`, hạn dùng; từ chối
  phiên ẩn danh. Thiếu token hoặc token sai → 401; không tải được JWKS hoặc thiếu `SUPABASE_URL` → 503.
- Cần đăng nhập: `/search/text`, `/search/info`, `/graph/subgraph`. Công khai: `/health`, `/images/{asset_id}`
  (thẻ `<img>` không gửi được token, và trang đăng nhập dùng ảnh ví dụ).
- Root `.env`: `SUPABASE_URL`, `REQUIRE_LOGIN=true`.
- Script đánh giá không gửi token: chạy API với `REQUIRE_LOGIN=false`, chỉ trên `127.0.0.1`.
- API kiểm tra token tại chỗ, nên sau khi đăng xuất, token cũ vẫn dùng được tới khi hết hạn (JWT expiry, 15 phút
  nếu đã đặt 900 giây ở bước 1).
- Test: `services/api/tests/test_auth.py`.

### Bước 5. Thử toàn bộ luồng

- [ ] Đăng ký bằng email mới → nhận mã → nhập mã → vào trang search.
- [ ] Nhập mã sai → báo lỗi. "Gửi lại mã" → chỉ mã mới dùng được. Mã quá 10 phút → bị từ chối.
- [ ] Đăng nhập khi chưa xác nhận → được đưa tới trang nhập mã.
- [ ] Đăng xuất, đăng nhập lại bằng email + mật khẩu.
- [ ] Đổi mật khẩu: sai mật khẩu hiện tại → bị từ chối; đúng → mật khẩu cũ không còn dùng được.
- [ ] Quên mật khẩu → nhận mã → đặt mật khẩu mới → đăng nhập bằng mật khẩu mới.
- [ ] Mở tab ẩn danh vào `http://localhost:3000` → chuyển về `/login`.
- [ ] Gọi thẳng API không kèm token: 401. Đăng nhập rồi search: có kết quả.

Xong thì commit trên `feat/auth-supabase` và mở PR. Không commit `.env`, `.env.local`.

### Bước 6. (Nếu còn thời gian) Phân quyền

Chạy SQL ở mục 3.5 của plan deploy, lưu tại `supabase/migrations/0001_profiles.sql`; chọn hook ở **Authentication →
Hooks → Customize Access Token (JWT) Claims**; thêm `require_admin`, route thử `GET /api/v1/admin/ping` và trang
`/admin` tạm thời. Người đăng ký trước khi có trigger phải được thêm vào `public.profiles` bằng tay.

---

## C. Nếu bị kẹt

| Triệu chứng | Nguyên nhân thường gặp |
| --- | --- |
| Không nhận được email | Sai App Password, chưa bật 2-Step Verification, hook Send Email còn bật, hoặc mail nằm trong Spam. Xem Logs → Auth |
| Email có link, không có mã | Template chưa dùng `{{ .Token }}`, hoặc chưa bấm Save |
| Mã luôn bị báo sai | OTP length trên dashboard không phải 6, nhập mã cũ sau khi gửi lại, hoặc quá 10 phút |
| API luôn trả 401 dù đã đăng nhập | JWKS rỗng vì project còn dùng legacy HS256 secret; hoặc sai `SUPABASE_URL` nên lệch issuer |
| Trình duyệt báo lỗi CORS khi gọi API | Thiếu `http://localhost:3000` trong `CORS_ORIGINS` của API |
