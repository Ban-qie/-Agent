const { chromium } = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const phase = process.argv[2];
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], posts = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.method() === 'POST') posts.push(new URL(r.url()).pathname); });
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort());
  const questions = [
    '分析2018年1月销售额、订单数、客单价按地区分组',
    '比较2018年2月与2018年1月销售额、订单数、客单价',
    '分析2016年11月销售额、订单数、客单价', '分析2020年1月销售额', '最近两个完整月销售额',
  ];
  try {
    await page.goto('http://127.0.0.1:5567', { waitUntil: 'networkidle' });
    if (phase === 'before-restart') {
      for (let i = 0; i < questions.length; i++) {
        const response = await page.request.post('http://127.0.0.1:5567/api/ecommerce/analyze', {
          data: { request_id: `v07-browser-${String(i + 2).padStart(3, '0')}`, user_question: questions[i] },
        });
        const data = await response.json();
        assert.equal(data.state, ['success', 'success', 'empty_result', 'outside_coverage', 'clarification_required'][i]);
      }
      const failed = await page.request.post('http://127.0.0.1:5567/api/ecommerce/analyze', {
        data: { request_id: 'v08-offline-failure-001', user_question: '分析2018年1月销售额' },
      });
      assert.equal((await failed.json()).error.code, 'MODEL_DISABLED');
      await page.reload({ waitUntil: 'networkidle' });
    }
    await page.getByRole('button', { name: '故障已排除，发起新请求' }).waitFor();
    await page.getByRole('button', { name: questions[0] + ' · 完成', exact: true }).click();
    await page.getByRole('table', { name: '分析结果表' }).waitFor();
    await page.locator('.vega-embed svg').waitFor();
    assert((await page.getByRole('table', { name: '分析结果表' }).innerText()).includes('368,611.43'));
    assert.equal(await page.getByLabel('分析问题').inputValue(), questions[0]);
    await page.screenshot({ path: path.join('.local', 'verification', `v08-${phase}.png`), fullPage: true });
    await page.getByRole('button', { name: questions[1] + ' · 完成', exact: true }).click();
    assert((await page.getByRole('table', { name: '分析结果表' }).innerText()).includes('826,437.13'));
    for (const [i, label] of [[2, '空结果'], [3, '覆盖外'], [4, '待澄清']]) {
      await page.getByRole('button', { name: questions[i] + ' · ' + label, exact: true }).click();
      assert.equal(await page.getByRole('table', { name: '分析结果表' }).count(), 0);
    }
    const response = await page.request.get('http://127.0.0.1:5567/api/ecommerce/workspace');
    const saved = await response.json();
    assert(saved.nodes.length >= 6);
    assert(saved.nodes.every(n => n.chart.kind === 'sales_bar' && n.node_id === n.request_id));
    assert.equal((await page.request.post('http://127.0.0.1:5567/api/agent/analyst-streaming', { data: {} })).status(), 403);
    assert.equal((await page.request.get('http://127.0.0.1:5567/api/ecommerce/workspace', {
      headers: { Origin: 'https://bad.example' },
    })).status(), 403);
    assert.deepEqual(errors, []);
    assert(!posts.includes('/api/ecommerce/analyze')); // Restoration/selection in the page never posts.
    fs.writeFileSync(path.join('docs', 'verification', `V0-8-browser-${phase}.json`), JSON.stringify({
      phase, restored_states: saved.nodes.map(n => n.response.state), node_count: saved.nodes.length,
      exact_results: true, chart_rendered: true, automatic_analysis_posts: 0, page_errors: errors,
      legacy_route_status: 403, external_origin_status: 403,
    }, null, 2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exitCode = 1; });
