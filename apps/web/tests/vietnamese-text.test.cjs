const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => {
  module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
  }).outputText, filename);
};
const { phrasePosition, normalize, compileText, ambiguousForms } = require('../lib/vietnamese-text.ts');

test('accented queries preserve unmarked syllables and whole words', () => {
  assert.equal(phrasePosition('Bánh chày', 'bánh chay'), -1);
  assert.equal(phrasePosition('Bánh cháy', 'bánh chay'), -1);
  assert.equal(phrasePosition('Bánh chay', 'bánh chay'), 0);
  assert.equal(phrasePosition('Bánh chày', 'banh chay'), 0);
  assert.equal(phrasePosition('Phong Nha', 'phở'), -1);
  assert.equal(phrasePosition('Phố cổ', 'phở'), -1);
  assert.equal(phrasePosition('Phở Việt Nam', 'pho'), 0);
  assert.equal(phrasePosition('Hoà Bình', 'Hòa Bình'), 0);
  assert.equal(phrasePosition('Bánh chay', 'ba\u0301nh chay'), 0);
  assert.equal(normalize("H'Mông"), normalize('HMông'));
  assert.deepEqual(ambiguousForms([compileText('Kẹo dân gian'), compileText('Keo dán gạch')], compileText('keo')).sort(), ['keo dán gạch', 'kẹo'].sort());
});

test('keyword search separates dishes and homographs and cannot re-add excluded sample images', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'culture-accents-'));
  process.env.CULTURE_DATA_DIR = root;
  const specs = [
    ['Phở Việt Nam', 'am_thuc', 'Một tô phở', false],
    ['Phố cổ Hà Nội', 'kien_truc', 'Ngôi nhà phố cổ', false],
    ['Phong Nha', 'phong_canh', 'Hang động Phong Nha', false],
    ['Xương bánh chè', 'am_thuc', 'bánh chay sơ đồ đầu gối', true],
    ['Bánh chay', 'am_thuc', 'Món bánh chay', false],
    ['Bánh chày', 'am_thuc', 'Tên bánh chày', false],
    ['Bánh cháy', 'am_thuc', 'Tên bánh cháy', false],
    ['Keo dán gạch', 'thu_cong_my_nghe', 'Bao bì keo dán gạch', false],
    ['Kẹo dân gian', 'am_thuc', 'Một đĩa kẹo', false],
  ];
  const rows = specs.map(([keyword, category, description, search_excluded], i) => ({
    asset_id: (i + 1).toString(16).padStart(64, '0'), keyword, category, description, tags: [],
    image_path: `images/${category}/test/${i}.png`, search_excluded,
  }));
  const imageRoot = path.join(root, 'raw/vietnamese-culture-vqa');
  for (const row of rows) {
    const file = path.join(imageRoot, row.image_path);
    fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, 'fixture image');
  }
  for (const [name, content] of [['culture-search', rows], ['culture-search-sample', [{ ...rows[3], search_excluded: false }]]]) {
    const folder = path.join(root, 'processed', name); fs.mkdirSync(folder, { recursive: true });
    fs.writeFileSync(path.join(folder, 'report.json'), JSON.stringify({ image_root: imageRoot }));
    fs.writeFileSync(path.join(folder, 'assets.jsonl'), content.map((row) => JSON.stringify(row)).join('\n'));
  }
  try {
    const { searchCulture, getImageFile } = require('../lib/culture-data.ts');
    assert.deepEqual((await searchCulture('phở', '', 1)).items.map((r) => r.title), ['Phở Việt Nam']);
    assert.deepEqual((await searchCulture('bánh chay', '', 1)).items.map((r) => r.title), ['Bánh chay']);
    const ambiguous = await searchCulture('pho', '', 1);
    assert.equal(ambiguous.clarification_required, true);
    assert.deepEqual(ambiguous.suggested_queries.sort(), ['phở', 'phố'].sort());
    assert.equal((await searchCulture('pho', 'am_thuc', 1)).clarification_required, undefined);
    const glue = await searchCulture('keo dán gạch', '', 1);
    assert.equal(glue.items[0].title, 'Keo dán gạch');
    assert.equal(glue.clarification_required, undefined);
    assert.equal(await getImageFile(rows[3].asset_id), undefined);
    assert.equal((await searchCulture('', '', 1)).libraryTotal, rows.length - 1);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
