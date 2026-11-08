export const categories = [
  { id: "am_thuc", label: "Ẩm thực", symbol: "◒" },
  { id: "kien_truc", label: "Kiến trúc", symbol: "⌂" },
  { id: "phong_canh", label: "Phong cảnh", symbol: "△" },
  { id: "le_hoi", label: "Lễ hội", symbol: "✺" },
  { id: "trang_phuc", label: "Trang phục", symbol: "✧" },
  { id: "thu_cong_my_nghe", label: "Thủ công mỹ nghệ", symbol: "❋" },
] as const;

export type CultureAsset = {
  id: string; title: string; category: string; categoryLabel: string;
  description: string; tags: string[]; context: Record<string, string>;
  imageUrl: string; sourceUrl: string;
  score?: number;
  match_reason?: string | null;
  match_kind?: string | null;
};

export type SearchResponse = {
  items: CultureAsset[]; total: number; page: number; pages: number;
  libraryTotal: number; counts: Record<string, number>;
  mode?: "contextual" | "semantic" | "rag";
  corpus_total?: number | null;
  coverage_complete?: boolean;
  interpretation?: { intent: string; visual_query: string; metadata_query: string;
    keywords: string[]; exclusions: string[];
    requirements?: { id: `R${number}`; text: string }[];
    preferences?: { id: `P${number}`; text: string }[];
    clarification_question?: string | null; suggested_queries?: string[] } | null;
  query_model?: string | null;
  search_route?: "lexical" | "hybrid" | "llm_hybrid";
  llm_used?: boolean;
  llm_call_count?: number;
  vector_cache_hit?: boolean;
  result_scope?: "top_k";
  answer?: string;
  abstained?: boolean;
  abstention_reason?: string | null;
  clarification_required?: boolean;
  clarification_question?: string | null;
  suggested_queries?: string[];
  evidence_type?: string;
  citations?: { id: string; asset_id: string; title: string }[];
  generator_model?: string | null;
  timings?: { embedding_ms: number; retrieval_ms: number; generation_ms?: number;
    planning_ms?: number; reranking_ms?: number; total_ms?: number };
};
