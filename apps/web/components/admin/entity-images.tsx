"use client";

import { type ChangeEvent, type FormEvent, useEffect, useState } from "react";

import { inputClass } from "@/components/admin/fields";
import { Button } from "@/components/ui/button";
import { admin, type EntityDetail, type EntityImage, imageUrl } from "@/lib/admin";
import { cn } from "@/lib/utils";

const ACCEPT = "image/jpeg,image/png,image/webp";
const MAX_BYTES = 10 * 1024 * 1024;

export function EntityImages({ detail, onChanged }: { detail: EntityDetail; onChanged: () => Promise<unknown> }) {
  const visible = detail.images.filter((i) => !i.hidden).length;
  return (
    <section aria-labelledby="images-title" className="mt-6 rounded-lg border bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="images-title" className="text-lg font-semibold">
          Ảnh <span className="font-normal text-muted-foreground">· {visible}/{detail.images.length} đang hiện trong tìm kiếm</span>
        </h2>
        <p className="text-sm text-muted-foreground">Ẩn và tải lên có hiệu lực ngay, không cần bấm áp dụng.</p>
      </div>
      {detail.images.length === 0 ? (
        <p className="mt-3 rounded-lg border border-dashed p-4 text-sm text-muted-foreground">Sản phẩm chưa có ảnh nào. Tải ảnh lên ở dưới.</p>
      ) : (
        <ul className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {detail.images.map((image) => (
            <ImageCard key={image.asset_id} image={image} onChanged={onChanged} />
          ))}
        </ul>
      )}
      <UploadForm entityId={detail.id} onDone={onChanged} />
    </section>
  );
}

function ImageCard({ image, onChanged }: { image: EntityImage; onChanged: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      await onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <li className={cn("flex flex-col overflow-hidden rounded-lg border", image.hidden && "border-dashed")}>
      <a
        href={imageUrl(image.asset_id)}
        target="_blank"
        rel="noreferrer"
        className="relative block aspect-square bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand"
        aria-label="Mở ảnh gốc trong tab mới"
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- served by the API, sizes vary */}
        <img
          src={imageUrl(image.asset_id)}
          alt=""
          width={240}
          height={240}
          loading="lazy"
          className={cn("size-full object-contain p-2", image.hidden && "opacity-40 grayscale")}
        />
        <span className="absolute left-2 top-2 flex flex-col items-start gap-1">
          {image.hidden && <span className="rounded bg-slate-900/80 px-1.5 text-xs font-medium text-white">Đang ẩn</span>}
          {image.uploaded && <span className="rounded bg-blue-700/90 px-1.5 text-xs font-medium text-white">Tải lên</span>}
        </span>
      </a>
      <div className="flex flex-1 flex-col gap-2 border-t p-2 text-xs">
        {image.description && <p className="line-clamp-2 text-muted-foreground" title={image.description}>{image.description}</p>}
        {image.hidden && image.hidden_reason && <p className="text-muted-foreground">Lý do: {image.hidden_reason}</p>}
        <div className="mt-auto flex flex-wrap gap-1.5">
          <Button
            variant="outline"
            className="h-9 px-2 text-xs"
            disabled={busy}
            onClick={() => void run(() => admin.setHidden(image.asset_id, !image.hidden))}
          >
            {image.hidden ? "Hiện lại" : "Ẩn khỏi tìm kiếm"}
          </Button>
          {image.uploaded &&
            (confirming ? (
              <Button
                className="h-9 bg-danger px-2 text-xs text-white hover:bg-danger/90"
                disabled={busy}
                onClick={() => void run(() => admin.deleteUpload(image.asset_id))}
              >
                Xác nhận xoá
              </Button>
            ) : (
              <Button variant="outline" className="h-9 px-2 text-xs text-danger" disabled={busy} onClick={() => setConfirming(true)}>
                Xoá
              </Button>
            ))}
          {confirming && (
            <Button variant="outline" className="h-9 px-2 text-xs" onClick={() => setConfirming(false)}>
              Không xoá
            </Button>
          )}
        </div>
        {error && <p role="alert" className="text-danger">{error}</p>}
      </div>
    </li>
  );
}

function UploadForm({ entityId, onDone }: { entityId: string; onDone: () => Promise<unknown> }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [caption, setCaption] = useState("");
  const [status, setStatus] = useState<{ kind: "idle" | "uploading" | "done" } | { kind: "error"; message: string }>({ kind: "idle" });
  const [inputKey, setInputKey] = useState(0);

  useEffect(() => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const pick = (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = event.target.files?.[0] ?? null;
    setStatus({ kind: "idle" });
    if (chosen && chosen.size > MAX_BYTES) {
      setFile(null);
      setStatus({ kind: "error", message: "Ảnh quá lớn (tối đa 10 MB)." });
      return;
    }
    setFile(chosen);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setStatus({ kind: "uploading" });
    try {
      await admin.upload(entityId, file, caption);
      await onDone();
      setFile(null);
      setPreview("");
      setCaption("");
      setInputKey((k) => k + 1);
      setStatus({ kind: "done" });
    } catch (e) {
      setStatus({ kind: "error", message: (e as Error).message });
    }
  };

  return (
    <form onSubmit={submit} className="mt-6 flex flex-col gap-3 rounded-lg border border-dashed p-4" aria-labelledby="upload-title">
      <h3 id="upload-title" className="font-semibold">Tải ảnh mới cho sản phẩm này</h3>
      <div className="flex flex-col gap-4 sm:flex-row">
        <div className="flex size-28 shrink-0 items-center justify-center overflow-hidden rounded-lg border bg-white">
          {preview ? (
            // eslint-disable-next-line @next/next/no-img-element -- local preview
            <img src={preview} alt="Ảnh sắp tải lên" className="size-full object-contain" />
          ) : (
            <span className="px-2 text-center text-xs text-slate-500">Chưa chọn ảnh</span>
          )}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Tệp ảnh (JPEG, PNG hoặc WebP, tối đa 10 MB)
            <input key={inputKey} type="file" accept={ACCEPT} onChange={pick} className="text-sm font-normal file:mr-3 file:h-10 file:rounded-md file:border file:bg-muted file:px-3 file:text-sm" />
          </label>
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Mô tả ngắn (không bắt buộc)
            <input
              value={caption}
              maxLength={300}
              onChange={(e) => setCaption(e.target.value)}
              placeholder="ví dụ: hộp 20 viên, mặt trước"
              className={inputClass}
            />
            <span className="font-normal text-muted-foreground">Được ghép với tên và hãng của sản phẩm để tìm kiếm bằng chữ.</span>
          </label>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="brand" disabled={!file || status.kind === "uploading"}>
          {status.kind === "uploading" ? "Đang tải lên và nhúng…" : "Tải lên"}
        </Button>
        <p aria-live="polite" className="text-sm">
          {status.kind === "done" && <span className="text-brand">Đã thêm ảnh; ảnh tìm được ngay.</span>}
          {status.kind === "error" && <span role="alert" className="text-danger">{status.message}</span>}
        </p>
      </div>
    </form>
  );
}
