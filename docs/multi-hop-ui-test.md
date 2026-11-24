# Test multi-hop trên UI V-Lookup

Tài liệu này gom 95 query `multi_hop` trong bộ **silver-v1 test**. Các câu đều tìm qua hai bước `sản phẩm gốc → hãng ← sản phẩm đích`. Nhãn đúng đến từ graph-lite và corpus, nên bảng dùng để kiểm tra pipeline và phát hiện cạnh graph cần duyệt.

## Chạy API và UI trên Windows

Mở hai cửa sổ PowerShell, cả hai bắt đầu từ thư mục gốc của repo.

**Cửa sổ 1 — API.** Dùng môi trường Python `product-ingest` có sẵn trên máy này. API đọc `QDRANT_URL` và `QDRANT_API_KEY` từ `.env` ở thư mục gốc. Dùng Qdrant đã có collection `products_v1_images` tương ứng với artifact trong `data/processed/products-v1/search`.

```powershell
$env:SEARCH_ENABLED = 'true'
$env:PYTHONPATH = (Resolve-Path '.\packages\retrieval\src').Path
& .\.venv\product-ingest\Scripts\python.exe -m uvicorn visualseek_api.main:app --app-dir services/api/src --host 127.0.0.1 --port 8000
```

Đợi log `Application startup complete`, rồi mở <http://127.0.0.1:8000/api/v1/search/info>. Nếu thấy JSON chứa `collection` và `graph_fingerprint` thì search đã sẵn sàng. Nếu model chưa có trong cache, đặt `$env:HF_HUB_OFFLINE = '0'` trước khi chạy lại API để cho phép tải BGE-M3.

**Cửa sổ 2 — UI.** Chạy `npm.cmd ci` trong `apps/web` nếu chưa có `node_modules`.

```powershell
Set-Location .\apps\web
$env:NEXT_PUBLIC_API_URL = 'http://127.0.0.1:8000'
npm.cmd run dev
```

Mở <http://localhost:3000>, dán một query và nhấn Enter. Có thể mở thẳng một query bằng URL `http://localhost:3000/?q=` kèm query đã mã hóa. Nếu thấy tên sản phẩm nhưng ảnh bị lỗi, kiểm tra thư mục ảnh trong `data/processed/products-v1/manifest.json`. Nếu ảnh đã được chuyển sang chỗ khác, đặt `$env:PRODUCTS_IMAGE_ROOT` thành thư mục ảnh thực tế rồi khởi động lại API.

## Kiểm tra nhanh

| Query | Đối chiếu với lượt test đã lưu |
|---|---|
| các sản phẩm khác cùng hãng với Gucci Sunglasses | `10/10` ảnh thuộc `Gucci Flora Necklace`. Một sản phẩm đích có thể xuất hiện qua nhiều ảnh. |
| ngoài Núm vú giả NUK Genius Đức thì hãng sản xuất còn làm gì khác | `10/10` ảnh thuộc `NUK Easy Learning Set`. |
| sản phẩm khác của hãng làm Chint LW26 Cam Switch | `2/10` ảnh thuộc `Chint NT00 Fuse`. Đó là cả 2 ảnh đúng đã biết, nên Recall@10 của câu này là 100%. |

1. Kiểm tra có chip **Quan hệ: cùng hãng**, **Loại trừ: [tên sản phẩm gốc]** và **Chia đều theo sản phẩm**. Số **sản phẩm liên quan** là số sản phẩm được mở rộng từ graph.
2. Kiểm tra tên dưới mỗi ảnh. Đếm ảnh của sản phẩm cùng hãng và gạch riêng ảnh của sản phẩm gốc nếu nó xuất hiện. Cùng một sản phẩm đích có thể có nhiều ảnh trong top 10.
3. So kết quả với hai cột cuối bên dưới. **Đúng trong top 10** là số ảnh đúng đã trả về trong lượt test v2. **Ảnh đúng trong corpus** là tổng ảnh đích theo nhãn silver của câu. Ví dụ, `2` ảnh đúng ở cả hai cột nghĩa là Precision@10 bằng 20% và Recall@10 bằng 100% nếu top 10 có đủ 10 ảnh.

Lượt test v2 trả đúng **872/950 ảnh** theo nhãn silver, với **P95 263,3 ms** cho 95 câu multi-hop. Đây là mốc để đối chiếu, còn số **trình duyệt** trong UI tính từ lúc gửi request đến lúc nhận và đọc JSON, trước khi ảnh tải xong. Thử vài câu bằng tay giúp kiểm tra hành vi từng câu, còn P95 cần đo trên cả lượt chạy.

Nguồn dữ liệu trong workspace: [query và nhãn silver](../data/processed/products-v1/eval/silver-v1/test.jsonl), [kết quả test v2](../data/processed/products-v1/eval/runs/test-mvp-v2.json), [định nghĩa chỉ số](../PLAN_PRODUCTS_V1.md#52-recall10-và-các-chỉ-số-chất-lượng).

## Danh sách 95 query

| ID | Query để dán vào UI | Đúng trong top 10 | Ảnh đúng trong corpus |
|---|---|---:|---:|
| `multi_hop-0000` | các sản phẩm khác cùng hãng với Gucci Sunglasses | 10/10 | 31 |
| `multi_hop-0005` | sản phẩm khác của hãng làm Lenovo ThinkStation P5 | 10/10 | 24 |
| `multi_hop-0006` | ngoài Specialized Crux thì hãng sản xuất còn làm gì khác | 6/10 | 19 |
| `multi_hop-0007` | ngoài Bơm cao áp Bosch VP44 thì hãng sản xuất còn làm gì khác | 10/10 | 152 |
| `multi_hop-0009` | sản phẩm khác của hãng làm SRAM Centerline X | 10/10 | 145 |
| `multi_hop-0010` | ngoài Khay rượu giao bôi thì hãng sản xuất còn làm gì khác | 10/10 | 25 |
| `multi_hop-0011` | sản phẩm khác của hãng làm Nerf Rival Charger MXX-1200 | 10/10 | 20 |
| `multi_hop-0012` | ngoài Ottobock 3R15 thì hãng sản xuất còn làm gì khác | 10/10 | 12 |
| `multi_hop-0013` | các sản phẩm khác cùng hãng với Trang sức vàng Óc Eo Núi Sam | 8/10 | 8 |
| `multi_hop-0014` | ngoài Núm vú giả NUK Genius Đức thì hãng sản xuất còn làm gì khác | 10/10 | 10 |
| `multi_hop-0015` | các sản phẩm khác cùng hãng với Mobil Delvac Coolant | 10/10 | 48 |
| `multi_hop-0016` | sản phẩm khác của hãng làm Bút thử điện Kyoritsu 5711 | 10/10 | 37 |
| `multi_hop-0019` | sản phẩm khác của hãng làm Bộ inverter Denso | 10/10 | 26 |
| `multi_hop-0020` | các sản phẩm khác cùng hãng với Hộp mực Samsung MLT-D101S | 10/10 | 132 |
| `multi_hop-0022` | các sản phẩm khác cùng hãng với OxyContin 10mg | 1/10 | 1 |
| `multi_hop-0023` | ngoài Bugi Bosch Super Plus thì hãng sản xuất còn làm gì khác | 10/10 | 144 |
| `multi_hop-0025` | sản phẩm khác của hãng làm Peugeot 405 | 10/10 | 81 |
| `multi_hop-0027` | ngoài Vali Roncato Box thì hãng sản xuất còn làm gì khác | 10/10 | 27 |
| `multi_hop-0030` | sản phẩm khác của hãng làm SKF Tensioner | 10/10 | 23 |
| `multi_hop-0031` | ngoài Mô tô nước Yamaha SuperJet thì hãng sản xuất còn làm gì khác | 10/10 | 47 |
| `multi_hop-0032` | sản phẩm khác của hãng làm BMW Genuine | 10/10 | 27 |
| `multi_hop-0039` | ngoài Đầu DVD ô tô Pioneer AVH thì hãng sản xuất còn làm gì khác | 10/10 | 21 |
| `multi_hop-0040` | sản phẩm khác của hãng làm Mitsubishi Pajero Sport | 10/10 | 28 |
| `multi_hop-0043` | các sản phẩm khác cùng hãng với Pin Yamaha 500Wh | 10/10 | 48 |
| `multi_hop-0044` | sản phẩm khác của hãng làm BenQ EX800ST | 10/10 | 12 |
| `multi_hop-0045` | ngoài Shantui SD22S thì hãng sản xuất còn làm gì khác | 9/10 | 9 |
| `multi_hop-0049` | các sản phẩm khác cùng hãng với Máy xạc cỏ pin Bosch UniversalGrassCut | 10/10 | 159 |
| `multi_hop-0050` | sản phẩm khác của hãng làm LG LED All-in-One LAEC Series | 10/10 | 56 |
| `multi_hop-0051` | sản phẩm khác của hãng làm Xe nâng Toyota | 9/10 | 81 |
| `multi_hop-0052` | sản phẩm khác của hãng làm Anker Soundcore Life Q30 | 10/10 | 55 |
| `multi_hop-0053` | sản phẩm khác của hãng làm Baga trước Velo Orange Porteur | 10/10 | 27 |
| `multi_hop-0054` | ngoài Ví đựng hộ chiếu Samsonite Passport Holder thì hãng sản xuất còn làm gì khác | 10/10 | 90 |
| `multi_hop-0055` | ngoài Cụm đèn pha LED Yamaha NVX 155 thì hãng sản xuất còn làm gì khác | 10/10 | 40 |
| `multi_hop-0056` | ngoài Nissan Almera đời 2000 thì hãng sản xuất còn làm gì khác | 3/10 | 3 |
| `multi_hop-0057` | ngoài Khay đựng mẫu xét nghiệm thì hãng sản xuất còn làm gì khác | 3/10 | 3 |
| `multi_hop-0059` | sản phẩm khác của hãng làm SRAM PG-1230 Eagle | 10/10 | 137 |
| `multi_hop-0060` | ngoài Xe nâng Toyota 8FD40N thì hãng sản xuất còn làm gì khác | 9/10 | 99 |
| `multi_hop-0061` | ngoài Metformin Denk 850 thì hãng sản xuất còn làm gì khác | 10/10 | 10 |
| `multi_hop-0063` | các sản phẩm khác cùng hãng với Má phanh Bosch | 10/10 | 149 |
| `multi_hop-0068` | sản phẩm khác của hãng làm Bộ ly pha lê Bohemia Lara | 10/10 | 17 |
| `multi_hop-0070` | ngoài 3T Revo LTD thì hãng sản xuất còn làm gì khác | 10/10 | 20 |
| `multi_hop-0071` | các sản phẩm khác cùng hãng với Pin Bosch eBike PowerTube 750 | 10/10 | 151 |
| `multi_hop-0072` | các sản phẩm khác cùng hãng với Lò nướng âm tủ Electrolux EOB2100COX | 10/10 | 15 |
| `multi_hop-0073` | sản phẩm khác của hãng làm BMW X5 | 10/10 | 28 |
| `multi_hop-0074` | sản phẩm khác của hãng làm Lốp Schwalbe Tough Tom | 10/10 | 30 |
| `multi_hop-0075` | sản phẩm khác của hãng làm Bơm mini Cannondale Airspeed | 10/10 | 21 |
| `multi_hop-0080` | sản phẩm khác của hãng làm Mobil Super 1000 | 10/10 | 30 |
| `multi_hop-0081` | ngoài DT Swiss ARC 1100 thì hãng sản xuất còn làm gì khác | 10/10 | 82 |
| `multi_hop-0082` | các sản phẩm khác cùng hãng với Roncato We Are Glam | 10/10 | 31 |
| `multi_hop-0085` | sản phẩm khác của hãng làm Redmi Note 11 | 10/10 | 98 |
| `multi_hop-0086` | các sản phẩm khác cùng hãng với Đèn sưởi hồng ngoại Braun 3 bóng | 1/10 | 1 |
| `multi_hop-0087` | các sản phẩm khác cùng hãng với Bvlgari Incontro d'Amore | 10/10 | 72 |
| `multi_hop-0088` | sản phẩm khác của hãng làm Kindle Paperwhite đọc sách | 10/10 | 46 |
| `multi_hop-0089` | sản phẩm khác của hãng làm DT Swiss Competition | 10/10 | 86 |
| `multi_hop-0091` | ngoài Epson TM-U220 thì hãng sản xuất còn làm gì khác | 10/10 | 34 |
| `multi_hop-0092` | sản phẩm khác của hãng làm DT Swiss Aero Comp | 10/10 | 85 |
| `multi_hop-0093` | sản phẩm khác của hãng làm Gạt mưa Valeo Silencio Performance | 10/10 | 24 |
| `multi_hop-0094` | các sản phẩm khác cùng hãng với MSI Pulse 15 | 10/10 | 11 |
| `multi_hop-0096` | sản phẩm khác của hãng làm Chint LW26 Cam Switch | 2/10 | 2 |
| `multi_hop-0098` | các sản phẩm khác cùng hãng với Topeak QuickTrack Bottle Cage | 10/10 | 42 |
| `multi_hop-0099` | ngoài Canon PowerShot SX740 HS thì hãng sản xuất còn làm gì khác | 10/10 | 87 |
| `multi_hop-0101` | các sản phẩm khác cùng hãng với Beurer FT 90 | 10/10 | 23 |
| `multi_hop-0102` | ngoài Schwalbe Hans Dampf thì hãng sản xuất còn làm gì khác | 8/10 | 26 |
| `multi_hop-0103` | ngoài IKEA VISTHUS Tủ thì hãng sản xuất còn làm gì khác | 10/10 | 92 |
| `multi_hop-0104` | sản phẩm khác của hãng làm Komatsu D37 | 10/10 | 56 |
| `multi_hop-0107` | ngoài Mực in Canon PG-810 thì hãng sản xuất còn làm gì khác | 10/10 | 111 |
| `multi_hop-0109` | sản phẩm khác của hãng làm Sơn chống cháy Jotun Steelmaster | 2/10 | 2 |
| `multi_hop-0110` | các sản phẩm khác cùng hãng với Denso Coolant | 10/10 | 28 |
| `multi_hop-0113` | các sản phẩm khác cùng hãng với Mitsubishi TD05 Turbo | 10/10 | 53 |
| `multi_hop-0115` | ngoài Nước làm mát Liqui Moly Motorbike Coolant thì hãng sản xuất còn làm gì khác | 10/10 | 37 |
| `multi_hop-0116` | sản phẩm khác của hãng làm Beurer FT 95 | 10/10 | 29 |
| `multi_hop-0117` | ngoài TRP Hydraulic Hose thì hãng sản xuất còn làm gì khác | 10/10 | 18 |
| `multi_hop-0118` | ngoài Amazon Fire TV Stick 4K Max thì hãng sản xuất còn làm gì khác | 10/10 | 45 |
| `multi_hop-0120` | các sản phẩm khác cùng hãng với Shantui SR26 | 8/10 | 8 |
| `multi_hop-0121` | ngoài Máy cắt bê tông Makita DPC7311 thì hãng sản xuất còn làm gì khác | 10/10 | 24 |
| `multi_hop-0123` | ngoài Gregory Optic 58 thì hãng sản xuất còn làm gì khác | 8/10 | 8 |
| `multi_hop-0125` | sản phẩm khác của hãng làm Đái tháo đường type 2 uống Metformin sau ăn | 9/10 | 9 |
| `multi_hop-0127` | các sản phẩm khác cùng hãng với IKEA STJÄRNBRÄCKA Duvet | 10/10 | 85 |
| `multi_hop-0129` | các sản phẩm khác cùng hãng với Ví da Bellroy Travel Wallet | 10/10 | 43 |
| `multi_hop-0130` | ngoài Komatsu PC2000 thì hãng sản xuất còn làm gì khác | 10/10 | 55 |
| `multi_hop-0131` | sản phẩm khác của hãng làm Canon RF 24mm f/1.8 Macro IS STM | 10/10 | 87 |
| `multi_hop-0132` | sản phẩm khác của hãng làm Máy Hút Bụi Samsung Jet 75 | 10/10 | 139 |
| `multi_hop-0133` | các sản phẩm khác cùng hãng với Bosch Knock Sensor | 10/10 | 150 |
| `multi_hop-0134` | sản phẩm khác của hãng làm Valleylab LS10 | 9/10 | 9 |
| `multi_hop-0135` | các sản phẩm khác cùng hãng với Cân thông minh Xiaomi Mi Body Composition Scale 2 | 10/10 | 107 |
| `multi_hop-0136` | sản phẩm khác của hãng làm Schwalbe Marathon Mondial | 10/10 | 31 |
| `multi_hop-0137` | sản phẩm khác của hãng làm Cáp điện thoại LS | 4/10 | 4 |
| `multi_hop-0139` | các sản phẩm khác cùng hãng với Động cơ Toyota G16E-GTS | 10/10 | 96 |
| `multi_hop-0140` | các sản phẩm khác cùng hãng với Mitsubishi Electric | 10/10 | 56 |
| `multi_hop-0141` | các sản phẩm khác cùng hãng với NUK Easy Learning Set | 10/10 | 16 |
| `multi_hop-0142` | ngoài Siemens Magnetom Terra thì hãng sản xuất còn làm gì khác | 10/10 | 23 |
| `multi_hop-0143` | sản phẩm khác của hãng làm Bếp Naturehike mini | 7/10 | 9 |
| `multi_hop-0144` | các sản phẩm khác cùng hãng với Caterpillar 972M | 10/10 | 17 |
| `multi_hop-0146` | ngoài Kính cửa Việt Phủ Quang / Viglacera thì hãng sản xuất còn làm gì khác | 10/10 | 17 |
| `multi_hop-0147` | các sản phẩm khác cùng hãng với Vittoria Pista | 6/10 | 21 |
