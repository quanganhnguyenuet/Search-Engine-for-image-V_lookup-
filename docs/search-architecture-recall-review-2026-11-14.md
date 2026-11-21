# Rà soát kiến trúc, Recall@10 và multi intent

Ngày: 14/11/2026 · Nhánh: `feat/text-search-baseline` (commit `b774161`)
Phạm vi: đọc code. Không chạy encoder vì checkout này không có `data/`
(artifact, ảnh, model cache). Mọi nhận định về độ lớn ảnh hưởng là **giả thuyết
cần đo**; mục 6 liệt kê thí nghiệm cụ thể để xác nhận từng giả thuyết.

Báo cáo này bổ sung, không thay thế, [search-logic-audit.md](search-logic-audit.md)
(05/11) và [embedding-search-review-2026-11-06.md](embedding-search-review-2026-11-06.md).
Các gap đã được hai báo cáo trước ghi nhận được đánh dấu *(đã biết)*.

---

## 0. Tóm tắt

Recall@10 thấp ở baseline cosine (`/search/semantic`) và ở hybrid local nhiều khả
năng đến từ **bốn nhóm nguyên nhân cộng dồn**, xếp theo mức nghi ngờ:

1. **Cách đo** có thể đang tự giới hạn recall (trần 10/|R|, pool thiên lệch, scope
   gate trả rỗng bị tính là miss).
2. **Query đưa vào SigLIP2 sai phân phối huấn luyện**: tiếng Việt thô, còn tiền tố
   "Tôi muốn tìm ảnh…", giữ chữ hoa, không có prompt template.
3. **Kênh metadata và lexical yếu nhưng có trọng số RRF ngang kênh ảnh**: SigLIP
   text–text + mean-pool chunk; lexical theo âm tiết, không IDF, hoà điểm hàng loạt.
4. **Fusion cắt cứng ở depth 100** và không được hiệu chỉnh bằng nhãn.

Multi intent chưa xử lý được vì **toàn bộ pipeline giả định một intent**: plan
có một `visual_query`, retriever trả một danh sách, aggregation là `max` (OR), và
response không có nhóm. Đây là giới hạn kiến trúc, không sửa được bằng prompt.

---

## 1. Đánh giá cấu trúc dự án

### Điểm tốt

- Monorepo `uv` workspace tách `preprocessing / embedding / retrieval / evaluation`
  khỏi `services/api`; artifact có manifest, checksum, model revision cố định
  ([artifact.py](../packages/retrieval/src/visualseek_retrieval/artifact.py)).
- Exact NumPy search phù hợp quy mô 15.423 ảnh; không cần ANN lúc này.
- Có tư duy đánh giá đúng: nhãn người chấm, split dev/held-out, gate được ký theo
  `pipeline_signature`, không coi cosine là xác suất.
- Xử lý tiếng Việt cẩn thận (accent key, H'Mông, ambiguity không dấu).

### Vấn đề cấu trúc

| # | Vấn đề | Vị trí | Hệ quả |
| --- | --- | --- | --- |
| A1 | `SearchService` là god class 690 dòng: scope, routing, clarification, planning, embedding, retrieval, rerank, dựng response, RAG | [search.py](../services/api/src/visualseek_api/application/search.py) | `text_search` ~180 dòng với nhiều cờ đan xen; khó thêm nhánh per-intent; khó test từng bước |
| A2 | Response là `dict` tự do (~40 key), không có Pydantic response model | [search.py:455-507](../services/api/src/visualseek_api/application/search.py#L455-L507) | Không có contract cho `groups[]` khi thêm multi intent; frontend dễ vỡ |
| A3 | `contracts.py` (Protocol async `VectorRetriever`, `TextEmbedder`…) và `ports/storage.py` là code chết; implementation thật không theo contract | [retrieval/contracts.py](../packages/retrieval/src/visualseek_retrieval/contracts.py) | Ranh giới package chỉ trên danh nghĩa; đổi backend (Qdrant) sẽ phải sửa service |
| A4 | Không có lớp "pipeline" độc lập HTTP: đánh giá phải dựng cả API **và** web Next.js | [evaluate_text_search.py:17-51](../scripts/evaluate_text_search.py#L17-L51) | Vòng lặp thí nghiệm chậm; khó ablation từng kênh |
| A5 | Coupling ngược: `query_routing` import `normalize` qua `hybrid` | [query_routing.py](../services/api/src/visualseek_api/application/query_routing.py) | Nhỏ, nhưng cho thấy text utils chưa có chỗ đứng rõ |
| A6 | Notebook 52 MB của dự án khác (ICTAI K-sensitivity) ở root, JSON bị cắt cụt (không parse được) | `ictai-2026-camera-ready-k-sensitivity.ipynb` | Nặng repo, gây nhầm lẫn; nên xoá khỏi repo/đưa sang LFS |
| A7 | Lọc category/exclusion bằng list comprehension Python mỗi request | [artifact.py:95](../packages/retrieval/src/visualseek_retrieval/artifact.py#L95), [hybrid.py rank](../packages/retrieval/src/visualseek_retrieval/hybrid.py#L188) | Hiệu năng, không ảnh hưởng recall; `HybridRetriever` đã có `positions` cache, nên dùng lại |

**Đề xuất cấu trúc**: tách `text_search` thành các stage thuần có input/output
typed — `ScopeStage → RouteStage → PlanStage → RetrieveStage(per intent) →
VerifyStage → MergeStage → ResponseBuilder`. Stage `Retrieve…Merge` nên nằm trong
package `retrieval`/`evaluation` để script offline gọi trực tiếp, không qua HTTP.

---

## 2. Vì sao Recall@10 thấp khi so cosine

### 2.1 Cách đo (kiểm tra trước tiên)

**R1 — Trần recall do |R| lớn.** Recall@10 = (số ảnh relevant trong top 10) / |R|.
Nếu ground truth là "mọi ảnh cùng keyword/chủ đề" (ví dụ vài chục ảnh bánh chưng,
hàng trăm ảnh áo dài), recall@10 tối đa là 10/|R| — với |R| = 80 thì trần 0,125
dù top 10 đúng hết. Code đánh giá hiện tại thực ra **không tính recall**: chỉ có
HitRate, nDCG, Precision ([relevance.py:38](../packages/evaluation/src/visualseek_evaluation/relevance.py#L38)).
→ Nếu đang tính recall bằng script riêng, cần báo cáo kèm |R| và dùng thêm
R-Precision hoặc Recall@|R|, hoặc Precision@10/nDCG@10.

**R2 — Pool thiên lệch.** Ứng viên chấm nhãn = hợp top 30 của semantic/contextual/
keyword ([evaluate_text_search.py:26-28](../scripts/evaluate_text_search.py#L26-L28)).
Ảnh relevant mà cả ba hệ đều bỏ sót không bao giờ vào mẫu số, và hệ nào đóng góp
nhiều ứng viên vào pool sẽ được lợi khi so sánh.

**R3 — Scope gate trả rỗng bị tính là miss.** `/search/semantic` dừng khi scope
khác `in_scope` ([search.py:534-537](../services/api/src/visualseek_api/application/search.py#L534-L537)).
`assess_scope` chỉ trả `in_scope` nếu query chứa một cụm trong `SUPPORTED_SUBJECTS`
hoặc trùng tên catalog ([search_scope.py:71-89](../services/api/src/visualseek_api/application/search_scope.py#L71-L89)).
Query hợp lệ như "người đang cấy lúa", "chợ đông người", "người bán hàng rong" có
thể nhận `unclear` → 0 kết quả → recall 0. Baseline cosine **không còn là cosine
thuần**; nên tách số liệu "retrieval" và "scope" ra hai bảng.

**R4 — Nhãn corpus nhiễu.** Keyword lấy từ tên thư mục, đã phải sửa nhãn
([vietnamese-label-repair.md](vietnamese-label-repair.md)), còn near-duplicate
ngoài 296 bản trùng tuyệt đối. Nếu GT là keyword, nhiều "miss" thực ra là ảnh đúng
nội dung nhưng khác nhãn.

### 2.2 Query embedding lệch phân phối của SigLIP2

**Q1 — Câu gốc kèm tiền tố được embed nguyên văn.** `search_term()` đã bóc
"Tôi muốn tìm ảnh…" nhưng chỉ dùng cho exact lookup; nhánh hybrid local embed và
tách token từ **query gốc** ([search.py:428-447](../services/api/src/visualseek_api/application/search.py#L428-L447)).
Với tập 200 query sinh bởi `prepare_text_search_queries.py`, một nửa có dạng
"Tôi muốn tìm ảnh …" → vector bị kéo về khái niệm "ảnh/tìm", và lexical cộng điểm
cho token `toi/muon/tim/anh` (rất có thể "ảnh" xuất hiện trong hầu hết description
kiểu "Bức ảnh…", cần kiểm chứng trên data).

**Q2 — Không lowercase, không prompt template.** `encode_texts` giữ nguyên chữ hoa
([siglip2.py:98-111](../packages/embedding/src/visualseek_embedding/siglip2.py#L98-L111)),
comment ở [search.py:222](../services/api/src/visualseek_api/application/search.py#L222)
còn chủ đích "preserve case". Tài liệu HF cho SigLIP/SigLIP2 lưu ý model được train
với text lowercase và khuyến nghị dạng "This is a photo of …". Query như "Hội An",
"Bản Giốc", "Văn Miếu" bị viết hoa → lệch phân phối.

**Q3 — Tiếng Việt với SigLIP2 base-224.** SigLIP2 có dữ liệu đa ngôn ngữ nhưng chủ
yếu là tiếng Anh; bản base-patch16-224 yếu hơn nhiều so với so400m/giant ở retrieval
đa ngôn ngữ. Route LLM dùng `visual_query` tiếng Anh, nhưng route `auto` gửi đa số
mô tả thông thường sang local hybrid **tiếng Việt** → kênh ảnh, kênh mạnh nhất, chạy
ở chế độ yếu nhất với phần lớn traffic.

### 2.3 Kênh metadata và lexical

**M1 — Text–text bằng tháp text của SigLIP.** Metadata vector = mean của các chunk
64 token (title + tags + description), so với query qua cùng tháp text
([siglip2.py:113-136](../packages/embedding/src/visualseek_embedding/siglip2.py#L113-L136),
[metadata.py](../packages/embedding/src/visualseek_embedding/metadata.py)). SigLIP
được huấn luyện cho image↔text, không cho text↔text; cosine text–text thường dồn
trong khoảng hẹp. Mean-pool làm description dài lấn tên/tags. *(đã biết một phần)*

**M2 — Lexical theo âm tiết, không IDF, không chuẩn hoá độ dài.**
`lexical_scores` cộng trọng số cố định theo field cho mọi token
([hybrid.py:120-142](../packages/retrieval/src/visualseek_retrieval/hybrid.py#L120-L142)):
- "áo dài" thành `áo` + `dài` (dài = long); "người", "có", "trên", "ảnh" đóng góp
  ngang "Bản Giốc".
- Hàng nghìn ảnh **cùng điểm**; `argsort(stable)` xếp hạng theo thứ tự dòng trong
  `assets.jsonl` ([hybrid.py:213-217](../packages/retrieval/src/visualseek_retrieval/hybrid.py#L213-L217)).
  Rank 1..100 của kênh lexical khi đó gần như ngẫu nhiên nhưng vẫn được cộng RRF
  trọng số 1,0 — **nhiễu được bơm vào fusion ngang hàng với kênh ảnh**.

### 2.4 Fusion

**F1 — Trọng số bằng nhau, chưa hiệu chỉnh.** `.env` đặt 1.0/1.0/1.0, k=60, depth 100.
`tune_fusion` có sẵn nhưng cần pool đã chấm; chưa có bằng chứng đã chạy. *(đã biết)*

**F2 — Cắt depth 100 mỗi kênh.** Ảnh đúng ở rank 101 của một kênh mất toàn bộ phần
điểm kênh đó; ví dụ "ceramic Hue" trong audit 05/11 bị đẩy từ image rank 4 xuống
fused rank 7. Với 15k ảnh, chi phí tính toàn bộ rank là nhỏ. *(đã biết)*

**F3 — RRF làm mất biên độ.** Kênh ảnh có cosine 0,31 vs 0,12 (cách xa) và kênh
lexical hoà điểm đều thành 1/(60+r). Kênh tự tin nhất không được nghe nhiều hơn.

### Thứ tự nghi ngờ (cần đo để xác nhận)

| Mã | Nguyên nhân | Ảnh hưởng dự kiến lên recall@10 | Chi phí sửa |
| --- | --- | --- | --- |
| R1/R3 | Trần 10/\|R\|, scope trả rỗng | Rất lớn (có thể là toàn bộ "recall thấp") | Rất thấp |
| Q1 | Tiền tố trong embedding/lexical | Lớn với query dạng "Tôi muốn tìm…" | Thấp |
| M2 | Lexical hoà điểm + không IDF | Lớn ở hybrid | Thấp–TB |
| Q3 | Query tiếng Việt vào kênh ảnh | Lớn | TB (dịch local hoặc đổi model) |
| Q2 | Chữ hoa/không template | Nhỏ–TB | Rất thấp |
| F1/F2 | Fusion chưa hiệu chỉnh, depth 100 | TB | Thấp (khi đã có nhãn) |
| M1 | Metadata text–text | TB | TB–cao (embedding text riêng) |

---

## 3. Vì sao chưa xử lý được multi intent

Phân biệt ba loại, vì mỗi loại cần cơ chế khác nhau:

- **Conjunctive (một ảnh, nhiều điều kiện):** "áo dài đỏ trước phố cổ Hội An" → AND.
- **Disjunctive (một ảnh, lựa chọn):** "áo dài đỏ hoặc xanh" → OR.
- **Multi intent độc lập (nhiều nhóm ảnh):** "bánh đậu xanh với trà, và nghệ nhân
  làm gốm" → hai lần truy vấn, hai nhóm kết quả.

| Tầng | Hiện trạng | Hệ quả |
| --- | --- | --- |
| Router | `IntentDecision` chỉ là `needs_reasoning: bool` ([query_routing.py:47](../services/api/src/visualseek_api/application/query_routing.py#L47)) | Không phát hiện "có mấy intent"; câu multi intent có thể bị gửi local và embed thành **một** vector trung bình ngữ nghĩa |
| Plan | `QueryPlan` có **một** `visual_query`, **một** `metadata_query`, `keywords` phẳng, `requirements` phẳng ([query_understanding.py:45-59](../services/api/src/visualseek_api/application/query_understanding.py#L45-L59)) | LLM buộc phải nhồi hai intent vào một câu 30 từ; requirements không biết thuộc intent nào |
| Embedding | Một vector/kênh khi `query_roles=true` | Vector nằm giữa hai cụm → không gần cụm nào |
| Retrieval | `_cosine` lấy `max` trên các query vector ([hybrid.py:165](../packages/retrieval/src/visualseek_retrieval/hybrid.py#L165)) | `max` = OR mềm; không có AND giữa điều kiện, không có quota theo intent |
| Lexical | `keywords` của mọi intent gộp vào một túi token | Ảnh khớp nhiều token của intent mạnh thắng; intent yếu bị nuốt (audit 05/11: 9/10 ảnh ẩm thực) |
| Exclusions | Chỉ so title/tags ([entities.py](../packages/retrieval/src/visualseek_retrieval/entities.py) `_excluded_positions`) | Phủ định chỉ áp ở mức tên, không áp theo intent |
| Rerank | LLM chọn từ **một** shortlist 32 ứng viên đã fuse | Nếu intent B không lọt top 32, rerank không cứu được |
| Response | Một `items[]` | Không có chỗ để trả `groups[]`, coverage từng intent |

**Kết luận:** muốn multi intent, phải đổi contract ở **cả bốn tầng** (plan → retrieve
→ merge → response), không chỉ chỉnh prompt.

### Thiết kế đề xuất

```text
QueryPlanV2
  combination: "single" | "all_of" | "any_of" | "separate_groups"
  intents: [                       # 1..4
    { id: "I1",
      visual_query_en, metadata_query_vi, keywords[],
      requirements[], exclusions[], quota? }
  ]

RetrieveStage: chạy hybrid độc lập cho từng intent (song song, cache theo intent)
MergeStage:
  separate_groups → trả groups[{intent_id, items}], mỗi nhóm top_k/len(intents)
  all_of          → giao/tích điểm per-requirement (ví dụ min hoặc geometric mean
                    của cosine chuẩn hoá theo điều kiện), không dùng max
  any_of          → RRF hoặc round-robin giữa các intent để giữ đa dạng
VerifyStage: judge per-requirement → supported/contradicted/unknown
Response: items[] (tương thích ngược) + groups[] + coverage per intent
```

Router local cũng nên phát hiện dấu hiệu nhiều nhóm ("và cả", "cùng với ảnh",
dấu ";", liệt kê) để gửi sang planner thay vì embed một vector.

---

## 4. Lộ trình đề xuất (theo dõi)

Trạng thái: ☐ chưa làm · ◐ đang làm · ☑ xong. Cập nhật cột "Kết quả" bằng số đo.

### Giai đoạn 0 — Đo đúng (làm trước mọi thay đổi ranking)

| ID | Việc | File | Trạng thái | Kết quả |
| --- | --- | --- | --- | --- |
| E1 | Ghi rõ định nghĩa recall đang dùng và \|R\| mỗi query; thêm Recall@K, R-Precision, MRR vào `evaluate_pool` | `packages/evaluation/.../relevance.py` | ☐ | |
| E2 | Tách số liệu: (a) retrieval với scope tắt, (b) tỉ lệ query bị scope trả rỗng | `scripts/evaluate_text_search.py` | ☐ | |
| E3 | Runner offline gọi thẳng `HybridRetriever`/`ArtifactIndex`, xuất rank từng kênh, không cần API/web | `scripts/` mới | ☐ | |
| E4 | Chấm nhãn tối thiểu 50 query dev + 50 held-out; với query tên riêng, GT có thể bán tự động theo keyword đã sửa nhãn | `data/evaluation/` | ☐ | |
| E5 | Mở rộng pool: thêm top 50 của từng kênh đơn (image/metadata/lexical) để giảm thiên lệch | như E3 | ☐ | |

### Giai đoạn 1 — Sửa nhanh, rủi ro thấp

| ID | Việc | File | Trạng thái | Kết quả |
| --- | --- | --- | --- | --- |
| Q1 | Dùng `search_term(query, preserve_accents=True)` (đã bóc tiền tố) cho embedding và lexical ở route hybrid | `search.py` (~L428-447) | ☐ | |
| Q2 | Ablation lowercase + template "ảnh về …"/"a photo of …" cho query; đo trước khi giữ | `siglip2.py`, `search.py` `text_vector` | ☐ | |
| M2a | Lexical: BM25 (IDF + chuẩn hoá độ dài) trên postings sẵn có; stopword tiếng Việt; bigram âm tiết ("áo dài", "bậc thang") | `hybrid.py` `prepare`/`lexical_scores` | ☐ | |
| M2b | Không xếp hạng các ảnh hoà điểm lexical bằng thứ tự file: cùng điểm → cùng rank (hoặc chỉ giữ ảnh có token IDF cao) | `hybrid.py` `rank` | ☐ | |
| F2 | Tính rank trên toàn corpus (hoặc depth ≥ 1000) thay vì cắt 100 | `.env` `SEARCH_CANDIDATE_DEPTH`, `hybrid.py` | ☐ | |
| S1 | Scope: `unclear` ở `/search/semantic` không nên trả rỗng khi dùng làm baseline đánh giá (hoặc tách cờ) | `search.py` L534 | ☐ | |

### Giai đoạn 2 — Cải thiện chất lượng kênh

| ID | Việc | Trạng thái | Kết quả |
| --- | --- | --- | --- |
| Q3 | Dịch query sang tiếng Anh cho kênh ảnh ở route local (MT local nhỏ hoặc cache LLM), giữ tiếng Việt cho metadata/lexical | ☐ | |
| F1 | Chạy `tune_fusion` trên dev; thử weighted RRF và fusion theo score chuẩn hoá (z-score/min-max per query) | ☐ | |
| M1 | Thử embedding text chuyên text–text đa ngôn ngữ (ví dụ multilingual-e5, bge-m3) cho kênh metadata; embed title/tags tách description | ☐ | |
| X1 | So sánh model ảnh lớn hơn/đa ngôn ngữ (siglip2-so400m, jina-clip-v2…) trên cùng tập nhãn trước khi re-embed toàn corpus | ☐ | |
| D1 | Khử near-duplicate trong top-K (cosine ảnh > ngưỡng) để tăng đa dạng | ☐ | |

### Giai đoạn 3 — Multi intent và kiến trúc

| ID | Việc | Trạng thái | Kết quả |
| --- | --- | --- | --- |
| MI1 | `QueryPlanV2` với `intents[]` + `combination`; validator và test | ☐ | |
| MI2 | Retrieve per intent song song; MergeStage theo `combination` | ☐ | |
| MI3 | Response thêm `groups[]`, coverage từng intent; UI hiển thị theo nhóm | ☐ | |
| MI4 | Router phát hiện multi intent → planner | ☐ | |
| MI5 | Tập đánh giá multi intent riêng: coverage@K từng intent, full-constraint satisfaction | ☐ | |
| A1 | Tách `SearchService.text_search` thành stage typed; response Pydantic | ☐ | |
| A3/A6 | Xoá contracts chết (hoặc implement thật); gỡ notebook ICTAI khỏi repo | ☐ | |

---

## 5. Chỉ số theo dõi

Báo cáo mỗi lần thay đổi, tách theo nhóm query (objects/scenes/attributes/spelling/
names/outside) và theo route (`lexical`, `hybrid`, `llm_hybrid`):

- Recall@10 **kèm \|R\|**, R-Precision, nDCG@10, Precision@10, MRR.
- Recall@100 của **từng kênh đơn** (trần cho fusion: nếu ảnh đúng không có trong
  top 100 của kênh nào, fusion/rerank không thể cứu).
- Tỉ lệ query trả rỗng do scope (answerable nhưng bị chặn).
- Multi intent: coverage@K từng intent, tỉ lệ nhóm rỗng.
- Outside-domain false accept rate; latency p50/p95.

---

## 6. Thí nghiệm xác nhận giả thuyết (ưu tiên chạy đầu)

1. **Trần recall:** in |R| của mỗi query và 10/|R|. Nếu trần < 0,3 ở đa số query,
   "recall thấp" chủ yếu là do metric.
2. **Ablation tiền tố:** cùng intent, so "phố cổ Hội An" vs "Tôi muốn tìm ảnh phố cổ
   Hội An" trên `/search/semantic` và hybrid: overlap top 10 và Recall@10.
3. **Ablation case/template:** query gốc vs lowercase vs "this is a photo of …"
   (bản dịch Anh) trên kênh ảnh.
4. **Kênh đơn:** Recall@10/100 của image-only, metadata-only, lexical-only, rồi
   fusion. Nếu fusion < image-only, kênh phụ đang gây hại → giảm trọng số ngay.
5. **Hoà điểm lexical:** với mỗi query, đếm số ảnh có cùng điểm lexical tại rank 100.
   Nếu hàng trăm, M2b là ưu tiên cao.
6. **Scope:** đếm query answerable bị `unclear`/`out_of_scope` ở `/search/semantic`.
