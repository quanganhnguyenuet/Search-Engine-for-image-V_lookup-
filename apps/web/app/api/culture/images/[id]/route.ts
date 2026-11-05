import { readFile } from "node:fs/promises";
import path from "node:path";
import { getImageFile } from "@/lib/culture-data";

export const runtime = "nodejs";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[a-f0-9]{64}$/.test(id)) return new Response(null, { status: 404 });
  try {
    const file = await getImageFile(id);
    if (!file) return new Response(null, { status: 404 });
    const mime: Record<string, string> = { ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif" };
    const contentType = mime[path.extname(file).toLowerCase()];
    if (!contentType) return new Response(null, { status: 415 });
    return new Response(new Uint8Array(await readFile(file)), { headers: {
      "Content-Type": contentType, "Cache-Control": "public, max-age=86400",
      "X-Content-Type-Options": "nosniff",
    } });
  } catch { return new Response(null, { status: 404 }); }
}
