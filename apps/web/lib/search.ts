export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type SearchHit = {
  asset_id: string;
  score: number;
  scores: SearchScores;
  entity_ids: string[];
  entity_names: string[];
  brand_ids?: string[] | null;
  page_title?: string | null;
  domain?: string | null;
  categories?: string[];
  market_tiers?: string[];
  image_url: string;
};

type RankedScore = { rank: number; score: number };

export type SearchScores = {
  dense: RankedScore | null;
  sparse: RankedScore | null;
  graph_prefetch: RankedScore | null;
  graph: number | null;
  category: number | null;
  img: number | null;
  rrf: number;
};

type TraceNode = { id: string; label: string };

export type TraceEdge = {
  entity_id: string;
  source_entity_id: string;
  source: string | null;
  confidence: number | null;
  reviewed: boolean;
  evidence: { field: string; text: string }[];
};

export type SearchTrace = {
  version: 1;
  relation: string | null;
  cue: string | null;
  mode: "shared_value" | "value_filter" | null;
  origins: TraceNode[];
  values: (TraceNode & {
    kind: string;
    origin_edges: TraceEdge[];
    target_edges: TraceEdge[];
  })[];
  targets: TraceNode[];
  excluded: TraceNode[];
  brand_filter: TraceNode | null;
  unsupported_cues: { relation: string; cue: string }[];
  negation: {
    cue: string;
    applied: boolean;
    reason: "no_targets" | "no_origin" | "not_searched" | null;
  } | null;
  category: {
    source: "lexicon" | "knn" | "mixed" | null;
    categories: TraceNode[];
    matches: { text: string; category: string; support: number; share: number }[];
    tiers: string[];
    ignored_tiers: { tier: string; cue: string; reason: string }[];
    segments: { text: string; categories: string[]; tiers: string[] }[];
    groups: string[];
  } | null;
  status:
    | "clarification"
    | "out_of_scope"
    | "expanded"
    | "no_target"
    | "no_edge"
    | "no_origin"
    | "unsupported_relation"
    | "category"
    | "content";
  channels: Record<Exclude<keyof SearchScores, "rrf">, "ready" | "inactive" | "unavailable">;
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
  trace: SearchTrace;
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
