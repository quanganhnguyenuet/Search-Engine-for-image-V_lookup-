import assert from "node:assert/strict";

const base = process.env.TEST_BASE_URL ?? "http://127.0.0.1:3000";
async function search(params = "") {
  const response = await fetch(`${base}/api/culture${params}`);
  assert.equal(response.status, 200);
  return response.json();
}

const home = await fetch(base);
assert.equal(home.status, 200);
assert.match(await home.text(), /VietNam Culture/);
const all = await search();
assert.ok(all.total > 0);
const accented = await search(`?q=${encodeURIComponent("áo dài")}`);
const plain = await search("?q=ao%20dai");
assert.equal(accented.total, plain.total);
assert.ok(plain.total > 0);
const food = await search("?category=am_thuc");
assert.ok(food.items.every((item) => item.category === "am_thuc"));
const second = await search("?page=2");
assert.ok(second.items.every((item) => !all.items.some((first) => first.id === item.id)));
const image = await fetch(`${base}${all.items[0].imageUrl}`);
assert.equal(image.status, 200);
assert.match(image.headers.get("content-type"), /^image\//);
assert.ok((await image.arrayBuffer()).byteLength > 100);
const thumbnail = await fetch(`${base}/_next/image?url=${encodeURIComponent(all.items[0].imageUrl)}&w=640&q=75`);
assert.equal(thumbnail.status, 200);
assert.match(thumbnail.headers.get("content-type"), /^image\//);
for (const params of ["?category=invalid", "?page=-1", "?page=NaN"]) {
  assert.equal((await fetch(`${base}/api/culture${params}`)).status, 400);
}
assert.equal((await fetch(`${base}/api/culture/images/not-a-valid-id`)).status, 404);
assert.equal((await search("?q=zzzzzzzzzzzzzzz")).total, 0);
console.log(JSON.stringify({ status: "passed", libraryTotal: all.libraryTotal, counts: all.counts,
  accentSearchResults: plain.total, checks: ["homepage", "search", "diacritics", "categories", "pagination", "original image", "optimized thumbnail", "invalid input", "empty results"] }, null, 2));
