"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import Image from "next/image";
import { categories, type CultureAsset, type SearchResponse } from "@/lib/culture-types";

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
  const searchRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLElement>(null);

  const readUrl = useCallback(() => {
    const params = new URLSearchParams(window.location.search);
    const term = params.get("q") ?? "";
    const selectedCategory = params.get("category") ?? "";
    const requestedPage = Number(params.get("page") ?? 1);
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
    fetch(`/api/culture?${params}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Không thể tải thư viện ảnh. Vui lòng thử lại.");
        return response.json() as Promise<SearchResponse>;
      })
      .then(setData)
      .catch((reason) => { if (reason.name !== "AbortError") setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, category, page, retry]);

  function navigate(term: string, nextCategory = category, nextPage = 1) {
    const cleaned = term.trim().slice(0, 200);
    setDraft(cleaned); setQuery(cleaned); setCategory(nextCategory); setPage(nextPage);
    const params = new URLSearchParams();
    if (cleaned) params.set("q", cleaned);
    if (nextCategory) params.set("category", nextCategory);
    if (nextPage > 1) params.set("page", String(nextPage));
    window.history.pushState(null, "", params.size ? `?${params}` : "/");
    resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function submit(event: FormEvent) { event.preventDefault(); navigate(draft); }
  function changePage(next: number) {
    navigate(query, category, next);
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
        <form className="search-form" onSubmit={submit} role="search">
          <label className="sr-only" htmlFor="culture-search">Tìm kiếm ảnh văn hóa Việt Nam</label>
          <SearchIcon />
          <input ref={searchRef} id="culture-search" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Bạn muốn khám phá điều gì?" maxLength={200} autoComplete="off" enterKeyHint="search" />
          {draft && <button className="clear-search" type="button" onClick={() => { setDraft(""); searchRef.current?.focus(); }} aria-label="Xóa từ khóa">×</button>}
          <button className="search-submit" type="submit">Tìm kiếm <span aria-hidden="true">↗</span></button>
        </form>
        <div className="suggestions"><span>Thử khám phá</span>{["áo dài", "lễ hội", "Hội An", "gốm"].map((term) => <button key={term} onClick={() => navigate(term, "")}>{term}<span aria-hidden="true">↗</span></button>)}</div>
        <div className="hero-foot"><span>Tìm kiếm bằng tiếng Việt có dấu hoặc không dấu</span><span>VĂN HÓA KẾT NỐI CHÚNG TA <span aria-hidden="true">↓</span></span></div>
      </section>

      <section className="collection" id="collection" ref={resultsRef}>
        <div className="collection-top"><div><p className="eyebrow">NHỮNG CÂU CHUYỆN BẰNG HÌNH ẢNH</p><h2>{query ? <>Kết quả cho <em>“{query}”</em></> : <>Khám phá <em>nét Việt</em></>}</h2></div><span className="library-count">{data ? data.libraryTotal.toLocaleString("vi-VN") : "…"} hình ảnh · 6 chủ đề</span></div>
        <div className="category-filters" role="group" aria-label="Lọc theo chủ đề">
          <button aria-pressed={!category} className={!category ? "active" : ""} onClick={() => navigate(query, "")}><span aria-hidden="true">▦</span>Tất cả<span className="filter-count">{data ? Object.values(data.counts).reduce((a, b) => a + b, 0) : ""}</span></button>
          {categories.map((item) => <button key={item.id} aria-pressed={category === item.id} className={category === item.id ? "active" : ""} onClick={() => navigate(query, item.id)}><span aria-hidden="true">{item.symbol}</span>{item.label}<span className="filter-count">{data?.counts[item.id] ?? ""}</span></button>)}
        </div>
        <div className="results-toolbar"><p role="status" aria-live="polite">{loading ? "Đang tìm những khung hình dành cho bạn…" : error ? "Tạm thời chưa tải được dữ liệu" : `${data?.total.toLocaleString("vi-VN") ?? 0} hình ảnh${category ? ` · ${categories.find(({ id }) => id === category)?.label}` : " trong bộ sưu tập"}`}</p>{(query || category) ? <button onClick={() => navigate("", "")}>Xóa bộ lọc ×</button> : <span>Chạm vào ảnh để khám phá câu chuyện <span aria-hidden="true">↗</span></span>}</div>

        {error ? <div className="empty-state"><span aria-hidden="true">↻</span><h3>Thư viện đang tạm nghỉ một chút</h3><p>{error}</p><button onClick={() => setRetry((value) => value + 1)}>Thử lại</button></div> : loading ? <div className="photo-grid" aria-busy="true" aria-label="Đang tải ảnh">{Array.from({ length: 12 }, (_, index) => <div key={index} className="skeleton-card"><div /><span /><span /></div>)}</div> : !data?.items.length ? <div className="empty-state"><SearchIcon /><h3>{data?.libraryTotal ? "Chưa tìm thấy khung hình phù hợp" : "Thư viện đang chờ những khung hình đầu tiên"}</h3><p>{data?.libraryTotal ? "Thử một từ khóa ngắn hơn, không dấu hoặc chọn chủ đề khác." : "Chuẩn bị dataset ảnh local rồi làm mới trang để bắt đầu khám phá."}</p><button onClick={() => navigate("", "")}>Khám phá tất cả</button></div> : <>
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
