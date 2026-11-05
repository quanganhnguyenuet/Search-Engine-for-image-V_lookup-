# Lấy endpoint và API key Qdrant

## Qdrant Cloud

1. Đăng nhập [Qdrant Cloud Console](https://cloud.qdrant.io/), tạo cluster nếu chưa có.
2. Mở **Cluster Detail**, copy **Cluster Endpoint**. Đây là địa chỉ database dùng
   cho `QDRANT_URL`, không phải URL của trang dashboard.
3. Vào tab **API Keys** của cluster → **Create**. Đặt tên và thời hạn key.
4. Chọn quyền đọc cho search; nếu cần import/upsert embedding, dùng key có quyền
   ghi/manage phù hợp, có thể giới hạn theo collection.
5. Copy key ngay khi tạo và lưu vào `.env`. Qdrant chỉ hiển thị key một lần.

Đây phải là **Database API key** của cluster. **Cloud Management Key** trong
Access Management dùng quản lý hạ tầng và không thay thế Database API key khi
query collection. [Tài liệu xác thực chính thức](https://qdrant.tech/documentation/cloud/authentication/).

```dotenv
QDRANT_URL=https://YOUR_CLUSTER_ENDPOINT:6333
QDRANT_API_KEY=YOUR_DATABASE_API_KEY
```

Copy endpoint thực tế, dùng HTTPS; REST hỗ trợ port 6333 hoặc 443.
[Tài liệu kết nối](https://qdrant.tech/documentation/cloud/cluster-access/).

Kiểm tra từ repo root, không in giá trị key:

```bash
.venv/bin/python - <<'PY'
import httpx
from visualseek_api.core.config import Settings

settings = Settings()
response = httpx.get(
    settings.qdrant_url.rstrip('/') + '/collections',
    headers={'api-key': settings.qdrant_api_key},
    timeout=15,
)
response.raise_for_status()
print(response.json())
PY
```

Phản hồi có `result.collections` nghĩa là đã kết nối được. Collection rỗng vẫn là
kết nối thành công; chưa có nghĩa embeddings đã được import.

## Qdrant local

Service Qdrant trong Compose hiện chưa bật API key; không cần lấy key từ Cloud:

```dotenv
QDRANT_URL=http://127.0.0.1:6333
QDRANT_API_KEY=
```

Trong mạng Compose, URL service là `http://qdrant:6333`.

## Trạng thái tích hợp trong repo

Search hiện dùng **NumPy hybrid RRF** trên artifact local. Chỉ đặt hai biến trên
**chưa chuyển search sang Qdrant**. Chưa có adapter import/upsert/search Qdrant.
Đổi LLM sang GPT-4o mini hoạt động độc lập với việc tích hợp Qdrant; corpus
embedding SigLIP2 hiện có vẫn dùng được.
