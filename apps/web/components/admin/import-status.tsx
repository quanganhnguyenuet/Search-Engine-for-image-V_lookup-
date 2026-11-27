import type { GroupStatus, ImportStatus } from "@/lib/admin";
import { cn } from "@/lib/utils";

type Tone = "brand" | "warn" | "danger" | "muted";

const TONE: Record<Tone, string> = {
  brand: "bg-brand-soft text-brand",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  muted: "bg-muted text-muted-foreground",
};

export const IMPORT_STATUS: Record<ImportStatus, [string, Tone]> = {
  queued: ["Đang chờ chạy", "muted"],
  running: ["Đang xử lý", "warn"],
  waiting_match: ["Cần so khớp tay", "warn"],
  waiting_release: ["Chờ áp dụng", "warn"],
  label_failed: ["Có lỗi gán nhãn", "danger"],
  done: ["Xong", "brand"],
  failed: ["Lỗi", "danger"],
};

export const GROUP_STATUS: Record<GroupStatus, [string, Tone]> = {
  processing: ["Đang xử lý", "muted"],
  waiting_match: ["Chờ so khớp", "warn"],
  no_images: ["Không còn ảnh mới", "muted"],
  publishing: ["Đang nạp ảnh", "warn"],
  published: ["Đã nạp ảnh", "brand"],
  labelling: ["Đang gán nhãn", "warn"],
  label_failed: ["Gán nhãn lỗi", "danger"],
  ready: ["Chờ áp dụng", "warn"],
  ready_unlabelled: ["Chờ áp dụng · chưa có nhãn", "danger"],
  released: ["Đã lên tìm kiếm", "brand"],
};

export const STEP_LABEL: Record<string, string> = {
  queued: "Đang chờ",
  fetch: "Tải ảnh và đọc trang nguồn",
  ocr: "Đọc chữ trên ảnh",
  match: "So khớp tên với sản phẩm có sẵn",
  dedupe: "Khử ảnh gần trùng",
  publish: "Nạp ảnh vào tìm kiếm",
  label: "AI phân loại và bổ sung thông tin sản phẩm mới",
  done: "Xong",
};

export function StatusBadge({ label, tone, live }: { label: string; tone: Tone; live?: boolean }) {
  return (
    <span
      className={cn("inline-flex items-center whitespace-nowrap rounded px-2 py-0.5 text-xs font-medium", TONE[tone])}
      aria-live={live ? "polite" : undefined}
    >
      {label}
    </span>
  );
}
