# Chạy VisualSeek trên Kaggle T4 ×2 và lưu embedding trên Hugging Face

Mở [notebook](../notebooks/visualseek-kaggle-t4x2-huggingface.ipynb), import vào Kaggle.
Notebook giữ luồng chuẩn bị → chạy GPU → đóng gói → upload của notebook tham chiếu
`ictai-2026-camera-ready-k-sensitivity.ipynb`, đồng thời dùng encoder và artifact
đúng với project. File tham chiếu hiện bị cắt ở 52.428.801 byte, JSON không hoàn chỉnh;
notebook mới không sao chép output hoặc phụ thuộc vào việc chạy được file tham chiếu.

## Chuẩn bị local

Chạy từ repo root để tạo bundle mới, gồm cả runner multi-GPU:

```bash
python3 scripts/prepare_kaggle_bundle.py
python3 scripts/prepare_kaggle_selection.py
```

Đưa `data/exports/culture-kaggle-code.zip` và `data/exports/culture-selection.zip`
vào một Kaggle Dataset riêng rồi Add Input cho notebook. Không upload `.env` hay
toàn bộ thư mục project. Manifest hiện tại có 15.423 asset duy nhất trong 6 nhóm;
selection được tạo từ `data/processed/culture-search/assets.jsonl`, xác minh
SHA-256 với ảnh trong `data/raw/vietnamese-culture-vqa`.

Add Input [dataset ảnh nguồn](https://www.kaggle.com/datasets/blackcubo/vietnamese-culture-vqa-dataset)
cùng version/file bytes với local, hoặc dataset riêng giữ cây `images/<category>/...`.
Bundle selection chỉ chứa metadata/allowlist, không chứa ảnh. Nếu dataset ảnh chỉ có
ZIP, giải nén vào `/kaggle/working` và đặt `IMAGE_ROOT` là thư mục cha của `images/`.

## Kaggle và Hugging Face

1. Trong Settings / Session options chọn **Accelerator → GPU T4 x2**, bật Internet.
   Cần chọn accelerator trong Kaggle UI; code chỉ kiểm tra và sử dụng GPU đã được cấp.
2. Thêm Kaggle Secret **`HF_TOKEN`**, bật quyền notebook truy cập secret. Token có
   quyền write vào **`Quanganh6905/Embedding_for_searchengine`**. Nếu repo chưa tồn tại,
   token cần quyền tạo Dataset repo trong namespace này.
3. Run All. Mặc định chạy full allowlist, image và metadata vectors, batch 16/GPU.
   Để thử nhỏ, đổi `LIMIT_PER_CATEGORY = 20` và dùng `RUN_NAME` khác.
4. Cell upload tạo Dataset repo private nếu chưa tồn tại và tải artifact vào
   `artifacts/siglip2-base-224-t4x2-full-v1/`. Repo đã có giữ nguyên visibility.
   Giữ upload receipt trong Kaggle output để lấy commit SHA và SHA-256 của ZIP.

Notebook dùng hai subprocess cùng lúc, mỗi subprocess thấy một GPU qua
`CUDA_VISIBLE_DEVICES`. Runner chia xen kẽ asset, chạy encoder hiện có trên từng
phần, rồi gộp về **đúng thứ tự manifest ban đầu**. Mỗi GPU giữ một bản SigLIP2 FP16;
hai GPU có VRAM riêng. Đây là inference độc lập, không cần huấn luyện hoặc DDP.

Model `google/siglip2-base-patch16-224`, revision
`75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2`, preprocessing ảnh và metadata chunking
giống backend. Không dùng VQA questions/answers. Đầu ra L2/float32 gồm:

```text
culture-embeddings.zip
├── vectors.npy
├── metadata_vectors.npy
├── assets.jsonl
├── manifest.json
└── smoke_report.json
```

Hugging Face lưu ZIP và các file báo cáo/manifest/selection/README; không upload lại
ảnh gốc, model weights hoặc checkpoint. Cả hai worker phải thành công và artifact
phải qua kiểm tra checksum/shape/L2/tập asset trước khi upload. Smoke query trong
báo cáo tìm trên từng phần worker, chưa phải đánh giá chất lượng retrieval toàn corpus.

## Tiếp tục và tải về

Chạy lại cell embedding để dùng checkpoint trong `OUTPUT/workers/rank-*/`.
Nếu OOM khi encode ảnh, giảm batch còn 8/4; nếu checkpoint metadata đã tạo với batch
cũ, giữ batch size hoặc chọn run mới. Đổi corpus/model/GPU cũng cần run mới.
Qua session mới, attach output đã Save Version và copy **toàn bộ thư mục run**
về `/kaggle/working/<RUN_NAME>`; chỉ ZIP final không đủ để resume.

Nếu upload lỗi 401/403 hoặc mất mạng, sửa Secret/quyền và chạy lại riêng cell upload;
không cần embedding lại. Notebook lưu commit SHA, filename, checksum ZIP và count
trong `/kaggle/working/<RUN_NAME>-upload-receipt.json` sau khi upload thành công.

Chạy trên máy local, dùng commit từ receipt:

```python
from huggingface_hub import hf_hub_download

archive = hf_hub_download(
    repo_id="Quanganh6905/Embedding_for_searchengine",
    repo_type="dataset",
    filename="artifacts/siglip2-base-224-t4x2-full-v1/culture-embeddings.zip",
    revision="COPY_COMMIT_SHA_FROM_RECEIPT",
)
print(archive)
```

Sau đó import vào thư mục mới:

```bash
python3 scripts/import_embeddings.py /path/to/culture-embeddings.zip \
  --output data/embeddings/culture-siglip2-t4x2-v1
```

Repo private cần token read trên máy local. Import không encode lại corpus; backend
vẫn dùng ảnh local và encoder cùng revision cho query. Cấu hình backend xem
[Kaggle embedding và RAG](kaggle-embedding-rag.md) và
[contextual search](contextual-image-search.md).

Tài liệu API được đối chiếu:
[Kaggle Notebooks](https://www.kaggle.com/docs/notebooks),
[PyTorch GPU isolation](https://docs.pytorch.org/docs/stable/notes/cuda.html#cuda-environment-variables),
[Hugging Face Hub 0.36 HfApi](https://huggingface.co/docs/huggingface_hub/v0.36.0/en/package_reference/hf_api).
