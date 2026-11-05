import { searchCulture } from "@/lib/culture-data";
import { categories } from "@/lib/culture-types";

export const runtime = "nodejs";

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const query = (params.get("q") ?? "").trim().slice(0, 200);
  const category = params.get("category") ?? "";
  const page = Number(params.get("page") ?? 1);
  if ((category && !categories.some(({ id }) => id === category)) || !Number.isSafeInteger(page) || page < 1) {
    return Response.json({ error: "Bộ lọc hoặc số trang không hợp lệ." }, { status: 400 });
  }
  try {
    return Response.json(await searchCulture(query, category, page), { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Culture dataset read failed", error);
    return Response.json({ error: "Chưa đọc được thư viện ảnh. Vui lòng thử lại." }, { status: 503 });
  }
}
