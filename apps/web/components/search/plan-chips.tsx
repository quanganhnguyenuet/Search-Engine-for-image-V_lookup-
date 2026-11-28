import { relationLabel, type SearchResponse } from "@/lib/search";
import { cn } from "@/lib/utils";

const TIERS: Record<string, string> = { luxury: "cao cấp", premium: "tầm trung cao", mass: "phổ thông" };

export function PlanChips({ data }: { data: SearchResponse }) {
  const { plan } = data;
  const chips: { label: string; tone?: "brand" | "warn" }[] = [];
  const seeds = plan.seed_names.slice(0, plan.seeds.length);
  const anchors = plan.seed_names.slice(plan.seeds.length);
  for (const name of seeds) chips.push({ label: `Sản phẩm: ${name}`, tone: "brand" });
  // "đặc sản Bến Tre": the place is the value, there is no product to relate it to.
  const placeQuery = data.trace.relation === "ORIGIN" && data.trace.origins.length === 0;
  // "thuốc đau dạ dày": the complaint picked the drugs, again with no product to start from.
  const conditionQuery = data.trace.relation === "TREATS" && data.trace.origins.length === 0;
  if (plan.relation && placeQuery) {
    for (const { label } of data.trace.values) chips.push({ label: `Địa danh: ${label}`, tone: "brand" });
  } else if (plan.relation && conditionQuery) {
    const { values } = data.trace;
    for (const { label } of values.slice(0, 3)) chips.push({ label: `Công dụng: ${label}`, tone: "brand" });
    if (values.length > 3) chips.push({ label: `và ${values.length - 3} công dụng gần nghĩa` });
  } else if (plan.relation) {
    chips.push({ label: `Tìm theo: ${relationLabel(data.trace.relation)}`, tone: "brand" });
  }
  for (const name of anchors) chips.push({ label: `Không lấy lại: ${name}`, tone: "warn" });
  if (plan.hops) chips.push({ label: `${plan.hops} sản phẩm liên quan` });
  if (plan.dense_seed) chips.push({ label: "Tên khớp gần đúng" });
  const byCategory = (data.trace.category?.groups.length ?? 0) > 1;
  if (plan.grouped && !byCategory) chips.push({ label: "Chia đều theo sản phẩm" });
  const category = data.trace.category;
  for (const { label } of category?.categories ?? []) chips.push({ label: `Loại hàng: ${label}`, tone: "brand" });
  for (const tier of category?.tiers ?? []) chips.push({ label: `Hạng: ${TIERS[tier] ?? tier}`, tone: "brand" });
  if (byCategory) chips.push({ label: "Chia đều theo loại hàng" });
  if (!chips.length) chips.push({ label: "Tìm theo mô tả" });
  const list = (
    <ul className="flex flex-wrap gap-1.5" aria-label="Cách hệ thống hiểu câu">
      {chips.map((chip) => (
        <li
          key={chip.label}
          className={cn(
            "rounded-full border px-3 py-1 text-sm",
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
  if (!conditionQuery) return list;
  return (
    <div className="flex flex-col gap-2">
      {list}
      <p role="note" className="max-w-[70ch] rounded-md border border-warn/40 bg-warn-soft px-3 py-2 text-sm">
        Danh sách dựa trên công dụng do AI ghi nhận, chưa được dược sĩ duyệt, và không xét độ tuổi hay bệnh nền. Đây
        không phải lời khuyên dùng thuốc: hãy hỏi bác sĩ hoặc dược sĩ.
      </p>
    </div>
  );
}
