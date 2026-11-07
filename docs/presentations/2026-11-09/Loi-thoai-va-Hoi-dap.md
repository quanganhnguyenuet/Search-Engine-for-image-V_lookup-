# VisualSeek — Lời thoại và hỏi đáp

Buổi góp ý: thứ Hai 09/11/2026. Bộ chính: 8 slide / 8 phút; bản ngắn: 5 phút.

## Cách sử dụng

Mở PowerPoint ở Presenter View để xem lời thoại đã nhúng. PDF slide dùng khi cần giữ bố cục; PDF tài liệu này dành cho luyện nói và hỏi đáp. Slide 4 dùng ảnh chụp UI thật; ảnh full và response nằm trong assets/ và evidence/.

## Phân bổ thời gian

| Slide | Nội dung | Bản 8 phút | Bản 5 phút |
|---|---|---:|---:|
| 1 | Tìm ảnh theo nhu cầu, không chỉ theo tên. | 40 giây | 25 giây |
| 2 | Pipeline đã chạy trên toàn bộ corpus | 60 giây | 35 giây |
| 3 | Chọn luồng xử lý theo truy vấn | 65 giây | 40 giây |
| 4 | Kết quả quan sát ≠ benchmark chất lượng | 60 giây | 40 giây |
| 5 | Đo đúng chỉ số, ở đúng tầng | 90 giây | 55 giây |
| 6 | Tối ưu từ phần đang tốn tài nguyên | 70 giây | 45 giây |
| 7 | Agent search: tìm, kiểm tra, rồi quyết định | 60 giây | 40 giây |
| 8 | Xin góp ý để chọn bước tiếp theo | 35 giây | 20 giây |

Bản 10 phút: giữ bài nói 8 phút, dành thêm 2 phút cho kế hoạch benchmark và trao đổi. Mốc cộng dồn bản chính: 0:40, 1:40, 2:45, 3:45, 5:15, 6:25, 7:25, 8:00.

## Slide 1 — Tìm ảnh theo nhu cầu, không chỉ theo tên.

Thời lượng: 40 giây; bản ngắn 25 giây.

VisualSeek là dự án tìm kiếm ảnh trên corpus văn hóa Việt Nam. Mục tiêu là cho phép người dùng tìm bằng tên, mô tả tự nhiên hoặc ảnh tham chiếu. Ví dụ, một người chuẩn bị bài giảng có thể cần ảnh nghệ nhân hướng dẫn trẻ làm gốm nhưng không biết tên ảnh hay làng nghề. Cái khó nằm ở khoảng cách giữa một ảnh cùng chủ đề và một ảnh thực sự đáp ứng yêu cầu. Khi thêm màu sắc, số lượng, quan hệ hoặc điều kiện loại trừ, chỉ tìm ảnh giống về ngữ nghĩa là chưa đủ. Hôm nay em muốn trình bày phần đã làm được và xin góp ý về cách đánh giá cùng hướng phát triển.

Nguồn / đối chiếu:

- README.md
- docs/contextual-image-search.md
- https://www.kaggle.com/datasets/blackcubo/vietnamese-culture-vqa-dataset

## Slide 2 — Pipeline đã chạy trên toàn bộ corpus

Thời lượng: 60 giây; bản ngắn 35 giây.

Hiện dữ liệu có sáu nhóm văn hóa gồm ẩm thực, kiến trúc, phong cảnh, lễ hội, trang phục và thủ công mỹ nghệ. Sau khử trùng lặp bằng hash, corpus có 15.423 asset. Index hiện cho phép tìm kiếm 15.411 asset; 12 asset được cách ly do vấn đề dữ liệu đã xác nhận. Em đã có quy trình chuẩn bị dữ liệu, tạo hai loại vector ảnh và metadata bằng SigLIP2 768 chiều, chạy embedding trên Kaggle và lưu artifact trên Hugging Face. Phía ứng dụng đã có giao diện, API và các chế độ keyword, semantic, hybrid. Router, cache, planner và reranker tùy chọn cũng đã có. Đây là bằng chứng hệ thống hoạt động; còn kết luận về chất lượng tổng thể cần tập đánh giá có nhãn.

Nguồn / đối chiếu:

- data/processed/culture-search/report.json
- data/embeddings/culture-labels-v1/manifest.json
- data/embeddings/culture-labels-v1/label-repair-report.json

## Slide 3 — Chọn luồng xử lý theo truy vấn

Thời lượng: 65 giây; bản ngắn 40 giây.

Khi người dùng nhập truy vấn, hệ thống định tuyến trước. Với tên hoặc tag rõ ràng, lookup local có thể trả kết quả mà không cần encoder hay LLM. Mô tả thông thường đi qua hybrid local. Hybrid kết hợp ba kênh: query so với vector ảnh, query so với vector metadata và đối sánh từ khóa. RRF hợp nhất thứ hạng của các kênh thay vì cộng trực tiếp các score khác thang đo. Với yêu cầu cần suy luận, nhánh LLM lập kế hoạch rồi dùng cùng retriever; rerank chỉ chạy khi được bật và có ứng viên. Tên không dấu nhập nhằng có thể được hỏi lại trước retrieval. Backend hiện vẫn là NumPy exact trên vector local. Hugging Face phân phối artifact; Qdrant chưa nằm trong search path. Cấu hình trên slide là cấu hình API được đọc khi chụp minh họa.

Nguồn / đối chiếu:

- services/api/src/visualseek_api/application/search.py
- packages/retrieval/src/visualseek_retrieval/hybrid.py
- evidence/status.json

## Slide 4 — Kết quả quan sát ≠ benchmark chất lượng

Thời lượng: 60 giây; bản ngắn 40 giây.

Đây là ba ảnh chụp từ giao diện hiện tại, kèm response API được lưu để đối chiếu. Bánh chưng minh họa tìm theo tên, ruộng bậc thang minh họa mô tả trực tiếp, còn áo dài đỏ không đội nón minh họa yêu cầu nhiều điều kiện. Chúng ta cần mở ảnh và kiểm tra từng điều kiện thay vì chỉ đọc tên hoặc score. Bộ lọc loại trừ hiện dựa vào tên và tag; khi rerank dùng annotation, model cũng chỉ biết nội dung có trong chú thích. Có hỗ trợ gửi pixel cho model phù hợp, nhưng em chưa dùng kết quả đó để khẳng định toàn bộ điều kiện được xác minh. Bộ đánh giá có 200 truy vấn, song chưa có nhãn relevance và answerable được xác nhận. Vì vậy em chưa công bố recall và chưa kết luận hybrid tốt hơn semantic. Nếu một lần gọi LLM thất bại, ảnh local dự phòng sẽ được ghi rõ trên slide và trong evidence.

Nguồn / đối chiếu:

- evidence/capture-summary.json
- data/evaluation/fusion-pool-labels-v1.json
- docs/search-fusion-setup.md

## Slide 5 — Đo đúng chỉ số, ở đúng tầng

Thời lượng: 90 giây; bản ngắn 55 giây.

Latency trả lời người dùng chờ bao lâu. p50 là phân vị giữa, p95 là mức mà khoảng 95 phần trăm request không vượt quá trong tập đo. Em sẽ đo cả API lẫn trải nghiệm hiển thị ảnh, và tách thời gian routing, encoding, retrieval, planning, rerank. Recall trả lời hệ thống thu hồi được bao nhiêu trong tổng số ảnh đúng. Ví dụ có 20 ảnh đúng trong corpus, top 10 lấy được 6 ảnh đúng: Recall@10 là 6 chia 20 bằng 30 phần trăm; Precision@10 là 6 chia 10 bằng 60 phần trăm. Ví dụ này chỉ minh họa, không phải kết quả dự án. Precision và nDCG hữu ích cho danh sách cuối mà người dùng thấy. Recall thường được đo offline với tập đáp án đủ tin cậy; không tính được recall thật cho mỗi query mới chỉ từ score. Khi thử ANN, ta còn đo độ trùng top-K với exact search. ANN recall tốt chỉ cho biết giữ được hàng xóm vector, không bảo đảm ảnh đúng theo yêu cầu. HitRate chỉ cần có một ảnh đúng, không thay thế recall. Runner hiện có HitRate, Precision, nDCG và chỉ số từ chối trên pool; pool chưa đầy đủ không cho phép công bố corpus recall.

Nguồn / đối chiếu:

- https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-unranked-retrieval-sets-1.html
- https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-ranked-retrieval-results-1.html
- packages/evaluation/src/visualseek_evaluation/relevance.py

## Slide 6 — Tối ưu từ phần đang tốn tài nguyên

Thời lượng: 70 giây; bản ngắn 45 giây.

Số đo local cho thấy ảnh gốc chiếm khoảng 6,51 GiB, còn hai ma trận vector float32 khoảng 90,4 MiB. Hai con số này chỉ là các thành phần cụ thể, chưa phải toàn bộ dung lượng dự án. Còn có metadata, model cache, checkpoint và các bản artifact. Storage nên được tách thành ảnh gốc theo hash, thumbnail cho hiển thị, metadata và vector liên kết bằng asset ID. Khi triển khai có thể dùng object storage và CDN; phần này hiện chưa được tích hợp. Về latency, router và cache đã có, bước tiếp theo là warmup và profiling để xác định thời gian đang nằm ở encoder, retrieval hay LLM. Cần tách cold với warm, cache hit với miss và các mức đồng thời. Chỉ thử ANN hoặc quantization khi có nhu cầu đã đo được, so với exact baseline. Nén biểu diễn vector có thể giảm bộ nhớ phần đó nhưng không đồng nghĩa tổng disk giảm cùng tỷ lệ, đặc biệt nếu vẫn giữ bản float32 để rescore.

Nguồn / đối chiếu:

- evidence/facts.json
- scripts/benchmark_text_search.py
- https://qdrant.tech/documentation/operations/optimize/

## Slide 7 — Agent search: tìm, kiểm tra, rồi quyết định

Thời lượng: 60 giây; bản ngắn 40 giây.

Hệ thống hiện có router, planner, retrieval và rerank theo luồng định sẵn. Agent search là hướng để model quyết định bước tiếp theo dựa trên kết quả thực tế: đổi truy vấn, tìm tiếp, kiểm tra ảnh hoặc hỏi người dùng. Ví dụ tìm riêng ảnh làm gốm và ảnh dệt thổ cẩm cần hai nhóm kết quả; người lớn hướng dẫn trẻ làm gốm là một cảnh có nhiều điều kiện, không phải hai nhu cầu độc lập. Một thử nghiệm nhỏ có thể cho phép tối đa hai vòng retrieval trong corpus, có điều kiện dừng và hỏi rõ khi nhập nhằng. Agent không thay thế index và cũng không tự sửa được nhãn sai hoặc ảnh thiếu. Em muốn đánh giá mức đáp ứng điều kiện, coverage từng nhóm và chất lượng ranking, đồng thời ghi latency, số lần gọi model và chi phí. Chỉ mở rộng nếu phần chất lượng tăng thêm xứng đáng với chi phí và thời gian chờ.

Nguồn / đối chiếu:

- https://www.anthropic.com/research/building-effective-agents
- services/api/src/visualseek_api/application/search.py

## Slide 8 — Xin góp ý để chọn bước tiếp theo

Thời lượng: 35 giây; bản ngắn 20 giây.

Em đề xuất ưu tiên hoàn thiện benchmark trước, dùng nhãn để xác định lỗi chất lượng chính rồi mới chọn cách sửa. Sau đó em sẽ tối ưu storage và latency trong giới hạn chất lượng đã đo, và thử agent trên nhóm truy vấn khó. Em muốn xin góp ý ba điểm: bộ truy vấn và cách chấm đã phản ánh đúng bài toán chưa; nên ưu tiên metadata, retrieval hay kiểm chứng điều kiện bằng pixel; và trường hợp nào cần agent để tạo giá trị rõ ràng. Các tiêu chí và ngưỡng latency hoặc recall sẽ được chốt dựa trên baseline cùng yêu cầu sử dụng, thay vì đặt một con số chưa có căn cứ.

Nguồn / đối chiếu:

- docs/search-fusion-setup.md
- packages/evaluation/src/visualseek_evaluation/relevance.py

## Minh họa đã chụp

Cấu hình API thật tại thời điểm capture lưu ở evidence/status.json; cấu hình file .env được đọc lại trong evidence/facts.json, chỉ gồm các trường được cho phép, không có khóa bí mật.

- **bánh chưng**: route `lexical`, 0 lần gọi LLM; 24 kết quả. Local dự phòng: False. Top 3: Bánh chưng Tết, Bánh chưng Tết, Bánh chưng gù.
- **ruộng bậc thang xanh ở vùng núi**: route `hybrid`, 0 lần gọi LLM; 24 kết quả. Local dự phòng: False. Top 3: Ruộng bậc thang kiến trúc, Ruộng bậc thang Sa Pa, Ruộng bậc thang Sa Pa.
- **người mặc áo dài đỏ, không đội nón**: route `hybrid`, 0 lần gọi LLM; 24 kết quả. Local dự phòng: True. Top 3: Áo dài Việt Nam, Áo dài Việt Nam, Đám cưới Việt Nam.

Ảnh trên slide 4 là ảnh chụp nguyên card đầu tiên trong UI, không phải ảnh tự chọn để đại diện cho top-1. Đây là kiểm tra chức năng và quan sát trực quan; chưa có nhãn độc lập để tính chất lượng. Timing trong response là thời gian của lần capture, không phải benchmark.

## Kế hoạch benchmark để thảo luận

1. Chấm ảnh theo pixel bằng grade 0/1/2, xác nhận answerable; không coi mô tả tự động là ground truth. Rà soát một phần nhãn bởi người thứ hai và giải quyết bất đồng.
2. Dùng development để tune trọng số/gate; giữ held-out cho đánh giá sau khi khóa cấu hình. Ghi corpus, revision, pipeline signature, model và top-K.
3. So keyword, image-only, metadata-only, RRF và rerank trên cùng query. Report theo nhóm tên, mô tả, thuộc tính, phủ định, nhập nhằng, nhiều nhóm độc lập và ngoài miền. Bổ sung query khó còn thiếu trong bộ 200 query.
4. Dùng HitRate/Precision/nDCG trên judged pool; ghi scope rõ. Chỉ báo corpus Recall@K khi có tập đáp án đủ đầy. Không coi ứng viên chưa chấm là không liên quan.
5. Latency: dùng benchmark_text_search.py với local/auto/forced-LLM, concurrency 1/4 và ít nhất 200 request mỗi cấu hình; tách route, cold/warm, cache hit/miss. Ghi lỗi, throughput và p50/p95; UI tải ảnh đo riêng.
6. Storage: báo logical disk bytes và RAM riêng. Đo từng thành phần và theo dõi phiên bản/model cache. Thử ANN/quantization với exact oracle, sau đó kiểm tra cả ANN recall lẫn relevance.
7. Agent: thử tối đa hai vòng retrieval trên cùng nhóm query khó, có ngân sách lời gọi/thời gian; đo full-constraint satisfaction, coverage theo nhóm, ranking, latency và chi phí. Lưu cả ca không cải thiện. Chưa đặt mục tiêu số trước baseline.

## Câu hỏi có thể gặp

### Vì sao dùng hybrid?

Ba kênh cung cấp tín hiệu bổ sung: pixel, mô tả và tên/từ khóa. Đây là giả thuyết cần ablation trên cùng tập query có nhãn; hiện chưa chứng minh hybrid vượt baseline.

### Vì sao chưa có recall?

200 query và candidate pool đã được tạo nhưng chưa chấm grade 0/1/2 và answerable. Hơn nữa pooled judgments chưa bảo đảm tìm hết ảnh đúng trong corpus. Có thể báo recall trên tập đáp án giới hạn đã xác nhận nếu mô tả rõ phạm vi; chưa gọi đó là corpus recall.

### Có nên báo HitRate trước?

Sau khi chấm nhãn có thể báo HitRate@10, Precision@10 và nDCG@10 trên judged pool. HitRate chỉ cho biết có ít nhất một ảnh đúng, nên cần đi cùng precision và mức đáp ứng toàn bộ điều kiện.

### Exact search có nghĩa recall 100% không?

Exact cho top-K chính xác theo vector và metric được chọn; không bảo đảm semantic relevance. ANN recall so với exact là chỉ số kỹ thuật khác recall theo tập ảnh đúng do con người xác nhận.

### Vì sao không chuyển Qdrant ngay?

Exact NumPy hiện là baseline trên khoảng 15 nghìn asset. Cần đo nút thắt retrieval và tải trước; vector DB hữu ích cho index, cập nhật và vận hành, nhưng không tự sửa nhãn sai, biểu diễn kém hay điều kiện thị giác bị bỏ sót.

### Storage nên tối ưu chỗ nào?

Đo riêng ảnh gốc, thumbnail, metadata, vector, model cache, checkpoint và artifact trùng phiên bản. Ảnh gốc local khoảng 6,51 GiB; hai ma trận vector khoảng 90,4 MiB; assets.jsonl hiện khoảng 46 MiB. Quản lý vòng đời và tránh bản sao thừa trước khi kết luận cần nén vector.

### Nén int8 có giảm tổng dung lượng bốn lần?

Chỉ có thể nói về kích thước biểu diễn vector so với float32 trong điều kiện cụ thể. Tổng dung lượng còn originals, metadata, graph index, backup; nếu giữ float32 để rescore thì tổng disk không giảm cùng tỷ lệ. Phải kiểm tra hỗ trợ dtype: loader hiện yêu cầu float32.

### Latency đang là bao nhiêu?

Ba lần capture là ví dụ có cấu hình và timing, không phải benchmark tải. Không dùng chúng làm p95/SLO. Cần warmup, tách cache hit/miss và từng route, chạy nhiều query ở concurrency 1/4, ghi lỗi và throughput. Đo API và hiển thị ảnh riêng.

### Làm sao đánh giá phủ định và không có đáp án?

Chấm bằng pixel cho toàn bộ điều kiện; theo dõi tỉ lệ vi phạm phủ định, false accept ngoài miền và empty rate trên query có đáp án. Không xem thiếu mô tả là bằng chứng không có đối tượng. Relevance gate hiện ở shadow, chưa tự loại chỉ dựa vào ngưỡng chưa hiệu chỉnh.

### Agent khác planner hiện có thế nào?

Planner hiện tạo plan rồi đi qua luồng cố định. Agent đề xuất chọn hành động tiếp dựa trên kết quả công cụ, có thể tìm lại/kiểm tra/hỏi rõ. Giới hạn thử nghiệm là hai vòng retrieval, trong corpus, có điều kiện dừng; chưa có vòng agent này trong production code.

### Agent có bắt buộc gọi nhiều agent con?

Không. Thử một agent có bộ công cụ tối thiểu trước. Chỉ thêm thành phần khi chứng minh được cải thiện trên nhóm query khó so với pipeline hiện tại cùng ngân sách.

### Thí nghiệm tiếp theo cụ thể là gì?

Chấm development trước, chốt config fusion/relevance gate trên development, sau đó đánh giá held-out. So keyword, image-only, metadata-only, RRF và rerank trên cùng artifact. Ghi pipeline signature để tránh dùng nhãn/ngưỡng cho pipeline khác.

## Kiểm tra trước buổi trình bày

- Mở PPTX trên máy trình chiếu, kiểm tra font Noto Sans; dùng PDF nếu máy thay font.
- Bật Presenter View; thử màn hình ngoài, tỉ lệ 16:9 và khả năng đọc slide 4/5.
- Luyện một lượt 8 phút và một lượt 5 phút theo bảng; không đọc toàn bộ tài liệu hỏi đáp.
- Bản chính dùng ảnh chụp nên có thể trình bày offline. Nếu muốn demo thêm, warmup API và chuẩn bị ảnh dự phòng.
- Không nói ‘recall cao’, ‘đã hiểu mọi điều kiện’ hoặc ‘Qdrant đang chạy search’ khi chưa có bằng chứng.

## Nguồn kỹ thuật

- ir: https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-unranked-retrieval-sets-1.html
- rank: https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-ranked-retrieval-results-1.html
- qdrant: https://qdrant.tech/documentation/operations/optimize/
- agents: https://www.anthropic.com/research/building-effective-agents
- dataset: https://www.kaggle.com/datasets/blackcubo/vietnamese-culture-vqa-dataset
