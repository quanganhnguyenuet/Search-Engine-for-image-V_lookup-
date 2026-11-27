import { relationLabel } from "@/lib/search";
import type { SearchResponse, SearchScores, SearchTrace, TraceEdge } from "@/lib/search";

export const STATUS: Record<SearchTrace["status"], string> = {
  expanded: "Đã mở rộng qua đồ thị",
  no_target: "Không còn sản phẩm đích",
  no_edge: "Không tìm thấy quan hệ của sản phẩm gốc",
  no_origin: "Chưa xác định được sản phẩm gốc",
  unsupported_relation: "Quan hệ chưa hỗ trợ, chỉ tìm theo nội dung",
  category: "Tìm theo nội dung, ưu tiên loại hàng",
  content: "Tìm theo nội dung",
  clarification: "Cần xác định sản phẩm",
  out_of_scope: "Ngoài phạm vi dữ liệu",
};

const NEGATION = {
  applied: "đã loại sản phẩm gốc khỏi kết quả",
  no_targets: "bị bỏ qua: không có tập đích để loại, sản phẩm gốc vẫn được tìm và cộng điểm",
  no_origin: "bị bỏ qua: không xác định được sản phẩm cần loại",
  not_searched: "bị bỏ qua: câu không được tìm",
} as const;

const TIER_LABEL: Record<string, string> = {
  luxury: "cao cấp (luxury)",
  premium: "tầm trung cao (premium)",
  mass: "phổ thông (mass)",
};

const TIER_IGNORED = {
  not_fashion: "bỏ qua: đối tượng không thuộc thời trang",
  no_fashion_category: "bỏ qua: chưa nhận ra loại hàng thời trang",
} as const;

const SOURCE = { lexicon: "từ cụm từ", knn: "từ sản phẩm gần nhất", mixed: "theo từng vế" } as const;

export const CHANNELS = [
  ["dense", "Dense"],
  ["sparse", "Sparse"],
  ["graph_prefetch", "Dense theo tập đích"],
  ["graph", "Điều chỉnh theo đồ thị"],
  ["category", "Điều chỉnh loại hàng"],
  ["img", "Ảnh"],
] as const;

export function SearchTracePanel({ data }: { data: SearchResponse }) {
  const { trace, results } = data;
  const labels = new Map([...trace.origins, ...trace.targets].map(({ id, label }) => [id, label]));
  const names = new Map((trace.category?.categories ?? []).map(({ id, label }) => [id, label]));

  return (
    <details key={data.query} className="rounded-lg border bg-surface text-sm">
      <summary className="cursor-pointer px-4 py-3 font-medium focus-visible:outline-brand">
        Chi tiết kỹ thuật: {STATUS[trace.status]}
      </summary>
      <div className="space-y-4 border-t p-4">
        <dl className="grid gap-3 sm:grid-cols-2">
          <div>
            <dt className="text-muted-foreground">Quan hệ nhận ra</dt>
            <dd className="font-medium">
              {trace.relation ? `${relationLabel(trace.relation)} (${trace.relation})` : "—"}
              {trace.cue && <> · <q>{trace.cue}</q></>}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Mẫu truy vấn</dt>
            <dd className="font-medium">
              {trace.mode === "shared_value" ? "Cùng giá trị" : trace.mode === "value_filter" ? "Lọc theo giá trị" : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Sản phẩm gốc</dt>
            <dd>{trace.origins.map((node) => node.label).join(", ") || "—"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Sản phẩm bị loại khỏi tập đích và trừ điểm</dt>
            <dd>{trace.excluded.map((node) => node.label).join(", ") || "—"}</dd>
          </div>
          {trace.brand_filter && (
            <div>
              <dt className="text-muted-foreground">Bộ lọc hãng</dt>
              <dd>{trace.brand_filter.label}</dd>
            </div>
          )}
          {trace.unsupported_cues.length > 0 && (
            <div>
              <dt className="text-muted-foreground">Cụm quan hệ nhận ra nhưng chưa xử lý</dt>
              <dd className="font-medium text-warn">
                {trace.unsupported_cues.map(({ relation, cue }) => `“${cue}” (${relation})`).join(", ")}
              </dd>
            </div>
          )}
          {trace.category && (
            <div>
              <dt className="text-muted-foreground">Loại hàng nhận ra</dt>
              <dd className="font-medium">
                {trace.category.categories.map((c) => c.label).join(", ") || "—"}
                {trace.category.source && (
                  <span className="font-normal text-muted-foreground">
                    {" "}· {SOURCE[trace.category.source]}
                  </span>
                )}
                {trace.category.tiers.length > 0 && (
                  <> · hạng {trace.category.tiers.map((t) => TIER_LABEL[t] ?? t).join(", ")}</>
                )}
                {trace.category.segments.length > 1 && (
                  <span className="block font-normal text-muted-foreground">
                    Theo vế:{" "}
                    {trace.category.segments
                      .map(({ text, categories }) => `“${text}” → ${categories.map((c) => names.get(c) ?? c).join(", ") || "—"}`)
                      .join(" · ")}
                    {trace.category.groups.length > 1 && <> · chia đều top 10 cho {trace.category.groups.length} loại</>}
                  </span>
                )}
                {trace.category.ignored_tiers.map((t) => (
                  <span key={t.cue} className="block font-normal text-warn">
                    “{t.cue}” {TIER_IGNORED[t.reason as keyof typeof TIER_IGNORED] ?? t.reason}
                  </span>
                ))}
              </dd>
            </div>
          )}
          {trace.negation && (
            <div>
              <dt className="text-muted-foreground">Phủ định</dt>
              <dd className={trace.negation.applied ? "font-medium" : "font-medium text-warn"}>
                “{trace.negation.cue}” · {NEGATION[trace.negation.reason ?? "applied"]}
              </dd>
            </div>
          )}
        </dl>

        {trace.values.length > 0 && (
          <section className="space-y-2" aria-label="Giá trị trung gian">
            <h2 className="font-medium">Giá trị trung gian và bằng chứng</h2>
            {trace.values.map((value) => (
              <details key={value.id} className="rounded-md border px-3 py-2">
                <summary className="cursor-pointer">
                  <strong>{value.label}</strong> · {value.kind} · {value.target_edges.length} cạnh tới đích
                </summary>
                <div className="mt-3 grid gap-4 sm:grid-cols-2">
                  <EdgeList title="Gốc → giá trị" edges={value.origin_edges} labels={labels} />
                  <EdgeList title="Giá trị ← đích" edges={value.target_edges} labels={labels} />
                </div>
              </details>
            ))}
          </section>
        )}

        {trace.relation && (
          <details className="rounded-md border px-3 py-2">
            <summary className="cursor-pointer font-medium">Tập sản phẩm đích · {trace.targets.length}</summary>
            {trace.targets.length > 0 && (
              <ul className="mt-2 max-h-60 list-disc space-y-1 overflow-auto pl-5">
                {trace.targets.map((node) => <li key={node.id}>{node.label}</li>)}
              </ul>
            )}
          </details>
        )}

        {results.length > 0 && (
          <section className="space-y-2" aria-label="Điểm theo ảnh">
            <h2 className="font-medium">Điểm theo ảnh</h2>
            <p className="text-xs text-muted-foreground">
              Các nhánh dense và sparse hiển thị điểm gốc và hạng trong danh sách ứng viên.
              Điểm cuối bằng RRF cộng điều chỉnh theo đồ thị.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="border-b text-muted-foreground">
                  <tr>
                    <th scope="col" className="p-2">Ảnh trong kết quả</th>
                    {CHANNELS.map(([channel, label]) => <th key={channel} scope="col" className="p-2">{label}</th>)}
                    <th scope="col" className="p-2">RRF</th>
                    <th scope="col" className="p-2">Điểm cuối</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((hit, index) => (
                    <tr key={hit.asset_id} className="border-b last:border-b-0">
                      <th scope="row" className="p-2 font-medium">{index + 1}. {hit.entity_names.join(" · ")}</th>
                      {CHANNELS.map(([channel]) => (
                        <td key={channel} className="whitespace-nowrap p-2 tabular-nums">
                          {formatScore(hit.scores[channel], trace.channels[channel])}
                        </td>
                      ))}
                      <td className="p-2 tabular-nums">{hit.scores.rrf.toFixed(4)}</td>
                      <td className="p-2 tabular-nums">{hit.score.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
    </details>
  );
}

function EdgeList({ title, edges, labels }: {
  title: string;
  edges: TraceEdge[];
  labels: Map<string, string>;
}) {
  return (
    <div>
      <h3 className="font-medium">{title} · {edges.length}</h3>
      <ul className="max-h-64 divide-y overflow-auto text-xs">
        {edges.map((edge) => (
          <li key={edge.source_entity_id} className="space-y-1 py-2">
            <p className="font-medium">{labels.get(edge.entity_id) ?? edge.entity_id}</p>
            {edge.evidence.map(({ field, text }) => (
              <p key={field} className="text-muted-foreground">
                {field === "name" ? "Tên trong corpus" : field} · <q>{text}</q>
              </p>
            ))}
            <p className="text-muted-foreground">
              {edge.reviewed ? "Đã duyệt" : "Chưa duyệt"}
              {edge.confidence !== null && <> · confidence {edge.confidence.toFixed(2)}</>}
              {edge.source && <> · nguồn suy ra {edge.source}</>}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function formatScore(value: SearchScores[keyof SearchScores], status: SearchTrace["channels"]["dense"]) {
  if (value === null) {
    if (status === "inactive") return "Chưa chạy";
    return status === "unavailable" ? "Không lấy được điểm" : "Ngoài danh sách nhánh";
  }
  if (typeof value === "number") return value.toFixed(4);
  return `${value.score.toFixed(4)} (#${value.rank})`;
}
