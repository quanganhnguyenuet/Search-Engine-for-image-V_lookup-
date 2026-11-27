"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { ErrorBox, PendingBadge } from "@/components/admin/admin-shell";
import { type Apply, ApplyBox, useApply } from "@/components/admin/apply-changes";
import { EntityImages } from "@/components/admin/entity-images";
import { BrandInput, ChipsInput, Field, inputClass, SuggestInput } from "@/components/admin/fields";
import { RelationGraph } from "@/components/graph/relation-graph";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  admin,
  type EntityDetail,
  FIELD_LABEL,
  type FieldName,
  type Fields,
  formatTime,
  type Preview,
  formatValue,
  imageUrl,
  MARKET_TIERS,
  SOURCE_LABEL,
  type VocabItem,
} from "@/lib/admin";
import { REL_TEXT } from "@/lib/graph";

type Draft = {
  brand: string | null;
  brandLabel: string;
  category: string | null;
  product_type: string;
  market_tier: string;
  reviewed: boolean;
  treats: string[];
  ingredients: string[];
  fits: string[];
  origin: string;
  form_factor: string;
  target_audience: string;
  material: string;
};

function toDraft(fields: Fields): Draft {
  return {
    brand: fields.brand?.new ? `new:${fields.brand.name}` : (fields.brand?.id ?? null),
    brandLabel: fields.brand?.name ?? "",
    category: fields.category?.id ?? null,
    product_type: fields.product_type ?? "",
    market_tier: fields.market_tier ?? "not_applicable",
    reviewed: fields.reviewed,
    treats: fields.treats,
    ingredients: fields.ingredients,
    fits: fields.fits,
    origin: fields.origin,
    form_factor: fields.form_factor,
    target_audience: fields.target_audience,
    material: fields.material,
  };
}

const TEXT_KEYS = ["product_type", "origin", "form_factor", "target_audience", "material"] as const;
const LIST_KEYS = ["treats", "ingredients", "fits"] as const;

/** The fields of `next` that differ from `base`, in the API's shape. */
function changesOf(base: Draft, next: Draft): Partial<Record<FieldName, unknown>> {
  const out: Partial<Record<FieldName, unknown>> = {};
  if (next.brand !== base.brand) out.brand = next.brand;
  if (next.category !== base.category && next.category) out.category = next.category;
  if (next.market_tier !== base.market_tier) out.market_tier = next.market_tier;
  if (next.reviewed !== base.reviewed) out.reviewed = next.reviewed;
  for (const key of TEXT_KEYS) if (next[key].trim() !== base[key].trim()) out[key] = next[key].trim();
  for (const key of LIST_KEYS) if (JSON.stringify(next[key]) !== JSON.stringify(base[key])) out[key] = next[key];
  return out;
}

export function EntityEditor({ id }: { id: string }) {
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setError("");
    admin.entity(id).then(setDetail, (e: Error) => setError(e.message));
  }, [id]);
  useEffect(load, [load]);

  if (error && !detail) return <ErrorBox message={error} onRetry={load} />;
  if (!detail) {
    return (
      <div className="space-y-4" role="status" aria-label="Đang tải sản phẩm">
        <div className="h-9 w-2/3 animate-pulse rounded bg-muted" />
        <div className="h-96 animate-pulse rounded-lg bg-muted" />
      </div>
    );
  }
  return <Loaded detail={detail} onDetail={setDetail} />;
}

function Loaded({ detail, onDetail }: { detail: EntityDetail; onDetail: (d: EntityDetail) => void }) {
  const label = detail.label;
  // The form's unsaved values, so the graph preview follows them.
  const [unsaved, setUnsaved] = useState<Partial<Record<FieldName, unknown>>>({});
  // Bumped after an apply, so the preview reloads the new live graph.
  const [version, setVersion] = useState(0);
  const apply = useApply(async () => {
    onDetail(await admin.entity(detail.id));
    setVersion((n) => n + 1);
  });
  const applyNow = () => {
    apply.open();
    window.requestAnimationFrame(() => document.getElementById("pending-panel")?.scrollIntoView({ block: "start" }));
  };
  const cover = detail.images.find((image) => !image.hidden) ?? detail.images[0];
  return (
    <>
      <nav aria-label="Vị trí" className="mb-3 text-sm text-muted-foreground">
        <Link href="/admin/entities" className="rounded text-brand underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          Sản phẩm
        </Link>{" "}
        / <span className="text-foreground">{detail.name}</span>
      </nav>
      <header className="mb-6 flex flex-wrap items-start gap-4">
        {cover && (
          <span className="flex size-20 shrink-0 items-center justify-center overflow-hidden rounded-lg border bg-white">
            {/* eslint-disable-next-line @next/next/no-img-element -- served by the API, sizes vary */}
            <img src={imageUrl(cover.asset_id)} alt="" width={80} height={80} className="size-full object-contain" />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{detail.name}</h1>
          <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{detail.id}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            {label.category_name ? (
              <span className="rounded bg-muted px-2 py-0.5">{label.category_name}</span>
            ) : (
              <span className="rounded border border-warn/50 px-2 py-0.5 text-warn">Chưa có loại hàng</span>
            )}
            {label.source && (
              <span className="rounded bg-muted px-2 py-0.5">
                Nhãn: {SOURCE_LABEL[label.source] ?? label.source}
                {label.basis === "knowledge" && " · dựa trên hiểu biết của mô hình AI"}
              </span>
            )}
            {label.needs_review && <span className="rounded border border-warn/50 px-2 py-0.5 text-warn">Cần duyệt</span>}
            {detail.members.length > 1 && (
              <span className="rounded bg-muted px-2 py-0.5">Gộp {detail.members.length} bản ghi</span>
            )}
          </div>
        </div>
      </header>

      <PendingPanel detail={detail} onDetail={onDetail} apply={apply} />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <EditorForm
          detail={detail}
          onDetail={onDetail}
          onUnsaved={setUnsaved}
          onApply={applyNow}
          applying={apply.state.kind === "running"}
        />
        <div className="flex min-w-0 flex-col gap-6">
          <Relations detail={detail} unsaved={unsaved} version={version} />
          <Identity detail={detail} />
        </div>
      </div>
      <EntityImages detail={detail} onChanged={() => admin.entity(detail.id).then(onDetail)} />
      <History detail={detail} />
    </>
  );
}

function PendingPanel({ detail, onDetail, apply }: { detail: EntityDetail; onDetail: (d: EntityDetail) => void; apply: Apply }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const waiting = detail.pending.length > 0;
  if (!waiting && apply.state.kind === "idle") return null;
  const locked = busy || apply.state.kind === "running";
  const discard = async (fields: FieldName[]) => {
    setBusy(true);
    setError("");
    try {
      onDetail(await admin.discard(detail.id, fields));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      id="pending-panel"
      aria-labelledby="pending-title"
      className={cn("mb-6 scroll-mt-4 rounded-lg border p-4 text-sm", waiting ? "border-warn/40 bg-warn-soft" : "bg-surface")}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="pending-title" className="font-semibold">
          {waiting ? `${detail.pending.length} thay đổi đã lưu, chưa áp dụng` : "Thay đổi của sản phẩm"}
        </h2>
        {waiting && (
          <div className="flex flex-wrap gap-2">
            <Button variant="brand" disabled={locked || apply.state.kind === "confirm"} onClick={apply.open}>
              Áp dụng vào tìm kiếm
            </Button>
            <Button variant="outline" disabled={locked} onClick={() => void discard(detail.pending)}>
              Huỷ tất cả
            </Button>
          </div>
        )}
      </div>
      {waiting && <p className="mt-1 text-muted-foreground">Người dùng vẫn thấy giá trị cũ cho tới khi bạn áp dụng.</p>}
      <ApplyBox apply={apply} entityId={detail.id} />
      <ul className="mt-3 divide-y divide-warn/20">
        {detail.pending.map((field) => (
          <li key={field} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
            <span className="w-32 font-medium">{FIELD_LABEL[field]}</span>
            <span className="min-w-0 flex-1">
              <span className="text-muted-foreground line-through">{formatValue(detail.live[field])}</span>
              <span aria-hidden="true"> → </span>
              <span className="sr-only"> đổi thành </span>
              <span className="font-medium">{formatValue(detail.draft[field])}</span>
            </span>
            <button
              type="button"
              disabled={locked}
              onClick={() => void discard([field])}
              className="min-h-9 rounded px-2 font-medium text-brand hover:underline disabled:opacity-50"
            >
              Huỷ
            </button>
          </li>
        ))}
      </ul>
      {error && <p role="alert" className="mt-2 text-danger">{error}</p>}
    </section>
  );
}

function EditorForm({
  detail,
  onDetail,
  onUnsaved,
  onApply,
  applying,
}: {
  detail: EntityDetail;
  onDetail: (d: EntityDetail) => void;
  onUnsaved: (changes: Partial<Record<FieldName, unknown>>) => void;
  onApply: () => void;
  applying: boolean;
}) {
  const base = useMemo(() => toDraft(detail.draft), [detail]);
  const live = detail.live;
  const [draft, setDraft] = useState<Draft>(base);
  const [note, setNote] = useState("");
  const [categories, setCategories] = useState<VocabItem[]>([]);
  const [status, setStatus] = useState<{ kind: "idle" | "saving" | "saved" } | { kind: "error"; message: string }>({ kind: "idle" });

  useEffect(() => setDraft(base), [base]);
  useEffect(() => {
    admin.vocab("category", "", 200).then(
      (items) => setCategories([...items].sort((a, b) => a.id.localeCompare(b.id))),
      () => setCategories([]),
    );
  }, []);

  const changes = changesOf(base, draft);
  const dirty = Object.keys(changes).length > 0;
  const changesKey = JSON.stringify(changes);
  useEffect(() => onUnsaved(JSON.parse(changesKey)), [changesKey, onUnsaved]);
  const pending = new Set(detail.pending);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setStatus({ kind: "idle" });
  };
  const isFashion = (draft.category ?? "").startsWith("fashion.");
  const changed = (key: FieldName) => key in changes;

  const save = async (): Promise<boolean> => {
    if (!dirty) return true;
    setStatus({ kind: "saving" });
    try {
      onDetail(await admin.edit(detail.id, changes, note));
      setNote("");
      setStatus({ kind: "saved" });
      return true;
    } catch (e) {
      setStatus({ kind: "error", message: (e as Error).message });
      return false;
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void save();
  };
  const saveAndApply = async () => {
    if (await save()) onApply();
  };

  const fieldProps = (key: FieldName) => ({ pending: pending.has(key), changed: changed(key), live: live[key] });

  return (
    <form onSubmit={submit} aria-labelledby="editor-title" className="flex flex-col gap-5 rounded-lg border bg-surface p-4 sm:p-5">
      <div>
        <h2 id="editor-title" className="text-lg font-semibold">Tri thức của sản phẩm</h2>
        <p className="text-sm text-muted-foreground">
          Đồ thị bên cạnh cho xem trước ngay khi bạn sửa. Người dùng tìm kiếm chỉ thấy sau khi bạn áp dụng.
        </p>
      </div>

      <Field label="Hãng" htmlFor="f-brand" {...fieldProps("brand")} hint={draft.brand?.startsWith("new:") ? "Hãng này chưa có; sẽ được tạo khi áp dụng." : undefined}>
        <BrandInput id="f-brand" value={draft.brand} label={draft.brandLabel} onValue={(value, name) => setDraft((d) => ({ ...d, brand: value, brandLabel: name }))} />
      </Field>

      <Field label="Loại hàng" htmlFor="f-category" {...fieldProps("category")}>
        <select
          id="f-category"
          value={draft.category ?? ""}
          onChange={(event) => set("category", event.target.value || null)}
          className={inputClass}
        >
          {!draft.category && <option value="">Chọn loại hàng…</option>}
          {categories.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label} ({item.count})
            </option>
          ))}
        </select>
      </Field>

      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Loại sản phẩm" htmlFor="f-type" {...fieldProps("product_type")} hint="Danh từ chính đứng đầu, ví dụ “Siro ho”, “Má phanh ô tô”.">
          <input id="f-type" value={draft.product_type} maxLength={80} onChange={(e) => set("product_type", e.target.value)} className={inputClass} />
        </Field>
        <Field label="Phân khúc" htmlFor="f-tier" {...fieldProps("market_tier")} hint={isFashion ? undefined : "Chỉ dùng cho thời trang và trang sức."}>
          <select id="f-tier" value={draft.market_tier} disabled={!isFashion} onChange={(e) => set("market_tier", e.target.value)} className={inputClass}>
            {MARKET_TIERS.map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field label="Công dụng" htmlFor="f-treats" {...fieldProps("treats")}>
        <ChipsInput id="f-treats" kind="purpose" values={draft.treats} onValues={(v) => set("treats", v)} placeholder="Thêm công dụng, Enter để thêm" />
      </Field>
      <Field label="Thành phần" htmlFor="f-ingredients" {...fieldProps("ingredients")}>
        <ChipsInput id="f-ingredients" kind="ingredient" values={draft.ingredients} onValues={(v) => set("ingredients", v)} placeholder="Thêm thành phần" />
      </Field>
      <Field label="Lắp cho dòng xe / thiết bị" htmlFor="f-fits" {...fieldProps("fits")}>
        <ChipsInput id="f-fits" kind="model" values={draft.fits} onValues={(v) => set("fits", v)} placeholder="Thêm dòng xe hoặc thiết bị" />
      </Field>

      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Xuất xứ" htmlFor="f-origin" {...fieldProps("origin")}>
          <SuggestInput id="f-origin" kind="place" value={draft.origin} onValue={(v) => set("origin", v)} placeholder="Tỉnh hoặc quốc gia" />
        </Field>
        <Field label="Dạng" htmlFor="f-form" {...fieldProps("form_factor")}>
          <input id="f-form" value={draft.form_factor} maxLength={80} onChange={(e) => set("form_factor", e.target.value)} className={inputClass} />
        </Field>
        <Field label="Đối tượng" htmlFor="f-audience" {...fieldProps("target_audience")}>
          <input id="f-audience" value={draft.target_audience} maxLength={80} onChange={(e) => set("target_audience", e.target.value)} className={inputClass} />
        </Field>
        <Field label="Chất liệu" htmlFor="f-material" {...fieldProps("material")}>
          <input id="f-material" value={draft.material} maxLength={80} onChange={(e) => set("material", e.target.value)} className={inputClass} />
        </Field>
      </div>

      <label className="flex min-h-11 items-start gap-3 rounded-md border p-3 text-sm">
        <input type="checkbox" checked={draft.reviewed} onChange={(e) => set("reviewed", e.target.checked)} className="mt-0.5 size-5 accent-[hsl(var(--brand))]" />
        <span>
          <span className="font-medium">Tôi đã kiểm tra nhãn của sản phẩm này</span>
          {pending.has("reviewed") && <> <PendingBadge /></>}
          <span className="block text-muted-foreground">Sản phẩm rời hàng đợi “cần duyệt”; nhãn được tính là nhãn người duyệt.</span>
        </span>
      </label>

      <label className="flex flex-col gap-1.5 text-sm font-medium">
        Ghi chú cho lần sửa (không bắt buộc)
        <input value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="ví dụ: theo hướng dẫn sử dụng trên bao bì" className={inputClass} />
      </label>

      <div className="sticky bottom-0 -mx-4 -mb-4 flex flex-wrap items-center gap-3 border-t bg-surface/95 px-4 py-3 backdrop-blur sm:-mx-5 sm:-mb-5 sm:px-5">
        <Button type="submit" variant="outline" disabled={!dirty || status.kind === "saving"}>
          {status.kind === "saving" ? "Đang lưu…" : dirty ? `Lưu nháp ${Object.keys(changes).length} thay đổi` : "Chưa có thay đổi"}
        </Button>
        {(dirty || pending.size > 0) && (
          <Button type="button" variant="brand" disabled={status.kind === "saving" || applying} onClick={() => void saveAndApply()}>
            {dirty ? "Lưu và áp dụng" : "Áp dụng vào tìm kiếm"}
          </Button>
        )}
        {dirty && (
          <Button type="button" variant="outline" onClick={() => setDraft(base)}>
            Bỏ thay đổi chưa lưu
          </Button>
        )}
        <p aria-live="polite" className="text-sm">
          {status.kind === "saved" && <span className="text-brand">Đã lưu nháp, chưa áp dụng.</span>}
          {status.kind === "error" && <span role="alert" className="text-danger">{status.message}</span>}
        </p>
      </div>
    </form>
  );
}

const PREVIEW_DELAY_MS = 450;
const ATTRIBUTE_LABEL: Record<string, string> = {
  category_name: "Loại hàng",
  product_type: "Loại sản phẩm",
  form_factor: "Dạng",
  target_audience: "Đối tượng",
  material: "Chất liệu",
  origin: "Xuất xứ",
};

type PreviewState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "done"; data: Preview };

/** The relations as applying would build them: saved drafts plus the form's unsaved values. */
function usePreview(id: string, changesKey: string, siblings: number, version: string) {
  const [state, setState] = useState<PreviewState>({ kind: "loading" });
  const [stale, setStale] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setStale(true);
    const timer = window.setTimeout(() => {
      admin.preview(id, JSON.parse(changesKey), siblings, controller.signal).then(
        (data) => {
          if (controller.signal.aborted) return;
          setState({ kind: "done", data });
          setStale(false);
        },
        (e: Error) => {
          if (controller.signal.aborted) return;
          setState({ kind: "error", message: e.message });
          setStale(false);
        },
      );
    }, PREVIEW_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [id, changesKey, siblings, version]);
  return { state, stale };
}

function Relations({ detail, unsaved, version }: { detail: EntityDetail; unsaved: Partial<Record<FieldName, unknown>>; version: number }) {
  const [siblings, setSiblings] = useState(3);
  // An empty product type is refused by the API; it is a field being typed, not a value.
  const changesKey = JSON.stringify(
    Object.fromEntries(Object.entries(unsaved).filter(([k, v]) => !(k === "product_type" && !v))),
  );
  const savedKey = `${version}|${detail.history[0]?.seq ?? 0}`;
  const { state, stale } = usePreview(detail.id, changesKey, siblings, savedKey);
  const data = state.kind === "done" ? state.data : null;
  const differs = !!data && (data.added > 0 || data.removed > 0 || data.attributes.length > 0);
  const hasUnsaved = changesKey !== "{}";
  // Redraw only when the graph itself changes, so typing does not reshuffle the nodes.
  const graphKey = data ? JSON.stringify([data.nodes, data.edges]) : "";
  const graph = useMemo(() => {
    if (!graphKey) return null;
    const [nodes, edges] = JSON.parse(graphKey) as [Preview["nodes"], Preview["edges"]];
    return { nodes, edges, truncated: false };
  }, [graphKey]);
  return (
    <section aria-labelledby="relations-title" aria-busy={stale} className="rounded-lg border bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="relations-title" className="text-lg font-semibold">
            {differs ? "Xem trước đồ thị" : "Đồ thị quan hệ đang chạy"}
          </h2>
          {differs && <span className="rounded bg-warn-soft px-2 py-0.5 text-xs font-semibold text-warn">Chưa áp dụng</span>}
        </div>
        <label className="flex items-center gap-2 text-sm">
          Sản phẩm cùng giá trị
          <select value={siblings} onChange={(e) => setSiblings(Number(e.target.value))} className="h-9 rounded-md border bg-surface px-2">
            {[0, 3, 6, 8].map((n) => (
              <option key={n} value={n}>
                {n === 0 ? "ẩn" : `${n} mỗi giá trị`}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p aria-live="polite" className="mt-1 text-sm text-muted-foreground">
        {stale
          ? "Đang cập nhật…"
          : differs
            ? `Sau khi áp dụng: thêm ${data!.added} quan hệ, bỏ ${data!.removed} quan hệ.${hasUnsaved ? " Gồm cả giá trị bạn chưa lưu." : ""}`
            : "Chưa có thay đổi nào so với dữ liệu người dùng đang thấy."}
      </p>
      {data?.blocked === "category_required" && (
        <p role="note" className="mt-2 rounded-md bg-warn-soft p-2 text-sm">Chọn loại hàng trước thì mới xem trước và lưu được các quan hệ.</p>
      )}
      {data && data.attributes.length > 0 && (
        <dl className="mt-2 rounded-md border border-warn/40 p-3 text-sm">
          {data.attributes.map((a) => (
            <div key={a.field} className="flex flex-wrap gap-x-2">
              <dt className="w-28 text-muted-foreground">{ATTRIBUTE_LABEL[a.field] ?? a.field}</dt>
              <dd className="min-w-0 flex-1">
                <span className="text-muted-foreground line-through">{a.before || "—"}</span>
                <span aria-hidden="true"> → </span>
                <span className="sr-only"> đổi thành </span>
                <span className="font-medium">{a.after || "—"}</span>
              </dd>
            </div>
          ))}
        </dl>
      )}
      <div className={cn("mt-3 transition-opacity", stale && graph && "opacity-60")}>
        {state.kind === "loading" && <div className="h-80 animate-pulse rounded-lg bg-muted" role="status" aria-label="Đang tải đồ thị" />}
        {state.kind === "error" && <ErrorBox message={state.message} />}
        {graph && <RelationGraph graph={graph} layout="stacked" preview />}
      </div>
      {data && (
        <div className="overflow-x-auto">
          <table className="mt-4 w-full text-left text-sm">
            <caption className="sr-only">Các quan hệ của sản phẩm</caption>
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1 pr-2 font-medium">Quan hệ</th>
                <th className="py-1 pr-2 font-medium">Giá trị</th>
                <th className="py-1 pr-2 font-medium">Nguồn</th>
                <th className="py-1 text-right font-medium">Sản phẩm khác dùng chung</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.table.map((edge) => (
                <tr
                  key={`${edge.rel}|${edge.target}`}
                  className={cn(edge.change === "added" && "bg-brand-soft", edge.change === "removed" && "bg-danger-soft")}
                >
                  <td className="py-1.5 pr-2 text-muted-foreground">{REL_TEXT[edge.rel] ?? edge.rel}</td>
                  <td className="py-1.5 pr-2 font-medium">
                    <span className={cn(edge.change === "removed" && "line-through")}>{edge.label}</span>
                    {edge.change && (
                      <span className={cn("ml-2 text-xs font-semibold", edge.change === "added" ? "text-brand" : "text-danger")}>
                        {edge.change === "added" ? "+ sẽ thêm" : "− sẽ bỏ"}
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 pr-2 text-muted-foreground">
                    {edge.source ? (SOURCE_LABEL[edge.source] ?? edge.source) : "nhãn loại hàng"}
                    {edge.reviewed && " · đã duyệt"}
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-muted-foreground">{edge.shared_with}</td>
                </tr>
              ))}
              {data.table.length === 0 && (
                <tr>
                  <td colSpan={4} className="py-3 text-muted-foreground">Sản phẩm chưa có quan hệ nào.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Identity({ detail }: { detail: EntityDetail }) {
  return (
    <section aria-labelledby="identity-title" className="rounded-lg border bg-surface p-4 text-sm sm:p-5">
      <h2 id="identity-title" className="text-lg font-semibold">Định danh</h2>
      <dl className="mt-2 grid gap-x-4 gap-y-2 sm:grid-cols-[max-content_1fr]">
        <dt className="text-muted-foreground">Bản ghi đã gộp</dt>
        <dd>
          <ul>
            {detail.members.map((m) => (
              <li key={m.id}>
                {m.name} <span className="font-mono text-xs text-muted-foreground">{m.id.slice(0, 8)}</span>
              </li>
            ))}
          </ul>
        </dd>
        <dt className="text-muted-foreground">Bí danh để nhận diện</dt>
        <dd className="break-words">{detail.aliases.length ? detail.aliases.join(", ") : "—"}</dd>
        {detail.label.model && (
          <>
            <dt className="text-muted-foreground">Mô hình AI gán nhãn</dt>
            <dd>
              {detail.label.model}
              {detail.label.confidence && ` · độ tin ${detail.label.confidence}`}
            </dd>
          </>
        )}
      </dl>
    </section>
  );
}

function History({ detail }: { detail: EntityDetail }) {
  const rows = [
    ...detail.history.map((r) => ({
      at: r.at,
      who: r.actor.email ?? r.actor.id,
      text: `${FIELD_LABEL[r.field as FieldName] ?? r.field}: ${formatValue(r.before)} → ${formatValue(r.after)}`,
      note: r.note,
    })),
    ...detail.image_history.map((r) => ({
      at: r.at,
      who: r.actor.email ?? "",
      text: `${r.hidden ? "Ẩn" : "Hiện lại"} ảnh ${r.asset_id.slice(0, 8)}`,
      note: r.reason,
    })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  return (
    <section aria-labelledby="history-title" className="mt-6 rounded-lg border bg-surface p-4 text-sm sm:p-5">
      <h2 id="history-title" className="text-lg font-semibold">Lịch sử chỉnh sửa</h2>
      {rows.length === 0 ? (
        <p className="mt-2 text-muted-foreground">Chưa ai sửa sản phẩm này trong trang quản trị.</p>
      ) : (
        <ol className="mt-2 divide-y">
          {rows.map((row, i) => (
            <li key={i} className="flex flex-wrap gap-x-3 py-2">
              <time dateTime={row.at} className="w-32 shrink-0 tabular-nums text-muted-foreground">{formatTime(row.at)}</time>
              <span className="order-last min-w-0 basis-full break-words sm:order-none sm:basis-0 sm:grow">
                {row.text}
                {row.note && <span className="text-muted-foreground"> · {row.note}</span>}
              </span>
              <span className="text-muted-foreground">{row.who}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
