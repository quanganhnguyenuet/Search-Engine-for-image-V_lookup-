# Triển khai đăng ký / đăng nhập trong hôm nay (26/11/2026)

Mục tiêu cuối ngày, chạy trên máy dev (`http://localhost:3000` + API `:8000`):

- Đăng ký bằng email, có email xác nhận gửi từ Gmail.
- Đăng nhập bằng email và bằng Google.
- Quên mật khẩu, đặt lại mật khẩu qua email.
- Chưa đăng nhập thì không search được (API trả 401). Tài khoản `user` vào `/admin` bị chặn (403). `admin` vào được.

Không làm hôm nay: trang admin thật, deploy production, chuyển Qdrant về Docker local. Chi tiết đầy đủ ở
`docs/ke-hoach-deploy-demo-2026-11-26.md` (mục 2–4).

---

## A. Cần chuẩn bị (làm tay, trước khi code)

### A1. Tài khoản

- [ ] Tài khoản Supabase (đăng nhập bằng GitHub được).
- [ ] Một Gmail **riêng cho hệ thống**, ví dụ `vlookup.noreply@gmail.com`, đã bật **2-Step Verification**.
- [ ] Tài khoản Google Cloud Console (dùng chính Gmail trên hoặc Gmail của team).
- [ ] Email sẽ làm **admin đầu tiên**.
- [ ] Một tài khoản Google khác để thử đăng nhập Google, và một email khác để thử đăng ký bằng email.

### A2. Giá trị cần lấy ra (ghi vào trình quản lý mật khẩu, không gửi qua chat)

| Giá trị | Lấy ở đâu | Dùng cho |
| --- | --- | --- |
| P 
Service role key **không cần** hôm nay và không bao giờ đặt trong web.

### A3. Máy dev

- [ ] Node 22+, npm 10+, `uv`, venv Python của dự án chạy được API như README.
- [ ] Luôn mở web bằng `http://localhost:3000` (không dùng `127.0.0.1:3000`): cookie và redirect URL của Supabase gắn
      với đúng host này.
- [ ] Nhánh mới: `git checkout -b feat/auth-supabase`.

---

## B. Các bước trong ngày

| # | Việc | Thời gian ước lượng |
| --- | --- | --- |
| 1 | Cấu hình Supabase | 30 phút |
| 2 | Gmail SMTP + mẫu email tiếng Việt | 20 phút |
| 3 | Google OAuth | 20 phút |
| 4 | SQL: `profiles`, trigger, hook role | 15 phút |
| 5 | Code web | 2 giờ |
| 6 | Code API + test | 1 giờ |
| 7 | Thử toàn bộ luồng | 45 phút |

### Bước 1. Supabase

1. New project, region **Southeast Asia (Singapore)**.
2. **Authentication → JWT Keys**: bật asymmetric signing key (để API verify bằng JWKS). Kiểm tra
   `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json` trả về ít nhất một key.
3. **Authentication → Sessions**: JWT expiry = 900 giây.
4. **Authentication → URL Configuration**:
   - Site URL: `http://localhost:3000` (đổi sang domain thật khi deploy)
   - Redirect URLs: `http://localhost:3000/auth/callback`
5. **Authentication → Providers → Email**: bật "Confirm email".

### Bước 2. Gmail SMTP

1. Gmail hệ thống → Google Account → Security → **App passwords** → tạo một mật khẩu tên "Supabase".
2. Supabase → **Authentication → Emails → SMTP Settings**: host `smtp.gmail.com`, port `465`, username và sender là
   địa chỉ Gmail, password là App Password, sender name `V-Lookup`.
3. **Templates**: dịch "Confirm signup" và "Reset password" sang tiếng Việt, giữ `{{ .ConfirmationURL }}`.
4. Thử: Authentication → Users → Invite user tới email thử. Mail phải tới (xem cả Spam).

### Bước 3. Google OAuth

1. Google Cloud Console → New project `vlookup`.
2. **OAuth consent screen**: External; app name "V-Lookup"; support email; scopes `openid`, `email`, `profile`; thêm
   tài khoản thử vào **Test users** (hôm nay để chế độ Testing là được).
3. **Credentials → Create OAuth client ID → Web application**:
   - Authorized JavaScript origins: `http://localhost:3000`
   - Authorized redirect URIs: `https://<ref>.supabase.co/auth/v1/callback`
4. Supabase → **Authentication → Providers → Google**: bật, dán Client ID và Client Secret.

### Bước 4. SQL

Chạy đoạn SQL ở mục 3.5 của `docs/ke-hoach-deploy-demo-2026-11-26.md` trong Supabase SQL Editor, và lưu vào repo tại
`supabase/migrations/0001_profiles.sql`. Sau đó:

1. **Authentication → Hooks → Customize Access Token (JWT) Claims** → chọn `public.custom_access_token_hook`.
2. Admin đầu tiên đăng ký trước, rồi chạy:
   `update public.profiles set role = 'admin' where email = '<email-admin>';`

### Bước 5. Web (`apps/web`)

1. `npm install @supabase/supabase-js @supabase/ssr`
2. `apps/web/.env.local`:

   ```bash
   NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co
   NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<publishable key>
   NEXT_PUBLIC_SITE_URL=http://localhost:3000
   NEXT_PUBLIC_API_URL=http://localhost:8000
   ```

3. Đọc hướng dẫn trong `node_modules/next/dist/docs/` theo `apps/web/AGENTS.md`. Next 16 dùng `proxy.ts` thay cho
   `middleware.ts`.
4. Tạo file:
   - `lib/supabase/client.ts`, `lib/supabase/server.ts`, `lib/supabase/proxy.ts`, `proxy.ts`
   - `app/login/page.tsx` (email + mật khẩu, nút Google, link quên mật khẩu)
   - `app/signup/page.tsx`, `app/forgot-password/page.tsx`, `app/reset-password/page.tsx`
   - `app/auth/callback/route.ts` (chỉ redirect tới đường dẫn nội bộ)
   - `app/admin/page.tsx` tạm thời: chỉ hiện "Trang quản trị", chặn nếu `user_role !== "admin"`
   - `lib/api.ts` với `authFetch` gắn `Authorization: Bearer`
5. Đổi `fetch` sang `authFetch` trong `lib/search.ts` và `lib/graph.ts`. Thêm email người dùng, nút "Đăng xuất" và nút
   "Quản trị" (chỉ admin) lên header trang search.
6. `npm run typecheck`.

### Bước 6. API (`services/api`)

1. Thêm `pyjwt[crypto]` vào `services/api/pyproject.toml`, `uv lock`, `uv sync`.
2. `Settings`: `supabase_url`, `require_login: bool = True`.
3. `core/auth.py`: `current_user` (verify JWKS, audience `authenticated`, issuer `<url>/auth/v1`, đọc `user_role`) và
   `require_admin`. Code mẫu ở mục 4.1 của plan deploy.
4. Tách `GET /images/{asset_id}` sang `routes/images.py` (public). Gắn `Depends(current_user)` cho search và graph.
   Thêm route thử `GET /api/v1/admin/ping` với `Depends(require_admin)`.
5. Root `.env`: thêm `SUPABASE_URL=https://<ref>.supabase.co` và `REQUIRE_LOGIN=true`.
6. Test: fixture đặt `REQUIRE_LOGIN=false` cho test cũ; test mới với token tự ký (mock JWKS): token đúng → 200, hết hạn
   → 401, thiếu token → 401, `user_role=user` gọi admin → 403. Chạy `make check`.

### Bước 7. Thử toàn bộ luồng

- [ ] Đăng ký bằng email mới → nhận mail xác nhận → bấm link → vào trang search.
- [ ] Đăng xuất, đăng nhập lại bằng email + mật khẩu.
- [ ] Đăng nhập bằng Google (tài khoản nằm trong Test users).
- [ ] Quên mật khẩu → nhận mail → đặt mật khẩu mới → mật khẩu cũ không còn dùng được.
- [ ] Search khi đã đăng nhập: có kết quả. Mở tab ẩn danh gọi thẳng API không kèm token: 401.
- [ ] Tài khoản thường mở `/admin` → bị chuyển về trang chủ; gọi `/api/v1/admin/ping` → 403.
- [ ] Tài khoản admin (sau khi đăng nhập lại) mở `/admin` được; `/api/v1/admin/ping` → 200.
- [ ] Trong Supabase Table Editor, `public.profiles` có đủ dòng cho mọi tài khoản vừa tạo, kể cả tài khoản Google.

Xong thì commit trên `feat/auth-supabase` và mở PR. Không commit `.env`, `.env.local`.

---

## C. Nếu bị kẹt

| Triệu chứng | Nguyên nhân thường gặp |
| --- | --- |
| Bấm link trong email ra trang lỗi hoặc về `/login?error=callback` | Redirect URL chưa có trong danh sách của Supabase, hoặc mở web bằng `127.0.0.1` thay vì `localhost` |
| Không nhận được email | Sai App Password, chưa bật 2-Step Verification, hoặc mail nằm trong Spam |
| Google báo "access blocked" | Tài khoản chưa có trong Test users, hoặc redirect URI trên Google Cloud sai `<ref>` |
| API luôn trả 401 dù đã đăng nhập | JWKS rỗng vì project còn dùng legacy HS256 secret; hoặc sai `SUPABASE_URL` nên lệch issuer |
| Token không có `user_role` | Chưa chọn hook ở Authentication → Hooks, hoặc chưa đăng xuất/đăng nhập lại sau khi đổi role |
| Trình duyệt báo lỗi CORS khi gọi API | Thiếu `http://localhost:3000` trong `CORS_ORIGINS` của API |
