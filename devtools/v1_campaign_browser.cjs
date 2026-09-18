// Real browser, fixed request IDs only; never fabricates a response or alters a question/parent.
const { chromium } = require('playwright-core');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const dir = 'docs/verification/V1-R04', invocation = process.env.CAMPAIGN_INVOCATION;
const read = p => JSON.parse(fs.readFileSync(p, 'utf8'));
const create = (p, v) => fs.writeFileSync(p, JSON.stringify(v, null, 2), { flag: 'wx' });
const campaign = read(path.join(dir, 'campaign.json')), name = process.argv[2];
if (name === 'restored-followup') campaign.cases.push({ name, body: { request_id: 'v1-r05-followup-001',
  user_question: '按地区比较订单数', parent_node_id: campaign.cases[0].body.request_id }, expected: 'success', historical: false });
const ledger = () => read('.local/verification/qwen-usage.json');
const origin = 'http://127.0.0.1:5567';
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], posts = [], before = ledger().length;
  const ready = () => page.waitForFunction(() => { const b = document.querySelector('button[type=submit]'); return b && !b.disabled; });
  const workspace = async () => (await page.request.get(origin + '/api/ecommerce/workspace')).json();
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/analyze')) posts.push(r.postDataJSON()); });
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  const labels = { success: '完成', empty_result: '空结果', failed: '失败', waiting_clarification: '待澄清' };
  async function select(node) {
    const matches = [...(await workspace()).nodes].reverse().filter(n => n.question === node.question);
    const index = matches.findIndex(n => n.node_id === node.node_id);
    assert(index >= 0);
    await page.getByRole('button', { name: node.question + ' · ' + labels[node.status], exact: true }).nth(index).click();
  }
  try {
    await page.goto(origin, { waitUntil: 'networkidle' }); await ready();
    if (name === 'v0-restore-checks') {
      const state = await workspace();
      assert.notEqual(state.orchestrator, 'v1');
      for (const item of read('docs/verification/V1-15-v0-comparison.json')) {
        const node = state.nodes.find(n => n.request_id === item.body.request_id);
        assert(node); assert.deepEqual(node.response, item.response);
      }
      assert.equal(posts.length, 0); assert.equal(ledger().length, before);
    } else if (name.endsWith('-checks')) {
      const state = await workspace();
      for (const c of campaign.cases) {
        const saved = read(path.join(dir, 'cases', c.name + '.result.json'));
        assert.deepEqual(state.nodes.find(n => n.node_id === c.body.request_id).result, saved.response);
      }
      const old = read('docs/verification/V1-readiness-6f8d69ec4d/demo.json').cases[2];
      assert.deepEqual(state.nodes.find(n => n.node_id === old.body.request_id).result, old.response);
      await page.reload({ waitUntil: 'networkidle' }); await ready();
      assert.equal(posts.length, 0); assert.equal(ledger().length, before);
      for (const [url, method, headers] of [
        ['/api/ecommerce/workspace', 'GET', { Origin: 'https://foreign.example' }],
        ['/api/ecommerce/workspace', 'GET', { Host: 'foreign.example:5567' }],
        ['/api/agent/analyst-streaming', 'POST', {}], ['/api/ecommerce/workspace', 'POST', {}]
      ]) assert.equal((await page.request.fetch(origin + url, { method, headers })).status(), 403);
    } else {
      const c = campaign.cases.find(c => c.name === name), body = c.body;
      let node = (await workspace()).nodes.find(n => n.node_id === body.request_id);
      const start = Date.now();
      let response;
      if (node) { response = node.result; await select(node); }
      else {
        if (body.parent_node_id) {
          const parent = (await workspace()).nodes.find(n => n.node_id === body.parent_node_id);
          assert(parent); await select(parent);
        }
        await page.getByRole('button', { name: body.parent_node_id ? '基于此分析继续追问' : '开始独立分析', exact: true }).click();
        await page.getByLabel('分析问题').fill(body.user_question);
        await page.route('**/api/ecommerce/analyze', route => {
          const sent = route.request().postDataJSON();
          assert.equal(sent.user_question, body.user_question);
          assert.equal(sent.parent_node_id, body.parent_node_id);
          return route.continue({ postData: JSON.stringify(body) });
        });
        const pending = page.waitForResponse(r => r.url().endsWith('/analyze'), { timeout: 90000 });
        await page.getByRole('button', { name: '开始分析', exact: true }).click();
        response = await (await pending).json(); await ready();
        await page.unroute('**/api/ecommerce/analyze');
        node = (await workspace()).nodes.find(n => n.node_id === body.request_id);
        assert(node); await select(node);
      }
      const resultPath = path.join(dir, 'cases', name + '.result.json');
      const record = { body, response, elapsed_ms: Date.now() - start, new_calls: ledger().length - before,
        intent: name + '.intent.json', recovered: ledger().length === before, historical: c.historical };
      if (fs.existsSync(resultPath)) assert.deepEqual(read(resultPath).response, response);
      else create(resultPath, record); // Preserve unexpected failures BEFORE asserting acceptance.
      assert.equal(response.state, c.expected, name + ': ' + JSON.stringify(response.error || response.question));
      if (['success', 'empty_result'].includes(c.expected)) {
        assert.equal(response.collaboration.review, 'approve');
        assert.equal(response.explanation.grounded, true);
        assert.equal(response.budget.model_calls, 3);
      } else assert.equal(response.executed, false);
      const count = ledger().length;
      const duplicate = page.waitForResponse(r => r.url().endsWith('/analyze'));
      await page.getByRole('button', { name: '开始分析', exact: true }).click();
      assert.deepEqual(await (await duplicate).json(), response); await ready();
      assert.equal(ledger().length, count);
      if (['day', 'month'].includes(name)) {
        assert.equal(response.conditions.sort, null); assert.equal(response.conditions.top_n, null);
        assert.equal(response.chart_spec.type, 'line'); assert(!response.result.ranking);
        await page.locator('.vega-embed svg').first().waitFor();
        await page.locator('.vega-embed svg').first().scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(invocation, name + '-chart.png') });
      }
      if (name === 'day') {
        assert.deepEqual(response.conditions.regions, ['SP']);
        for (const [period, n] of [['current', 28], ['baseline', 31]]) {
          const keys = response.result[period].groups.map(g => g.key);
          assert.equal(keys.length, n); assert.deepEqual(keys, [...keys].sort());
        }
      }
      if (['branch', 'restored-followup'].includes(name)) {
        assert.deepEqual(response.conditions.regions, []); assert.deepEqual(response.conditions.metrics, ['order_count']);
        assert.equal(response.conditions.top_n, null);
      }
      if (name === 'regions') await page.getByRole('table', { name: '分组差额排名' }).waitFor();
      if (name === 'outside') assert.equal(response.result.state, 'outside_coverage');
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(invocation, name + '.png'), fullPage: true });
    assert.deepEqual(errors, []);
    create(path.join(invocation, name + '-ui.json'), { passed: true, errors, analysis_posts: posts.length,
      new_calls: ledger().length - before });
  } finally { await browser.close(); }
})().catch(e => { console.error(e.stack); process.exitCode = 1; });
