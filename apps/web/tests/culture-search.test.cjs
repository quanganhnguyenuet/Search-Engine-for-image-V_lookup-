const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const ts = require("typescript");

// Use the installed TypeScript compiler; no additional test dependencies required.
require.extensions[".ts"] = (module, filename) => {
  const source = fs.readFileSync(filename, "utf8");
  module._compile(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true,
  } }).outputText, filename);
};

const { normalize, searchCulture, getImageFile } = require("../lib/culture-data.ts");

test("Vietnamese diacritics, đ and punctuation normalize consistently", () => {
  assert.equal(normalize("Áo dài – ĐỒNG!"), "ao dai dong");
});

test("local corpus search, ranking, filters, pagination and image lookup", async () => {
  const all = await searchCulture("", "", 1);
  assert.ok(all.libraryTotal > 0, "Prepare the local culture dataset before running this integration test");
  assert.equal(all.items.length, Math.min(24, all.total));
  assert.equal(new Set(all.items.map((item) => item.id)).size, all.items.length);
  assert.equal(Object.values(all.counts).reduce((a, b) => a + b, 0), all.total);
  for (const item of all.items) assert.ok(fs.existsSync(await getImageFile(item.id)));
  const accented = await searchCulture("áo dài", "", 1);
  const plain = await searchCulture("ao dai", "", 1);
  assert.ok(plain.total > 0);
  assert.deepEqual(accented.items, plain.items);
  const category = await searchCulture("", "am_thuc", 1);
  assert.ok(category.items.every((item) => item.category === "am_thuc"));
  assert.equal(category.total, all.counts.am_thuc);
  const ranked = await searchCulture(all.items[0].title, "", 1);
  assert.equal(normalize(ranked.items[0].title), normalize(all.items[0].title));
  if (all.pages > 1) {
    const second = await searchCulture("", "", 2);
    assert.ok(second.items.every((item) => !all.items.some((first) => first.id === item.id)));
  }
  const absent = await searchCulture("zzzzzzzz-not-a-cultural-term", "", 1);
  assert.equal(absent.total, 0);
  assert.equal(absent.pages, 1);
  assert.equal(await getImageFile("../../etc/passwd"), undefined);
  assert.equal((await searchCulture("", "", 9999)).page, all.pages);
});
