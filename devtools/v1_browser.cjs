// Real restricted API and production UI. Synthetic display cases are reported separately.
const { chromium } = require('playwright-core');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const [stage, phase] = process.argv.slice(2);
const origin = 'http://127.0.0.1:5567';
const evidence = `docs/verification/${stage}-browser-v1.json`;

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], posts = [], cases = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/analyze')) posts.push(r.postDataJSON()); });
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  const ready = () => page.waitForFunction(() => !document.querySelector('button[type=submit]').disabled);
  async function submit(question, expected, parent) {
    if (parent) await page.getByRole('button', { name: '基于此分析继续追问', exact: true }).click();
    else await page.getByRole('button', { name: '开始独立分析', exact: true }).click();
    await page.getByLabel('分析问题').fill(question);
    const start = Date.now();
    const responseReady = page.waitForResponse(r => r.url().endsWith('/analyze'));
    await page.getByRole('button', { name: '开始分析', exact: true }).click();
    const response = await (await responseReady).json();
    assert.equal(response.state, expected, question);
    await ready();
    const body = posts.at(-1);
    if (parent) assert.equal(body.parent_node_id, parent);
    else assert.equal(body.parent_node_id, undefined);
    const elapsed_ms = Date.now() - start;
    const duplicate = page.waitForResponse(r => r.url().endsWith('/analyze'));
    await page.getByRole('button', { name: '开始分析', exact: true }).click();
    assert.deepEqual(await (await duplicate).json(), response);
    assert.deepEqual(posts.at(-1), body);
    await ready();
    cases.push({ body, response, elapsed_ms, duplicate_equal: true });
    return body.request_id;
  }
  try {
    await page.goto(origin, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: '分析你的电商数据' }).waitFor();
    await ready();
    if (phase === 'v1') {
      const root = await submit('比较2018年2月与2018年1月销售额、订单数、客单价', 'success');
      assert.equal(cases[0].response.chart_spec.type, 'table');
      await page.getByRole('table', { name: '分析结果表' }).waitFor();
      await submit('按地区分组', 'success', root);
      await page.locator('.vega-embed svg').first().waitFor();
      const savedRoot = page.getByRole('button', { name: '比较2018年2月与2018年1月销售额、订单数、客单价 · 完成', exact: true }).first();
      await savedRoot.click();
      // Select this run's root even when earlier checks saved the same question.
      const roots = await (await page.request.get(origin + '/api/ecommerce/workspace')).json();
      const rootIndex = [...roots.nodes].reverse().filter(n => n.question === cases[0].body.user_question).findIndex(n => n.node_id === root);
      await page.getByRole('button', { name: cases[0].body.user_question + ' · 完成', exact: true }).nth(rootIndex).click();
      await submit('分析2018年1月销售额、订单数按日显示', 'success', root);
      assert.equal(cases[2].response.chart_spec.type, 'line');
      await page.locator('.vega-embed svg').first().waitFor();
      await page.screenshot({ path: `.local/verification/${stage}-trend.png`, fullPage: true });
      await submit('分析2016年11月销售额', 'empty_result');
      assert.equal(await page.getByRole('table', { name: '分析结果表' }).count(), 0);
      await submit('分析2020年1月销售额', 'empty_result');
      assert.equal(cases.at(-1).response.result.state, 'outside_coverage');
      assert.equal(await page.getByRole('table', { name: '分析结果表' }).count(), 0);
      await submit('最新两个完整月销售额', 'clarification_required');
      await page.getByText(/需要澄清/).waitFor();
      const count = posts.length;
      await page.reload({ waitUntil: 'networkidle' });
      await ready();
      assert.equal(posts.length, count);
      assert.equal(await page.getByLabel('分析问题').inputValue(), cases.at(-1).body.user_question);
      const saved = await (await page.request.get(origin + '/api/ecommerce/workspace')).json();
      for (const item of cases) assert.deepEqual(saved.nodes.find(n => n.node_id === item.body.request_id).result, item.response);
      fs.writeFileSync(evidence, JSON.stringify({ cases, errors, refresh_analysis_posts: 0 }, null, 2));
    } else if (phase === 'restart') {
      const prior = JSON.parse(fs.readFileSync(evidence, 'utf8'));
      const saved = await (await page.request.get(origin + '/api/ecommerce/workspace')).json();
      for (const item of prior.cases) assert.deepEqual(saved.nodes.find(n => n.node_id === item.body.request_id).result, item.response);
      await page.getByRole('button', { name: '按地区分组 · 完成', exact: true }).first().click();
      await page.locator('.vega-embed svg').first().waitFor();
      assert.equal(posts.length, 0);
    } else {
      assert.equal(await page.getByRole('button', { name: '开始独立分析', exact: true }).count(), 0);
      const saved = await (await page.request.get(origin + '/api/ecommerce/workspace')).json();
      const node = saved.nodes.find(n => n.request_id === 'v09-live-002');
      assert(node, 'V0 baseline saved node is missing');
      await page.getByRole('button', { name: node.user_question + ' · 完成', exact: true }).first().click();
      await page.locator('.vega-embed svg').waitFor();
      assert((await page.getByRole('table', { name: '分析结果表' }).innerText()).includes('368,611.43'));
      const duplicate = page.waitForResponse(r => r.url().endsWith('/analyze'));
      await page.getByRole('button', { name: '开始分析', exact: true }).click();
      assert.deepEqual(await (await duplicate).json(), node.response);
      await ready();
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: `.local/verification/${stage}-${phase}-mobile.png`, fullPage: true });
    // Display-only fixtures exercise failure/partial/network states without modifying persisted history.
    if (phase === 'v1') {
      const result = cases[0].response.result;
      for (const response of [{ state: 'failed', error: { code: 'BUDGET_EXHAUSTED' }, partial_result: result },
                              { state: 'partial', result, chart_spec: { type: 'table' } }]) {
        await page.route('**/api/ecommerce/analyze', route => route.fulfill({ json: response }));
        await page.getByRole('button', { name: '开始独立分析', exact: true }).click();
        await page.getByLabel('分析问题').fill('展示故障注入');
        await page.getByRole('button', { name: '开始分析', exact: true }).click();
        await page.getByText(/以下仅为已验证的部分结果/).waitFor();
        await ready();
        await page.getByRole('table', { name: '分析结果表' }).waitFor();
        await page.unroute('**/api/ecommerce/analyze');
      }
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(`docs/verification/${stage}-browser-${phase}-summary.json`, JSON.stringify({
      phase, errors, analysis_posts: posts.length, narrow_screen_no_overflow: true,
      synthetic_display_cases: phase === 'v1' ? ['failed_partial_budget', 'partial'] : [],
    }, null, 2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.stack); process.exitCode = 1; });
