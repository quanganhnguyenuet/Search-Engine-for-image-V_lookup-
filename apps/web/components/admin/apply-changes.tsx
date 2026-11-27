"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { admin, applySteps, type ReleaseStatus } from "@/lib/admin";
import { clearSubgraphCache } from "@/lib/graph";

const POLL_MS = 1500;

type State =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "confirm"; status: ReleaseStatus }
  | { kind: "running"; steps: string[] }
  | { kind: "done" }
  | { kind: "error"; message: string; steps?: string[] };

/** Applies every pending change to the search from the page the admin is on. */
export function useApply(onApplied: () => void | Promise<void>) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const done = useRef(onApplied);
  done.current = onApplied;

  const open = useCallback(() => {
    setState({ kind: "checking" });
    admin.release().then(
      (status) =>
        setState(
          status.job.status === "running"
            ? { kind: "running", steps: applySteps(status.job.log) }
            : { kind: "confirm", status },
        ),
      (e: Error) => setState({ kind: "error", message: e.message }),
    );
  }, []);

  const start = useCallback(async () => {
    setState({ kind: "running", steps: [] });
    try {
      await admin.startRelease();
    } catch (e) {
      setState({ kind: "error", message: (e as Error).message });
    }
  }, []);

  const running = state.kind === "running";
  useEffect(() => {
    if (!running) return;
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      try {
        const { job } = await admin.release();
        if (stopped) return;
        if (job.status === "succeeded") {
          clearSubgraphCache();
          await done.current();
          setState({ kind: "done" });
          return;
        }
        if (job.status === "failed") {
          setState({ kind: "error", message: job.error ?? "Áp dụng thay đổi bị lỗi.", steps: applySteps(job.log) });
          return;
        }
        setState({ kind: "running", steps: applySteps(job.log) });
      } catch {
        // The server swaps its data near the end; a failed poll is retried.
      }
      if (!stopped) timer = window.setTimeout(poll, POLL_MS);
    };
    timer = window.setTimeout(poll, POLL_MS);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [running]);

  return { state, open, start, close: () => setState({ kind: "idle" }) };
}

export type Apply = ReturnType<typeof useApply>;

/** The confirm step, progress and result of an apply started on this page. */
export function ApplyBox({ apply, entityId }: { apply: Apply; entityId?: string }) {
  const { state } = apply;
  if (state.kind === "idle") return null;

  if (state.kind === "checking") {
    return <p role="status" className="mt-3 text-sm">Đang kiểm tra các thay đổi đang chờ…</p>;
  }
  if (state.kind === "running") {
    return (
      <div className="mt-3 rounded-md border bg-surface p-3" role="status">
        <p className="flex items-center gap-2 font-medium">
          <span aria-hidden="true" className="size-3 animate-spin rounded-full border-2 border-brand border-t-transparent motion-reduce:animate-none" />
          Đang áp dụng vào tìm kiếm… Có thể mất vài phút nếu phải tính lại dữ liệu.
        </p>
        {state.steps.length > 0 && (
          <ol className="mt-2 max-h-40 space-y-0.5 overflow-auto text-xs text-muted-foreground">
            {state.steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        )}
      </div>
    );
  }
  if (state.kind === "done") {
    return (
      <div className="mt-3 flex flex-wrap items-center gap-3 rounded-md border border-brand/40 bg-brand-soft p-3" role="status">
        <p className="min-w-0 flex-1 font-medium text-brand">Đã áp dụng. Người dùng tìm kiếm đã thấy dữ liệu mới.</p>
        <Button variant="outline" onClick={apply.close}>Đóng</Button>
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="mt-3 rounded-md border border-danger/40 bg-danger-soft p-3" role="alert">
        <p>{state.message}</p>
        {state.steps && state.steps.length > 0 && (
          <details className="mt-2 text-xs">
            <summary className="cursor-pointer font-medium">Các bước đã chạy</summary>
            <ol className="mt-1 space-y-0.5">
              {state.steps.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          </details>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="outline" onClick={apply.open}>Thử lại</Button>
          <Button variant="outline" onClick={apply.close}>Đóng</Button>
        </div>
      </div>
    );
  }

  const { pending, new_entities: created } = state.status;
  const others = [...new Map(pending.filter((p) => p.entity_id !== entityId).map((p) => [p.entity_id, p.name])).entries()];
  const mine = pending.filter((p) => p.entity_id === entityId).length;
  const nothing = pending.length === 0 && created.length === 0;
  // On a product page the question is about that product; elsewhere about everything waiting.
  const question = entityId
    ? `Áp dụng ${mine} thay đổi của sản phẩm này vào tìm kiếm?`
    : "Áp dụng mọi thay đổi đang chờ vào tìm kiếm?";
  return (
    <div className="mt-3 rounded-md border bg-surface p-3 text-sm" role="group" aria-label="Xác nhận áp dụng">
      {nothing ? (
        <p>Không còn thay đổi nào đang chờ.</p>
      ) : (
        <>
          <p className="font-medium">{question}</p>
          {(others.length > 0 || created.length > 0) && (
            <div className="mt-2 rounded-md bg-warn-soft p-2">
              <p>{entityId ? "Các thay đổi đang chờ khác cũng được áp dụng cùng lúc:" : "Gồm có:"}</p>
              {others.length > 0 && (
                <>
                  <p className="mt-1 font-medium">
                    Sửa {others.length} sản phẩm{entityId ? " khác" : ""}
                  </p>
                  <ul className="max-h-32 list-disc overflow-auto pl-5">
                    {others.map(([id, name]) => (
                      <li key={id}>
                        <Link href={`/admin/entities/${id}`} className="text-brand underline-offset-4 hover:underline">
                          {name}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {created.length > 0 && (
                <>
                  <p className="mt-1 font-medium">Thêm {created.length} sản phẩm mới từ CSV</p>
                  <ul className="max-h-32 list-disc overflow-auto pl-5">
                    {created.map((row) => (
                      <li key={row.entity_id}>
                        {row.name}
                        {!row.labelled && <span className="text-danger"> (chưa có nhãn loại hàng)</span>}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {!nothing && (
          <Button variant="brand" onClick={() => void apply.start()}>
            Xác nhận áp dụng
          </Button>
        )}
        <Button variant="outline" onClick={apply.close}>
          Để sau
        </Button>
      </div>
    </div>
  );
}
