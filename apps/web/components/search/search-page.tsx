"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { API_URL, type SearchResponse, searchText } from "@/lib/search";
import { cn } from "@/lib/utils";

const EXAMPLES = [
  "Chuột Logitech MX Master 3",
  "chuột không dây màu đen",
  "Kindle Paperwhite và chuột Logitech MX Master 3",
  "sản phẩm khác của hãng làm tai nghe Kraken",
  "sản phẩm Shimano",
];

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
          ? `Không kết nối được API tại ${API_URL}. Kiểm tra API đang chạy với SEARCH_ENABLED=true.`
          : (error as Error).message;
      setState({ kind: "error", query: trimmed, message });
    }
  }, []);

  useEffect(() => {
    const initial = new URL(window.location.href).searchParams.get("q");
    if (initial) void run(initial);
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

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void run(query);
  };

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col gap-6 px-4 py-8 sm:px-6 lg:py-12">
      <header className="flex flex-col gap-2">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-brand">products-v1</p>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">V-Lookup</h1>
        <p className="max-w-[65ch] text-base text-muted-foreground">
          Tìm ảnh sản phẩm bằng câu tiếng Việt: tên sản phẩm, mô tả, nhiều sản phẩm một lúc hoặc
          quan hệ theo hãng. Một đường xử lý duy nhất, không gọi LLM lúc truy vấn.
        </p>
      </header>

      <form onSubmit={onSubmit} role="search" className="flex flex-col gap-3">
        <label htmlFor="q" className="text-sm font-medium">
          Câu tìm kiếm
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
            placeholder="Ví dụ: sản phẩm khác của hãng làm tai nghe Kraken"
            className="h-12 w-full rounded-md border bg-surface px-4 text-base shadow-sm outline-none transition-shadow placeholder:text-muted-foreground/80 focus-visible:ring-2 focus-visible:ring-brand"
          />
          <Button
            type="submit"
            variant="brand"
            size="lg"
            className="h-12 focus-visible:ring-brand"
            disabled={state.kind === "loading"}
          >
            {state.kind === "loading" ? "Đang tìm…" : "Tìm"}
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">Thử:</span>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => void run(example)}
              className="min-h-9 rounded-full border bg-surface px-3 py-1 text-left transition-colors hover:border-brand hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
            >
              {example}
            </button>
          ))}
        </div>
      </form>

      <section aria-live="polite" aria-busy={state.kind === "loading"} className="flex flex-col gap-4">
        {state.kind === "idle" && (
          <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
            Nhập một câu rồi nhấn Enter, hoặc chọn một ví dụ. Phím tắt: <kbd className="rounded border px-1 font-mono">/</kbd> để
            đưa con trỏ vào ô tìm kiếm.
          </p>
        )}
        {state.kind === "loading" && <ResultSkeleton />}
        {state.kind === "error" && (
          <div role="alert" className="rounded-lg border border-danger/40 bg-danger-soft p-4 text-sm">
            <p className="font-medium text-danger">Không tìm được</p>
            <p className="mt-1">{state.message}</p>
            <Button variant="outline" className="mt-3" onClick={() => void run(state.query)}>
              Thử lại
            </Button>
          </div>
        )}
        {state.kind === "done" && <Results data={state.data} clientMs={state.clientMs} onPick={run} />}
      </section>
    </main>
  );
}

function Results({
  data,
  clientMs,
  onPick,
}: {
  data: SearchResponse;
  clientMs: number;
  onPick: (text: string) => void;
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
        <p className="font-medium">Câu này nằm ngoài phạm vi dữ liệu sản phẩm.</p>
        <p className="mt-1 text-muted-foreground">
          Kho ảnh gồm sản phẩm có hãng/mẫu, thuốc, món ăn, phụ tùng, đồ thủ công. Hãy nêu tên hoặc mô tả
          một sản phẩm.
        </p>
        <Timings timings={data.timings} clientMs={clientMs} />
      </div>
    );
  }
  return (
    <>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex flex-col gap-2">
          <p className="text-sm">
            <span className="font-semibold">{data.results.length}</span> ảnh cho{" "}
            <q className="font-medium">{data.query}</q>
          </p>
          <PlanChips data={data} />
        </div>
        <Timings timings={data.timings} clientMs={clientMs} />
      </div>
      {data.results.length === 0 ? (
        <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
          Không có ảnh phù hợp. Thử bỏ bớt chi tiết hoặc dùng tên sản phẩm.
        </p>
      ) : (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {data.results.map((hit, index) => (
            <li key={hit.asset_id}>
              <figure className="group flex h-full flex-col overflow-hidden rounded-lg border bg-surface">
                <div className="relative aspect-square bg-white">
                  {/* eslint-disable-next-line @next/next/no-img-element -- served by the API, sizes vary */}
                  <img
                    src={`${API_URL}${hit.image_url}`}
                    alt={hit.entity_names.join(", ")}
                    width={320}
                    height={320}
                    loading={index < 5 ? "eager" : "lazy"}
                    className="h-full w-full object-contain p-2"
                  />
                  <span className="absolute left-2 top-2 rounded bg-background/90 px-1.5 font-mono text-[11px] text-muted-foreground">
                    {index + 1}
                  </span>
                </div>
                <figcaption className="flex flex-1 flex-col gap-1 border-t p-3">
                  <span className="line-clamp-2 text-sm font-medium leading-snug">
                    {hit.entity_names.join(" · ")}
                  </span>
                  {hit.domain && (
                    <span className="truncate text-xs text-muted-foreground">{hit.domain}</span>
                  )}
                </figcaption>
              </figure>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function PlanChips({ data }: { data: SearchResponse }) {
  const { plan } = data;
  const chips: { label: string; tone?: "brand" | "warn" }[] = [];
  const seeds = plan.seed_names.slice(0, plan.seeds.length);
  const anchors = plan.seed_names.slice(plan.seeds.length);
  for (const name of seeds) chips.push({ label: `Thực thể: ${name}`, tone: "brand" });
  if (plan.relation) chips.push({ label: "Quan hệ: cùng hãng", tone: "brand" });
  for (const name of anchors) chips.push({ label: `Loại trừ: ${name}`, tone: "warn" });
  if (plan.hops) chips.push({ label: `${plan.hops} sản phẩm liên quan` });
  if (plan.dense_seed) chips.push({ label: "Khớp gần đúng" });
  if (plan.grouped) chips.push({ label: "Chia đều theo sản phẩm" });
  if (!chips.length) chips.push({ label: "Tìm theo nội dung" });
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Cách hệ thống hiểu câu">
      {chips.map((chip) => (
        <li
          key={chip.label}
          className={cn(
            "rounded-md border px-2 py-0.5 text-xs",
            chip.tone === "brand" && "border-brand/30 bg-brand-soft text-foreground",
            chip.tone === "warn" && "border-warn/40 bg-warn-soft text-foreground",
            !chip.tone && "bg-muted text-muted-foreground",
          )}
        >
          {chip.label}
        </li>
      ))}
    </ul>
  );
}

function Timings({ timings, clientMs }: { timings: Record<string, number>; clientMs: number }) {
  return (
    <dl className="mt-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-xs text-muted-foreground sm:mt-0">
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
