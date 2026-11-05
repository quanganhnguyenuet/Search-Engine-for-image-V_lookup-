> Cập nhật provider LLM
>
> Luồng chạy hiện tại đã chuyển sang GPT-4o mini qua OpenAI API; xem
> [contextual-image-search.md](contextual-image-search.md). Các phần Ollama bên dưới
> ghi lại baseline cũ. Export/import embedding SigLIP2 vẫn dùng được, không cần
> embedding lại khi đổi provider LLM.

# Embedding trên Kaggle → Hugging Face Dataset → tìm kiếm và RAG local

## 1. Phân chia công việc

Luồng triển khai của dự án:

```text
Kaggle GPU
  ảnh corpus → SigLIP2 → vector + metadata + manifest → ZIP artifact
                                                    |
                                                    v
Hugging Face Dataset repo
  lưu artifact theo phiên bản/commit, không chạy API hoặc RAG
                                                    |
                                                    v
Máy công ty / máy local CPU
  tải ZIP → kiểm tra/import → retrieval → RAG → giao diện VietNam Culture
```

| Nơi chạy | Công việc | Đầu ra |
| --- | --- | --- |
| Local, trước khi chạy Kaggle | Đóng gói code và allowlist ảnh local | `culture-kaggle-code.zip`, `culture-selection.zip` |
| Kaggle | Tạo image embedding cho corpus bằng GPU | `culture-embeddings.zip` |
| Hugging Face Dataset | Lưu artifact và các báo cáo theo phiên bản | Repo ID, đường dẫn artifact, commit SHA |
| Local, sau khi tải về | Import vector, encode query, retrieval, sinh câu trả lời, phục vụ web | Semantic search và RAG |

Không khởi động FastAPI, Next.js, Ollama hay dịch vụ retrieval trên Kaggle. Script
`kaggle_embed.py` hiện có một bước kiểm tra vài query sau khi export; đó chỉ là smoke
test artifact, không phải chạy hệ thống search/RAG hoặc benchmark sản phẩm trên Kaggle.

Máy local **không embedding lại toàn bộ corpus**. Khi người dùng gửi câu hỏi hoặc một
ảnh query mới, backend vẫn cần encode riêng query đó bằng cùng model. Phần này chạy
CPU được; import vector corpus không thay thế query encoder.

## 2. Model và artifact được chuyển giữa hai máy

Model embedding: `google/siglip2-base-patch16-224`, revision cố định:

```text
75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2
```

Ảnh corpus được encode từ pixel, áp dụng EXIF orientation, RGB và processor cố định,
sau đó chuẩn hóa L2. Keyword, tags và description không dùng để tạo/boost image
embedding. Model và processor ở local phải khớp với bản tạo artifact trên Kaggle.
[Model card SigLIP2](https://huggingface.co/google/siglip2-base-patch16-224).

ZIP dùng để chuyển dữ liệu có cấu trúc:

```text
culture-embeddings.zip
├── vectors.npy          # float32, mỗi hàng tương ứng một asset
├── assets.jsonl         # asset_id, đường dẫn tương đối và metadata hiển thị/RAG
├── manifest.json        # model revision, dimension, scope, count và checksum
└── smoke_report.json    # kết quả kiểm tra query, không phải relevance benchmark
```

Phải chuyển cả ZIP, không chỉ riêng `vectors.npy`: mất mapping hàng vector → asset ID
sẽ làm retrieval trả sai ảnh. VQA questions/answers không nằm trong metadata xuất ra.

ZIP không chứa ảnh gốc, model weights, API server hoặc Ollama. Máy local tiếp tục dùng
bộ ảnh đã lưu trong `data/raw/vietnamese-culture-vqa`. Asset ID SHA-256 nối vector từ
Kaggle với ảnh local. Hugging Face ở đây lưu **artifact embedding**, không phải nơi
deploy model inference hoặc nơi upload lại weights SigLIP2.

## 3. Chuẩn bị một lần ở local và Hugging Face

### 3.1. Đóng gói code

Chạy tại repo root:

```bash
python3 scripts/prepare_kaggle_bundle.py
```

File đầu ra: `data/exports/culture-kaggle-code.zip`. Bundle chỉ gồm code cần cho export,
không chứa `.env`, dataset ảnh, token, vector hoặc model cache.

### 3.2. Cố định đúng tập ảnh đang có ở local

Kaggle Dataset nguồn có nhiều ảnh/nhóm hơn phần đã lưu local. Gắn toàn bộ nguồn làm
Input không có nghĩa là phải embedding tất cả. Với luồng này, **manifest local là
allowlist**, còn Kaggle Dataset chỉ cung cấp file ảnh để đọc.

Tạo một bundle nhỏ từ manifest của bộ ảnh local hiện tại:

```bash
python3 scripts/prepare_kaggle_selection.py \
  --manifest data/processed/culture-search/assets.jsonl \
  --image-root data/raw/vietnamese-culture-vqa \
  --output data/exports/culture-selection.zip
```

Script kiểm tra từng file local và SHA-256, rồi đóng gói `culture-assets.jsonl` cùng
`selection.json`. Không chạy model, không upload ảnh và không giữ VQA questions/answers.
Bundle hiện tại chọn **15.423 asset duy nhất** của local. Nếu muốn đúng tập sample,
đổi `--manifest` sang `data/processed/culture-search-sample/assets.jsonl` và dùng output
khác; số ảnh sẽ theo manifest đó, không theo tổng ảnh của Kaggle Dataset.

Có hai cách cung cấp ảnh cho notebook:

- Dùng Kaggle Dataset nguồn đầy đủ, cùng version/file bytes như local, rồi truyền
  `--manifest` để chỉ đọc các ảnh trong allowlist. Cách này không cần upload lại ảnh.
- Nếu local có ảnh riêng hoặc đã chỉnh sửa ảnh, upload tập ảnh local thành Kaggle
  Dataset riêng và giữ nguyên cây `images/<category>/<subject>/<filename>`. Vẫn dùng
  cùng allowlist; `image_root` là thư mục cha của `images/` trong Input mới.

Notebook sẽ dừng nếu file thiếu hoặc SHA-256 khác. Không tự chọn ảnh gần giống hay
tự mở rộng sang ảnh khác trên Kaggle. Manifest chỉ mang metadata, không mang pixel;
ảnh local không tồn tại trong nguồn công khai thì phải dùng cách thứ hai.

### 3.3. Chuẩn bị nơi lưu artifact

Trên Hugging Face, tạo một **Dataset repo**, ví dụ:

```text
YOUR_HF_USERNAME/vietnam-culture-embeddings
```

Chọn private nếu chỉ dùng nội bộ. Tạo token có quyền write vào repo này, hoặc
fine-grained token có quyền ghi đúng repo đã tạo. Trên Kaggle, thêm token vào Secrets
với tên `HF_TOKEN` và cấp quyền dùng secret cho notebook. Không ghi token trực tiếp
vào cell, code ZIP hay output. Máy local chỉ cần quyền read nếu repo là private.
[Tài liệu Hugging Face về token](https://huggingface.co/docs/hub/security-tokens).

Các cell phía dưới giả định repo đã được tạo. Thay `YOUR_HF_USERNAME` bằng tài khoản
hoặc organization của bạn trước khi upload.

## 4. Chạy embedding trên Kaggle

Tạo Kaggle Notebook, bật GPU accelerator và Internet. Add Input:

1. Dataset `blackcubo/vietnamese-culture-vqa-dataset`.
2. Một Kaggle Dataset chứa `culture-kaggle-code.zip` và `culture-selection.zip` vừa
   chuẩn bị; cũng có thể đặt hai ZIP trong hai Input riêng.

Nếu dùng tập ảnh local tự upload thay cho nguồn công khai, thay Input ở mục 1 và
chỉ định `image_root` tương ứng trong Cell 2.

Nếu gặp `Attach exactly one code bundle` ở cell cũ, số file ZIP tìm được đang là
0 hoặc lớn hơn 1. Dataset ảnh nguồn không chứa code dự án: cần attach thêm bundle
code. Nếu Input hiển thị trực tiếp `scripts/` và `packages/` thay vì file ZIP, dùng
cell mới bên dưới; cell này hỗ trợ cả ZIP và thư mục đã giải nén. Khi có nhiều bản,
đặt `CODE_SOURCE` bằng đúng đường dẫn được in ra thay vì chọn một bản ngẫu nhiên.

Nếu Cell 3 báo `No module named 'visualseek_embedding.contracts'`, bundle code cũ
đã thiếu các file `contracts.py` mà package cần import. Tạo lại ZIP bằng
`python3 scripts/prepare_kaggle_bundle.py`, upload ZIP mới vào Dataset code, rồi
cập nhật Notebook Input sang version mới. Thay Cell 1 bằng bản bên dưới và chạy lại
Cell 1, sau đó Cell 3. Nếu đã restart session thì chạy lại cả Cell 2 để nạp biến.
Không cần upload lại dataset ảnh hoặc `culture-selection.zip`. Không sửa file trong
`/kaggle/input` vì đây là thư mục chỉ đọc.

### Cell 1 — giải nén code và cài dependency export/upload

```python
from pathlib import Path, PurePosixPath
from zipfile import ZipFile
import subprocess
import sys

INPUT_ROOT = Path('/kaggle/input')
CODE_SOURCE = None  # Hoặc '/kaggle/input/your-code-dataset/culture-kaggle-code.zip'

def find_bundle(zip_name, marker, required_files, explicit=None):
    if explicit is not None:
        source = Path(explicit)
        if not source.exists():
            raise FileNotFoundError(f'Configured source does not exist: {source}')
        return source
    archives = [p for p in INPUT_ROOT.rglob(zip_name) if p.is_file()]
    directories = set()
    marker = Path(marker)
    for file in INPUT_ROOT.rglob(marker.name):
        root = file.parents[len(marker.parts) - 1]
        if all((root / name).is_file() for name in required_files):
            directories.add(root)
    candidates = sorted(set(archives) | directories)
    print(f'Candidates for {zip_name}:', [str(p) for p in candidates])
    if not candidates:
        print('Attached inputs:', [str(p) for p in INPUT_ROOT.iterdir()])
        raise FileNotFoundError(
            f'Attach {zip_name} as a Notebook Input, or attach its extracted files. '
            'The image dataset alone is not enough. If the code Input is an old '
            'incomplete bundle, regenerate it and update the Input version.'
        )
    if len(candidates) > 1:
        raise RuntimeError('Multiple bundles found. Set the explicit source to one printed path.')
    return candidates[0]

def materialize_bundle(source, destination, required_files):
    if source.is_dir():
        root = source  # Kaggle Input có thể đọc trực tiếp, không cần ghi vào đó.
    else:
        root = Path(destination)
        root.mkdir(parents=True, exist_ok=True)
        with ZipFile(source) as archive:
            missing = sorted(set(required_files) - set(archive.namelist()))
            if missing:
                raise FileNotFoundError(
                    f'Incomplete bundle {source}: {missing}. Regenerate and update the Input.'
                )
            # Giữ đủ module trong ZIP, kể cả module mới chưa nằm trong required_files.
            for member in archive.infolist():
                path = PurePosixPath(member.filename)
                if (path.is_absolute() or '..' in path.parts or '\\' in member.filename
                        or not (root / member.filename).resolve().is_relative_to(root.resolve())):
                    raise ValueError(f'Unsafe bundle path: {member.filename}')
            archive.extractall(root)
    missing = [name for name in required_files if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f'Incomplete bundle directory {root}: {missing}. Regenerate and update the Input.'
        )
    return root

code_files = [
    'scripts/kaggle_embed.py', 'scripts/_bootstrap.py', 'scripts/kaggle-requirements.txt',
    'scripts/enrich_embeddings.py',
    'packages/embedding/src/visualseek_embedding/__init__.py',
    'packages/embedding/src/visualseek_embedding/contracts.py',
    'packages/embedding/src/visualseek_embedding/siglip2.py',
    'packages/embedding/src/visualseek_embedding/metadata.py',
    'packages/retrieval/src/visualseek_retrieval/__init__.py',
    'packages/retrieval/src/visualseek_retrieval/contracts.py',
    'packages/retrieval/src/visualseek_retrieval/artifact.py',
    'packages/preprocessing/src/visualseek_preprocessing/__init__.py',
    'packages/preprocessing/src/visualseek_preprocessing/contracts.py',
    'packages/preprocessing/src/visualseek_preprocessing/culture_dataset.py',
]
source = find_bundle('culture-kaggle-code.zip', 'scripts/kaggle_embed.py', code_files, CODE_SOURCE)
code = materialize_bundle(source, '/kaggle/working/visualseek-code', code_files)
print('Using code:', code)

subprocess.check_call([
    sys.executable, '-m', 'pip', 'install', '-q',
    '-r', str(code / 'scripts/kaggle-requirements.txt'),
    'huggingface_hub>=0.34,<1',
])

# Kiểm tra import ngay, chưa tải weights hay chạy embedding.
subprocess.check_call([sys.executable, str(code / 'scripts/kaggle_embed.py'), '--help'])

import torch
assert torch.cuda.is_available(), 'Enable the GPU accelerator first'
print(torch.__version__, torch.cuda.get_device_name(0))
```

Giữ bản torch/CUDA có sẵn của Kaggle; không cài bản `torch+cpu` ở đây. SigLIP2 sẽ tải
weights từ repo model chính thức lần đầu và cache chúng trong phiên Kaggle.

### Cell 2 — nạp allowlist local và xác định nơi chứa ảnh

```python
import hashlib
import json
from pathlib import Path, PurePosixPath

SELECTION_SOURCE = None  # Hoặc đường dẫn ZIP/thư mục selection cụ thể trong Input.
IMAGE_ROOT = None  # Hoặc '/kaggle/input/your-image-dataset/folder-containing-images'
selection_files = ['culture-assets.jsonl', 'selection.json']
source = find_bundle('culture-selection.zip', 'culture-assets.jsonl',
                     selection_files, SELECTION_SOURCE)
selection_dir = materialize_bundle(source, '/kaggle/working/local-selection', selection_files)

local_manifest = selection_dir / 'culture-assets.jsonl'
selection = json.loads((selection_dir / 'selection.json').read_text())
assert hashlib.sha256(local_manifest.read_bytes()).hexdigest() == selection['manifest_sha256']
local_rows = [json.loads(line) for line in local_manifest.read_text().splitlines() if line.strip()]
expected_ids = {row['asset_id'] for row in local_rows}
assert len(expected_ids) == len(local_rows) == selection['count']
print('Selected local assets:', selection['count'], selection['categories'])

# Manifest đã đủ metadata; chỉ cần tìm nơi chứa các file ảnh, không cần annotation nguồn.
def find_image_root(rows, input_root, explicit=None):
    relatives = [PurePosixPath(row['image_path']) for row in rows]
    if not relatives or any(path.is_absolute() or '..' in path.parts
                            or not path.parts or path.parts[0] != 'images'
                            for path in relatives):
        raise ValueError('The local manifest must contain safe images/... paths')
    candidates = ([Path(explicit)] if explicit is not None else sorted({
        directory.parent.resolve() for directory in input_root.rglob('images')
        if directory.is_dir()
    }))
    print('Image root candidates:', [str(root) for root in candidates])
    if not candidates:
        print('Attached inputs:', [str(path) for path in input_root.iterdir()])
        raise FileNotFoundError(
            'No extracted images/ folder found. Attach the image dataset, not only '
            'code/selection or annotation files. If images are still zipped, extract '
            'them to /kaggle/working and set IMAGE_ROOT to the parent of images/.'
        )
    matches = []
    for root in candidates:
        missing = []
        present = 0
        for relative in relatives:
            if (root / str(relative)).is_file():
                present += 1
            elif len(missing) < 3:
                missing.append(str(relative))
        print(f'{root}: {present}/{len(relatives)} manifest images found; missing examples={missing}')
        if present == len(relatives):
            matches.append(root)
    if not matches:
        raise FileNotFoundError(
            'No candidate contains the complete local image selection. Check the '
            'dataset version, directory layout and IMAGE_ROOT. Do not remove missing '
            'rows just to make the check pass.'
        )
    if len(matches) > 1:
        raise RuntimeError('Multiple complete image roots found. Set IMAGE_ROOT to one printed path.')
    return matches[0]

image_root = find_image_root(local_rows, INPUT_ROOT, IMAGE_ROOT)
print('Image root:', image_root)
```

Nếu cell cũ báo `Choose the source annotation explicitly if multiple copies exist`,
chạy lại Cell 2 mới ở trên. Không cần tìm hoặc upload thêm annotation nguồn: manifest
local đã chứa metadata. Cell mới tìm các thư mục `images/` và đối chiếu toàn bộ đường
dẫn tương đối trong manifest, áp dụng được cả nguồn công khai và Dataset ảnh riêng.

Khi có nhiều nguồn ảnh hợp lệ, đặt `IMAGE_ROOT` ở đầu cell bằng một đường dẫn được
in trong `Image root candidates`, ví dụ nếu ảnh thật nằm ở:

```text
/kaggle/input/YOUR_DATASET/Vietnamese Culture VQA Dataset/images/am_thuc/...
```

thì cấu hình rồi chạy lại cả Cell 2:

```python
IMAGE_ROOT = '/kaggle/input/YOUR_DATASET/Vietnamese Culture VQA Dataset'
```

`IMAGE_ROOT` là **thư mục cha của `images/`**, không phải chính `images/`, thư mục
annotation hoặc một category. Nếu không có thư mục ảnh nào, attach Input chứa pixel
ảnh thật; `culture-selection.zip` chỉ chứa danh sách/metadata, không chứa ảnh.
Nếu dữ liệu chỉ có ZIP ảnh, cần giải nén ảnh vào `/kaggle/working` trước và chỉ định
root rõ ràng; cell không tự giải nén archive ảnh lớn. Sau bước đối chiếu đường dẫn,
script embedding còn kiểm tra SHA-256 từng file trước khi gọi model.

### Cell 3 — tạo embedding và theo dõi tiến độ

Dùng bundle code mới được tạo bởi `python3 scripts/prepare_kaggle_bundle.py`, cập nhật
Input code trên Kaggle và chạy lại Cell 1 trước. Chỉ thay cell gọi subprocess thì
script cũ vẫn không có log cho bước kiểm tra checksum hoặc khởi tạo model.

Dùng các biến sau cho toàn bộ tập ảnh được chỉ định trong manifest local. Tham số
`-u` giữ log Python hiển thị ngay trong output của cell:

```python
from pathlib import Path
import subprocess
import sys

RUN_NAME = 'siglip2-base-224-v1-full'
LIMIT_PER_CATEGORY = 0  # 0 = toàn bộ allowlist local, không phải toàn bộ Kaggle Dataset
output = Path('/kaggle/working') / RUN_NAME

subprocess.check_call([
    sys.executable, '-u', str(code / 'scripts/kaggle_embed.py'),
    '--input-root', str(image_root),
    '--manifest', str(local_manifest),
    '--output', str(output),
    '--limit-per-category', str(LIMIT_PER_CATEGORY),
    '--batch-size', '16',
    '--device', 'cuda',
    '--with-metadata',  # tạo thêm vectors cho tags/description, dùng cho contextual search
    '--zip',
])
```

Script mới in thời gian và giai đoạn đang chạy:

- `[validate]`: kiểm tra đường dẫn/SHA-256 trên CPU và ổ đĩa; cập nhật khoảng mỗi
  5 giây giữa các ảnh, kèm số ảnh, phần trăm, tốc độ và ETA. Một lần đọc file chậm
  có thể khiến khoảng cách giữa hai log dài hơn.
- `[resume]`: đọc checkpoint, báo số ảnh đã có và số ảnh còn lại.
- `[model]`: import thư viện, báo tên GPU, tải processor/weights và chuyển model
  lên thiết bị. Tiến độ download phụ thuộc output của Hugging Face; ETA kiểm tra
  ảnh không bao gồm thời gian tải model.
- `[embed]`: báo từng batch đang decode ảnh hay encoding trên CUDA, số ảnh thành
  công, số ảnh lỗi, tốc độ xử lý và ETA phần embedding còn lại.
- `[export]`, `[smoke]`, `[zip]`, `[done]`: ghi artifact, thử retrieval và đóng gói.

ETA được ước tính từ tốc độ trung bình của từng giai đoạn, có thể dao động lúc đầu.
GPU chưa hoạt động ở `[validate]` là bình thường. Đoạn `--device` phải là
`'--device', 'cuda',` — xóa ký tự `S` nếu đã vô tình gõ sau dấu phẩy.

Nếu muốn thử trước, đặt `RUN_NAME = 'siglip2-base-224-v1-smoke'` và
`LIMIT_PER_CATEGORY = 20`. Dùng output mới khi chuyển từ smoke sang full để không
trộn hai tập vector. Nếu OOM, chạy lại cùng run với batch size 8 hoặc 4; các shard
đã hoàn tất được tái sử dụng.

`--manifest` là tham số quyết định giới hạn ảnh. Mỗi ảnh được kiểm tra lại SHA-256
trước khi embedding; ảnh ngoài manifest bị bỏ qua dù cùng chủ đề hoặc cùng thư mục.
Manifest local đã xử lý tên `H'Mông`/`HMông` và nhãn cho ảnh thiếu annotation ở bước
chuẩn bị corpus. Phiên bản local hiện tại có 15.423 asset sau loại trùng.

### Cell 4 — kiểm tra trước khi upload

```python
import json

manifest = json.loads((output / 'manifest.json').read_text())
report = json.loads((output / 'smoke_report.json').read_text())
assert (output / 'culture-embeddings.zip').is_file()
assert manifest['complete'], 'Export incomplete; inspect failures.jsonl'
assert manifest['count'] == manifest['requested']
assert report['failures'] == 0, 'Resolve failed images before publishing this run'
assert manifest['scope']['limit_per_category'] == LIMIT_PER_CATEGORY
assert manifest['model_revision'] == '75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2'

exported_rows = [json.loads(line) for line in (output / 'assets.jsonl').read_text().splitlines()
                 if line.strip()]
exported_ids = {row['asset_id'] for row in exported_rows}
assert exported_ids <= expected_ids, 'Export contains assets outside the local corpus'
assert len(exported_ids) == manifest['count']
if LIMIT_PER_CATEGORY == 0:
    assert exported_ids == expected_ids, 'Full run must match the exact local asset set'
    digest = hashlib.sha256('\n'.join(sorted(exported_ids)).encode()).hexdigest()
    assert digest == selection['asset_ids_sha256']

# Giữ dấu vết lựa chọn local bên cạnh artifact để upload lên HF.
import shutil
shutil.copyfile(selection_dir / 'selection.json', output / 'selection.json')
print({key: manifest[key] for key in ('count', 'dimension', 'scope', 'model_revision')})
```

Trong run full của manifest local hiện tại, `count` dự kiến là 15.423; nếu dùng một
manifest local khác, số lượng phải theo `selection['count']`. Không gắn nhãn `full`
cho artifact smoke 120 ảnh. `smoke_report.json` chỉ kiểm tra luồng và timing
một vài query; chưa chứng minh Recall@10 hoặc P90 sản phẩm.

Checkpoint trong `/kaggle/working` cần được giữ nếu phiên chạy kết thúc giữa chừng.
ZIP cuối cùng không chứa các shard resume; upload checkpoint riêng chỉ khi cần tiếp
tục một run bị gián đoạn.

## 5. Từ Kaggle upload artifact lên Hugging Face

Chạy tiếp trong cùng notebook, sau khi Cell 4 thành công:

```python
from huggingface_hub import HfApi
from kaggle_secrets import UserSecretsClient

REPO_ID = 'YOUR_HF_USERNAME/vietnam-culture-embeddings'
hf_token = UserSecretsClient().get_secret('HF_TOKEN')
api = HfApi(token=hf_token)
api.repo_info(repo_id=REPO_ID, repo_type='dataset')

commit = api.upload_folder(
    repo_id=REPO_ID,
    repo_type='dataset',
    folder_path=str(output),
    path_in_repo=f'artifacts/{RUN_NAME}',
    allow_patterns=[
        'culture-embeddings.zip',
        'manifest.json',
        'smoke_report.json',
        'failures.jsonl',
        'selection.json',
    ],
    commit_message=f'Add embedding artifact: {RUN_NAME}',
)

ARTIFACT_COMMIT = commit.oid
print('Dataset repo:', REPO_ID)
print('Artifact path:', f'artifacts/{RUN_NAME}/culture-embeddings.zip')
print('Artifact commit:', ARTIFACT_COMMIT)
```

`upload_folder` với `repo_type='dataset'` và `allow_patterns` giới hạn upload đúng
các file đã chọn. Không upload `model-cache/`, `shards/`, ảnh hoặc toàn bộ
`/kaggle/working`. [Hướng dẫn upload chính thức](https://huggingface.co/docs/huggingface_hub/guides/upload).

Repo có cấu trúc:

```text
YOUR_HF_USERNAME/vietnam-culture-embeddings
└── artifacts/
    └── siglip2-base-224-v1-full/
        ├── culture-embeddings.zip
        ├── manifest.json
        ├── smoke_report.json
        ├── failures.jsonl
        └── selection.json
```

`vectors.npy` và `assets.jsonl` đã nằm trong ZIP nên không upload thêm bản rời trùng
dữ liệu. Dataset repo này dùng để lưu/download artifact; không cần Data Viewer đọc ZIP.
`selection.json` ghi count và fingerprint tập asset local ban đầu. Nếu run là smoke,
artifact chỉ chứa tập con theo `LIMIT_PER_CATEGORY`; file selection vẫn mô tả allowlist
local đầy đủ. Cell 4 kiểm tra sự khác biệt này trước upload.

Ghi lại đủ **REPO_ID, RUN_NAME, ARTIFACT_COMMIT**. Có hai revision khác nhau:

- `model_revision` trong manifest: commit của **model SigLIP2**.
- `ARTIFACT_COMMIT` vừa in: commit của **Dataset repo chứa vector của bạn**.

Không dùng revision model làm revision khi tải Dataset repo. Sau khi có commit trên
Hub, Kaggle có thể kết thúc phiên; không cần giữ notebook chạy để phục vụ ứng dụng.

## 6. Máy local tải artifact về, không tạo lại vector corpus

### 6.1. Cài phần download/import

Chạy từ repo root, Python 3.12 và uv:

```bash
uv sync --all-packages
uv pip install 'huggingface_hub>=0.34,<1'
```

Nếu repo là private, đăng nhập một lần bằng token có quyền read:

```bash
uv run --no-sync hf auth login
```

### 6.2. Download ZIP theo commit cố định

Thay ba giá trị tương ứng với kết quả upload. Đoạn sau chạy ở terminal local:

```bash
.venv/bin/python - <<'PY'
from huggingface_hub import hf_hub_download

REPO_ID = 'YOUR_HF_USERNAME/vietnam-culture-embeddings'
RUN_NAME = 'siglip2-base-224-v1-full'
ARTIFACT_COMMIT = 'PASTE_FULL_DATASET_COMMIT_SHA_HERE'

zip_path = hf_hub_download(
    repo_id=REPO_ID,
    repo_type='dataset',
    filename=f'artifacts/{RUN_NAME}/culture-embeddings.zip',
    revision=ARTIFACT_COMMIT,
    local_dir='data/downloads/hf-embeddings',
)
print('Downloaded:', zip_path)
PY
```

`hf_hub_download` hỗ trợ tải file trong Dataset repo và pin bằng commit đầy đủ qua
`revision`. Dùng commit cố định giúp các máy nhận cùng artifact thay vì âm thầm tải
bản mới trên `main`. [Hướng dẫn download chính thức](https://huggingface.co/docs/huggingface_hub/guides/download).

### 6.3. Import vào index local mới

Với `RUN_NAME` như ví dụ bên dưới. Nếu đã đổi tên run ở Kaggle, sửa cùng tên trong
đường dẫn ZIP của lệnh import:

```bash
.venv/bin/python scripts/import_embeddings.py \
  data/downloads/hf-embeddings/artifacts/siglip2-base-224-v1-full/culture-embeddings.zip \
  --output data/embeddings/culture-siglip2-full-v1
```

Importer chỉ kiểm tra checksum, revision, dimension, unit norm và asset IDs rồi lưu
artifact. Bước này không gọi image encoder, không cần GPU và không embedding lại ảnh.
Importer không ghi đè output đã tồn tại; đổi tên thư mục cho mỗi phiên bản mới.

Tạo hoặc sửa các giá trị tương ứng trong `.env`, giữ lại cấu hình khác đang có:

```dotenv
EMBEDDING_ARTIFACT_DIR=data/embeddings/culture-siglip2-full-v1
EMBEDDING_CACHE_DIR=data/models/huggingface
EMBEDDING_DEVICE=cpu
EMBEDDING_CPU_THREADS=4
OLLAMA_URL=http://127.0.0.1:11434
RAG_MODEL=qwen2.5:3b
```

Giữ ảnh gốc local để giao diện hiển thị kết quả. Metadata trong artifact phục vụ
hydrate/RAG, không thay thế binary ảnh. Restart API sau khi đổi artifact.

## 7. Chỉ tại local: query encoder, retrieval, RAG và giao diện

### 7.1. Cài và warm-up query encoder CPU

Các lệnh torch/transformers dưới đây chạy **trên máy local**, không chạy trên Kaggle:

```bash
uv pip install 'torch==2.7.1+cpu' --index-url https://download.pytorch.org/whl/cpu
uv pip install 'transformers==4.57.6' 'sentencepiece>=0.2,<0.3'

.venv/bin/python -c "from visualseek_embedding.siglip2 import Siglip2Encoder; e = Siglip2Encoder(device='cpu', cache_dir='data/models/huggingface'); e.encode_texts(['Việt Nam']); print('Query encoder ready')"
```

Vector corpus đã nằm trong artifact, nhưng query mới chưa có vector. Backend dùng
SigLIP2 CPU để encode câu mô tả hoặc ảnh query, rồi so với vector corpus. Lần đầu tải
weights về cache local, những lần sau tái sử dụng cache. Đây không phải chạy lại job
embedding 15.423 ảnh ở Kaggle.

Retrieval hiện là exact cosine NumPy trên CPU, chưa phải Qdrant/HNSW. Không tự chuyển
sang keyword khi artifact/model lỗi. Keyword vẫn có chế độ riêng để đối chứng.

Sau khi cài dependency model thủ công, dùng `.venv/bin/python` hoặc `uv run --no-sync`
để tránh uv đồng bộ base environment rồi gỡ các package model tùy chọn.

### 7.2. Khởi động generator RAG local

Cài Ollama theo [hướng dẫn chính thức](https://docs.ollama.com/linux). Ở các terminal
riêng, chạy:

```bash
ollama serve
```

```bash
ollama pull qwen2.5:3b
```

Trong workspace đã chuẩn bị runtime portable, có thể dùng thay lệnh `ollama serve`:

```bash
OLLAMA_MODELS="$PWD/data/models/ollama" data/tools/ollama/bin/ollama serve
```

Cache Qwen đã có trong workspace này; một checkout mới vẫn cần tải model. Tất cả
runtime/weights local nằm ngoài Git. Kaggle không cần cài hoặc chạy Qwen/Ollama.

RAG dùng **description và cultural context của ảnh truy hồi** làm evidence, rồi gọi
`qwen2.5:3b` qua Ollama để trả lời tiếng Việt kèm nguồn `[S1]`. Generator chưa nhìn
trực tiếp pixel ảnh. Backend kiểm tra ID dẫn nguồn và từ chối câu trả lời chỉ có
ký hiệu nguồn; không đủ evidence sẽ abstain. Citation hợp lệ không tự chứng minh
mọi phát biểu trong chú thích là đúng về lịch sử/văn hóa.

`RAG_MIN_SCORE=0.10` là ngưỡng cosine khởi đầu cần hiệu chỉnh, không phải xác suất
tin cậy. Generation có latency budget riêng, không nằm trong mục tiêu retrieval
P90 dưới 100 ms.

### 7.3. Khởi động API và web

Terminal API tại repo root, dùng một worker để không nhân bản model trong RAM:

```bash
.venv/bin/python -m uvicorn visualseek_api.main:app \
  --app-dir services/api/src --host 127.0.0.1 --port 8000 --workers 1
```

Terminal frontend:

```bash
cd apps/web
npm ci
npm run dev
```

Web mặc định proxy sang `127.0.0.1:8000`. Có thể đổi `SEARCH_API_URL` trong
`apps/web/.env.local`. Không gọi Hugging Face trong mỗi request tìm kiếm: API đọc
artifact đã import trên đĩa và dùng model cache local.

Trang khám phá chưa có query vẫn liệt kê ảnh local. Khi tìm ngữ nghĩa/RAG, chỉ ảnh
trong artifact đã import mới tham gia retrieval. Artifact demo sẵn có chỉ chứa
120 ảnh; cần đổi `.env` sang artifact full vừa tải để tìm trên bộ đầy đủ.

## 8. Kiểm tra và cập nhật phiên bản

```text
GET  /api/v1/search/status
POST /api/v1/search/semantic
POST /api/v1/search/rag
```

Ví dụ semantic text query:

```json
{"query":"người mặc áo dài màu đỏ","category":"trang_phuc","top_k":10}
```

Image query gửi `image_base64` thuần thay cho `query`; UI nhận JPG/PNG/WebP tối đa
10 MiB. RAG hiện nhận text question. Response semantic là top-K, không phải tổng số
ảnh đã được gán nhãn là relevant.

Kiểm tra tại local khi cần:

```bash
uv run --no-sync pytest
uv run --no-sync ruff check services packages scripts
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:ai
TEST_RAG=1 npm --prefix apps/web run test:ai
```

Các lệnh `test:ai` cần API/web đang chạy; dòng có `TEST_RAG=1` cần thêm Ollama và Qwen.
Unit tests dùng doubles; không tự chạy GPU Kaggle hay upload lên Hugging Face.
Muốn đo Recall@10 phải có query → relevant asset IDs do người đánh giá, tách khỏi
VQA answers. Smoke timing không thay thế load test hoặc benchmark SLO.

Khi thêm ảnh hoặc đổi model:

1. Chạy một run embedding mới trên Kaggle với output/RUN_NAME mới.
2. Upload artifact mới lên Dataset repo, ghi lại commit mới.
3. Local download đúng commit, import vào thư mục mới.
4. Đổi `EMBEDDING_ARTIFACT_DIR` và restart API.

Không cần chuyển retrieval, Ollama hoặc frontend lên Kaggle ở bất kỳ bước nào của
luồng này. Tài liệu chỉ hướng dẫn thao tác; chưa tự upload dữ liệu lên tài khoản HF
của bạn.
