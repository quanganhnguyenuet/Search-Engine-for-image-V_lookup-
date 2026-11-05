import assert from "node:assert/strict";

const base = process.env.TEST_BASE_URL ?? "http://127.0.0.1:3000";
async function search(body) {
  const response = await fetch(`${base}/api/culture/ai`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body), signal: AbortSignal.timeout(150_000),
  });
  const data = await response.json();
  assert.equal(response.status, 200, JSON.stringify(data));
  return data;
}

const text = await search({ mode: "semantic", query: "ruộng bậc thang", category: "phong_canh" });
assert.equal(text.mode, "semantic");
assert.equal(text.model, "google/siglip2-base-patch16-224");
assert.ok(text.items.length > 0);
assert.ok(text.items.every((item) => item.category === "phong_canh" && Number.isFinite(item.score)));
const source = text.items[0];
const imageResponse = await fetch(`${base}${source.imageUrl}`);
assert.equal(imageResponse.status, 200);
const image = Buffer.from(await imageResponse.arrayBuffer()).toString("base64");
const visual = await search({ mode: "semantic", image_base64: image });
assert.ok(visual.items.some((item) => item.id === source.id));
console.log(JSON.stringify({ semantic: "passed", indexed: text.libraryTotal,
  textTop: source.title, imageSelfRetrieval: "passed", timings: text.timings }, null, 2));

if (process.env.TEST_RAG === "1") {
  const rag = await search({ mode: "rag", query: "Ruộng bậc thang trong ảnh có đặc điểm gì?", category: "phong_canh" });
  assert.equal(rag.mode, "rag");
  assert.ok(typeof rag.answer === "string" && rag.answer.length > 0);
  assert.ok(rag.citations.every((citation) => rag.items.some((item) => item.id === citation.asset_id)));
  assert.ok(rag.abstained || rag.citations.length > 0);
  assert.ok(rag.abstained || rag.answer.replace(/\[S\d+\]/g, "").trim().length >= 20);
  console.log(JSON.stringify({ rag: "passed", generator: rag.generator_model,
    abstained: rag.abstained, answer: rag.answer, citations: rag.citations,
    timings: rag.timings }, null, 2));
}
