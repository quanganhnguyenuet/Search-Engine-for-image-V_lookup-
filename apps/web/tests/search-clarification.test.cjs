const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');

require.extensions['.ts'] = (module, filename) => {
  module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, filename);
};
const { POST } = require('../app/api/culture/ai/route.ts');

test('proxy preserves clarification history and structured scope rejection', async (t) => {
  const history = [{ query: 'pho ban đêm không có người', question: 'Phở hay phố?' }];
  const captured = [];
  const rejected = { items: [], scope_rejected: true, scope_message: 'Ngoài phạm vi',
    suggested_queries: ['Phố cổ Hội An'], clarification_required: false };
  t.mock.method(global, 'fetch', async (url, options) => {
    captured.push({ url, body: JSON.parse(options.body) });
    return Response.json(rejected);
  });
  const response = await POST(new Request('http://localhost/api/culture/ai', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode: 'contextual', query: 'phố cổ', use_llm: true,
      clarification_context: history }),
  }));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), rejected);
  assert.deepEqual(captured[0].body.clarification_context, history);
  assert.equal(captured[0].body.use_llm, true);
  assert.equal(captured[0].body.query, 'phố cổ');
  assert.match(captured[0].url, /\/api\/v1\/search\/text$/);
  assert.equal(response.headers.get('cache-control'), 'no-store');
});

test('semantic baseline does not forward conversational planner controls', async (t) => {
  let body;
  t.mock.method(global, 'fetch', async (_url, options) => {
    body = JSON.parse(options.body);
    return Response.json({ items: [], clarification_required: true });
  });
  await POST(new Request('http://localhost/api/culture/ai', {
    method: 'POST', body: JSON.stringify({ mode: 'semantic', query: 'cái đó',
      use_llm: true, clarification_context: [{ query: 'pho', question: 'Phở hay phố?' }] }),
  }));
  assert.equal(body.use_llm, undefined);
  assert.equal(body.clarification_context, undefined);
});
