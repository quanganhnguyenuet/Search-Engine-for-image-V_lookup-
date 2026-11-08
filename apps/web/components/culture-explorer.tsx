"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import Image from "next/image";
import { categories, type ClarificationTurn, type CultureAsset, type SearchResponse } from "@/lib/culture-types";

type SearchMode = "contextual" | "semantic" | "rag" | "keyword";

function SearchIcon() {
  return <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><circle cx="10.8" cy="10.8" r="7.3" /><path d="m16 16 5 5" /></svg>;
}

function AssetImage({ asset, eager = false }: { asset: CultureAsset; eager?: boolean }) {
  const [failed, setFailed] = useState(false);
  return failed ? <div className="image-fallback">Ảnh hiện không khả dụng</div> :
    <Image src={asset.imageUrl} alt={asset.description || asset.title} width={1200} height={900} sizes="(max-width: 700px) 50vw, (max-width: 1050px) 33vw, 30vw" loading={eager ? "eager" : "lazy"} onError={() => setFailed(true)} />;
}

function Detail({ asset, close, search }: { asset: CultureAsset; close: () => void; search: (term: string) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { element?.close(); document.body.style.overflow = previous; };
  }, []);
  return <dialog className="detail-dialog" ref={dialog} onCancel={close} onClick={(event) => { if (event.target === event.currentTarget) close(); }} aria-labelledby="detail-title">
    <div className="detail-layout">
      <div className="detail-image"><AssetImage asset={asset} eager /></div>
      <div className="detail-copy">
        <button className="close-button" onClick={close} aria-label="Đóng chi tiết">×</button>
        <span className="eyebrow">{asset.categoryLabel}</span>
        <h2 id="detail-title">{asset.title}</h2>
        <p>{asset.description}</p>
        {asset.match_reason && <p className="source-note">Lý do gợi ý theo chú thích: {asset.match_reason}</p>}
        <div className="tag-list">{asset.tags.map((tag, index) => <button key={`${tag}-${index}`} onClick={() => { close(); search(tag); }}>{tag}</button>)}</div>
        {asset.context.regional_significance && <section><h3>Dấu ấn vùng miền</h3><p>{asset.context.regional_significance}</p></section>}
        {asset.context.historical_context && <section><h3>Câu chuyện văn hóa</h3><p>{asset.context.historical_context}</p></section>}
        <div className="source-note">Mô tả từ bộ dữ liệu, có thể cần đối chiếu thêm.<br /><a href={asset.sourceUrl} target="_blank" rel="noreferrer">Xem nguồn Vietnamese Culture VQA ↗</a></div>
        <a className="original-link" href={asset.imageUrl} target="_blank" rel="noreferrer">Mở ảnh gốc <span>↗</span></a>
      </div>
    </div>
  </dialog>;
}

export default function CultureExplorer() {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState<CultureAsset | null>(null);
  const [mode, setMode] = useState<SearchMode>("contextual");
  const [imageQuery, setImageQuery] = useState<{ name: string; base64: string } | null>(null);
  const [clarificationContext, setClarificationContext] = useState<ClarificationTurn[]>([]);
  const [clarificationAnswer, setClarificationAnswer] = useState("");
  const [forcePlanning, setForcePlanning] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLElement>(null);

  const readUrl = useCallback(() => {
    const params = new URLSearchParams(window.location.search);
    const term = params.get("q") ?? "";
    const selectedCategory = params.get("category") ?? "";
    const requestedPage = Number(params.get("page") ?? 1);
    const requestedMode = params.get("mode");
    setMode(requestedMode === "keyword" || requestedMode === "rag" || requestedMode === "semantic" ? requestedMode : "contextual");
    setImageQuery(null);
    const savedContext = window.history.state?.clarificationContext;
    setClarificationContext(Array.isArray(savedContext) ? savedContext : []);
    setClarificationAnswer("");
    setDraft(term); setQuery(term);
    setCategory(categories.some(({ id }) => id === selectedCategory) ? selectedCategory : "");
    setPage(Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1);
  }, []);

  useEffect(() => {
    readUrl();
    window.addEventListener("popstate", readUrl);
    return () => window.removeEventListener("popstate", readUrl);
  }, [readUrl]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    const params = new URLSearchParams({ q: query, category, page: String(page) });
    const useAI = mode !== "keyword" && Boolean(query || imageQuery);
    const request = useAI ? fetch("/api/culture/ai", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
      body: JSON.stringify({ mode, query: imageQuery ? undefined : query,
        image_base64: imageQuery?.base64, category,
        ...(mode === "contextual" ? { clarification_context: clarificationContext,
          use_llm: forcePlanning ? true : undefined } : {}) }),
    }) : fetch(`/api/culture?${params}`, { signal: controller.signal });
    request
      .then(async (response) => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.error ?? "Không thể tải thư viện ảnh. Vui lòng thử lại.");
        return result as SearchResponse;
      })
      .then((result) => { if (!controller.signal.aborted) setData(result); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, category, page, retry, mode, imageQuery, clarificationContext, forcePlanning]);

  function navigate(term: string, nextCategory = category, nextPage = 1, nextMode = mode,
                    context: ClarificationTurn[] = []) {
    const cleaned = term.normalize("NFC").trim();
    if (cleaned.length > (nextMode === "contextual" ? 1000 : 200)) {
      setError("Mô tả quá dài. Hãy rút gọn trước khi tìm kiếm."); return;
    }
    setDraft(cleaned); setQuery(cleaned); setCategory(nextCategory); setPage(nextPage);
    setMode(nextMode); setImageQuery(null);
    setClarificationContext(context); setClarificationAnswer("");
    const params = new URLSearchParams();
    if (cleaned) params.set("q", cleaned);
    if (nextCategory) params.set("category", nextCategory);
    if (nextPage > 1) params.set("page", String(nextPage));
    if (nextMode !== "contextual") params.set("mode", nextMode);
    window.history.pushState({ clarificationContext: context }, "", params.size ? `?${params}` : "/");
    resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function submit(event: FormEvent) { event.preventDefault(); navigate(draft); }
  function answerClarification(event: FormEvent) {
    event.preventDefault();
    if (!clarificationAnswer.trim()) return;
    navigate(clarificationAnswer, category, 1, "contextual", data?.clarification_context ?? []);
  }
  function changePage(next: number) {
    navigate(query, category, next, mode, clarificationContext);
  }

  function filterCategory(next: string) {
    if (imageQuery) {
      setCategory(next); setPage(1);
      resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else navigate(query, next, 1, mode, clarificationContext);
  }

  function uploadImage(file: File | undefined) {
    if (!file) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 10 * 1024 * 1024) {
      setError("Chọn ảnh JPG, PNG hoặc WebP, tối đa 10 MB."); return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      navigate("", "", 1, "semantic");
      setImageQuery({ name: file.name, base64: String(reader.result).split(",")[1] });
    };
    reader.onerror = () => setError("Không đọc được ảnh đã chọn.");
    reader.readAsDataURL(file);
    if (fileRef.current) fileRef.current.value = "";
  }

  return <>
    <a className="skip-link" href="#collection">Đến bộ sưu tập ảnh</a>
    <header className="site-header">
      <a className="brand" href="/" aria-label="VietNam Culture — Trang chủ"><span className="brand-seal" aria-hidden="true">✳</span><span>VietNam <strong>Culture</strong><small>GÌN GIỮ NÉT VIỆT</small></span></a>
      <nav aria-label="Điều hướng chính"><a className="nav-active" href="#collection">Khám phá</a><a href="#about">Về thư viện <span>↗</span></a></nav>
      <span className="header-note"><span /> Một Việt Nam, muôn sắc màu</span>
    </header>

    <main>
      <section className="hero">
        <div className="hero-decoration" aria-hidden="true">✺</div>
        <p className="eyebrow"><span /> THƯ VIỆN HÌNH ẢNH VĂN HÓA VIỆT NAM</p>
        <h1>Một góc nhìn.<br />Muôn nét <em>Việt Nam.</em></h1>
        <p className="hero-description">Từ hương vị thân quen đến những di sản lâu đời.<br className="desktop-break" /> Khám phá câu chuyện Việt Nam qua từng khung hình.</p>
        <div className="search-modes" role="group" aria-label="Chế độ tìm kiếm">
          {([ ["contextual", "Tìm theo yêu cầu"], ["semantic", "Ngữ nghĩa"], ["keyword", "Từ khóa"] ] as const).map(([value, label]) =>
            <button type="button" key={value} aria-pressed={mode === value} className={mode === value ? "active" : ""} onClick={() => navigate(query, category, 1, value)}>{label}</button>)}
        </div>
        <form className="search-form" onSubmit={submit} role="search">
          <label className="sr-only" htmlFor="culture-search">Tìm kiếm ảnh văn hóa Việt Nam</label>
          <SearchIcon />
          <input ref={searchRef} id="culture-search" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={mode === "keyword" ? "Nhập tên, tags hoặc từ khóa…" : "Mô tả ảnh bạn muốn tìm, ví dụ: áo dài đỏ trước phố cổ…"} maxLength={mode === "contextual" ? 1000 : 200} autoComplete="off" enterKeyHint="search" />
          {draft && <button className="clear-search" type="button" onClick={() => { setDraft(""); searchRef.current?.focus(); }} aria-label="Xóa từ khóa">×</button>}
          <button className="search-submit" type="submit">Tìm kiếm <span aria-hidden="true">↗</span></button>
        </form>
        {mode === "contextual" && <label className="ai-planning-option"><input type="checkbox" checked={forcePlanning} onChange={(event) => setForcePlanning(event.target.checked)} /> Nhờ AI phân tích yêu cầu và hỏi lại khi cần</label>}
        {mode === "semantic" && <div className="image-query-controls"><input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" aria-label="Chọn ảnh để tìm ảnh tương tự" onChange={(event) => uploadImage(event.target.files?.[0])} /><button onClick={() => fileRef.current?.click()}>▧ Tìm bằng ảnh</button>{imageQuery && <span>{imageQuery.name} <button onClick={() => navigate("", "")} aria-label="Xóa ảnh tìm kiếm">×</button></span>}</div>}
        <div className="suggestions"><span>Thử khám phá</span>{["người mặc áo dài đỏ", "ruộng bậc thang xanh", "phố cổ Hội An", "nghệ nhân làm gốm"].map((term) => <button key={term} onClick={() => navigate(term, "")}>{term}<span aria-hidden="true">↗</span></button>)}</div>
        <div className="hero-foot"><span>{mode === "keyword" ? "Tìm từ khóa có dấu hoặc không dấu" : mode === "contextual" ? "Tìm nhanh bằng tên, tags hoặc mô tả ảnh" : "Tìm bằng mô tả tự nhiên hoặc một ảnh tương tự"}</span><span>VĂN HÓA KẾT NỐI CHÚNG TA <span aria-hidden="true">↓</span></span></div>
      </section>

      <section className="collection" id="collection" ref={resultsRef}>
        <div className="collection-top"><div><p className="eyebrow">NHỮNG CÂU CHUYỆN BẰNG HÌNH ẢNH</p><h2>{query ? <>Kết quả cho <em>“{query}”</em></> : <>Khám phá <em>nét Việt</em></>}</h2></div><span className="library-count">{data ? data.libraryTotal.toLocaleString("vi-VN") : "…"} hình ảnh · 6 chủ đề</span></div>
        <div className="category-filters" role="group" aria-label="Lọc theo chủ đề">
          <button aria-pressed={!category} className={!category ? "active" : ""} onClick={() => filterCategory("")}><span aria-hidden="true">▦</span>Tất cả<span className="filter-count">{data ? Object.values(data.counts).reduce((a, b) => a + b, 0) : ""}</span></button>
          {categories.map((item) => <button key={item.id} aria-pressed={category === item.id} className={category === item.id ? "active" : ""} onClick={() => filterCategory(item.id)}><span aria-hidden="true">{item.symbol}</span>{item.label}<span className="filter-count">{data?.counts[item.id] ?? ""}</span></button>)}
        </div>
        <div className="results-toolbar"><p role="status" aria-live="polite">{loading ? "Đang tìm những khung hình dành cho bạn…" : error ? "Tạm thời chưa tải được dữ liệu" : `${data?.total.toLocaleString("vi-VN") ?? 0} hình ảnh${category ? ` · ${categories.find(({ id }) => id === category)?.label}` : " trong bộ sưu tập"}`}</p>{(query || category) ? <button onClick={() => navigate("", "")}>Xóa bộ lọc ×</button> : <span>Chạm vào ảnh để khám phá câu chuyện <span aria-hidden="true">↗</span></span>}</div>

        {!loading && !error && data?.answer && <section className="rag-answer" aria-label="Câu trả lời RAG"><p className="eyebrow">{data.abstained ? "CHƯA ĐỦ BẰNG CHỨNG" : "CÂU TRẢ LỜI TỪ THƯ VIỆN"}</p><p className="answer-text">{data.answer}</p><div className="rag-citations">{data.citations?.map((citation) => <button key={citation.id} onClick={() => { const asset = data.items.find((item) => item.id === citation.asset_id); if (asset) setSelected(asset); }}>[{citation.id}] {citation.title} ↗</button>)}</div><small>Tổng hợp từ chú thích dataset, chưa phải xác minh lịch sử độc lập.{data.generator_model ? ` · ${data.generator_model}` : ""}</small></section>}
        {!loading && !error && !!data?.items.length && data.result_scope === "top_k" && <p className="semantic-note">Hiển thị {data.items.length} ảnh gần nhất trong {data.libraryTotal.toLocaleString("vi-VN")} ảnh đã index. Số ảnh theo chủ đề là thống kê của index.{data.timings ? ` · Embedding ${Math.round(data.timings.embedding_ms)} ms · Retrieval ${Math.round(data.timings.retrieval_ms)} ms` : ""}</p>}
        {!loading && !error && data?.coverage_complete === false && <p className="semantic-note" role="status">Index hiện mới có {data.libraryTotal.toLocaleString("vi-VN")}{data.corpus_total ? ` / ${data.corpus_total.toLocaleString("vi-VN")}` : ""} ảnh. Kết quả chỉ lấy từ phần đã index.</p>}
        {!loading && !error && data?.scope_rejected && <section className="rag-answer" role="status" aria-label="Yêu cầu ngoài phạm vi"><p className="eyebrow">YÊU CẦU NGOÀI PHẠM VI</p><p>{data.scope_message}</p><p>Bạn có thể khám phá các chủ đề sau:</p><div className="rag-citations">{data.suggested_queries?.map((suggestion) => <button key={suggestion} onClick={() => navigate(suggestion, category, 1, mode)}>{suggestion}</button>)}</div></section>}
        {!loading && !error && data?.clarification_required && <section className="rag-answer" aria-label="Làm rõ yêu cầu tìm ảnh"><p className="eyebrow">CẦN LÀM RÕ YÊU CẦU</p>{!!data.clarification_context?.length && <p>Yêu cầu ban đầu: {data.clarification_context[0].query}</p>}<p>{data.clarification_question || "Tên không dấu này có nhiều nghĩa. Bạn muốn tìm:"}</p><div className="rag-citations">{data.suggested_queries?.map((suggestion) => <button key={suggestion} onClick={() => navigate(suggestion, category, 1, "contextual", data.clarification_context ?? [])}>{suggestion}</button>)}</div><form className="clarification-form" onSubmit={answerClarification}><label htmlFor="clarification-answer">Hoặc trả lời theo ý bạn</label><input id="clarification-answer" value={clarificationAnswer} maxLength={1000} onChange={(event) => setClarificationAnswer(event.target.value)} placeholder="Ví dụ: phố cổ vào ban đêm" required /><button type="submit" disabled={!clarificationAnswer.trim()}>Tiếp tục tìm kiếm</button></form></section>}
        {!loading && !error && data?.interpretation && !data.clarification_required && !data.scope_rejected && <section className="rag-answer" aria-label="Cách hiểu yêu cầu">
          <p className="eyebrow">YÊU CẦU ĐƯỢC HIỂU LÀ</p>
          <p>{data.interpretation.intent}</p>
          {!!data.interpretation.requirements?.length && <p><strong>Điều kiện bắt buộc:</strong> {data.interpretation.requirements.map((condition) => condition.text).join("; ")}</p>}
          {!!data.interpretation.preferences?.length && <p><strong>Ưu tiên tùy chọn:</strong> {data.interpretation.preferences.map((condition) => condition.text).join("; ")}</p>}
          {!!data.interpretation.exclusions.length && <p><strong>Loại trừ:</strong> {data.interpretation.exclusions.join("; ")}</p>}
          <small>{data.evidence_type === "retrieved-image-pixels" ? "Model đã đối chiếu ảnh với yêu cầu; hãy mở ảnh để xem rõ chi tiết." : "Ảnh được gợi ý theo chú thích trong thư viện; chưa xác minh trực tiếp từng chi tiết trong ảnh."}</small>
        </section>}

        {error ? <div className="empty-state"><span aria-hidden="true">↻</span><h3>Thư viện đang tạm nghỉ một chút</h3><p>{error}</p><button onClick={() => setRetry((value) => value + 1)}>Thử lại</button></div> : loading ? <div className="photo-grid" aria-busy="true" aria-label="Đang tải ảnh">{Array.from({ length: 12 }, (_, index) => <div key={index} className="skeleton-card"><div /><span /><span /></div>)}</div> : !data?.items.length ? <div className="empty-state"><SearchIcon /><h3>{data?.scope_rejected ? "Yêu cầu nằm ngoài phạm vi thư viện" : data?.clarification_required ? "Làm rõ yêu cầu để tiếp tục" : data?.libraryTotal ? "Chưa tìm thấy khung hình phù hợp" : "Thư viện đang chờ những khung hình đầu tiên"}</h3><p>{data?.libraryTotal ? (data.scope_rejected ? "Chọn một gợi ý phía trên hoặc nhập yêu cầu về văn hóa Việt Nam." : data.clarification_required ? "Trả lời câu hỏi phía trên hoặc chọn một gợi ý để tiếp tục." : data.abstained ? "Chưa có ảnh đủ bằng chứng phù hợp. Thử điều chỉnh mô tả hoặc chọn chủ đề khác." : "Thử một từ khóa ngắn hơn, không dấu hoặc chọn chủ đề khác.") : "Chuẩn bị dataset ảnh local rồi làm mới trang để bắt đầu khám phá."}</p><button onClick={() => navigate("", "")}>Khám phá tất cả</button></div> : <>
          <div className="photo-grid">{data.items.map((asset, index) => <article className="photo-card" key={asset.id}><button className="photo-open" onClick={() => setSelected(asset)} aria-label={`Xem ${asset.title}`}><div className="photo-frame"><AssetImage asset={asset} eager={index < 4} /><span className="photo-category">{asset.categoryLabel}</span><span className="photo-arrow" aria-hidden="true">↗</span></div><div className="photo-caption"><h3>{asset.title}</h3><p>{asset.tags.slice(0, 2).join(" · ") || asset.categoryLabel}</p></div></button></article>)}</div>
          {data.pages > 1 && <nav className="pagination" aria-label="Phân trang kết quả"><button disabled={data.page === 1} onClick={() => changePage(data.page - 1)}>← <span>Trang trước</span></button><span>Trang <strong>{data.page}</strong> / {data.pages}</span><button disabled={data.page === data.pages} onClick={() => changePage(data.page + 1)}><span>Trang sau</span> →</button></nav>}
        </>}
      </section>
      <section className="about-strip" id="about"><span className="about-mark" aria-hidden="true">✳</span><div><p className="eyebrow">MỖI KHUNG HÌNH, MỘT DI SẢN</p><h2>Thêm một lần nhìn.<br />Thêm một lần yêu Việt Nam.</h2></div><p>Một không gian nhỏ để khám phá sự phong phú của văn hóa Việt. Từ những điều bình dị đến những giá trị được trao truyền qua nhiều thế hệ.</p></section>
    </main>
    <footer><a className="footer-brand" href="/">VietNam Culture<span>Kho tàng hình ảnh, câu chuyện và bản sắc.</span></a><a href="https://www.kaggle.com/datasets/blackcubo/vietnamese-culture-vqa-dataset" target="_blank" rel="noreferrer">Nguồn ảnh: Vietnamese Culture VQA ↗</a><span>Được tạo nên từ tình yêu nét Việt.</span></footer>
    {selected && <Detail asset={selected} close={() => setSelected(null)} search={(term) => navigate(term, "")} />}
  </>;
}
