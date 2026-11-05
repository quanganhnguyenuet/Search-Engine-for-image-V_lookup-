export const runtime = "nodejs";

const apiUrl = process.env.SEARCH_API_URL ?? "http://127.0.0.1:8000";

export async function POST(request: Request) {
  if (Number(request.headers.get("content-length") ?? 0) > 14_100_000) {
    return Response.json({ error: "Ảnh vượt giới hạn 10 MB." }, { status: 413 });
  }
  let body;
  try {
    const text = await request.text();
    if (text.length > 14_100_000) return Response.json({ error: "Ảnh quá lớn." }, { status: 413 });
    body = JSON.parse(text);
  } catch {
    return Response.json({ error: "Dữ liệu tìm kiếm không hợp lệ." }, { status: 400 });
  }
  if (!body || typeof body !== "object" || !["semantic", "rag"].includes(body.mode)) {
    return Response.json({ error: "Chế độ tìm kiếm không hợp lệ." }, { status: 400 });
  }
  try {
    const response = await fetch(`${apiUrl}/api/v1/search/${body.mode}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify({ query: body.query || undefined, image_base64: body.image_base64,
        category: body.category || undefined, top_k: body.mode === "rag" ? 5 : 24 }),
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(150_000)]),
    });
    const data = await response.json();
    if (!response.ok) {
      return Response.json({ error: data.error?.message ?? "Yêu cầu AI không hợp lệ. Kiểm tra câu hỏi hoặc ảnh.",
        code: data.error?.code ?? "search_failed" }, { status: response.status });
    }
    return Response.json(data, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ error: "Không kết nối được dịch vụ AI. Hãy khởi động API, import embedding và kiểm tra model.",
      code: "backend_unavailable" }, { status: 503 });
  }
}
