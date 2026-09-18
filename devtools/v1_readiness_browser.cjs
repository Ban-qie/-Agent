const { chromium } = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const phase = process.argv[2], origin = 'http://127.0.0.1:5567';
const dir = process.env.READINESS_EVIDENCE;
const read = name => JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'));
const save = (name, value) => fs.writeFileSync(path.join(dir, name), JSON.stringify(value, null, 2));
const ledger = () => JSON.parse(fs.readFileSync('.local/verification/qwen-usage.json', 'utf8'));
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], posts = [], cases = [];
  const ready = () => page.waitForFunction(() => {
    const b = document.querySelector('button[type=submit]'); return b && !b.disabled;
  });
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/analyze')) posts.push(r.postDataJSON()); });
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  async function select(item) {
    const state = await (await page.request.get(origin + '/api/ecommerce/workspace')).json();
    const matches = [...state.nodes].reverse().filter(n => n.question === item.body.user_question);
    const index = matches.findIndex(n => n.node_id === item.body.request_id);
    assert(index >= 0);
    const suffix = item.response.state === 'clarification_required' ? '待澄清' : '完成';
    await page.getByRole('button', { name: item.body.user_question + ' · ' + suffix, exact: true }).nth(index).click();
  }
  async function submit(question, expected = 'success', parent = null) {
    if (parent) await select(parent);
    await page.getByRole('button', { name: parent ? '基于此分析继续追问' : '开始独立分析', exact: true }).click();
    await page.getByLabel('分析问题').fill(question);
    const before = ledger(), start = Date.now();
    const pending = page.waitForResponse(r => r.url().endsWith('/analyze'), { timeout: 90000 });
    await page.getByRole('button', { name: '开始分析', exact: true }).click();
    const response = await (await pending).json();
    await ready();
    const item = { body: posts.at(-1), response, elapsed_ms: Date.now() - start,
      calls: ledger().length - before.length };
    cases.push(item); save(phase + '.json', { cases, errors });
    assert.equal(response.state, expected, question + ': ' + JSON.stringify(response.error || response.question));
    assert.equal(item.body.parent_node_id, parent?.body.request_id);
    if (['success', 'empty_result'].includes(expected)) {
      assert.equal(item.calls, 3); assert.equal(response.collaboration.review, 'approve');
      assert.equal(response.explanation.grounded, true);
    }
    const n = ledger().length;
    const replay = page.waitForResponse(r => r.url().endsWith('/analyze'));
    await page.getByRole('button', { name: '开始分析', exact: true }).click();
    assert.deepEqual(await (await replay).json(), response); await ready();
    assert.equal(ledger().length, n); item.duplicate_equal = true;
    save(phase + '.json', { cases, errors });
    return item;
  }
  try {
    await page.goto(origin, { waitUntil: 'networkidle' }); await ready();
    if (phase === 'demo') {
      const root = await submit('比较2018年2月与2018年1月销售额、订单数、客单价');
      const regions = await submit('按地区分组销售金额减少最多', 'success', root);
      await page.getByRole('table', { name: '分组差额排名' }).waitFor();
      assert(regions.response.result.ranking.some(r => Number(r.absolute) < 0));
      const day = await submit('地区SP按日显示销售额', 'success', regions);
      assert.deepEqual(day.response.conditions.regions, ['SP']);
      assert.equal(day.response.conditions.group_by, 'day');
      await page.locator('.vega-embed svg').first().waitFor();
      const branch = await submit('按地区比较订单数', 'success', root);
      assert.deepEqual(branch.response.conditions.regions, []);
      assert.equal(branch.response.conditions.top_n, null);
      assert.deepEqual(branch.response.conditions.metrics, ['order_count']);
      await submit('按月显示', 'success', root);
      await submit('分析2016年11月销售额', 'empty_result');
      const outside = await submit('分析2020年1月销售额', 'empty_result');
      assert.equal(outside.response.result.state, 'outside_coverage');
      const clarify = await submit('最新两个完整月销售额', 'clarification_required', root);
      await submit('按地区分组', 'success', clarify);
      await submit('分析2018年1月销量', 'clarification_required');
      const n = posts.length; await page.reload({ waitUntil: 'networkidle' }); await ready();
      assert.equal(posts.length, n);
      for (const [url, method, headers] of [
        ['/api/ecommerce/workspace', 'GET', { Origin: 'https://foreign.example' }],
        ['/api/ecommerce/workspace', 'GET', { Host: 'foreign.example:5567' }],
        ['/api/agent/analyst-streaming', 'POST', {}], ['/api/ecommerce/workspace', 'POST', {}],
      ]) assert.equal((await page.request.fetch(origin + url, { method, headers })).status(), 403);
      save('demo-checks.json', { refresh_analysis_posts: posts.length - n, guards_passed: true });
    } else if (phase === 'restore') {
      const before = ledger().length;
      const state = await (await page.request.get(origin + '/api/ecommerce/workspace')).json();
      for (const item of read('demo.json').cases) {
        assert.deepEqual(state.nodes.find(n => n.node_id === item.body.request_id).result, item.response);
      }
      assert.equal(posts.length, 0); assert.equal(ledger().length, before);
      save('restore-checks.json', { restored_nodes: read('demo.json').cases.length, analysis_posts: 0, new_calls: 0 });
    } else if (phase === 'followup') {
      await submit('按地区比较订单数', 'success', read('demo.json').cases[0]);
    } else {
      const mode = phase.endsWith('v0') ? 'v0' : 'v1';
      const questions = ['比较2018年2月与2018年1月销售额、订单数、客单价',
        '分析2018年1月销售额、订单数、客单价地区SP、RJ按地区分组',
        '分析2018年1月销售额、订单数、客单价按月分组',
        '分析2018年2月销售额、订单数、客单价按天分组'];
      for (let i = 0; i < 20; i++) {
        const before = ledger(), start = Date.now();
        const body = { request_id: 'readiness-' + mode + '-' + Date.now() + '-' + i, user_question: questions[i % questions.length] };
        const response = await (await page.request.post(origin + '/api/ecommerce/analyze', { data: body, timeout: 90000 })).json();
        const added = ledger().slice(before.length);
        cases.push({ body, response, elapsed_ms: Date.now() - start, calls: added.length,
          estimated_cny: added.reduce((s, r) => s + (r.estimated_cny || 0), 0),
          reserved_cny: added.reduce((s, r) => s + r.reserved_cny, 0) });
        save(phase + '.json', { cases, errors });
        assert.equal(response.state, 'success', mode + ' sample ' + i + ': ' + JSON.stringify(response.error || response.question));
        assert.equal(added.length, mode === 'v0' ? 2 : 3);
        console.log(mode + ' sample ' + (i + 1) + '/20 passed');
      }
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(dir, phase + '.png'), fullPage: true });
    assert.deepEqual(errors, []);
    save(phase + '.json', { cases, errors, passed: true });
  } finally { await browser.close(); }
})().catch(e => { console.error(e.stack); process.exitCode = 1; });
