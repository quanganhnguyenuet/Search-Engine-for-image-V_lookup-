import { readFile, realpath, stat } from "node:fs/promises";
import path from "node:path";
import { categories, type CultureAsset, type SearchResponse } from "./culture-types";

type Entry = { asset: CultureAsset; file: string; title: string; tags: string; text: string };
type RawAsset = {
  asset_id: string; image_path: string; category: string; keyword?: string;
  description?: string; tags?: string[]; cultural_context?: Record<string, unknown>;
};

export function normalize(text: string) {
  return text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d")
    .replace(/Đ/g, "D").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

// The corpus is mounted at runtime, never traced into the application bundle.
const dataRoot = path.resolve(/* turbopackIgnore: true */ process.env.CULTURE_DATA_DIR ?? path.join(process.cwd(), "../../data"));
let cached: { signature: string; entries: Entry[] } | undefined;
let loading: Promise<Entry[]> | undefined;

async function loadEntries(): Promise<Entry[]> {
  const manifests = await Promise.all(["culture-search", "culture-search-sample"].map(async (name) => {
    const directory = path.join(dataRoot, "processed", name);
    try {
      const info = await stat(path.join(directory, "assets.jsonl"));
      return { directory, signature: `${name}:${info.mtimeMs}:${info.size}` };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
      throw error;
    }
  }));
  const available = manifests.filter((item) => item !== null);
  const signature = available.map((item) => item.signature).join("|");
  if (cached?.signature === signature) return cached.entries;
  const entries = new Map<string, Entry>();
  for (const { directory } of available) {
    const lines = (await readFile(path.join(directory, "assets.jsonl"), "utf8")).split(/\r?\n/).filter(Boolean);
    const report = JSON.parse(await readFile(path.join(directory, "report.json"), "utf8"));
    const roots = [process.env.CULTURE_IMAGE_ROOT, path.join(dataRoot, "raw/vietnamese-culture-vqa"), report.image_root]
      .filter((root): root is string => typeof root === "string");
    for (const line of lines) {
      const raw: RawAsset = JSON.parse(line);
      if (!/^[a-f0-9]{64}$/.test(raw.asset_id) || entries.has(raw.asset_id)) continue;
      let file: string | undefined;
      for (const root of roots) {
        try {
          const safeRoot = await realpath(root);
          const candidate = await realpath(path.resolve(safeRoot, raw.image_path));
          if (!candidate.startsWith(safeRoot + path.sep)) continue;
          if ((await stat(candidate)).isFile()) { file = candidate; break; }
        } catch (error) {
          if (["ENOENT", "ENOTDIR"].includes((error as NodeJS.ErrnoException).code ?? "")) continue;
          throw error;
        }
      }
      if (!file) continue;
      const label = categories.find((category) => category.id === raw.category)?.label;
      if (!label) continue;
      const title = (raw.keyword ?? raw.category).replace(/_/g, " ").trim();
      const context = Object.fromEntries(Object.entries(raw.cultural_context ?? {})
        .filter((entry): entry is [string, string] => typeof entry[1] === "string"));
      const asset: CultureAsset = {
        id: raw.asset_id, title: title.charAt(0).toLocaleUpperCase("vi") + title.slice(1),
        category: raw.category, categoryLabel: label, description: raw.description ?? "",
        tags: (raw.tags ?? []).filter((tag) => typeof tag === "string"), context,
        imageUrl: `/api/culture/images/${raw.asset_id}`,
        sourceUrl: "https://www.kaggle.com/datasets/blackcubo/vietnamese-culture-vqa-dataset",
      };
      entries.set(asset.id, { asset, file, title: normalize(title), tags: normalize(asset.tags.join(" ")),
        text: normalize([asset.description, label, ...Object.values(context)].join(" ")) });
    }
  }
  const buckets = categories.map(({ id }) => [...entries.values()].filter(({ asset }) => asset.category === id));
  const ordered: Entry[] = [];
  for (let i = 0; buckets.some((bucket) => i < bucket.length); i++) {
    for (const bucket of buckets) if (bucket[i]) ordered.push(bucket[i]);
  }
  cached = { signature, entries: ordered };
  return ordered;
}

async function getEntries() {
  if (!loading) loading = loadEntries().finally(() => { loading = undefined; });
  return loading;
}

export async function searchCulture(query: string, category: string, page: number): Promise<SearchResponse> {
  const entries = await getEntries();
  const phrase = normalize(query);
  const tokens = [...new Set(phrase.split(" ").filter(Boolean))];
  const matches = entries.map((entry, index) => {
    const all = `${entry.title} ${entry.tags} ${entry.text}`;
    if (!tokens.every((token) => all.includes(token))) return null;
    const score = tokens.reduce((sum, token) => sum + (entry.title.includes(token) ? 12 : 0)
      + (entry.tags.includes(token) ? 5 : 0) + (entry.text.includes(token) ? 1 : 0), 0)
      + (phrase && entry.title.includes(phrase) ? 30 : 0);
    return { entry, index, score };
  }).filter((item) => item !== null).sort((a, b) => b.score - a.score || a.index - b.index);
  const counts = Object.fromEntries(categories.map(({ id }) => [id, matches.filter(({ entry }) => entry.asset.category === id).length]));
  const filtered = matches.filter(({ entry }) => !category || entry.asset.category === category);
  const pages = Math.max(1, Math.ceil(filtered.length / 24));
  const currentPage = Math.min(Math.max(1, page), pages);
  return { items: filtered.slice((currentPage - 1) * 24, currentPage * 24).map(({ entry }) => entry.asset),
    total: filtered.length, page: currentPage, pages, libraryTotal: entries.length, counts };
}

export async function getImageFile(id: string) {
  return (await getEntries()).find(({ asset }) => asset.id === id)?.file;
}
