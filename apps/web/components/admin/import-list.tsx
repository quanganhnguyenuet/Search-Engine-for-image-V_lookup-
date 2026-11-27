"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorBox, PageTitle } from "@/components/admin/admin-shell";
import { IMPORT_STATUS, STEP_LABEL, StatusBadge } from "@/components/admin/import-status";
import { Button } from "@/components/ui/button";
import { admin, formatTime, type ImportSummary } from "@/lib/admin";

const POLL_MS = 3000;
const ACTIVE = new Set(["queued", "running"]);

export function ImportList() {
  const router = useRouter();
  const [items, setItems] = useState<ImportSummary[] | null>(null);
  const [error, setError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    admin.imports().then(
      (rows) => {
        setItems(rows);
        setError("");
      },
      (e: Error) => setError(e.message),
    );
  }, []);
  useEffect(load, [load]);

  const active = items?.some((item) => ACTIVE.has(item.status)) ?? false;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, load]);

  const upload = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) {
      setUploadError("Chọn một tệp CSV trước.");
      return;
    }
    setUploading(true);
    setUploadError("");
    try {
      const created = await admin.createImport(file);
      router.push(`/admin/imports/${created.id}`);
    } catch (e) {
      setUploadError((e as Error).message);
      setUploading(false);
    }
  };

  return (
    <>
      <PageTitle
        title="Nhập ảnh từ CSV"
        description="Mỗi dòng là một ảnh đã duyệt. Tải tệp lên là hệ thống tự chạy: tải ảnh, đọc trang nguồn, đọc chữ trên ảnh, bỏ ảnh trùng rồi so khớp tên. Sản phẩm có sẵn nhận ảnh ngay; tên gần giống chờ bạn chọn; sản phẩm mới được AI phân loại, bổ sung thông tin và vào đồ thị khi bạn áp dụng."
      />

      <form onSubmit={(e) => void upload(e)} className="mb-8 rounded-lg border bg-surface p-4 sm:p-5" aria-labelledby="upload-title">
        <h2 id="upload-title" className="text-lg font-semibold">Tải CSV lên</h2>
        <div className="mt-3 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm font-medium">
              Tệp CSV (UTF-8, tối đa 5 MB · 5.000 dòng)
              <input
                ref={input}
                type="file"
                accept=".csv,text/csv"
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  setUploadError("");
                }}
                aria-describedby="csv-columns"
                className="min-h-11 w-full rounded-md border bg-surface p-2 text-sm font-normal file:mr-3 file:rounded file:border-0 file:bg-muted file:px-3 file:py-1.5 file:text-sm file:font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
              />
            </label>
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" variant="brand" disabled={uploading}>
                {uploading ? "Đang tải lên…" : "Tải lên và xử lý"}
              </Button>
              {file && !uploading && (
                <span className="text-sm text-muted-foreground">
                  {file.name} · {(file.size / 1024).toLocaleString("vi-VN", { maximumFractionDigits: 0 })} KB
                </span>
              )}
            </div>
            {uploadError && (
              <p role="alert" className="text-sm text-danger">
                {uploadError}
              </p>
            )}
          </div>
          <div id="csv-columns" className="rounded-md bg-muted/60 p-3 text-sm">
            <p className="font-medium">Cột được nhận (tên tiếng Việt của bộ đánh nhãn cũng được):</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
              <li>
                <code className="font-mono text-xs">entity_name</code> / Tên entity · <strong>bắt buộc</strong>
              </li>
              <li>
                <code className="font-mono text-xs">image_url</code> / URL ảnh · <strong>bắt buộc</strong>
              </li>
              <li>
                <code className="font-mono text-xs">page_url</code> / URL trang nguồn, <code className="font-mono text-xs">page_title</code> / Tiêu đề nguồn
              </li>
              <li>
                <code className="font-mono text-xs">entity_id</code> / Entity_ID: trùng mã trong đồ thị thì nhận thẳng vào sản phẩm đó
              </li>
              <li>
                <code className="font-mono text-xs">approve</code>: nếu có, dòng khác yes bị bỏ qua
              </li>
            </ul>
          </div>
        </div>
      </form>

      <section aria-labelledby="imports-title">
        <h2 id="imports-title" className="mb-3 text-lg font-semibold">Các lần nhập</h2>
        {error && <ErrorBox message={error} onRetry={load} />}
        {!items && !error && (
          <ul className="divide-y rounded-lg border bg-surface" aria-label="Đang tải">
            {Array.from({ length: 3 }, (_, i) => (
              <li key={i} className="p-4">
                <div className="h-4 w-1/3 animate-pulse rounded bg-muted" />
              </li>
            ))}
          </ul>
        )}
        {items && items.length === 0 && (
          <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
            Chưa có lần nhập nào. Tải một tệp CSV lên ở trên để bắt đầu.
          </p>
        )}
        {items && items.length > 0 && (
          <ul className="divide-y rounded-lg border bg-surface">
            {items.map((item) => {
              const [label, tone] = IMPORT_STATUS[item.status] ?? [item.status, "muted"];
              const c = item.counts;
              return (
                <li key={item.id}>
                  <Link
                    href={`/admin/imports/${item.id}`}
                    className="flex flex-col gap-1.5 p-4 transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand sm:flex-row sm:items-center sm:gap-4"
                  >
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="truncate font-medium">{item.file_name}</span>
                      <span className="text-xs text-muted-foreground">
                        {formatTime(item.created_at)}
                        {item.actor && ` · ${item.actor}`}
                        {ACTIVE.has(item.status) && ` · ${STEP_LABEL[item.step] ?? item.step} ${item.progress.done}/${item.progress.total}`}
                      </span>
                    </span>
                    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs tabular-nums text-muted-foreground">
                      <span>{c.groups} sản phẩm</span>
                      <span>{c.existing} có sẵn</span>
                      <span>{c.new} mới</span>
                      {c.waiting_match > 0 && <span className="font-medium text-warn">{c.waiting_match} cần so khớp</span>}
                      <span>{c.published_images} ảnh đã nạp</span>
                      <StatusBadge label={label} tone={tone} />
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}
