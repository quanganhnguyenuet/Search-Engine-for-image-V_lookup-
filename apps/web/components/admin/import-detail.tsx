"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ErrorBox, PageTitle } from "@/components/admin/admin-shell";
import { ApplyBox, useApply } from "@/components/admin/apply-changes";
import { GROUP_STATUS, IMPORT_STATUS, STEP_LABEL, StatusBadge } from "@/components/admin/import-status";
import { Button } from "@/components/ui/button";
import {
  admin,
  type EntityRow,
  formatTime,
  type ImportDetail,
  type ImportGroup,
  imageUrl,
  thumbUrl,
} from "@/lib/admin";
import { cn } from "@/lib/utils";

const POLL_MS = 2500;
const BUSY_GROUP = new Set(["processing", "publishing", "labelling"]);

type Tab = "match" | "new" | "existing" | "issues";

function Thumb({ assetId, size = 56, label = "" }: { assetId: string | null; size?: number; label?: string }) {
  return (
    <span
      className="flex shrink-0 items-center justify-center overflow-hidden rounded border bg-white"
      style={{ width: size, height: size }}
    >
      {assetId ? (
        // eslint-disable-next-line @next/next/no-img-element -- served by the API, sizes vary
        <img src={thumbUrl(assetId)} alt={label} width={size} height={size} loading="lazy" className="size-full object-contain" />
      ) : (
        <span className="text-[10px] text-slate-500">không ảnh</span>
      )}
    </span>
  );
}

function Thumbs({ ids, name }: { ids: string[]; name: string }) {
  if (!ids.length) return <p className="text-xs text-muted-foreground">Không còn ảnh mới sau khi khử trùng.</p>;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label={`Ảnh nhập của ${name}`}>
      {ids.map((id) => (
        <li key={id}>
          <a href={imageUrl(id)} target="_blank" rel="noreferrer" className="block rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
            <Thumb assetId={id} label={`Ảnh của ${name}`} />
          </a>
        </li>
      ))}
    </ul>
  );
}

/** Search the graph for an entity to match a group with. */
function EntityPicker({ onPick, disabled }: { onPick: (row: EntityRow) => void; disabled: boolean }) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<EntityRow[] | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) {
      setRows(null);
      return;
    }
    const timer = window.setTimeout(() => {
      const id = ++latest.current;
      admin.entities(query, "all", 0, 6).then(
        (data) => id === latest.current && setRows(data.items),
        () => id === latest.current && setRows([]),
      );
    }, 250);
    return () => window.clearTimeout(timer);
  }, [q]);

  return (
    <div className="flex flex-col gap-2">
      <label className="flex flex-col gap-1 text-sm font-medium">
        Tìm sản phẩm khác trong đồ thị
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="gõ tên, hãng hoặc mã"
          className="h-11 w-full rounded-md border bg-surface px-3 text-base font-normal outline-none focus-visible:ring-2 focus-visible:ring-brand"
        />
      </label>
      {rows && rows.length === 0 && <p className="text-sm text-muted-foreground">Không thấy sản phẩm nào.</p>}
      {rows && rows.length > 0 && (
        <ul className="divide-y rounded-md border">
          {rows.map((row) => (
            <li key={row.id} className="flex items-center gap-3 p-2">
              <Thumb assetId={row.thumb} size={40} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{row.name}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {[row.brand || "chưa có hãng", row.category || "chưa có loại", `${row.images} ảnh`].join(" · ")}
                </span>
              </span>
              <Button variant="outline" disabled={disabled} onClick={() => onPick(row)}>
                Chọn
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MatchCard({
  group,
  busy,
  onDecide,
  allowNew = true,
}: {
  group: ImportGroup;
  busy: boolean;
  onDecide: (choice: { entityId: string } | { new: true }) => void;
  allowNew?: boolean;
}) {
  const [searching, setSearching] = useState(false);
  return (
    <div className="flex flex-col gap-3">
      {group.candidates.length > 0 && (
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium">Sản phẩm có tên gần giống</legend>
          <ul className="divide-y rounded-md border">
            {group.candidates.map((c) => (
              <li key={c.entity_id} className="flex flex-wrap items-center gap-3 p-2 sm:flex-nowrap">
                <Thumb assetId={c.thumb} size={48} label={c.name} />
                <span className="min-w-0 flex-1">
                  <Link
                    href={`/admin/entities/${c.entity_id}`}
                    target="_blank"
                    className="block truncate text-sm font-medium text-brand underline-offset-4 hover:underline"
                  >
                    {c.name}
                  </Link>
                  <span className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
                    <span className="tabular-nums">giống {Math.round(c.score * 100)}%</span>
                    <span>{c.images} ảnh</span>
                    {c.code_conflict && <span className="font-medium text-warn">mã mẫu khác nhau</span>}
                  </span>
                </span>
                <Button variant="outline" disabled={busy} onClick={() => onDecide({ entityId: c.entity_id })}>
                  Là sản phẩm này
                </Button>
              </li>
            ))}
          </ul>
        </fieldset>
      )}
      {searching ? (
        <EntityPicker disabled={busy} onPick={(row) => onDecide({ entityId: row.id })} />
      ) : (
        <div className="flex flex-wrap gap-2">
          {allowNew && (
            <Button variant="brand" disabled={busy} onClick={() => onDecide({ new: true })}>
              Tạo sản phẩm mới
            </Button>
          )}
          <Button variant="outline" disabled={busy} onClick={() => setSearching(true)}>
            Tìm sản phẩm khác…
          </Button>
        </div>
      )}
    </div>
  );
}

function GroupHeader({ group }: { group: ImportGroup }) {
  const [label, tone] = GROUP_STATUS[group.status] ?? [group.status, "muted"];
  return (
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0">
        <h3 className="font-semibold">{group.name}</h3>
        <p className="text-xs text-muted-foreground">
          {group.rows} dòng · {group.images} ảnh mới
          {group.duplicates > 0 && ` · ${group.duplicates} trùng`}
          {group.failed > 0 && ` · ${group.failed} lỗi tải`}
          {group.csv_entity_id && ` · id CSV ${group.csv_entity_id}`}
        </p>
      </div>
      <StatusBadge label={label} tone={tone} />
    </div>
  );
}

function Relations({ group }: { group: ImportGroup }) {
  const e = group.enrich;
  if (!e) {
    return (
      <p className="text-sm text-muted-foreground">
        {group.status === "labelling" ? "AI đang bổ sung thông tin…" : "Chưa có"}
      </p>
    );
  }
  const parts: [string, string][] = (
    [
      ["Hãng", e.brand ?? ""],
      ["Công dụng", (e.treats ?? []).join(", ")],
      ["Thành phần", (e.ingredients ?? []).join(", ")],
      ["Lắp cho", (e.fits ?? []).join(", ")],
      ["Xuất xứ", e.origin ?? ""],
      ["Chất liệu", e.material ?? ""],
      ["Dạng", e.form_factor ?? ""],
      ["Đối tượng", e.target_audience ?? ""],
    ] as [string, string][]
  ).filter(([, v]) => v);
  if (!parts.length) return <p className="text-xs text-muted-foreground">AI không tìm thấy quan hệ nào.</p>;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-sm">
      {parts.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function NewEntityCard({
  group,
  busy,
  onDecide,
  onRetry,
}: {
  group: ImportGroup;
  busy: boolean;
  onDecide: (choice: { entityId: string } | { new: true }) => void;
  onRetry: () => void;
}) {
  const [merging, setMerging] = useState(false);
  const canMerge = !["released", "publishing"].includes(group.status);
  return (
    <li className="flex flex-col gap-3 rounded-lg border bg-surface p-4">
      <GroupHeader group={group} />
      <Thumbs ids={group.thumbs} name={group.name} />
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="text-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Phân loại</p>
          {group.label ? (
            <p className="mt-0.5">
              <span className="font-medium">{group.label.category_name ?? group.label.category}</span>
              {group.label.product_type && ` · ${group.label.product_type}`}
              <span className="block text-xs text-muted-foreground">
                {group.label.model} · {group.label.basis === "knowledge" ? "theo hiểu biết của AI" : "theo bằng chứng"} · tin cậy {group.label.confidence}
              </span>
            </p>
          ) : (
            <p className="mt-0.5 text-muted-foreground">{group.status === "labelling" ? "AI đang phân loại…" : "Chưa có nhãn"}</p>
          )}
        </div>
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Quan hệ do AI bổ sung</p>
          <div className="mt-0.5">
            <Relations group={group} />
          </div>
        </div>
      </div>
      {group.error && (
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-md bg-danger-soft p-3 text-sm">
          <span className="min-w-0 flex-1">{group.error}</span>
          <Button variant="outline" disabled={busy} onClick={onRetry}>
            Gán nhãn lại
          </Button>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3 border-t pt-3 text-sm">
        {group.target?.live ? (
          <Link href={`/admin/entities/${group.target.id}`} className="font-medium text-brand underline-offset-4 hover:underline">
            Mở sản phẩm trong đồ thị
          </Link>
        ) : (
          <span className="text-muted-foreground">
            {group.status === "ready" || group.status === "ready_unlabelled"
              ? "Vào đồ thị và tìm kiếm ở lần áp dụng kế tiếp. Sau đó vẫn sửa được như mọi sản phẩm."
              : ""}
          </span>
        )}
        {canMerge && !merging && (
          <Button variant="outline" disabled={busy} onClick={() => setMerging(true)} className="ml-auto">
            Thật ra là sản phẩm có sẵn…
          </Button>
        )}
      </div>
      {canMerge && merging && <MatchCard group={group} busy={busy} onDecide={onDecide} allowNew={false} />}
    </li>
  );
}

export function ImportDetailView({ id }: { id: string }) {
  const [data, setData] = useState<ImportDetail | null>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab | null>(null);

  const load = useCallback(() => {
    admin.importDetail(id).then(
      (result) => {
        setData(result);
        setError("");
      },
      (e: Error) => setError(e.message),
    );
  }, [id]);
  useEffect(load, [load]);
  // Applying adds the new products to the graph; the server then loads their images.
  const apply = useApply(load);

  const active =
    !!data && (["queued", "running"].includes(data.status) || data.groups.some((g) => BUSY_GROUP.has(g.status)));
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, load]);

  const groups = useMemo(() => {
    const all = data?.groups ?? [];
    return {
      match: all.filter((g) => g.status === "waiting_match"),
      new: all.filter((g) => g.decision?.type === "new"),
      existing: all.filter((g) => g.decision?.type === "existing"),
    };
  }, [data]);

  const current: Tab = tab ?? (groups.match.length ? "match" : groups.new.length ? "new" : "existing");

  const act = async (run: () => Promise<ImportDetail>) => {
    setBusy(true);
    setActionError("");
    try {
      setData(await run());
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const decide = (group: ImportGroup) => (choice: { entityId: string } | { new: true }) =>
    void act(() => admin.decide(id, group.key, choice));

  if (error && !data) return <ErrorBox message={error} onRetry={load} />;
  if (!data) return <div className="h-64 animate-pulse rounded-lg bg-muted" role="status" aria-label="Đang tải" />;

  const [statusLabel, statusTone] = IMPORT_STATUS[data.status] ?? [data.status, "muted"];
  const c = data.counts;
  const running = data.status === "queued" || data.status === "running";
  const percent = data.progress.total ? Math.round((100 * data.progress.done) / data.progress.total) : 0;
  const tabs: [Tab, string, number][] = [
    ["match", "Cần so khớp", groups.match.length],
    ["new", "Sản phẩm mới", groups.new.length],
    ["existing", "Sản phẩm có sẵn", groups.existing.length],
    ["issues", "Dòng lỗi / trùng", data.issues_total],
  ];

  return (
    <>
      <nav aria-label="Đường dẫn" className="mb-2 text-sm">
        <Link href="/admin/imports" className="text-brand underline-offset-4 hover:underline">
          ← Các lần nhập
        </Link>
      </nav>
      <PageTitle
        title={data.file_name}
        description={`${formatTime(data.created_at)}${data.actor ? ` · ${data.actor}` : ""} · ${c.rows} dòng, ${c.groups} sản phẩm`}
        actions={<StatusBadge label={statusLabel} tone={statusTone} live />}
      />

      {running && (
        <section aria-label="Tiến độ" className="mb-6 rounded-lg border bg-surface p-4">
          <div className="flex flex-wrap justify-between gap-2 text-sm">
            <span className="font-medium">{STEP_LABEL[data.step] ?? data.step}</span>
            <span className="tabular-nums text-muted-foreground">
              {data.progress.done.toLocaleString("vi-VN")} / {data.progress.total.toLocaleString("vi-VN")}
            </span>
          </div>
          <div
            className="mt-2 h-2 overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            aria-label={STEP_LABEL[data.step] ?? data.step}
          >
            <div className="h-full rounded-full bg-brand transition-[width] duration-300" style={{ width: `${percent}%` }} />
          </div>
        </section>
      )}

      {data.error && (
        <div role="alert" className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-danger/40 bg-danger-soft p-4 text-sm">
          <span className="min-w-0 flex-1">Lần chạy dừng vì lỗi: {data.error}</span>
          <Button variant="outline" disabled={busy} onClick={() => void act(() => admin.retryImport(id))}>
            Chạy lại
          </Button>
        </div>
      )}
      {data.labeller && groups.new.length > 0 && (
        <p role="note" className="mb-4 rounded-lg border border-warn/40 bg-warn-soft p-3 text-sm">
          {data.labeller} Sản phẩm mới vẫn vào đồ thị khi áp dụng nhưng chưa có loại hàng và quan hệ; bấm gán nhãn lại khi AI dùng được.
        </p>
      )}
      {actionError && (
        <div className="mb-4">
          <ErrorBox message={actionError} />
        </div>
      )}

      <dl className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {(
          [
            ["Ảnh mới", c.images],
            ["Đã nạp vào tìm kiếm", c.published_images],
            ["Trùng ảnh", c.duplicates],
            ["Lỗi tải / bỏ qua", c.failed + c.skipped],
            ["Sản phẩm mới chờ áp dụng", c.ready],
            ["Cần so khớp tay", c.waiting_match],
          ] as [string, number][]
        ).map(([label, value]) => (
          <div key={label} className="rounded-lg border bg-surface p-3">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="text-xl font-semibold tabular-nums">{value.toLocaleString("vi-VN")}</dd>
          </div>
        ))}
      </dl>

      {(c.ready > 0 || apply.state.kind !== "idle") && (
        <section aria-label="Áp dụng sản phẩm mới" className="mb-4 rounded-lg border bg-surface p-4 text-sm">
          {c.ready > 0 && (
            <div className="flex flex-wrap items-center gap-3">
              <p className="min-w-0 flex-1">
                {c.ready} sản phẩm mới đang chờ. Áp dụng để đưa chúng vào đồ thị; ảnh của chúng được nạp ngay sau đó.
              </p>
              <Button
                variant="brand"
                disabled={running || apply.state.kind === "confirm" || apply.state.kind === "running"}
                onClick={apply.open}
              >
                Áp dụng vào tìm kiếm
              </Button>
            </div>
          )}
          <ApplyBox apply={apply} />
        </section>
      )}

      <div role="tablist" aria-label="Nhóm kết quả" className="mb-4 flex gap-1 overflow-x-auto border-b">
        {tabs.map(([key, label, count]) => (
          <button
            key={key}
            type="button"
            role="tab"
            id={`tab-${key}`}
            aria-selected={current === key}
            aria-controls={`panel-${key}`}
            onClick={() => setTab(key)}
            className={cn(
              "-mb-px inline-flex min-h-11 items-center gap-2 whitespace-nowrap border-b-2 px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand",
              current === key ? "border-brand text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {label}
            <span
              className={cn(
                "rounded-full px-1.5 text-xs tabular-nums",
                key === "match" && count > 0 ? "bg-warn-soft text-warn" : "bg-muted text-muted-foreground",
              )}
            >
              {count}
            </span>
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${current}`} aria-labelledby={`tab-${current}`}>
        {current === "match" &&
          (groups.match.length === 0 ? (
            <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
              {running ? "Đang xử lý; tên gần giống sẽ hiện ở đây." : "Không có tên nào cần so khớp tay."}
            </p>
          ) : (
            <ul className="flex flex-col gap-4">
              {groups.match.map((group) => (
                <li key={group.key} className="flex flex-col gap-3 rounded-lg border bg-surface p-4">
                  <GroupHeader group={group} />
                  <Thumbs ids={group.thumbs} name={group.name} />
                  <MatchCard group={group} busy={busy} onDecide={decide(group)} />
                </li>
              ))}
            </ul>
          ))}

        {current === "new" &&
          (groups.new.length === 0 ? (
            <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">Không có sản phẩm mới trong lần nhập này.</p>
          ) : (
            <ul className="flex flex-col gap-4">
              {groups.new.map((group) => (
                <NewEntityCard
                  key={group.key}
                  group={group}
                  busy={busy}
                  onDecide={decide(group)}
                  onRetry={() => void act(() => admin.retryImport(id, group.key))}
                />
              ))}
            </ul>
          ))}

        {current === "existing" &&
          (groups.existing.length === 0 ? (
            <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">Chưa có nhóm nào khớp sản phẩm có sẵn.</p>
          ) : (
            <ul className="divide-y rounded-lg border bg-surface">
              {groups.existing.map((group) => {
                const [label, tone] = GROUP_STATUS[group.status] ?? [group.status, "muted"];
                return (
                  <li key={group.key} className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:gap-4">
                    <Thumb assetId={group.thumbs[0] ?? null} size={48} label={group.name} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{group.name}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        →{" "}
                        {group.target ? (
                          <Link href={`/admin/entities/${group.target.id}`} className="text-brand underline-offset-4 hover:underline">
                            {group.target.name}
                          </Link>
                        ) : (
                          "—"
                        )}
                        {group.decision?.auto ? " · khớp tự động" : " · quản trị viên chọn"}
                      </span>
                    </span>
                    <span className="flex flex-wrap items-center gap-3 text-xs tabular-nums text-muted-foreground">
                      <span>
                        {group.published}/{group.images} ảnh đã nạp
                      </span>
                      {group.duplicates > 0 && <span>{group.duplicates} trùng</span>}
                      {group.failed > 0 && <span className="text-danger">{group.failed} lỗi tải</span>}
                      <StatusBadge label={label} tone={tone} />
                    </span>
                  </li>
                );
              })}
            </ul>
          ))}

        {current === "issues" &&
          (data.issues.length === 0 ? (
            <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">Không có dòng lỗi hay trùng.</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border bg-surface">
              <table className="w-full min-w-[40rem] text-left text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-medium">Dòng</th>
                    <th className="px-3 py-2 font-medium">Sản phẩm</th>
                    <th className="px-3 py-2 font-medium">Ảnh</th>
                    <th className="px-3 py-2 font-medium">Lý do</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {data.issues.map((row) => (
                    <tr key={`${row.line}-${row.status}`} className="align-top">
                      <td className="px-3 py-2 tabular-nums">{row.line}</td>
                      <td className="px-3 py-2">{row.entity_name}</td>
                      <td className="max-w-xs truncate px-3 py-2">
                        <a href={row.image_url} target="_blank" rel="noreferrer" className="text-brand underline-offset-4 hover:underline">
                          {row.image_url}
                        </a>
                      </td>
                      <td className={cn("px-3 py-2", row.status === "failed" ? "text-danger" : "text-muted-foreground")}>
                        {row.status === "duplicate" ? "Trùng: " : row.status === "skipped" ? "Bỏ qua: " : "Lỗi: "}
                        {row.reason}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.issues_total > data.issues.length && (
                <p className="p-3 text-xs text-muted-foreground">
                  Hiện {data.issues.length} / {data.issues_total} dòng.
                </p>
              )}
              {data.counts.failed > 0 && (
                <div className="border-t p-3">
                  <Button variant="outline" disabled={busy} onClick={() => void act(() => admin.retryImport(id))}>
                    Tải lại các ảnh lỗi
                  </Button>
                </div>
              )}
            </div>
          ))}
      </div>
    </>
  );
}
