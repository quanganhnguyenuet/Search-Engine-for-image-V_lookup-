"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ErrorBox, PageTitle, PendingBadge } from "@/components/admin/admin-shell";
import { Button } from "@/components/ui/button";
import { admin, ENTITY_FILTERS, type EntityFilter, type EntityRow, FIELD_LABEL, type FieldName, imageUrl } from "@/lib/admin";
import { cn } from "@/lib/utils";

const PAGE = 30;
const DEBOUNCE_MS = 250;

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "done"; total: number; items: EntityRow[] };

function readUrl(): { q: string; filter: EntityFilter; page: number } {
  const params = new URL(window.location.href).searchParams;
  const filter = params.get("filter") as EntityFilter | null;
  return {
    q: params.get("q") ?? "",
    filter: filter && ENTITY_FILTERS.some(([id]) => id === filter) ? filter : "all",
    page: Math.max(0, Number(params.get("page") ?? 0) || 0),
  };
}

export function EntityList() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<EntityFilter>("all");
  const [page, setPage] = useState(0);
  const [ready, setReady] = useState(false);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const latest = useRef(0);

  useEffect(() => {
    const initial = readUrl();
    setQ(initial.q);
    setQuery(initial.q);
    setFilter(initial.filter);
    setPage(initial.page);
    setReady(true);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (q !== query) {
        setQuery(q);
        setPage(0);
      }
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [q, query]);

  useEffect(() => {
    if (!ready) return;
    const url = new URL(window.location.href);
    for (const [key, value] of [
      ["q", query],
      ["filter", filter === "all" ? "" : filter],
      ["page", page ? String(page) : ""],
    ] as const) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    window.history.replaceState(null, "", url);
    const id = ++latest.current;
    setState({ kind: "loading" });
    admin.entities(query, filter, page * PAGE, PAGE).then(
      (data) => id === latest.current && setState({ kind: "done", ...data }),
      (error: Error) => id === latest.current && setState({ kind: "error", message: error.message }),
    );
  }, [ready, query, filter, page, attempt]);

  const total = state.kind === "done" ? state.total : 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <>
      <PageTitle
        title="Sản phẩm"
        description="Mỗi dòng là một sản phẩm trong đồ thị (các bản ghi trùng đã được gộp). Mở một sản phẩm để xem quan hệ, sửa tri thức và quản lý ảnh."
      />
      <div className="mb-4 flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm font-medium">
          Tìm theo tên, bí danh, hãng hoặc mã sản phẩm
          <input
            type="search"
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder="ví dụ: Panadol, Shimano, nước mắm"
            className="h-11 w-full max-w-xl rounded-md border bg-surface px-3 text-base font-normal outline-none focus-visible:ring-2 focus-visible:ring-brand"
          />
        </label>
        <div role="group" aria-label="Lọc" className="flex flex-wrap gap-2">
          {ENTITY_FILTERS.map(([id, label]) => (
            <button
              key={id}
              type="button"
              aria-pressed={filter === id}
              onClick={() => {
                setFilter(id);
                setPage(0);
              }}
              className={cn(
                "min-h-9 rounded-full border px-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
                filter === id ? "border-brand bg-brand-soft font-medium text-brand" : "bg-surface hover:bg-muted",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <p aria-live="polite" className="mb-2 text-sm text-muted-foreground">
        {state.kind === "done" && `${total.toLocaleString("vi-VN")} sản phẩm`}
        {state.kind === "loading" && "Đang tải…"}
      </p>
      {state.kind === "error" && <ErrorBox message={state.message} onRetry={() => setAttempt((n) => n + 1)} />}
      {state.kind === "loading" && (
        <ul className="divide-y rounded-lg border bg-surface">
          {Array.from({ length: 8 }, (_, i) => (
            <li key={i} className="flex items-center gap-3 p-3">
              <div className="size-12 animate-pulse rounded bg-muted" />
              <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
            </li>
          ))}
        </ul>
      )}
      {state.kind === "done" && state.items.length === 0 && (
        <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
          Không có sản phẩm nào khớp. Thử bỏ bộ lọc hoặc gõ ít chữ hơn.
        </p>
      )}
      {state.kind === "done" && state.items.length > 0 && (
        <ul className="divide-y rounded-lg border bg-surface">
          {state.items.map((row) => (
            <li key={row.id}>
              <Link
                href={`/admin/entities/${row.id}`}
                className="flex items-center gap-3 p-3 transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand"
              >
                <span className="flex size-12 shrink-0 items-center justify-center overflow-hidden rounded border bg-white">
                  {row.thumb ? (
                    // eslint-disable-next-line @next/next/no-img-element -- served by the API, sizes vary
                    <img src={imageUrl(row.thumb)} alt="" width={48} height={48} loading="lazy" className="size-full object-contain" />
                  ) : (
                    <span className="text-[10px] text-slate-500">không ảnh</span>
                  )}
                </span>
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate font-medium">{row.name}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {[row.brand || "chưa có hãng", row.category || "chưa có loại hàng"].join(" · ")}
                  </span>
                </span>
                <span className="hidden flex-wrap items-center justify-end gap-1.5 text-xs sm:flex">
                  {row.needs_review && (
                    <span className="rounded border border-warn/50 px-1.5 py-0.5 text-warn">Cần duyệt</span>
                  )}
                  {row.pending.length > 0 && (
                    <PendingBadge>
                      {row.pending.map((f) => FIELD_LABEL[f as FieldName] ?? f).join(", ")}
                    </PendingBadge>
                  )}
                  <span className="tabular-nums text-muted-foreground">
                    {row.images} ảnh
                    {row.hidden_images > 0 && ` · ${row.hidden_images} ẩn`}
                    {row.uploaded_images > 0 && ` · ${row.uploaded_images} tải lên`}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {state.kind === "done" && pages > 1 && (
        <nav aria-label="Trang" className="mt-4 flex items-center gap-3 text-sm">
          <Button variant="outline" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            Trang trước
          </Button>
          <span className="tabular-nums text-muted-foreground">
            {page + 1} / {pages}
          </span>
          <Button variant="outline" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}>
            Trang sau
          </Button>
        </nav>
      )}
    </>
  );
}
