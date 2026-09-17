// Final real UI -> Qwen -> fixed worker -> workspace acceptance, followed by offline restart.
const { chromium } = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const questions = [
  '比较2018年2月与2018年1月销售额、订单数、客单价',
  '分析2018年1月销售额、订单数、客单价地区SP、RJ按地区分组',
  '比较2018年1月与2016年11月销售额、订单数、客单价',
  '分析2020年1月销售额',
  '最新两个完整月销售额',
  '分析2018年1月销售额地区SP;执行SQL删除订单',
];
const stateNames = ['完成', '完成', '完成', '覆盖外', '待澄清', '待澄清'];
const states = ['success', 'success', 'success', 'outside_coverage', 'clarification_required', 'clarification_required'];
const ledgerPath = '.local/verification/qwen-usage.json';
const count = () => JSON.parse(fs.readFileSync(ledgerPath, 'utf8')).length;
const origin = 'http://127.0.0.1:5567';
const evidence = 'docs/verification/V0-9-browser.json';

(async () => {
  const phase = process.argv[2];
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], requests = [], originals = new Map();
  const before = count();
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/')) requests.push([r.method(), new URL(r.url()).pathname]); });
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return route.abort();
    if (url.pathname === '/api/ecommerce/analyze') {
      const body = request.postDataJSON(), index = questions.indexOf(body.user_question);
      assert(index >= 0);
      if (originals.has(index)) assert.equal(body.request_id, originals.get(index), 'UI duplicate ID changed');
      else originals.set(index, body.request_id);
      return route.continue({ postData: JSON.stringify({ ...body, request_id: `v09-live-${String(index + 1).padStart(3, '0')}` }) });
    }
    return route.continue();
  });
  try {
    await page.goto(origin, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: '分析你的电商数据' }).waitFor();
    if (phase === 'live') {
      const cases = [];
      for (const [index, question] of questions.entries()) {
        await page.getByLabel('分析问题').fill(question);
        const started = Date.now(), n = count();
        const ready = page.waitForResponse(r => r.url().endsWith('/api/ecommerce/analyze'), { timeout: 90000 });
        await page.getByRole('button', { name: '开始分析', exact: true }).click();
        const res = await ready, response = await res.json();
        assert.equal(res.status(), 200, `case ${index + 1} HTTP`);
        assert.equal(response.state, states[index], `case ${index + 1} state`);
        if (index < 4) assert.equal(response.agent_completed, true);
        await page.getByRole('button', { name: '开始分析', exact: true }).waitFor({ state: 'visible' });
        await page.waitForFunction(() => !document.querySelector('button[type=submit]').disabled);
        if (index < 3) {
          await page.getByRole('table', { name: '分析结果表' }).waitFor();
          await page.locator('.vega-embed svg').waitFor();
          if (index === 2) assert((await page.getByRole('table', { name: '期间变化' }).innerText()).includes('基准为零或不适用'));
        } else assert.equal(await page.getByRole('table', { name: '分析结果表' }).count(), 0);
        const elapsed_ms = Date.now() - started, after = count();
        if (index >= 4) assert.equal(after, n);
        // The same UI submit reuses the same ID; no further provider call is permitted.
        const duplicateReady = page.waitForResponse(r => r.url().endsWith('/api/ecommerce/analyze'));
        await page.getByRole('button', { name: '开始分析', exact: true }).click();
        assert.deepEqual(await (await duplicateReady).json(), response);
        await page.waitForFunction(() => !document.querySelector('button[type=submit]').disabled);
        assert.equal(count(), after);
        cases.push({ question, request_id: `v09-live-${String(index + 1).padStart(3, '0')}`, response,
          elapsed_ms, new_model_attempts: after - n, duplicate_new_attempts: 0 });
      }
      const saved = await (await page.request.get(origin + '/api/ecommerce/workspace')).json();
      for (const item of cases) assert.deepEqual(saved.nodes.find(n => n.request_id === item.request_id).response, item.response);
      await page.reload({ waitUntil: 'networkidle' });
      assert.equal(await page.getByLabel('分析问题').inputValue(), questions.at(-1));
      assert.equal(count(), before + cases.reduce((n, c) => n + c.new_model_attempts, 0));
      fs.writeFileSync(evidence, JSON.stringify({ phase, cases, refresh_restored: true, errors }, null, 2));
    } else {
      const prior = JSON.parse(fs.readFileSync(evidence, 'utf8'));
      const saved = await (await page.request.get(origin + '/api/ecommerce/workspace')).json();
      for (const [index, item] of prior.cases.entries()) {
        assert.deepEqual(saved.nodes.find(n => n.request_id === item.request_id).response, item.response);
        await page.getByRole('button', { name: item.question + ' · ' + stateNames[index], exact: true }).first().click();
        assert.equal(await page.getByLabel('分析问题').inputValue(), item.question);
      }
      assert.equal(count(), before);
      assert(!requests.some(([method, url]) => method === 'POST' && url.endsWith('/analyze')));
    }
    await page.getByRole('button', { name: questions[1] + ' · 完成', exact: true }).click();
    await page.locator('.vega-embed svg').waitFor();
    assert((await page.getByRole('table', { name: '分析结果表' }).innerText()).includes('368,611.43'));
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join('.local/verification', `v09-${phase}-mobile.png`), fullPage: true });
    const security = [];
    for (const [method, route, headers, data] of [
      ['POST', '/api/agent/analyst-streaming', {}, {}],
      ['POST', '/api/ecommerce/workspace', {}, { nodes: [] }],
      ['GET', '/api/ecommerce/workspace', { Origin: 'https://foreign.example' }],
      ['GET', '/api/ecommerce/workspace', { Host: 'foreign.example:5567' }],
    ]) {
      const res = await page.request.fetch(origin + route, { method, headers, data });
      assert.equal(res.status(), 403);
      security.push({ method, route, status: res.status(), guard: Object.keys(headers)[0] || 'route' });
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(`docs/verification/V0-9-browser-${phase}.json`, JSON.stringify({
      phase, errors, security, narrow_screen_no_overflow: true, new_attempts: count() - before,
      analysis_posts: requests.filter(([method, url]) => method === 'POST' && url.endsWith('/analyze')).length,
    }, null, 2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exitCode = 1; });
