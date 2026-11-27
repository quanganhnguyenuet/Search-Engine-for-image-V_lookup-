"use client";

import { type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";

import { RelationGraph } from "@/components/graph/relation-graph";
import { Button } from "@/components/ui/button";
import { PlanChips } from "@/components/search/plan-chips";
import { CHANNELS, formatScore, STATUS } from "@/components/search/search-trace";
import { useSubgraph } from "@/lib/graph";
import { API_URL, type SearchHit, type SearchResponse } from "@/lib/search";

type Props = {
  data: SearchResponse;
  /** Index of the open image in `data.results`, or null when closed. */
  index: number | null;
  onIndex: (index: number) => void;
  onClose: () => void;
};

export function ImageDetail({ data, index, onIndex, onClose }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const open = index !== null;

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  const total = data.results.length;
  const go = (step: number) => {
    if (index !== null) onIndex((index + step + total) % total);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDialogElement>) => {
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, select")) return;
    if (event.key === "ArrowLeft") go(-1);
    if (event.key === "ArrowRight") go(1);
  };

  const hit = index !== null ? data.results[index] : undefined;

  return (
    <dialog
      ref={dialog}
      aria-labelledby="detail-title"
      onClose={onClose}
      onKeyDown={onKeyDown}
      onClick={(event) => event.target === event.currentTarget && onClose()}
      className="m-auto max-h-[94dvh] w-[min(76rem,calc(100%-1rem))] overflow-hidden rounded-xl border bg-background p-0 text-foreground shadow-2xl backdrop:bg-black/55"
    >
      {hit && index !== null && (
        <div className="flex max-h-[94dvh] flex-col">
          <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
            <p className="text-sm tabular-nums text-muted-foreground">
              Ảnh {index + 1} / {total}
            </p>
            <div className="flex items-center gap-1.5">
              <Button variant="outline" className="h-9 px-3" onClick={() => go(-1)} aria-label="Ảnh trước (phím ←)">
                ←
              </Button>
              <Button variant="outline" className="h-9 px-3" onClick={() => go(1)} aria-label="Ảnh sau (phím →)">
                →
              </Button>
              <Button variant="outline" className="h-9 px-3" onClick={onClose} aria-label="Đóng (phím Esc)">
                Đóng
              </Button>
            </div>
          </header>
          <div className="grid min-h-0 flex-1 gap-0 overflow-y-auto lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:overflow-hidden">
            <div className="flex items-start justify-center bg-white p-4 lg:overflow-y-auto">
              {/* eslint-disable-next-line @next/next/no-img-element -- served by the API, sizes vary */}
              <img
                src={`${API_URL}${hit.image_url}`}
                alt={hit.entity_names.join(", ")}
                className="max-h-[70dvh] w-full object-contain"
              />
            </div>
            <div className="space-y-6 p-4 sm:p-6 lg:overflow-y-auto">
              <DetailBody key={hit.asset_id} data={data} hit={hit} rank={index + 1} />
            </div>
          </div>
        </div>
      )}
    </dialog>
  );
}

function DetailBody({ data, hit, rank }: { data: SearchResponse; hit: SearchHit; rank: number }) {
  const { trace } = data;
  return (
    <>
      <div>
        <h2 id="detail-title" className="text-xl font-semibold leading-snug">
          {hit.entity_names.join(" · ") || "Ảnh chưa gắn sản phẩm"}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Hạng {rank} · {hit.domain?.replace(/^www\./, "") ?? "không rõ nguồn"}
          {hit.page_url && (
            <>
              {" · "}
              <a
                href={hit.page_url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded text-brand underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
              >
                Mở trang nguồn
              </a>
            </>
          )}
        </p>
      </div>

      <Section title="Mô tả sản phẩm">
        <Description text={hit.description} fallbackTitle={hit.page_title} />
        {(hit.categories?.length ?? 0) > 0 && (
          <ul className="flex flex-wrap gap-1.5" aria-label="Loại hàng">
            {hit.categories!.map((category) => (
              <li key={category} className="rounded-md border bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
                {category}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Sản phẩm này liên quan tới gì">
        <FocusGraph hit={hit} />
      </Section>

      <Section title="Câu bạn đã tìm">
        <p className="text-base font-medium">“{data.query}”</p>
        <PlanChips data={data} />
        <p className="text-xs text-muted-foreground">{STATUS[trace.status]}</p>
      </Section>

      <Section title="Mức độ khớp với câu tìm">
        {hit.similarity ? (
          <>
            <Meter label="Câu truy vấn ↔ mô tả" model="bge-m3" value={hit.similarity.text} max={1} />
            <Meter
              label="Câu truy vấn ↔ ảnh"
              model="SigLIP2"
              value={hit.similarity.image}
              max={0.4}
              note="Thanh vẽ trên thang 0 đến 0,4 vì cosine văn bản–ảnh của SigLIP2 thường thấp."
            />
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer font-medium text-foreground">Cách đọc các số này</summary>
              <p className="mt-2 max-w-[70ch]">
                Cosine nằm trong khoảng −1 đến 1. Hai vector đã chuẩn hoá L2 nên tích vô hướng chính là cosine.
                Hai số đo bằng hai mô hình khác nhau, không so trực tiếp được: văn bản–văn bản thường 0,4–0,7,
                văn bản–ảnh thường 0,1–0,3. Dùng chúng để so các ảnh với nhau trong cùng một câu. Thứ hạng cuối
                không chỉ theo cosine: nó hợp nhất nhiều nhánh rồi cộng điều chỉnh theo đồ thị (bảng dưới). Số “câu
                truy vấn ↔ mô tả” được đo trên đoạn mô tả sản phẩm ở phía trên.
              </p>
            </details>
          </>
        ) : (
          <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
            API chưa trả cosine cho ảnh này. Khởi động lại API để nạp phiên bản mới.
          </p>
        )}
        <ScoreBreakdown hit={hit} channels={trace.channels} />
      </Section>
    </>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3" aria-label={title}>
      <h3 className="text-base font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function Meter({
  label,
  model,
  value,
  max,
  note,
}: {
  label: string;
  model: string;
  value: number | null;
  max: number;
  note?: string;
}) {
  const share = value === null ? 0 : Math.min(Math.max(value / max, 0), 1);
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span>
          {label} <span className="font-mono text-xs text-muted-foreground">{model}</span>
        </span>
        <span className="font-mono text-lg font-semibold tabular-nums">{value === null ? "—" : value.toFixed(4)}</span>
      </div>
      <div
        role="meter"
        aria-label={`${label}, cosine`}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value ?? undefined}
        aria-valuetext={value === null ? "không có" : value.toFixed(4)}
        className="h-2 overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full rounded-full bg-brand transition-[width] duration-300" style={{ width: `${share * 100}%` }} />
      </div>
      {note && value !== null && <p className="text-xs text-muted-foreground">{note}</p>}
      {value === null && <p className="text-xs text-muted-foreground">Không tính được cho câu này.</p>}
    </div>
  );
}

function ScoreBreakdown({ hit, channels }: { hit: SearchHit; channels: SearchResponse["trace"]["channels"] }) {
  return (
    <details className="rounded-md border px-3 py-2 text-sm">
      <summary className="cursor-pointer font-medium">
        Điểm xếp hạng <span className="font-mono tabular-nums">{hit.score.toFixed(4)}</span>
      </summary>
      <p className="mt-2 text-xs text-muted-foreground">
        Không phải cosine: là điểm hợp nhất các nhánh (RRF hoặc trộn văn bản và ảnh) cộng điều chỉnh theo đồ thị và
        loại hàng. Nhánh dense và sparse hiện điểm gốc trong Qdrant kèm hạng trong danh sách ứng viên.
      </p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        {CHANNELS.map(([channel, label]) => (
          <div key={channel} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-mono tabular-nums">{formatScore(hit.scores[channel], channels[channel])}</dd>
          </div>
        ))}
        <dt className="text-muted-foreground">RRF</dt>
        <dd className="font-mono tabular-nums">{hit.scores.rrf.toFixed(4)}</dd>
        <dt className="font-medium">Điểm cuối</dt>
        <dd className="font-mono font-medium tabular-nums">{hit.score.toFixed(4)}</dd>
      </dl>
    </details>
  );
}

/** The text that was embedded is "Sản phẩm: …\nTrang: title | snippet…": show it as labelled rows. */
function Description({ text, fallbackTitle }: { text?: string | null; fallbackTitle?: string | null }) {
  const [expanded, setExpanded] = useState(false);
  if (!text) {
    return (
      <p className="text-sm text-muted-foreground">
        {fallbackTitle || "Chưa có mô tả cho ảnh này."}
      </p>
    );
  }
  const rows = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const colon = line.indexOf(": ");
      return colon > 0 && colon < 20 ? { key: line.slice(0, colon), value: line.slice(colon + 2) } : { key: "", value: line };
    });
  const long = rows.some((row) => row.value.length > 320);
  return (
    <div className="space-y-2">
      <dl className="space-y-2 text-sm">
        {rows.map((row, i) => (
          <div key={i}>
            {row.key && <dt className="text-xs text-muted-foreground">{row.key}</dt>}
            <dd className={long && !expanded ? "line-clamp-6 leading-relaxed" : "leading-relaxed"}>{row.value}</dd>
          </div>
        ))}
      </dl>
      {long && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          className="rounded text-xs text-brand underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
        >
          {expanded ? "Thu gọn" : "Xem thêm"}
        </button>
      )}
    </div>
  );
}

function FocusGraph({ hit }: { hit: SearchHit }) {
  const state = useSubgraph({ ids: hit.entity_ids, siblings: 4 });
  const thumb = `${API_URL}${hit.image_url}`;
  const thumbs = Object.fromEntries(hit.entity_ids.map((id) => [id, thumb]));
  if (hit.entity_ids.length === 0) {
    return <p className="text-sm text-muted-foreground">Ảnh này chưa gắn với sản phẩm nào.</p>;
  }
  if (state.status === "error") {
    return (
      <p role="alert" className="rounded-md border border-danger/40 bg-danger-soft p-3 text-sm">
        {state.message}
      </p>
    );
  }
  if (state.status !== "done") {
    return <div className="h-[22rem] animate-pulse rounded-lg bg-muted" role="status" aria-label="Đang tải đồ thị" />;
  }
  return (
    <>
      <p className="max-w-[70ch] text-xs text-muted-foreground">
        Sản phẩm trong ảnh (nút có hình) cùng hãng, loại hàng, công dụng, xuất xứ của nó. Nút nét đứt là vài sản
        phẩm khác có chung thông tin.
      </p>
      <RelationGraph graph={state.data} thumbs={thumbs} layout="stacked" />
    </>
  );
}
