import assert from "node:assert/strict";

const base = process.env.TEST_BASE_URL ?? "http://127.0.0.1:3000";
const testLLM = process.env.TEST_CONTEXTUAL_LLM === "1";
const body = { mode: "contextual", query: "ruộng bậc thang xanh ở vùng núi", category: "phong_canh",
  ...(testLLM ? { use_llm: true } : {}) };
async function search(payload) {
  const response = await fetch(`${base}/api/culture/ai`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload), signal: AbortSignal.timeout(210_000),
  });
  return { status: response.status, data: await response.json() };
}

const page = await fetch(base);
assert.equal(page.status, 200);
assert.ok((await page.text()).includes("Tìm theo yêu cầu"));
const fast = await search({ mode: "contextual", query: "bánh đậu xanh", category: "am_thuc" });
assert.equal(fast.status, 200, JSON.stringify(fast.data));
assert.equal(fast.data.search_route, "lexical");
assert.equal(fast.data.llm_used, false);
assert.equal(fast.data.llm_call_count, 0);
assert.equal(fast.data.query_model, null);
assert.ok(fast.data.items.length > 0);
const { status, data } = await search(body);
assert.equal(status, 200, JSON.stringify(data));
assert.equal(data.mode, "contextual");
assert.ok(["rrf", "lexical_exact"].includes(data.score_type));
if (testLLM) {
  assert.ok(data.interpretation.intent.length > 0 && data.interpretation.keywords.length > 0);
  assert.equal(data.search_route, "llm_hybrid");
} else {
  assert.equal(data.interpretation, null);
  assert.equal(data.llm_used, false);
  assert.equal(data.llm_call_count, 0);
}
assert.ok(data.items.length > 0 && data.items.length <= 24);
assert.equal(data.total, data.items.length);
assert.equal(data.pages, 1);
assert.equal(new Set(data.items.map((item) => item.id)).size, data.items.length);
assert.ok(data.items.every((item) => item.category === body.category && Number.isFinite(item.score)));
const image = await fetch(`${base}${data.items[0].imageUrl}`);
assert.equal(image.status, 200);
assert.ok(image.headers.get("content-type").startsWith("image/"));
const again = await search(body);
assert.equal(again.status, 200, JSON.stringify(again.data));
if (testLLM) assert.equal(again.data.plan_cache_hit, true);
else assert.equal(again.data.llm_call_count, 0);
assert.equal((await search({ ...body, category: "invalid" })).status, 422);
assert.equal((await search({ ...body, query: " " })).status, 422);
console.log(JSON.stringify({ contextual: "passed", indexed: data.libraryTotal,
  llmTested: testLLM,
  fastRoute: { route: fast.data.search_route, timings: fast.data.timings },
  corpusTotal: data.corpus_total, coverageComplete: data.coverage_complete,
  interpretation: data.interpretation, top: data.items.slice(0, 3).map((item) => item.title),
  firstTimings: data.timings, cachedTimings: again.data.timings }, null, 2));
