import { authFetch } from "@/lib/api";
import type { Subgraph } from "@/lib/graph";
import { API_URL } from "@/lib/search";

// Types of /api/v1/admin (services/api/src/visualseek_api/application/admin.py).

export type BrandValue = { id: string; name: string; new: boolean } | null;
export type CategoryValue = { id: string; name: string; l1: string } | null;

export type Fields = {
  brand: BrandValue;
  category: CategoryValue;
  product_type: string | null;
  market_tier: string | null;
  reviewed: boolean;
  treats: string[];
  ingredients: string[];
  fits: string[];
  origin: string;
  form_factor: string;
  target_audience: string;
  material: string;
};
export type FieldName = keyof Fields;

export type Overview = {
  entities: number;
  with_brand: number;
  without_category: number;
  needs_review: number;
  images: number;
  hidden_images: number;
  uploaded_images: number;
  pending_entities: number;
  pending_fields: number;
  imported_entities: number;
  pending_new_entities: number;
  last_release: Release | null;
  graph_fingerprint: string;
};

export type EntityRow = {
  id: string;
  name: string;
  brand: string;
  category: string | null;
  images: number;
  hidden_images: number;
  uploaded_images: number;
  needs_review: boolean;
  imported: boolean;
  pending: string[];
  thumb: string | null;
};

export type Change = {
  seq: number;
  at: string;
  actor: { id: string; email: string | null };
  entity_id: string;
  field: string;
  before: unknown;
  after: unknown;
  note: string;
};

export type EntityImage = {
  asset_id: string;
  hidden: boolean;
  hidden_reason: string | null;
  uploaded: boolean;
  description: string | null;
  page_url: string | null;
};

export type EntityEdge = {
  rel: string;
  target: string;
  label: string;
  type: string;
  source: string | null;
  confidence: number | null;
  reviewed: boolean | null;
  shared_with: number;
};

export type EntityDetail = {
  id: string;
  name: string;
  members: { id: string; name: string }[];
  aliases: string[];
  label: {
    category: string | null;
    category_name: string | null;
    category_l1: string | null;
    product_type: string | null;
    market_tier: string | null;
    source: string | null;
    model: string | null;
    basis: string | null;
    confidence: string | null;
    needs_review: boolean | null;
  };
  live: Fields;
  draft: Fields;
  pending: FieldName[];
  edges: EntityEdge[];
  images: EntityImage[];
  history: Change[];
  image_history: { at: string; actor: { email: string | null }; asset_id: string; hidden: boolean; reason: string }[];
};

/** Relations as applying the pending edits would build them (application/preview.py). */
export type Preview = Subgraph & {
  table: {
    rel: string;
    target: string;
    label: string;
    source: string | null;
    reviewed: boolean | null;
    shared_with: number;
    change: "added" | "removed" | null;
  }[];
  attributes: { field: string; before: string; after: string }[];
  added: number;
  removed: number;
  blocked: "category_required" | null;
};

export type VocabItem = { id: string; label: string; count: number };
export type VocabKind = "brand" | "category" | "purpose" | "ingredient" | "model" | "place";

export type Release = {
  at: string;
  actor: string;
  through_seq: number;
  made_by_changed: number;
  categories_changed: number;
  category_payload_points: number;
  graph_fingerprint: string;
  previous_dir: string;
};

export type ReleaseJob = {
  status: "idle" | "running" | "succeeded" | "failed";
  started_at: string | null;
  finished_at: string | null;
  actor: string | null;
  error: string | null;
  log: string[];
};

export type PendingNewEntity = {
  entity_id: string;
  name: string;
  import_id: string | null;
  category: string | null;
  category_name: string | null;
  product_type: string | null;
  labelled: boolean;
};

export type ReleaseStatus = {
  pending: { entity_id: string; name: string; field: FieldName; before: unknown; after: unknown }[];
  new_entities: PendingNewEntity[];
  job: ReleaseJob;
  history: Release[];
};

export const ENTITY_FILTERS = [
  ["all", "Tất cả"],
  ["needs_review", "Cần duyệt"],
  ["pending", "Chờ áp dụng"],
  ["no_category", "Chưa có loại"],
  ["no_brand", "Chưa có hãng"],
  ["no_images", "Không có ảnh"],
  ["hidden_images", "Có ảnh bị ẩn"],
  ["uploaded", "Có ảnh tải lên"],
  ["imported", "Nhập từ CSV"],
  ["edited", "Đã sửa"],
] as const;
export type EntityFilter = (typeof ENTITY_FILTERS)[number][0];

export const FIELD_LABEL: Record<FieldName, string> = {
  brand: "Hãng",
  category: "Loại hàng",
  product_type: "Loại sản phẩm",
  market_tier: "Phân khúc",
  reviewed: "Đã duyệt",
  treats: "Công dụng",
  ingredients: "Thành phần",
  fits: "Lắp cho",
  origin: "Xuất xứ",
  form_factor: "Dạng",
  target_audience: "Đối tượng",
  material: "Chất liệu",
};

export const MARKET_TIERS: [string, string][] = [
  ["not_applicable", "Không áp dụng"],
  ["luxury", "Cao cấp"],
  ["premium", "Tầm trung cao"],
  ["mass", "Phổ thông"],
  ["unknown", "Chưa rõ"],
];

export const SOURCE_LABEL: Record<string, string> = {
  human: "người duyệt",
  admin: "quản trị viên",
  gemini: "Gemini",
  claude: "Claude",
  other: "mô hình khác",
  free: "luật tự động",
  admin_edit: "quản trị viên",
  jsonld: "dữ liệu trang nguồn",
  name: "tên sản phẩm",
  review: "người duyệt",
};

const ERRORS: Record<string, string> = {
  forbidden: "Tài khoản này không có quyền quản trị.",
  category_required: "Chọn loại hàng cho sản phẩm trước.",
  duplicate_image: "Ảnh này đã có trong kho.",
  image_too_large: "Ảnh quá lớn (tối đa 10 MB).",
  invalid_image: "Tệp không phải ảnh JPEG, PNG hoặc WebP hợp lệ, hoặc ảnh quá nhỏ.",
  nothing_to_release: "Không có thay đổi nào đang chờ.",
  release_running: "Đang có một lần áp dụng thay đổi chạy.",
  uploads_unavailable: "Máy chủ chưa nạp vector ảnh nên chưa tải ảnh lên được.",
  search_unavailable: "Máy chủ tìm kiếm chưa nạp dữ liệu.",
  not_found: "Không tìm thấy.",
  csv_too_large: "Tệp CSV quá lớn (tối đa 5 MB). Chia nhỏ file rồi tải lên lần lượt.",
  group_locked: "Ảnh của nhóm này đã được nạp vào kho, không đổi được nữa.",
};

// Types of /api/v1/admin/imports (services/api/src/visualseek_api/application/imports.py).

export type ImportStatus =
  | "queued"
  | "running"
  | "waiting_match"
  | "waiting_release"
  | "label_failed"
  | "done"
  | "failed";

export type GroupStatus =
  | "processing"
  | "waiting_match"
  | "no_images"
  | "publishing"
  | "published"
  | "labelling"
  | "label_failed"
  | "ready"
  | "ready_unlabelled"
  | "released";

export type ImportSummary = {
  id: string;
  file_name: string;
  created_at: string;
  actor: string | null;
  status: ImportStatus;
  step: string;
  progress: { done: number; total: number };
  error: string | null;
  counts: {
    rows: number;
    images: number;
    duplicates: number;
    failed: number;
    skipped: number;
    groups: number;
    existing: number;
    new: number;
    waiting_match: number;
    ready: number;
    released: number;
    published_images: number;
    label_failed: number;
  };
};

export type ImportCandidate = {
  entity_id: string;
  name: string;
  score: number;
  code_conflict: boolean;
  thumb: string | null;
  images: number;
};

export type ImportGroup = {
  key: string;
  name: string;
  csv_entity_id: string;
  kind: "existing" | "near" | "new" | null;
  score: number | null;
  candidates: ImportCandidate[];
  decision: { type: "existing" | "new"; entity_id: string | null; at: string; auto: boolean } | null;
  status: GroupStatus;
  target: { id: string; name: string; live: boolean } | null;
  rows: number;
  images: number;
  duplicates: number;
  failed: number;
  published: number;
  thumbs: string[];
  label: {
    category: string | null;
    category_name: string | null;
    product_type: string | null;
    basis: string | null;
    confidence: string | null;
    model: string | null;
  } | null;
  enrich: {
    brand: string | null;
    treats: string[] | null;
    ingredients: string[] | null;
    fits: string[] | null;
    origin: string | null;
    material: string | null;
    form_factor: string | null;
    target_audience: string | null;
  } | null;
  error: string | null;
};

export type ImportIssue = {
  line: number;
  entity_name: string;
  image_url: string;
  status: "skipped" | "duplicate" | "failed";
  reason: string | null;
  dup_of: string | null;
};

export type ImportDetail = ImportSummary & {
  groups: ImportGroup[];
  issues: ImportIssue[];
  issues_total: number;
  labeller: string | null;
};

export class AdminError extends Error {
  constructor(
    message: string,
    readonly code: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await authFetch(`${API_URL}/api/v1/admin${path}`, init);
  } catch (error) {
    if (error instanceof TypeError) {
      throw new AdminError(`Chưa kết nối được máy chủ (${API_URL}).`, "network");
    }
    throw error;
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; message?: string };
      detail?: unknown;
    } | null;
    const code = body?.error?.code ?? (response.status === 422 ? "validation" : "error");
    const message =
      ERRORS[code] ??
      ((code === "invalid_csv" || code === "invalid_import") && body?.error?.message
        ? body.error.message
        : null) ??
      (code === "invalid_admin_request" && body?.error?.message
        ? `Giá trị chưa hợp lệ: ${body.error.message}`
        : `Máy chủ báo lỗi (${response.status}).`);
    throw new AdminError(message, code);
  }
  return (await response.json()) as T;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const admin = {
  overview: () => call<Overview>("/overview"),
  entities: (q: string, filter: EntityFilter, offset: number, limit = 30) =>
    call<{ total: number; items: EntityRow[] }>(
      `/entities?${new URLSearchParams({ q, filter, offset: String(offset), limit: String(limit) })}`,
    ),
  entity: (id: string) => call<EntityDetail>(`/entities/${encodeURIComponent(id)}`),
  edit: (id: string, changes: Partial<Record<FieldName, unknown>>, note = "") =>
    call<EntityDetail>(`/entities/${encodeURIComponent(id)}/changes`, json("POST", { changes, note })),
  preview: (id: string, changes: Partial<Record<FieldName, unknown>>, siblings: number, signal?: AbortSignal) =>
    call<Preview>(`/entities/${encodeURIComponent(id)}/preview`, { ...json("POST", { changes, siblings }), signal }),
  discard: (id: string, fields: FieldName[]) =>
    call<EntityDetail>(`/entities/${encodeURIComponent(id)}/discard`, json("POST", { fields })),
  vocab: (kind: VocabKind, q: string, limit = 20) =>
    call<VocabItem[]>(`/vocab/${kind}?${new URLSearchParams({ q, limit: String(limit) })}`),
  setHidden: (assetId: string, hidden: boolean, reason = "") =>
    call<{ asset_id: string; hidden: boolean }>(`/images/${assetId}/visibility`, json("POST", { hidden, reason })),
  upload: (entityId: string, file: File, caption: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("caption", caption);
    return call<{ asset_id: string }>(`/entities/${encodeURIComponent(entityId)}/images`, {
      method: "POST",
      body: form,
    });
  },
  deleteUpload: (assetId: string) => call<{ deleted: boolean }>(`/uploads/${assetId}`, { method: "DELETE" }),
  imports: () => call<ImportSummary[]>("/imports"),
  importDetail: (id: string) => call<ImportDetail>(`/imports/${encodeURIComponent(id)}`),
  createImport: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<ImportDetail>("/imports", { method: "POST", body: form });
  },
  decide: (id: string, key: string, choice: { entityId: string } | { new: true }) =>
    call<ImportDetail>(
      `/imports/${encodeURIComponent(id)}/decide`,
      json("POST", "entityId" in choice ? { key, entity_id: choice.entityId } : { key, new: true }),
    ),
  retryImport: (id: string, key?: string) =>
    call<ImportDetail>(`/imports/${encodeURIComponent(id)}/retry`, json("POST", { key: key ?? null })),
  release: () => call<ReleaseStatus>("/release"),
  startRelease: () => call<ReleaseJob>("/release", { method: "POST" }),
};

export function imageUrl(assetId: string): string {
  return `${API_URL}/api/v1/images/${assetId}`;
}

const STEP_TEXT: Record<string, string> = {
  plan: "Kiểm tra các thay đổi",
  stage: "Tạo bản sao dữ liệu để dựng",
  run: "Đang dựng lại dữ liệu",
  done: "Xong một bước",
  category_payload: "Cập nhật nhãn của ảnh",
  released: "Đã đổi sang bản mới",
  reloading_search: "Đang nạp lại máy tìm kiếm",
  search_reloaded: "Máy tìm kiếm đã nạp bản mới",
  nothing_to_release: "Không có gì để áp dụng",
  failed: "Lỗi",
};

/** The progress steps of an apply job in Vietnamese; the script's tool output is left out. */
export function applySteps(log: string[]): string[] {
  const out: string[] = [];
  for (const line of log) {
    try {
      const row = JSON.parse(line) as { at?: string; step?: string; error?: string };
      if (!row.step) continue;
      const text = STEP_TEXT[row.step] ?? row.step;
      out.push(`${String(row.at ?? "").slice(11, 19)} ${text}${row.error ? `: ${row.error}` : ""}`.trim());
    } catch {
      // raw output of a build step
    }
  }
  return out;
}

/** A field value as short Vietnamese text. */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "có" : "không";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (typeof value === "object" && "name" in (value as object)) {
    const named = value as { name: string; new?: boolean };
    return named.new ? `${named.name} (hãng mới)` : named.name;
  }
  if (typeof value === "string" && value.startsWith("new:")) return `${value.slice(4)} (hãng mới)`;
  const tier = MARKET_TIERS.find(([id]) => id === value);
  return tier ? tier[1] : String(value);
}

export function formatTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("vi-VN", { hour12: false, dateStyle: "short", timeStyle: "short" });
}
