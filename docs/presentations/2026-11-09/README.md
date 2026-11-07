# VisualSeek — General idea / 09.11.2026

Bộ 8 slide tiếng Việt dành cho buổi góp ý với giảng viên/mentor. Bài nói chính 8 phút, có mốc rút xuống 5 phút; bản 10 phút dành thêm 2 phút thảo luận benchmark.

- `VisualSeek-General-Idea.pptx`: slide 16:9, chữ/sơ đồ sửa được, có speaker notes.
- `VisualSeek-General-Idea.pdf`: slide để trình chiếu offline, font tiếng Việt nhúng.
- `Loi-thoai-va-Hoi-dap.pdf` và `.md`: lời thoại từng slide, bản 5 phút, kế hoạch benchmark và câu trả lời dự kiến.
- `preview.png`: ảnh tổng quan 8 slide.
- `assets/`: ảnh chụp giao diện thật, gồm toàn trang, vùng kết quả và card đầu tiên.
- `evidence/`: response/cấu hình capture, số liệu local và báo cáo kiểm tra artifact. Không lưu API key.

## Dựng lại

Từ thư mục gốc repo, dùng một môi trường Python riêng có các thư viện trong `requirements.txt`. Các thư viện dựng slide không phải dependency runtime của ứng dụng.

```bash
python -m pip install -r docs/presentations/2026-11-09/requirements.txt
python -m playwright install chromium --only-shell
```

Để chụp lại, khởi động API trên cổng 8000 và web trên cổng 3000 theo README dự án; giữ model cache và full artifact hiện tại. Script chụp dùng `localhost` cho Next.js dev để tránh chặn dev origin.

```bash
python docs/presentations/2026-11-09/capture_demo.py
python docs/presentations/2026-11-09/build_deck.py
python docs/presentations/2026-11-09/render_preview.py
```

Capture dùng cấu hình auto hiện hành, có thể gọi LLM cho truy vấn phức tạp. Nếu provider trả lỗi, script lưu lỗi riêng rồi chụp lại một lượt local có nhãn dự phòng. Không thay `.env`, API hay thuật toán tìm kiếm. Trước khi chụp lại, lưu bộ evidence cũ nếu muốn giữ nguyên mốc đối chiếu.

Build đọc `.env` theo allowlist cấu hình không bí mật, manifest/vector và pool đánh giá local để cập nhật số liệu. Bài trình bày giả định artifact `culture-labels-v1` và corpus hiện tại; xem lại nội dung nếu corpus/pipeline thay đổi.

Script preview cần `pdftoppm` (Poppler). PDF được dựng từ cùng tọa độ/nội dung với PPTX, không phải bản xuất từ PowerPoint. Mở thử PPTX trên máy trình chiếu, cài Noto Sans và DejaVu Sans hoặc dùng PDF để giữ bố cục. Các hình minh họa lấy từ dataset Vietnamese Culture VQA; nguồn đầy đủ ở lời thoại.

## Phạm vi bằng chứng

Ba truy vấn minh họa và timing trong response là smoke quan sát, không phải benchmark tải hoặc nhãn relevance. Bộ 200 query chưa chấm nhãn nên slide không công bố recall/độ chính xác. Dung lượng báo theo logical file bytes, không phải RAM hoặc toàn bộ dung lượng dự án.
