import { PreviewImage } from "@/components/search/preview-image";
import { SEARCH_EXAMPLES } from "@/lib/showcase";

/** What can be searched, shown as real queries next to what they return. */
export function SearchIntro({ onPick }: { onPick: (query: string) => void }) {
  return (
    <section aria-labelledby="ways-title" className="flex flex-col gap-3">
      <h2 id="ways-title" className="text-xl font-semibold tracking-tight sm:text-2xl">
        Bạn có thể tìm theo nhiều cách
      </h2>
      <p className="text-muted-foreground">Chọn một câu để xem ngay kết quả.</p>
      <ul className="mt-2 divide-y border-y">
        {SEARCH_EXAMPLES.map((example) => (
          <li key={example.query}>
            <button
              type="button"
              onClick={() => onPick(example.query)}
              className="group grid w-full items-center gap-4 rounded-md py-5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:grid-cols-[minmax(0,1fr)_26rem] md:gap-8"
            >
              <span className="flex flex-col gap-1">
                <span className="text-sm font-medium text-muted-foreground">{example.kind}</span>
                <span className="text-lg font-semibold leading-snug text-brand underline-offset-4 group-hover:underline sm:text-2xl">
                  “{example.query}”
                </span>
                <span className="text-sm text-muted-foreground">{example.hint}</span>
              </span>
              <span className="grid grid-cols-4 gap-2">
                {example.images.map((assetId) => (
                  <PreviewImage key={assetId} assetId={assetId} />
                ))}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
