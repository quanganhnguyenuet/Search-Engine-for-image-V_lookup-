import { relationLabel, type SearchResponse } from "@/lib/search";
import { cn } from "@/lib/utils";

const TIERS: Record<string, string> = { luxury: "cao cấp", premium: "tầm trung cao", mass: "phổ thông" };

export function PlanChips({ data }: { data: SearchResponse }) {
  const { plan } = data;
  const chips: { label: string; tone?: "brand" | "warn" }[] = [];
  const seeds = plan.seed_names.slice(0, plan.seeds.length);
  const anchors = plan.seed_names.slice(plan.seeds.length);
  for (const name of seeds) chips.push({ label: `Thực thể: ${name}`, tone: "brand" });
  // "đặc sản Bến Tre": the place is the value, there is no product to relate it to.
  const placeQuery = data.trace.relation === "ORIGIN" && data.trace.origins.length === 0;
  if (plan.relation && placeQuery) {
    for (const { label } of data.trace.values) chips.push({ label: `Địa danh: ${label}`, tone: "brand" });
  } else if (plan.relation) {
    chips.push({ label: `Quan hệ: ${relationLabel(data.trace.relation)}`, tone: "brand" });
  }
  for (const name of anchors) chips.push({ label: `Loại trừ: ${name}`, tone: "warn" });
  if (plan.hops) chips.push({ label: `${plan.hops} sản phẩm liên quan` });
  if (plan.dense_seed) chips.push({ label: "Khớp gần đúng" });
  const byCategory = (data.trace.category?.groups.length ?? 0) > 1;
  if (plan.grouped && !byCategory) chips.push({ label: "Chia đều theo sản phẩm" });
  const category = data.trace.category;
  for (const { label } of category?.categories ?? []) chips.push({ label: `Loại hàng: ${label}`, tone: "brand" });
  for (const tier of category?.tiers ?? []) chips.push({ label: `Hạng: ${TIERS[tier] ?? tier}`, tone: "brand" });
  if (byCategory) chips.push({ label: "Chia đều theo loại hàng" });
  if (!chips.length) chips.push({ label: "Tìm theo nội dung" });
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Cách hệ thống hiểu câu">
      {chips.map((chip) => (
        <li
          key={chip.label}
          className={cn(
            "rounded-md border px-2 py-0.5 text-xs",
            chip.tone === "brand" && "border-brand/30 bg-brand-soft text-foreground",
            chip.tone === "warn" && "border-warn/40 bg-warn-soft text-foreground",
            !chip.tone && "bg-muted text-muted-foreground",
          )}
        >
          {chip.label}
        </li>
      ))}
    </ul>
  );
}
