"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ErrorBox, PageTitle } from "@/components/admin/admin-shell";
import { buttonVariants } from "@/components/ui/button";
import { admin, formatTime, type Overview } from "@/lib/admin";

type Tile = { label: string; value: number; href?: string; hint?: string; tone?: "warn" };

export function AdminOverview() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setError("");
    admin.overview().then(setData, (e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const tiles: Tile[] = data
    ? [
        { label: "Sản phẩm", value: data.entities, href: "/admin/entities" },
        {
          label: "Cần duyệt nhãn",
          value: data.needs_review,
          href: "/admin/entities?filter=needs_review",
          hint: "Nhãn do AI gán, chưa có người kiểm",
          tone: data.needs_review ? "warn" : undefined,
        },
        { label: "Chưa có loại hàng", value: data.without_category, href: "/admin/entities?filter=no_category" },
        { label: "Có hãng", value: data.with_brand, href: "/admin/entities?filter=no_brand", hint: "Bấm để xem các sản phẩm chưa có hãng" },
        { label: "Ảnh trong kho", value: data.images },
        { label: "Ảnh đang ẩn", value: data.hidden_images, href: "/admin/entities?filter=hidden_images" },
        { label: "Ảnh tải lên", value: data.uploaded_images, href: "/admin/entities?filter=uploaded" },
        {
          label: "Sản phẩm nhập từ CSV",
          value: data.imported_entities,
          href: data.pending_new_entities ? "/admin/imports" : "/admin/entities?filter=imported",
          hint: data.pending_new_entities ? `${data.pending_new_entities} sản phẩm mới chờ áp dụng` : "Bấm để xem",
          tone: data.pending_new_entities ? "warn" : undefined,
        },
        {
          label: "Thay đổi chờ áp dụng",
          value: data.pending_fields,
          href: "/admin/entities?filter=pending",
          hint: `${data.pending_entities} sản phẩm`,
          tone: data.pending_fields ? "warn" : undefined,
        },
      ]
    : [];

  return (
    <>
      <PageTitle
        title="Tổng quan"
        description="Sửa thông tin trong đồ thị, kiểm tra sản phẩm và quản lý ảnh. Thay đổi về đồ thị chỉ tới người dùng sau khi áp dụng; ẩn ảnh có hiệu lực ngay."
        actions={
          <Link href="/admin/entities" className={buttonVariants({ variant: "brand" })}>
            Tìm sản phẩm
          </Link>
        }
      />
      {error && <ErrorBox message={error} onRetry={load} />}
      {!data && !error && (
        <ul className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Đang tải">
          {Array.from({ length: 8 }, (_, i) => (
            <li key={i} className="h-24 animate-pulse rounded-lg bg-muted" />
          ))}
        </ul>
      )}
      {data && (
        <>
          <ul className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {tiles.map((tile) => {
              const body = (
                <>
                  <span className="text-sm text-muted-foreground">{tile.label}</span>
                  <span className={`text-2xl font-semibold tabular-nums sm:text-3xl ${tile.tone === "warn" ? "text-warn" : ""}`}>
                    {tile.value.toLocaleString("vi-VN")}
                  </span>
                  {tile.hint && <span className="text-xs text-muted-foreground">{tile.hint}</span>}
                </>
              );
              const box = "flex h-full flex-col gap-1 rounded-lg border bg-surface p-4";
              return (
                <li key={tile.label}>
                  {tile.href ? (
                    <Link
                      href={tile.href}
                      className={`${box} transition-colors hover:border-brand/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand`}
                    >
                      {body}
                    </Link>
                  ) : (
                    <div className={box}>{body}</div>
                  )}
                </li>
              );
            })}
          </ul>
          <section className="mt-8 rounded-lg border bg-surface p-4 text-sm">
            <h2 className="font-semibold">Bản đang chạy</h2>
            <dl className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-[max-content_1fr]">
              <dt className="text-muted-foreground">Mã phiên bản đồ thị</dt>
              <dd className="font-mono text-xs">{data.graph_fingerprint.slice(0, 16)}…</dd>
              <dt className="text-muted-foreground">Lần áp dụng gần nhất</dt>
              <dd>
                {data.last_release
                  ? `${formatTime(data.last_release.at)} · ${data.last_release.actor || "chạy tay"} · ${data.last_release.categories_changed} sản phẩm`
                  : "Chưa áp dụng lần nào từ trang quản trị"}
              </dd>
            </dl>
          </section>
        </>
      )}
    </>
  );
}
