import { PreviewImage } from "@/components/search/preview-image";
import { TRACE_EXAMPLE } from "@/lib/showcase";

/** One real search, drawn the way the product answers it: query, place, products. */
export function SearchShowcase() {
  const { query, place, products } = TRACE_EXAMPLE;
  return (
    <aside
      aria-label="Ví dụ tìm kiếm"
      className="flex flex-col gap-3 border-b border-panel-border bg-panel px-5 py-5 lg:justify-center lg:gap-10 lg:border-b-0 lg:border-r lg:px-14 lg:py-12 xl:px-20"
    >
      <p className="text-lg font-bold tracking-tight text-brand lg:text-xl">V-Lookup</p>
      <div className="flex flex-col gap-2 lg:gap-4">
        <p className="text-sm text-muted-foreground lg:text-base">Tìm ảnh sản phẩm bằng tiếng Việt, ví dụ:</p>
        <p className="text-xl font-semibold leading-snug lg:text-5xl lg:leading-tight">“{query}”</p>
      </div>

      {/* The diagram is hidden on small screens so that the form comes first. */}
      <div className="hidden max-w-md lg:block">
        <div className="flex items-center gap-3">
          {/* Hexagon: the shape the relation graph uses for a place. */}
          <svg width="40" height="40" viewBox="-20 -20 40 40" aria-hidden="true" className="shrink-0">
            <polygon points="18,0 9,15.6 -9,15.6 -18,0 -9,-15.6 9,-15.6" fill="hsl(var(--node-place))" />
          </svg>
          <p>
            <span className="block text-lg font-semibold leading-tight">{place}</span>
            <span className="text-sm text-muted-foreground">nơi sản xuất</span>
          </p>
        </div>
        <ul aria-label={`Đặc sản của ${place}`} className="ml-5">
          {products.map((product, index) => (
            <li key={product.image} className="relative flex items-center gap-4 pb-1 pl-8 pt-4">
              {/* Trunk: stops at the middle of the last branch. */}
              <span
                aria-hidden="true"
                style={{ animationDelay: `${200 + index * 250}ms` }}
                className={`trace-line absolute left-0 top-0 w-px bg-brand/40 ${
                  index === products.length - 1 ? "h-[calc(50%+6px)]" : "h-full"
                }`}
              />
              <span aria-hidden="true" className="absolute left-0 top-[calc(50%+6px)] h-px w-6 bg-brand/40" />
              <span
                style={{ animationDelay: `${400 + index * 250}ms` }}
                className="trace-step flex items-center gap-4"
              >
                <PreviewImage assetId={product.image} className="size-20 shrink-0" />
                <span className="font-medium">{product.name}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
      <p className="hidden max-w-[45ch] text-sm text-muted-foreground lg:block">
        Cũng tìm được theo tên, mô tả, hãng, hoặc nhiều sản phẩm trong một câu.
      </p>
    </aside>
  );
}
