import { Button } from "@/components/ui/button";

const foundations = [
  { label: "Understand", detail: "OCR + vision-language contracts" },
  { label: "Represent", detail: "Text + image embedding contracts" },
  { label: "Retrieve", detail: "Qdrant retrieval boundary" },
];

export default function HomePage() {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

  return (
    <main className="min-h-screen overflow-hidden bg-background text-foreground">
      <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-8 sm:px-10">
        <header className="flex items-center justify-between border-b border-border pb-6">
          <div className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-primary text-sm font-semibold text-primary-foreground">
              VS
            </span>
            <span className="font-semibold tracking-tight">VisualSeek</span>
          </div>
          <span className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground">
            Foundation ready
          </span>
        </header>

        <section className="grid flex-1 items-center gap-12 py-20 lg:grid-cols-[1.15fr_0.85fr]">
          <div>
            <p className="mb-5 text-sm font-medium uppercase tracking-[0.2em] text-muted-foreground">
              Multimodal image discovery
            </p>
            <h1 className="max-w-3xl text-5xl font-semibold leading-[1.02] tracking-[-0.05em] sm:text-7xl">
              Search by what an image means.
            </h1>
            <p className="mt-7 max-w-xl text-lg leading-8 text-muted-foreground">
              The application shell is running. Visual understanding, vector indexing,
              and search behavior are intentionally waiting for the next iteration.
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <Button asChild size="lg">
                <a href={`${apiBaseUrl}/api/v1/health`}>Check API health</a>
              </Button>
              <Button asChild size="lg" variant="outline">
                <a href={`${apiBaseUrl}/docs`}>Explore API docs</a>
              </Button>
            </div>
          </div>

          <div className="relative">
            <div className="absolute -inset-16 -z-0 rounded-full bg-[radial-gradient(circle,hsl(var(--primary)/0.12),transparent_65%)]" />
            <div className="relative rounded-2xl border border-border bg-white/70 p-3 shadow-2xl shadow-slate-900/5 backdrop-blur">
              <div className="rounded-xl border border-border bg-background p-6">
                <div className="mb-7 flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
                  <span className="text-sm text-muted-foreground">System architecture</span>
                </div>
                <div className="space-y-3">
                  {foundations.map((foundation, index) => (
                    <div
                      className="flex items-center gap-4 rounded-lg border border-border p-4"
                      key={foundation.label}
                    >
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-muted text-sm font-semibold">
                        0{index + 1}
                      </span>
                      <div>
                        <p className="font-medium">{foundation.label}</p>
                        <p className="text-sm text-muted-foreground">{foundation.detail}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}

