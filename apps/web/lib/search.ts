export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type SearchHit = {
  asset_id: string;
  score: number;
  entity_ids: string[];
  entity_names: string[];
  brand_ids?: string[] | null;
  page_title?: string | null;
  domain?: string | null;
  image_url: string;
};

export type ClarificationOption = { entity_id: string; name: string };

export type SearchPlan = {
  seeds: string[];
  hops: number;
  anchors: string[];
  relation: boolean;
  negation: boolean;
  dense_seed: boolean;
  grouped: boolean;
  seed_names: string[];
};

export type SearchResponse = {
  query: string;
  results: SearchHit[];
  out_of_scope: boolean;
  clarification: { text: string; options: ClarificationOption[] }[] | null;
  plan: SearchPlan;
  timings: Record<string, number>;
  graph_fingerprint: string;
};

export async function searchText(query: string, signal?: AbortSignal): Promise<SearchResponse> {
  const response = await fetch(`${API_URL}/api/v1/search/text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `API trả về mã ${response.status}`);
  }
  return response.json();
}
