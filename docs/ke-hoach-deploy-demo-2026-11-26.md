# Kế hoạch deploy V-Lookup cho demo

Ngày lập: 26/11/2026. Ngày demo giả định: 24/12/2026 (cần chốt).

Tài liệu này là plan triển khai cụ thể cho team dev và DevOps. Nó thay phần 3 (phân quyền) và phần 8 (DevOps)
của doc "Kế hoạch deploy V-Lookup cho demo" ở các điểm sau:

| Điểm | Plan cũ | Plan này |
| --- | --- | --- |
| Đăng ký, đăng nhập | FastAPI tự quản: argon2, JWT, refresh token, CSRF, TOTP | **Supabase Auth**: email + mật khẩu, **Đăng nhập bằng Google**, quên mật khẩu qua email gửi từ **Gmail SMTP** |
| Role | 4 role (user, curator, pharmacist, admin) | **2 role**: `user`, `admin`. Còn chỗ để thêm `pharmacist` sau |
| Lưu role ở đâu | Bảng `users` trong Postgres riêng | Bảng `public.profiles` trên Supabase; role được nhúng vào JWT bằng Custom Access Token Hook |
| Postgres của ứng dụng | Container Postgres riêng | Dùng luôn Postgres của Supabase (profiles, assets, audit). Bỏ container Postgres |
| Qdrant | Self-host cạnh API | **Qdrant chạy bằng Docker trên chính máy chạy API**, gọi qua mạng nội bộ Docker (gRPC). Bỏ Qdrant Cloud Seoul |
| Ảnh | Object storage + CDN | Demo: ảnh nằm trên ổ đĩa của máy, API trả ảnh, Cloudflare cache. Object storage để sau |

---

## 1. Kiến trúc

```text
                         Internet
                            │
                 Cloudflare (DNS, TLS, cache ảnh, WAF)
                            │  (port 443, hoặc Cloudflare Tunnel nếu máy ở văn phòng/nhà)
┌───────────────────────── Máy chủ (Docker Compose) ─────────────────────────┐
│                                                                              │
│   caddy ──/──────────► web (Next.js 16, standalone)                          │
│     │                                                                        │
│     └──/api/*────────► api (FastAPI) ──gRPC :6334──► qdrant (local Docker)   │
│                          │   │                                               │
│                          │   └── graph release nạp sẵn trong RAM             │
│                          │                                                   │
│                          ├──► redis ◄── worker (job thêm ảnh, xuất release)  │
│                          └──► neo4j (chỉ route /admin và worker dùng)        │
│                                                                              │
│   Volume: qdrant_storage, neo4j_data, /srv/vlookup/data (ảnh, artifact)     │
└──────────────────────────────────────────────────────────────────────────────┘
          │                                        ▲
          │ verify JWT bằng JWKS (cache 1 giờ)     │ đăng ký / đăng nhập / quên mật khẩu
          ▼                                        │ (trình duyệt gọi thẳng Supabase)
   Supabase (region Singapore): Auth + Postgres (profiles, assets, audit_log)
          │
          ├── Google OAuth (Đăng nhập bằng Google)
          └── Gmail SMTP (email xác nhận, email đặt lại mật khẩu)
```

Nguyên tắc:

- Search của user chỉ đi qua `api` → `qdrant` trong cùng máy. Mỗi request **không** gọi Supabase: JWT được
  verify tại chỗ bằng public key (JWKS) đã cache.
- Chỉ `caddy` mở port ra ngoài. Qdrant, Neo4j, Redis không publish port.
- Trình duyệt nói chuyện trực tiếp với Supabase cho mọi thao tác tài khoản. API của mình không bao giờ thấy mật khẩu.

---

## 2. Luồng tài khoản

| Luồng | Người dùng làm | Kỹ thuật |
| --- | --- | --- |
| Đăng ký bằng email | Nhập email + mật khẩu ở `/signup` → nhận email xác nhận → bấm link | `supabase.auth.signUp({ email, password, options: { emailRedirectTo: SITE_URL + "/auth/callback" } })`. Email gửi qua Gmail SMTP. Link về `/auth/callback`, route đổi `code` lấy session |
| Đăng nhập bằng email | `/login`, nhập email + mật khẩu | `supabase.auth.signInWithPassword(...)` |
| Đăng nhập bằng Google | Bấm "Đăng nhập bằng Google" | `supabase.auth.signInWithOAuth({ provider: "google", options: { redirectTo: SITE_URL + "/auth/callback" } })`. Lần đầu đăng nhập là tự tạo tài khoản |
| Quên mật khẩu | `/forgot-password`, nhập email → nhận email → bấm link → `/reset-password` nhập mật khẩu mới | `resetPasswordForEmail(email, { redirectTo: SITE_URL + "/auth/callback?next=/reset-password" })`, rồi `updateUser({ password })` |
| Đăng xuất | Bấm "Đăng xuất" | `supabase.auth.signOut()` |
| Gán admin | Admin hiện tại chạy 1 câu SQL (mục 3.5) | Role nằm trong `profiles.role`, được hook nhúng vào JWT ở lần cấp token tiếp theo |

Quyền:

| Chức năng | Chưa đăng nhập | `user` | `admin` |
| --- | --- | --- | --- |
| Trang đăng nhập, đăng ký, quên mật khẩu | có | có | có |
| Search, xem ảnh, xem quan hệ entity | không (chuyển về `/login`) | có | có |
| `/admin`: thêm ảnh, sửa graph và quan hệ, chạy làm giàu dữ liệu, release | không | không (403) | có |

Ảnh (`/api/v1/images/{asset_id}`) để public, vì thẻ `<img>` không gửi được header `Authorization` và đây là ảnh sản phẩm
công khai. Mọi route khác dưới `/api/v1` đều cần đăng nhập, trừ `/health`.

---

## 3. Cấu hình Supabase (làm một lần)

### 3.1 Tạo project

1. Tạo project mới, region **Southeast Asia (Singapore)**.
2. Lưu lại: `Project URL` (`https://<ref>.supabase.co`), publishable/anon key, service role key (chỉ dùng phía server),
   connection string Postgres (dùng pooler).
3. **Authentication → JWT Keys** (Signing keys): bật **asymmetric signing key** (ECC P-256 / RS256). Backend verify bằng
   JWKS tại `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`. Nếu project còn dùng legacy HS256 secret thì JWKS
   trả về rỗng; cần chuyển sang signing key mới trước. Kiểm tra lại đường dẫn JWKS trong
   [tài liệu JWT](https://supabase.com/docs/guides/auth/jwts) và [signing keys](https://supabase.com/docs/guides/auth/signing-keys).
4. **Authentication → Sessions / JWT expiry**: đặt **900 giây** (15 phút), để khi hạ quyền admin thì token cũ hết hạn nhanh.

### 3.2 URL

**Authentication → URL Configuration**:

- Site URL: `https://vlookup.<domain>`
- Redirect URLs:
  - `https://vlookup.<domain>/auth/callback`
  - `https://staging.vlookup.<domain>/auth/callback`
  - `http://localhost:3000/auth/callback`

### 3.3 Gmail SMTP (email xác nhận và đặt lại mật khẩu)

Email mặc định của Supabase chỉ dùng để thử: giới hạn khoảng **2 email/giờ**
([rate limits](https://supabase.com/docs/guides/auth/rate-limits), [SMTP](https://supabase.com/docs/guides/auth/auth-smtp)).
Demo cần SMTP riêng.

1. Tạo một Gmail riêng cho hệ thống, ví dụ `vlookup.noreply@gmail.com`. Không dùng Gmail cá nhân.
2. Bật **2-Step Verification** cho tài khoản đó, rồi tạo **App Password** (16 ký tự).
3. Supabase → **Authentication → Emails → SMTP Settings**:

   | Trường | Giá trị |
   | --- | --- |
   | Host | `smtp.gmail.com` |
   | Port | `465` |
   | Username | `vlookup.noreply@gmail.com` |
   | Password | App Password ở bước 2 |
   | Sender email | `vlookup.noreply@gmail.com` |
   | Sender name | `V-Lookup` |

4. **Authentication → Rate Limits**: với custom SMTP, mặc định khoảng 30 email/giờ. Đủ cho demo; tăng nếu cần.
5. **Authentication → Emails → Templates**: dịch sang tiếng Việt hai mẫu "Confirm signup" và "Reset password". Giữ nguyên
   biến `{{ .ConfirmationURL }}`.
6. **Authentication → Providers → Email**: bật "Confirm email".

Gmail cá nhân có giới hạn gửi mỗi ngày (cỡ vài trăm email, theo chính sách của Google). Đủ cho demo. Nếu mở cho nhiều
người dùng thật thì chuyển sang Resend, Brevo hoặc SES với domain riêng.

### 3.4 Đăng nhập bằng Google

1. Google Cloud Console → tạo project `vlookup`.
2. **OAuth consent screen**: loại External; tên app "V-Lookup"; support email; scopes `openid`, `email`, `profile`.
   - Khi ở trạng thái **Testing**, chỉ các tài khoản trong danh sách test user đăng nhập được.
   - Trước demo, bấm **Publish app** (chuyển sang In production) để mọi tài khoản Google đều đăng nhập được. Với 3 scope
     cơ bản này thường không cần Google xét duyệt.
3. **Credentials → Create OAuth client ID → Web application**:
   - Authorized JavaScript origins: `https://vlookup.<domain>`, `http://localhost:3000`
   - Authorized redirect URIs: `https://<ref>.supabase.co/auth/v1/callback`
4. Supabase → **Authentication → Providers → Google**: bật, dán Client ID và Client Secret.

Người đã đăng ký bằng email rồi đăng nhập bằng Google cùng địa chỉ (đã xác nhận) sẽ được gộp vào cùng một tài khoản.

### 3.5 SQL: profiles, role, hook

Chạy trong **SQL Editor**, lưu thành migration `supabase/migrations/0001_profiles.sql` trong repo.

```sql
-- 1. Bảng profile, mỗi user một dòng
create table public.profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  email      text not null,
  full_name  text,
  role       text not null default 'user' check (role in ('user', 'admin')),
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- User chỉ đọc được profile của mình. KHÔNG có policy insert/update/delete:
-- user không thể tự đổi role của mình qua API của Supabase.
create policy "profiles: read own"
  on public.profiles for select to authenticated
  using ((select auth.uid()) = id);

-- 2. Tự tạo profile khi có user mới (cả email lẫn Google)
create function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.profiles (id, email, full_name)
  values (new.id, new.email, new.raw_user_meta_data ->> 'full_name');
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- 3. Hook nhúng role vào JWT (claim "user_role")
create function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
as $$
declare
  claims    jsonb;
  user_role text;
begin
  select role into user_role from public.profiles where id = (event ->> 'user_id')::uuid;
  claims := event -> 'claims';
  claims := jsonb_set(claims, '{user_role}', to_jsonb(coalesce(user_role, 'user')));
  return jsonb_set(event, '{claims}', claims);
end;
$$;

grant usage on schema public to supabase_auth_admin;
grant execute on function public.custom_access_token_hook to supabase_auth_admin;
revoke execute on function public.custom_access_token_hook from authenticated, anon, public;
grant select on table public.profiles to supabase_auth_admin;
create policy "profiles: auth admin reads"
  on public.profiles for select to supabase_auth_admin
  using (true);
```

Sau đó vào **Authentication → Hooks → Customize Access Token (JWT) Claims**, chọn `public.custom_access_token_hook`.

Gán admin đầu tiên (người đó đăng ký trước, rồi chạy):

```sql
update public.profiles set role = 'admin' where email = '<email-admin>';
```

Người đó đăng xuất rồi đăng nhập lại để nhận token có `user_role = admin`.

### 3.6 Bảng cho phần admin (cũng trên Supabase Postgres)

```sql
create table public.audit_log (
  id          bigint generated always as identity primary key,
  actor_id    uuid not null references auth.users (id),
  action      text not null,          -- ví dụ 'asset.upload', 'edge.verify', 'release.publish'
  target_type text not null,
  target_id   text not null,
  before      jsonb,
  after       jsonb,
  request_id  text,
  at          timestamptz not null default now()
);
alter table public.audit_log enable row level security;  -- không policy nào: chỉ backend ghi/đọc
```

Bảng `assets` theo mục 4.3 của doc plan. Bật RLS và không tạo policy cho mọi bảng ứng dụng: chỉ backend truy cập, qua
connection string Postgres (pooler) với user riêng, không qua key của trình duyệt.

---

## 4. Thay đổi code

### 4.1 API (FastAPI)

Thêm dependency: `pyjwt[crypto]` vào `services/api/pyproject.toml`; `asyncpg` (hoặc `psycopg[binary]`) cho các route admin.

Thêm vào `Settings` (`services/api/src/visualseek_api/core/config.py`):

```python
supabase_url: str = ""            # https://<ref>.supabase.co
supabase_db_url: str = ""         # postgres pooler, chỉ route admin và worker dùng
require_login: bool = True        # False khi chạy test và dev cục bộ
```

File mới `services/api/src/visualseek_api/core/auth.py`:

```python
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, Request, status
from jwt import PyJWKClient

from visualseek_api.core.config import get_settings
from visualseek_api.core.errors import ApplicationError


@dataclass(frozen=True)
class User:
    id: str
    email: str | None
    role: str  # "user" | "admin"


@lru_cache
def _jwks() -> PyJWKClient:
    url = f"{get_settings().supabase_url}/auth/v1/.well-known/jwks.json"
    return PyJWKClient(url, cache_keys=True, lifespan=3600)


def current_user(request: Request) -> User:
    settings = get_settings()
    if not settings.require_login:
        return User(id="dev", email=None, role="admin")
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise ApplicationError(
            "Cần đăng nhập.", code="unauthenticated", status_code=status.HTTP_401_UNAUTHORIZED
        )
    token = header[7:]
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            issuer=f"{settings.supabase_url}/auth/v1",
        )
    except jwt.PyJWTError as error:
        raise ApplicationError(
            "Phiên đăng nhập không hợp lệ hoặc đã hết hạn.",
            code="invalid_token",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from error
    request.state.user_id = claims["sub"]
    return User(id=claims["sub"], email=claims.get("email"), role=claims.get("user_role", "user"))


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise ApplicationError(
            "Bạn không có quyền admin.", code="forbidden", status_code=status.HTTP_403_FORBIDDEN
        )
    return user
```

Gắn vào router (`api/router.py`):

```python
from fastapi import Depends
from visualseek_api.core.auth import current_user, require_admin

api_router.include_router(health_router, tags=["system"])                     # public
api_router.include_router(images_router, tags=["images"])                     # public (thẻ <img>)
api_router.include_router(search_router, tags=["search"], dependencies=[Depends(current_user)])
api_router.include_router(graph_router, tags=["graph"], dependencies=[Depends(current_user)])
api_router.include_router(admin_router, prefix="/admin", tags=["admin"],
                          dependencies=[Depends(require_admin)])
```

- Tách route `GET /images/{asset_id}` ra khỏi `routes/search.py` thành `routes/images.py` để để public.
- Thêm `user_id` vào structlog context trong `RequestContextMiddleware` (đọc từ `request.state.user_id`).
- `tests/`: đặt `REQUIRE_LOGIN=false` trong fixture; thêm test cho `current_user` với token ký bằng một khoá EC sinh tại
  chỗ (mock `_jwks`): token đúng → 200, hết hạn → 401, sai audience → 401, `user_role=user` gọi admin → 403.
- `/ready`: model đã load, Qdrant trả lời. Giữ `/health` như hiện tại.
- Bỏ CORS ở production (`CORS_ORIGINS` rỗng), vì web và API chung domain.

### 4.2 Web (Next.js 16)

Đọc `apps/web/AGENTS.md`: Next 16 có thay đổi lớn, phải đối chiếu `node_modules/next/dist/docs/` trước khi viết code. Trong
Next 16, `middleware.ts` đổi tên thành `proxy.ts` (export `proxy`). Supabase đã cập nhật hướng dẫn theo tên mới
([Supabase SSR cho Next.js](https://supabase.com/docs/guides/auth/server-side/nextjs)).

```bash
npm --prefix apps/web install @supabase/supabase-js @supabase/ssr
```

Biến môi trường (build-time, đều public):

```bash
NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<publishable hoặc anon key>
NEXT_PUBLIC_SITE_URL=https://vlookup.<domain>
NEXT_PUBLIC_API_URL=                      # rỗng: gọi /api cùng domain
```

Service role key **không bao giờ** nằm trong web.

File mới:

| File | Việc |
| --- | --- |
| `lib/supabase/client.ts` | `createBrowserClient(url, key)` |
| `lib/supabase/server.ts` | `createServerClient` với `cookies()` (`getAll`/`setAll`) |
| `lib/supabase/proxy.ts` | `updateSession(request)`: làm mới session, chuyển người chưa đăng nhập về `/login` |
| `proxy.ts` (gốc app) | Gọi `updateSession`; `matcher` bỏ qua `_next`, ảnh tĩnh, `/auth/*`, `/login`, `/signup`, `/forgot-password` |
| `app/login/page.tsx` | Form email + mật khẩu, nút "Đăng nhập bằng Google", link quên mật khẩu |
| `app/signup/page.tsx` | Form đăng ký, báo "kiểm tra email để xác nhận" |
| `app/forgot-password/page.tsx` | Nhập email, gọi `resetPasswordForEmail` |
| `app/reset-password/page.tsx` | Nhập mật khẩu mới, gọi `updateUser({ password })` |
| `app/auth/callback/route.ts` | Đổi `code` lấy session, chuyển tiếp tới `next` |
| `app/admin/layout.tsx` | Server component: `supabase.auth.getClaims()`; `user_role !== "admin"` → `redirect("/")` |
| `lib/api.ts` | `authFetch`: gắn `Authorization: Bearer <access_token>` cho mọi lời gọi API |

`app/auth/callback/route.ts`:

```ts
import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL!;

export async function GET(request: Request) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const next = url.searchParams.get("next") ?? "/";
  // Chỉ chấp nhận đường dẫn nội bộ, tránh open redirect.
  const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/";
  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(`${SITE_URL}${safeNext}`);
  }
  return NextResponse.redirect(`${SITE_URL}/login?error=callback`);
}
```

Dùng `NEXT_PUBLIC_SITE_URL` thay cho `url.origin`: sau Caddy, `request.url` có thể mang scheme `http` hoặc host nội bộ.

`lib/api.ts`:

```ts
import { createClient } from "@/lib/supabase/client";

export async function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const { data } = await createClient().auth.getSession();
  const headers = new Headers(init.headers);
  if (data.session) headers.set("Authorization", `Bearer ${data.session.access_token}`);
  const response = await fetch(input, { ...init, headers });
  if (response.status === 401) window.location.assign("/login");
  return response;
}
```

Thay `fetch` bằng `authFetch` trong `lib/search.ts` (`searchText`) và `lib/graph.ts` (`fetchSubgraph`). Thêm nút đăng
xuất và tên người dùng ở header của `search-page.tsx`; nút "Quản trị" chỉ hiện khi `user_role === "admin"`.

### 4.3 Dockerfile

`services/api/Dockerfile` hiện **không cài extra `encoder`**, nên container lỗi khi `SEARCH_ENABLED=true`. Sửa:

```dockerfile
FROM ghcr.io/astral-sh/uv:0.6.9 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    PATH="/workspace/.venv/bin:$PATH" HF_HOME=/models HF_HUB_OFFLINE=1
WORKDIR /workspace
COPY pyproject.toml uv.lock README.md ./
COPY packages ./packages
COPY services/api ./services/api
# torch bản CPU; nếu máy có GPU NVIDIA thì bỏ index này và dùng bản CUDA khớp driver
RUN UV_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu \
    uv sync --frozen --package visualseek-api --extra encoder --no-dev
RUN useradd -r -u 10001 app && chown -R app /workspace
USER app
EXPOSE 8000
CMD ["uvicorn", "visualseek_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

Lưu ý: `uv.lock` phải khoá được bản torch CPU. Nếu `uv sync --frozen` báo lệch lock, thêm cấu hình `[tool.uv.sources]`
cho torch trỏ tới index CPU rồi `uv lock` lại.

Model (bge-m3 revision `5617a9f`, SigLIP2) tải sẵn **một lần** vào volume `/srv/vlookup/models`, mount vào `/models`.
`HF_HUB_OFFLINE=1` để container không tải lại khi khởi động.

`apps/web/next.config.ts`: thêm `output: "standalone"`. `apps/web/Dockerfile` stage production chép
`.next/standalone` và `.next/static`, chạy `node server.js`.

---

## 5. Qdrant trên máy local bằng Docker

Chạy Qdrant trong cùng Docker Compose với API là cách deploy hợp lệ. API gọi `qdrant:6334` (gRPC) qua mạng nội bộ của
Docker, nên thời gian mạng mỗi lần search chỉ còn cỡ dưới 1 ms. Trước đây gọi Qdrant Cloud ở Seoul đo được khoảng 170 ms.

### 5.1 Chọn phiên bản

- Lấy phiên bản của cluster Cloud hiện tại: `curl -H "api-key: $QDRANT_CLOUD_KEY" $QDRANT_CLOUD_URL/` (trường `version`).
- Image local dùng **cùng phiên bản hoặc mới hơn**, để khôi phục snapshot được.
- `compose.yaml` của dev đang ghim `qdrant/qdrant:v1.13.4`, trong khi `qdrant-client==1.20.0`. Client cảnh báo khi hai bên
  lệch nhiều phiên bản. Nâng image cho khớp client, và cập nhật luôn `compose.yaml`.

### 5.2 Chuyển dữ liệu từ Cloud về local

Cách A, snapshot (nhanh, giữ nguyên dữ liệu):

```bash
COL=products_v1_images
# 1. Tạo và tải snapshot từ Cloud
curl -X POST "$QDRANT_CLOUD_URL/collections/$COL/snapshots" -H "api-key: $QDRANT_CLOUD_KEY"
#    -> lấy "name" trong kết quả
curl -H "api-key: $QDRANT_CLOUD_KEY" -o $COL.snapshot \
     "$QDRANT_CLOUD_URL/collections/$COL/snapshots/<name>"

# 2. Khôi phục vào Qdrant local (mở tạm cổng 127.0.0.1:6333 khi khôi phục, xem 5.3)
curl -X POST "http://127.0.0.1:6333/collections/$COL/snapshots/upload?priority=snapshot" \
     -H "api-key: $QDRANT_API_KEY" -F "snapshot=@$COL.snapshot"
```

Cách B, dựng lại từ artifact (khi đã có vector trong `data/processed/products-v1`):

```bash
QDRANT_URL=http://127.0.0.1:6333 QDRANT_API_KEY=$QDRANT_API_KEY python scripts/qdrant_setup.py
QDRANT_URL=http://127.0.0.1:6333 QDRANT_API_KEY=$QDRANT_API_KEY python scripts/qdrant_add_image_vectors.py
```

Kiểm tra sau khi chuyển:

```bash
curl -s -H "api-key: $QDRANT_API_KEY" http://127.0.0.1:6333/collections/$COL | jq '.result.points_count'
# phải bằng 30198 (corpus products-v1, locks/products-v1-corpus.json)
curl -s https://vlookup.<domain>/api/v1/search/info -H "Authorization: Bearer $TOKEN" | jq '.qdrant_transport'
# phải là grpc
python scripts/bench_latency.py   # so p50/p95 trước và sau khi chuyển
```

Giữ cluster Cloud thêm 1 tuần làm dự phòng, sau đó xoá.

### 5.3 Cấu hình trong Compose

- Không publish port nào. Khi cần debug hoặc khôi phục: tạm bật `ports: ["127.0.0.1:6333:6333"]`, chỉ bind localhost.
- Vẫn đặt `QDRANT__SERVICE__API_KEY`, dù chỉ trong mạng nội bộ.
- Snapshot hằng ngày bằng cron trên máy chủ (mục 8).

---

## 6. Ảnh

Bản demo không dùng object storage:

- Thư mục ảnh corpus (8,3 GB, 30.198 file) chép lên máy chủ tại `/srv/vlookup/data/images`, mount **read-only** vào `api`.
  Đặt `PRODUCTS_IMAGE_ROOT=/data/images`.
- Ảnh admin upload ghi vào `/srv/vlookup/data/uploads/{aa}/{asset_id}.{ext}` (volume ghi được, chỉ `api` và `worker`
  mount). API nhận multipart, giới hạn 15 MB. Worker kiểm tra ảnh, xoá EXIF, tính sha256, embed, upsert Qdrant.
- `GET /api/v1/images/{asset_id}` trả ảnh với `Cache-Control: public, max-age=31536000, immutable` (`asset_id` là sha256,
  nội dung không đổi). Thêm **Cloudflare Cache Rule** cho đường dẫn `/api/v1/images/*`, vì URL không có đuôi file nên
  Cloudflare không tự cache.
- Khi ảnh tăng nhiều hoặc cần nhiều máy: chuyển sang Supabase Storage hoặc Cloudflare R2. Chỉ đổi phần `image_file()`
  và URL trả về.

---

## 7. File deploy

### 7.1 Cấu hình máy chủ

| Hạng mục | Tối thiểu cho demo |
| --- | --- |
| CPU / RAM | 8 vCPU / 32 GB (API khoảng 6–8 GB, worker 4–6 GB, Neo4j 3 GB, Qdrant 1 GB) |
| Ổ đĩa | SSD 200 GB (ảnh 8,3 GB, model khoảng 5 GB, image Docker, snapshot, log) |
| OS | Ubuntu 24.04 LTS, Docker Engine + Compose plugin |
| GPU | Không bắt buộc. Nếu `bench_latency.py` trên CPU vượt ngân sách 60 ms cho bước embed query thì dùng GPU hoặc ONNX int8 |
| Mạng | Chỉ mở 80/443 (hoặc không mở port nào nếu dùng Cloudflare Tunnel). SSH qua Tailscale |

Thư mục trên máy chủ:

```text
/srv/vlookup/
  compose.prod.yaml
  Caddyfile
  .env                 # chmod 600, không commit
  data/images/         # ảnh corpus (read-only)
  data/uploads/        # ảnh admin thêm
  data/releases/       # artifact search + graph theo release
  models/              # cache HuggingFace
  backups/
```

### 7.2 `compose.prod.yaml`

```yaml
name: vlookup

x-api-env: &api-env
  APP_ENV: production
  LOG_LEVEL: INFO
  SEARCH_ENABLED: "true"
  SEARCH_EMBED_DEVICE: cpu
  SEARCH_ARTIFACT_DIR: /data/releases/current/search
  PRODUCTS_IMAGE_ROOT: /data/images
  QDRANT_URL: http://qdrant:6333
  QDRANT_PREFER_GRPC: "true"
  QDRANT_API_KEY: ${QDRANT_API_KEY}
  SUPABASE_URL: ${SUPABASE_URL}
  SUPABASE_DB_URL: ${SUPABASE_DB_URL}
  REQUIRE_LOGIN: "true"
  CORS_ORIGINS: ""
  REDIS_URL: redis://redis:6379/0
  NEO4J_URI: bolt://neo4j:7687
  NEO4J_AUTH: ${NEO4J_AUTH}

services:
  caddy:
    image: caddy:2.8
    restart: unless-stopped
    ports: ["80:80", "443:443"]          # bỏ nếu dùng Cloudflare Tunnel
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
    depends_on: [web, api]

  web:
    image: ghcr.io/<org>/vlookup-web:${IMAGE_TAG}
    restart: unless-stopped
    environment:
      NODE_ENV: production

  api:
    image: ghcr.io/<org>/vlookup-api:${IMAGE_TAG}
    restart: unless-stopped
    environment: *api-env
    volumes:
      - ./data/images:/data/images:ro
      - ./data/uploads:/data/uploads
      - ./data/releases:/data/releases:ro
      - ./models:/models:ro
    depends_on: [qdrant, redis]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/ready')"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 180s

  worker:
    image: ghcr.io/<org>/vlookup-api:${IMAGE_TAG}
    restart: unless-stopped
    command: ["arq", "visualseek_api.worker.WorkerSettings"]
    environment: *api-env
    volumes:
      - ./data/uploads:/data/uploads
      - ./data/releases:/data/releases
      - ./models:/models:ro
    depends_on: [qdrant, redis, neo4j]

  qdrant:
    image: qdrant/qdrant:<phiên bản chốt ở mục 5.1>
    restart: unless-stopped
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY}
    volumes:
      - qdrant_storage:/qdrant/storage
      - ./backups/qdrant:/qdrant/snapshots
    # không publish port

  neo4j:
    image: neo4j:5.26.33
    restart: unless-stopped
    environment:
      NEO4J_AUTH: ${NEO4J_AUTH}
      NEO4J_server_memory_heap_max__size: 2G
      NEO4J_server_memory_pagecache_size: 1G
    volumes:
      - neo4j_data:/data
    # Neo4j Browser chỉ mở qua Tailscale: ports: ["100.x.y.z:7474:7474"]

  redis:
    image: redis:7.4-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis_data:/data

volumes:
  caddy_data:
  qdrant_storage:
  neo4j_data:
  redis_data:
```

Các biến `NEXT_PUBLIC_*` được nhúng lúc build image web trong CI (mục 7.5), không đặt lúc chạy.

### 7.3 `Caddyfile`

```caddyfile
vlookup.<domain> {
	encode zstd gzip

	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
		Referrer-Policy "strict-origin-when-cross-origin"
		-Server
	}

	request_body /api/v1/admin/* {
		max_size 16MB
	}

	handle /api/* {
		reverse_proxy api:8000 {
			health_uri /api/v1/ready
		}
	}

	handle {
		reverse_proxy web:3000
	}
}
```

Thêm `Content-Security-Policy` sau khi đã liệt kê đủ nguồn: `connect-src 'self' https://<ref>.supabase.co`, và
`accounts.google.com` cho luồng OAuth. Thử trên staging trước khi bật ở demo.

### 7.4 `.env` trên máy chủ (mẫu, không chứa giá trị thật)

```bash
IMAGE_TAG=<git-sha>
QDRANT_API_KEY=<chuỗi ngẫu nhiên 32 byte>
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_DB_URL=postgresql://<user-backend>:<mật-khẩu>@<pooler-host>:5432/postgres
NEO4J_AUTH=neo4j/<mật khẩu ≥ 8 ký tự>
GEMINI_API_KEY=<chỉ worker dùng, cho job làm giàu>
```

Sinh khoá: `openssl rand -base64 32`. File để `chmod 600`, chủ sở hữu là user deploy.

### 7.5 CI/CD (GitHub Actions)

`.github/workflows/deploy.yml`, ba job:

1. **check** (mọi PR và push): `uv sync`, `make check`.
2. **build** (push lên `main`):
   - build `services/api/Dockerfile` → `ghcr.io/<org>/vlookup-api:<sha>`;
   - build `apps/web/Dockerfile` (target production) với build args `NEXT_PUBLIC_SUPABASE_URL`,
     `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_SITE_URL` → `ghcr.io/<org>/vlookup-web:<sha>`;
   - quét bằng Trivy, lỗi CRITICAL thì dừng.
3. **deploy** (GitHub Environment `staging` tự động; `demo` cần người duyệt):

   ```bash
   cd /srv/vlookup
   sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$SHA/" .env
   docker compose -f compose.prod.yaml pull
   docker compose -f compose.prod.yaml up -d
   ./scripts/smoke.sh   # mục 9; lỗi thì đặt lại IMAGE_TAG cũ và up -d
   ```

   Máy chủ trên cloud: deploy qua SSH (Tailscale). Máy đặt tại văn phòng hoặc nhà, sau NAT: dùng **self-hosted runner**
   của GitHub cài trên chính máy đó.

API mất 1–2 phút để load model, nên mỗi lần deploy có khoảng 2 phút gián đoạn. Chấp nhận được với demo. Đóng băng
deploy 48 giờ trước giờ demo.

### 7.6 Nếu "máy local" là máy đặt tại văn phòng hoặc nhà

- Dùng **Cloudflare Tunnel** (`cloudflared` chạy như một service trong compose, trỏ tới `caddy:80`). Không cần mở port
  trên router, không cần IP tĩnh. Khi đó Caddy chỉ cần nghe HTTP; Cloudflare lo TLS.
- Cắm UPS. Tắt chế độ ngủ của máy. Bật `restart: unless-stopped` (đã có) và cho Docker tự chạy khi khởi động máy.
- Kiểm tra tốc độ upload của đường mạng: mỗi trang kết quả tải khoảng 20 ảnh, trung bình khoảng 275 KB mỗi ảnh. Cache
  của Cloudflare (mục 6) giảm tải cho đường lên.

---

## 8. Backup và giám sát

| Gì | Cách | Khi nào | Giữ |
| --- | --- | --- | --- |
| Qdrant | `curl -X POST -H "api-key: $QDRANT_API_KEY" http://qdrant:6333/collections/products_v1_images/snapshots` (chạy trong container `api` hoặc qua `docker compose exec`) | 03:00 hằng ngày | 7 bản |
| Neo4j | Dừng Neo4j, `neo4j-admin database dump neo4j --to-path=/backups`, chạy lại (bản Community phải dừng DB khi dump) | 03:30 hằng ngày | 14 bản |
| Supabase | Backup tự động theo gói của Supabase; thêm `pg_dump` các bảng `public.*` mỗi ngày | 04:00 | 14 bản |
| Ảnh upload, release | `restic` hoặc `rclone` thư mục `data/uploads`, `data/releases` sang ổ khác hoặc cloud | Hằng ngày | 30 ngày |
| `.env`, Caddyfile | Lưu trong trình quản lý mật khẩu của team | Khi đổi | |

Thử khôi phục toàn bộ lên một máy trống ít nhất một lần trước demo.

Giám sát tối thiểu cho demo:

- Uptime Kuma (hoặc Better Stack) gọi `https://vlookup.<domain>/api/v1/health` mỗi phút, báo qua Telegram hoặc email.
- `docker compose logs` chuyển sang JSON. Log có `request_id` và `user_id`, không có token.
- Sentry cho web và API (tuỳ chọn, gói miễn phí đủ cho demo).
- Theo dõi ổ đĩa và RAM bằng `node-exporter` + Grafana nếu team DevOps đã có sẵn.

Lưu ý về Supabase gói miễn phí: project không hoạt động một thời gian có thể bị tạm dừng. Kiểm tra chính sách hiện tại;
trước demo nên dùng gói trả phí hoặc đảm bảo project có hoạt động thường xuyên.

---

## 9. Kiểm tra nghiệm thu (`scripts/smoke.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail
BASE=https://vlookup.<domain>

# 1. API sống
curl -fsS $BASE/api/v1/health > /dev/null

# 2. Chưa đăng nhập thì bị chặn
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST $BASE/api/v1/search/text \
       -H 'content-type: application/json' -d '{"query":"paracetamol"}')
[ "$code" = 401 ]

# 3. Lấy token của tài khoản test (user thường) qua Supabase
TOKEN=$(curl -fsS "$SUPABASE_URL/auth/v1/token?grant_type=password" \
        -H "apikey: $SUPABASE_PUBLISHABLE_KEY" -H 'content-type: application/json' \
        -d "{\"email\":\"$SMOKE_USER_EMAIL\",\"password\":\"$SMOKE_USER_PASSWORD\"}" | jq -r .access_token)

# 4. User search được
curl -fsS -X POST $BASE/api/v1/search/text -H "Authorization: Bearer $TOKEN" \
     -H 'content-type: application/json' -d '{"query":"paracetamol"}' | jq -e '.results | length > 0' > /dev/null

# 5. User không vào được admin
code=$(curl -s -o /dev/null -w '%{http_code}' $BASE/api/v1/admin/releases -H "Authorization: Bearer $TOKEN")
[ "$code" = 403 ]

# 6. Search đang dùng Qdrant local qua gRPC
curl -fsS $BASE/api/v1/search/info -H "Authorization: Bearer $TOKEN" | jq -e '.qdrant_transport == "grpc"' > /dev/null

echo "smoke OK"
```

Kiểm tra tay trước demo:

- [ ] Đăng ký bằng email: nhận được email xác nhận từ Gmail (kiểm tra cả hộp thư Spam), bấm link thì vào được trang search.
- [ ] Đăng nhập bằng Google bằng một tài khoản **không** nằm trong danh sách test user (chứng tỏ app đã publish).
- [ ] Quên mật khẩu: nhận email, đặt mật khẩu mới, đăng nhập bằng mật khẩu mới được, mật khẩu cũ không được.
- [ ] Tài khoản `user` mở `/admin` thì bị chuyển về trang chủ.
- [ ] Đổi một tài khoản sang `admin` bằng SQL, đăng nhập lại, thấy nút "Quản trị".
- [ ] Hạ quyền admin đó về `user`: trong tối đa 15 phút (JWT expiry), API trả 403 cho route admin.
- [ ] p95 của search dưới 800 ms khi chạy k6 với 20 người dùng đồng thời trong 10 phút.
- [ ] Khôi phục Qdrant từ snapshot vào một container trống, số point bằng 30.198.

---

## 10. Lịch làm

| Ngày | Việc | Ai |
| --- | --- | --- |
| 27–28/11 | Chốt máy chủ (cloud VN/Singapore hay máy tại chỗ) và domain. Tạo Supabase project, Gmail hệ thống, Google OAuth client | DevOps + lead |
| 30/11–02/12 | Sửa Dockerfile (encoder, torch CPU), thêm `/ready`, `output: "standalone"`. Chuyển Qdrant về Docker local (mục 5), đo latency | Backend + DevOps |
| 01–04/12 | SQL Supabase (mục 3.5, 3.6), hook, SMTP Gmail, template tiếng Việt | Backend |
| 02–08/12 | `core/auth.py`, gắn dependency vào router, test 401/403. Web: Supabase client, `proxy.ts`, 4 trang tài khoản, callback, `authFetch` | Backend + Frontend |
| 03–08/12 | `compose.prod.yaml`, Caddyfile, `.env`, CI/CD, môi trường staging chạy được | DevOps |
| 09–18/12 | Phần admin (upload ảnh, sửa graph, release) theo doc plan, trên staging | Backend + Frontend |
| 14–18/12 | Backup, giám sát, thử khôi phục, `smoke.sh` chạy sau mỗi lần deploy | DevOps |
| 16–21/12 | k6, kiểm tra tay mục 9, chạy thử kịch bản demo hai lần | Cả team |
| 22/12 | Đóng băng deploy | |
| 24/12 | Demo | |

---

## 11. Rủi ro

| Rủi ro | Dấu hiệu | Cách xử lý |
| --- | --- | --- |
| Email xác nhận rơi vào Spam hoặc không tới | Người đăng ký không nhận được mail | Tạo sẵn tài khoản demo; khuyên dùng Google login trong demo; dài hạn chuyển sang nhà cung cấp email có domain riêng |
| Google OAuth vẫn ở chế độ Testing | Tài khoản lạ báo lỗi "access blocked" | Publish app trước demo; kiểm tra bằng tài khoản ngoài danh sách test |
| Supabase gặp sự cố | Không đăng nhập được | Search vẫn chạy với người đã có token còn hạn (verify tại chỗ). Có video quay sẵn |
| Lệch phiên bản Qdrant khi khôi phục snapshot | Upload snapshot báo lỗi | Dùng phiên bản bằng hoặc mới hơn Cloud; nếu vẫn lỗi thì dựng lại bằng cách B (mục 5.2) |
| Embed query trên CPU chậm | p95 > 800 ms | ONNX int8, hoặc máy có GPU |
| Máy tại chỗ mất điện hoặc mất mạng | Uptime Kuma báo lỗi | UPS, Cloudflare Tunnel tự kết nối lại, có video dự phòng |
| User tự đổi role | | Không có policy update trên `profiles`; role chỉ đổi bằng SQL của admin. Có test RLS |

Câu cần chốt sớm: máy chủ là VM trên cloud (VN/Singapore) hay máy đặt tại văn phòng? Câu trả lời quyết định mục 7.5
(SSH hay self-hosted runner) và 7.6 (mở port hay Cloudflare Tunnel).
