"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { AccountBar } from "@/components/auth/account-bar";
import { QueryGraph } from "@/components/graph/query-graph";
import { Button } from "@/components/ui/button";
import { ImageDetail } from "@/components/search/image-detail";
import { PlanChips } from "@/components/search/plan-chips";
import { SearchIntro } from "@/components/search/search-intro";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import { SearchTracePanel } from "@/components/search/search-trace";
import { API_URL, type SearchResponse, searchText, thumbnailOf } from "@/lib/search";
import { cn } from "@/lib/utils";

const STAGES: [string, string][] = [
  ["embed_query", "embed"],
  ["s1_spotting", "S1"],
  ["s2_expand", "S2"],
  ["qdrant", "Qdrant"],
  ["total", "tổng"],
];

type State =
  | { kind: "idle" }
  | { kind: "loading"; query: string }
  | { kind: "error"; query: string; message: string }
  | { kind: "done"; data: SearchResponse; clientMs: number };

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const controller = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const run = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) {
      input.current?.focus();
      return;
    }
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setQuery(trimmed);
    setOpenIndex(null);
    setState({ kind: "loading", query: trimmed });
    const url = new URL(window.location.href);
    url.searchParams.set("q", trimmed);
    window.history.replaceState(null, "", url);
    const started = performance.now();
    try {
      const data = await searchText(trimmed, current.signal);
      setState({ kind: "done", data, clientMs: performance.now() - started });
    } catch (error) {
      if (current.signal.aborted) return;
      const message =
        error instanceof TypeError
          ? `Chưa kết nối được máy chủ tìm kiếm (${API_URL}). Kiểm tra máy chủ đang chạy rồi thử lại.`
          : (error as Error).message;
      setState({ kind: "error", query: trimmed, message });
    }
  }, []);

  // React StrictMode (dev) mounts effects twice; the first request would be aborted client-side
  // but the API still runs it, and its single worker then queues the real one behind it.
  const initialRun = useRef(false);

  useEffect(() => {
    const initial = new URL(window.location.href).searchParams.get("q");
    if (initial && !initialRun.current) {
      initialRun.current = true;
      void run(initial);
    }
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key === "/" && target?.tagName !== "INPUT") {
        event.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run]);

  const reset = () => {
    controller.current?.abort();
    setQuery("");
    setOpenIndex(null);
    setState({ kind: "idle" });
    const url = new URL(window.location.href);
    url.searchParams.delete("q");
    window.history.replaceState(null, "", url);
    input.current?.focus();
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void run(query);
  };

  const idle = state.kind === "idle";

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col gap-5 px-4 py-4 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <button
          type="button"
          onClick={reset}
          aria-label="V-Lookup, về trang tìm kiếm ban đầu"
          className="min-h-11 rounded text-xl font-bold tracking-tight text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
        >
          V-Lookup
        </button>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
          <ThemeToggle />
          <AccountBar />
        </div>
      </header>

      <main className="flex flex-col gap-8">
        <div
          className={cn(
            "flex flex-col rounded-lg border border-panel-border bg-panel",
            idle ? "gap-6 px-5 py-8 sm:px-10 sm:py-12" : "gap-2 p-4 sm:px-6",
          )}
        >
          <div className={idle ? "flex flex-col gap-3" : "sr-only"}>
            <h1 className="text-3xl font-bold leading-tight tracking-tight sm:text-5xl sm:leading-tight">
              Tìm ảnh sản phẩm bằng tiếng Việt
            </h1>
            <p className="max-w-[62ch] text-muted-foreground sm:text-lg">
              Đồ điện tử, thuốc, món đặc sản, phụ tùng, đồ thủ công. Mỗi kết quả là ảnh sản phẩm,
              xem được trang nguồn.
            </p>
          </div>

          <form onSubmit={onSubmit} role="search" className="flex flex-col gap-2">
            <label htmlFor="q" className={idle ? "text-sm font-medium" : "sr-only"}>
              Bạn đang tìm sản phẩm gì?
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                ref={input}
                id="q"
                name="q"
                type="search"
                autoComplete="off"
                enterKeyHint="search"
                maxLength={300}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Tên, mô tả, hãng hoặc đặc sản…"
                className={cn(
                  "w-full min-w-0 rounded-md border bg-surface px-4 text-base outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-brand",
                  idle ? "min-h-14 sm:text-lg" : "min-h-12",
                )}
              />
              <Button
                type="submit"
                variant="brand"
                size="lg"
                className={cn(
                  "shrink-0 text-base font-semibold focus-visible:ring-brand",
                  idle ? "h-14" : "h-12",
                )}
                disabled={state.kind === "loading"}
              >
                {state.kind === "loading" ? "Đang tìm…" : "Tìm kiếm"}
              </Button>
            </div>
            {idle && (
              <p className="hidden text-sm text-muted-foreground sm:block">
                Mẹo: nhấn phím <kbd className="rounded border bg-surface px-1.5">/</kbd> để gõ
                vào ô tìm kiếm.
              </p>
            )}
          </form>
        </div>

        {idle && <SearchIntro onPick={(text) => void run(text)} />}

        <section
          aria-live="polite"
          aria-busy={state.kind === "loading"}
          className={cn("flex flex-col gap-4", idle && "sr-only")}
        >
          {state.kind === "loading" && (
            <>
              <p className="text-muted-foreground">Đang tìm “{state.query}”…</p>
              <ResultSkeleton />
            </>
          )}
          {state.kind === "error" && (
            <div role="alert" className="rounded-lg border border-danger/40 bg-danger-soft p-4">
              <p className="font-semibold text-danger">Chưa tìm được</p>
              <p className="mt-1 text-sm">{state.message}</p>
              <Button variant="outline" className="mt-3 h-11" onClick={() => void run(state.query)}>
                Thử lại
              </Button>
            </div>
          )}
          {state.kind === "done" && (
            <>
              <Results data={state.data} onPick={run} onOpen={setOpenIndex} />
              <SearchTracePanel data={state.data} />
              <Timings timings={state.data.timings} clientMs={state.clientMs} />
              <ImageDetail
                data={state.data}
                index={openIndex}
                onIndex={setOpenIndex}
                onClose={() => setOpenIndex(null)}
              />
            </>
          )}
        </section>
      </main>
    </div>
  );
}

function Results({
  data,
  onPick,
  onOpen,
}: {
  data: SearchResponse;
  onPick: (text: string) => void;
  onOpen: (index: number) => void;
}) {
  if (data.clarification) {
    return (
      <div className="rounded-lg border border-warn/40 bg-warn-soft p-4">
        <p className="font-medium">Tên này khớp nhiều sản phẩm. Bạn muốn tìm cái nào?</p>
        {data.clarification.map((item) => (
          <div key={item.text} className="mt-3 flex flex-wrap gap-2">
            {item.options.map((option) => (
              <Button key={option.entity_id} variant="outline" onClick={() => onPick(option.name)}>
                {option.name}
              </Button>
            ))}
          </div>
        ))}
      </div>
    );
  }
  if (data.out_of_scope) {
    return (
      <div className="rounded-lg border bg-surface p-4 text-sm">
        <p className="text-base font-semibold">Câu này không nói về sản phẩm nào trong kho.</p>
        <p className="mt-1 text-muted-foreground">
          Hãy nêu tên hoặc mô tả một sản phẩm: đồ điện tử, thuốc, món đặc sản, phụ tùng hay đồ thủ công.
        </p>
      </div>
    );
  }
  return (
    <>
      <div className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold sm:text-2xl">
          {data.results.length} ảnh cho “{data.query}”
        </h2>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <p className="text-sm text-muted-foreground">Hệ thống hiểu là:</p>
          <PlanChips data={data} />
        </div>
      </div>
      {data.results.length === 0 && data.trace.status === "no_target" ? (
        <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
          Trong kho, {data.trace.values.map((value) => value.label).join(", ") || "hãng này"} chưa có sản
          phẩm nào khác ngoài {data.trace.origins.map((node) => node.label).join(", ")}.
        </p>
      ) : data.results.length === 0 ? (
        <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
          Chưa có ảnh phù hợp. Thử bỏ bớt chi tiết, hoặc gõ tên sản phẩm.
        </p>
      ) : (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {data.results.map((hit, index) => (
            <li key={hit.asset_id}>
              <button
                type="button"
                onClick={() => onOpen(index)}
                aria-haspopup="dialog"
                aria-label={`Xem chi tiết ảnh ${index + 1}: ${hit.entity_names.join(", ")}`}
                className="group flex h-full w-full flex-col overflow-hidden rounded-lg border bg-surface text-left transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
              >
                <span className="relative block aspect-square bg-white">
                  {/* eslint-disable-next-line @next/next/no-img-element -- served by the API, sizes vary */}
                  <img
                    src={thumbnailOf(`${API_URL}${hit.image_url}`)}
                    alt=""
                    width={320}
                    height={320}
                    loading={index < 5 ? "eager" : "lazy"}
                    className="h-full w-full object-contain p-2 transition-transform duration-200 group-hover:scale-[1.03]"
                  />
                  <span className="absolute left-2 top-2 rounded bg-slate-900/75 px-2 text-xs font-medium text-white">
                    {index + 1}
                  </span>
                </span>
                <span className="flex flex-1 flex-col gap-1 border-t p-3">
                  <span className="line-clamp-2 text-sm font-medium leading-snug">
                    {hit.entity_names.join(" · ")}
                  </span>
                  {hit.domain && (
                    <span className="truncate text-xs text-muted-foreground">
                      {hit.domain.replace(/^www\./, "")}
                    </span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {data.results.length > 0 && <QueryGraph key={data.query} data={data} />}
    </>
  );
}

function Timings({ timings, clientMs }: { timings: Record<string, number>; clientMs: number }) {
  return (
    <dl aria-label="Thời gian xử lý" className="flex flex-wrap gap-x-3 gap-y-1 text-xs tabular-nums text-muted-foreground">
      {STAGES.filter(([key]) => key in timings).map(([key, label]) => (
        <div key={key} className="flex gap-1">
          <dt>{label}</dt>
          <dd className="text-foreground">{Math.round(timings[key])} ms</dd>
        </div>
      ))}
      <div className="flex gap-1">
        <dt>trình duyệt</dt>
        <dd className="text-foreground">{Math.round(clientMs)} ms</dd>
      </div>
    </dl>
  );
}

function ResultSkeleton() {
  return (
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5" aria-label="Đang tải kết quả">
      {Array.from({ length: 10 }, (_, index) => (
        <li key={index} className="overflow-hidden rounded-lg border bg-surface">
          <div className="aspect-square animate-pulse bg-muted" />
          <div className="space-y-2 p-3">
            <div className="h-3 w-4/5 animate-pulse rounded bg-muted" />
            <div className="h-3 w-1/2 animate-pulse rounded bg-muted" />
          </div>
        </li>
      ))}
    </ul>
  );
}
