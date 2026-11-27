import { useEffect, useState } from "react";

import { authFetch } from "@/lib/api";
import { API_URL } from "@/lib/search";

export type NodeType = "entity" | "brand" | "category" | "purpose" | "ingredient" | "model" | "place";

export type GraphNode = {
  id: string;
  label: string;
  type: NodeType;
  /** origin: where the query started; seed: a result entity; sibling: context; value: shared value. */
  role: "origin" | "seed" | "sibling" | "value";
  /** Number of relations (entity) or of linked entities (value) in the whole snapshot. */
  degree: number;
  props: Record<string, string | number>;
  /** Preview only: applying the pending edits adds or removes it. */
  change?: Change;
};

export type Change = "added" | "removed" | null;

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  rel: string;
  origin: string | null;
  confidence: number | null;
  reviewed: boolean | null;
  change?: Change;
};

export type Subgraph = { nodes: GraphNode[]; edges: GraphEdge[]; truncated: boolean };

export const NODE_TYPES: NodeType[] = ["entity", "brand", "category", "purpose", "ingredient", "model", "place"];

export const NODE_LABEL: Record<NodeType, string> = {
  entity: "Sản phẩm",
  brand: "Hãng",
  category: "Loại hàng",
  purpose: "Công dụng",
  ingredient: "Thành phần",
  model: "Dòng xe / thiết bị",
  place: "Xuất xứ",
};

/** Verb phrase of a relation, read from the entity towards the value. */
export const REL_TEXT: Record<string, string> = {
  MADE_BY: "do hãng sản xuất",
  IS_A: "thuộc loại",
  TREATS: "có công dụng",
  HAS_INGREDIENT: "chứa",
  FITS: "lắp được cho",
  ORIGIN: "xuất xứ từ",
};

/** Short label drawn on the edge. */
export const REL_SHORT: Record<string, string> = {
  MADE_BY: "hãng",
  IS_A: "loại",
  TREATS: "công dụng",
  HAS_INGREDIENT: "thành phần",
  FITS: "lắp cho",
  ORIGIN: "xuất xứ",
};

export const PROP_LABEL: Record<string, string> = {
  product_type: "Loại sản phẩm",
  form_factor: "Dạng",
  target_audience: "Đối tượng",
  material: "Chất liệu",
  origin: "Xuất xứ",
  merged_records: "Bản ghi đã gộp",
};

export type SubgraphQuery = {
  ids: string[];
  origins?: string[];
  siblings?: number;
  sharedOnly?: boolean;
};

export async function fetchSubgraph(query: SubgraphQuery, signal?: AbortSignal): Promise<Subgraph> {
  const params = new URLSearchParams();
  for (const id of query.ids) params.append("id", id);
  for (const id of query.origins ?? []) params.append("origin", id);
  if (query.siblings) params.set("siblings", String(query.siblings));
  if (query.sharedOnly) params.set("shared_only", "true");
  const response = await authFetch(`${API_URL}/api/v1/graph/subgraph?${params}`, { signal });
  if (response.status === 404) {
    throw new Error("API chưa có đồ thị quan hệ. Khởi động lại API để nạp phiên bản mới.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `API trả về mã ${response.status}`);
  }
  return response.json();
}

type SubgraphState =
  | { status: "idle" | "loading" }
  | { status: "error"; message: string }
  | { status: "done"; data: Subgraph };

const cache = new Map<string, Subgraph>();

/** After the admin applies changes, cached answers describe the old graph. */
export function clearSubgraphCache(): void {
  cache.clear();
}

/** Loads a subgraph; `null` keeps it idle. Answers are cached for the page's lifetime. */
export function useSubgraph(query: SubgraphQuery | null): SubgraphState {
  const key = query ? JSON.stringify(query) : null;
  const [state, setState] = useState<{ key: string | null; value: SubgraphState }>({
    key: null,
    value: { status: "idle" },
  });

  useEffect(() => {
    if (!key) return;
    const cached = cache.get(key);
    if (cached) {
      setState({ key, value: { status: "done", data: cached } });
      return;
    }
    const controller = new AbortController();
    setState({ key, value: { status: "loading" } });
    fetchSubgraph(JSON.parse(key) as SubgraphQuery, controller.signal)
      .then((data) => {
        cache.set(key, data);
        setState({ key, value: { status: "done", data } });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message =
          error instanceof TypeError ? `Không kết nối được API tại ${API_URL}.` : (error as Error).message;
        setState({ key, value: { status: "error", message } });
      });
    return () => controller.abort();
  }, [key]);

  if (!key) return { status: "idle" };
  // A stale answer for another key must not be drawn while the new one loads.
  return state.key === key ? state.value : { status: "loading" };
}
