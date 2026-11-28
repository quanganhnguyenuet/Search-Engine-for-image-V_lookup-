"use client";

import { useMemo, useState } from "react";

import { RelationGraph } from "@/components/graph/relation-graph";
import { Button } from "@/components/ui/button";
import { useSubgraph } from "@/lib/graph";
import { API_URL, type SearchResponse, thumbnailOf } from "@/lib/search";

const MAX_ENTITIES = 40;

/** Graph of how the query's entities relate to the entities of the returned images. */
export function QueryGraph({ data }: { data: SearchResponse }) {
  const { trace, results } = data;
  const [open, setOpen] = useState(trace.status === "expanded");

  const { query, thumbs, imageCounts } = useMemo(() => {
    const thumbs: Record<string, string> = {};
    const imageCounts: Record<string, number> = {};
    for (const hit of results) {
      for (const id of hit.entity_ids) {
        imageCounts[id] = (imageCounts[id] ?? 0) + 1;
        thumbs[id] ??= thumbnailOf(`${API_URL}${hit.image_url}`);
      }
    }
    return {
      query: {
        ids: Object.keys(imageCounts).slice(0, MAX_ENTITIES),
        origins: trace.origins.map((node) => node.id),
        sharedOnly: true,
      },
      thumbs,
      imageCounts,
    };
  }, [results, trace.origins]);

  const state = useSubgraph(open ? query : null);
  const origins = trace.origins.map((node) => node.label).join(", ");

  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="rounded-lg border bg-surface text-sm"
    >
      <summary className="cursor-pointer px-4 py-3 font-medium focus-visible:outline-brand">
        Các sản phẩm liên quan với nhau thế nào
        <span className="font-normal text-muted-foreground">
          {" "}
          · {origins ? `từ ${origins} tới ${results.length} ảnh kết quả` : `${results.length} ảnh kết quả`}
        </span>
      </summary>
      <div className="space-y-3 border-t p-4">
        <p className="max-w-[75ch] text-xs text-muted-foreground">
          Hình dưới nối sản phẩm trong kết quả (và sản phẩm bạn nhắc tới, nếu có) với hãng, loại hàng, công
          dụng… của chúng. Hãng và loại hàng luôn hiện; thông tin khác chỉ hiện khi nối được từ hai sản phẩm.
        </p>
        {state.status === "loading" && (
          <div className="h-[22rem] animate-pulse rounded-lg bg-muted sm:h-[28rem]" role="status" aria-label="Đang tải đồ thị" />
        )}
        {state.status === "error" && (
          <div role="alert" className="rounded-lg border border-danger/40 bg-danger-soft p-3">
            <p>{state.message}</p>
            <Button variant="outline" className="mt-2" onClick={() => setOpen(false)}>
              Đóng và thử lại
            </Button>
          </div>
        )}
        {state.status === "done" &&
          (state.data.nodes.length === 0 ? (
            <p className="rounded-lg border border-dashed p-4 text-muted-foreground">
              Các ảnh này không có sản phẩm trong đồ thị để vẽ.
            </p>
          ) : (
            <RelationGraph graph={state.data} thumbs={thumbs} imageCounts={imageCounts} />
          ))}
      </div>
    </details>
  );
}
