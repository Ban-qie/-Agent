const { chromium } = require('playwright-core');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const phase = process.argv[2], origin = 'http://127.0.0.1:5567';
const evidence = 'docs/verification/V1-team-browser.json';
const usage = () => JSON.parse(fs.readFileSync('.local/verification/qwen-usage.json', 'utf8')).length;
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } });
  const errors = [], posts = [], cases = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/analyze')) posts.push(r.postDataJSON()); });
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  const ready = () => page.waitForFunction(() => {
    const button = document.querySelector('button[type=submit]');
    return button && !button.disabled;
  });
  try {
    await page.goto(origin, { waitUntil: 'networkidle' });
    await ready();
    if (phase === 'live') {
      const followupOnly = process.env.V1_TEAM_FOLLOWUP_ONLY === 'true';
      if (followupOnly) {
        const prior = JSON.parse(fs.readFileSync(evidence, 'utf8'));
        cases.push(prior.cases[0]);
        await page.getByRole('button', { name: prior.cases[0].body.user_question + ' · 完成', exact: true }).first().click();
      }
      for (const [index, question] of ['帮我看看2018年一月份卖了多少钱，有多少笔已交付订单', '按地区分组销售额最高前3'].entries()) {
        if (followupOnly && index === 0) continue;
        await page.getByRole('button', { name: index ? '基于此分析继续追问' : '开始独立分析', exact: true }).click();
        await page.getByLabel('分析问题').fill(question);
        const n = usage(), start = Date.now();
        const pending = page.waitForResponse(r => r.url().endsWith('/analyze'), { timeout: 90000 });
        await page.getByRole('button', { name: '开始分析', exact: true }).click();
        const response = await (await pending).json();
        cases.push({ body: posts.at(-1), response, elapsed_ms: Date.now() - start, calls: usage() - n });
        fs.writeFileSync(evidence, JSON.stringify({ cases, errors }, null, 2));
        assert.equal(response.state, 'success', JSON.stringify(response.error || response));
        assert.equal(usage() - n, 3);
        assert.equal(response.collaboration.review, 'approve');
        assert.equal(response.collaboration.interpreter, 'qwen-flash');
        assert.equal(response.explanation.grounded, true);
        assert(response.trace.some(t => t.stage === 'reviewer'));
        await ready();
        await page.getByText(/条件复核通过/).waitFor();
        const repeat = page.waitForResponse(r => r.url().endsWith('/analyze'));
        await page.getByRole('button', { name: '开始分析', exact: true }).click();
        assert.deepEqual(await (await repeat).json(), response);
        await ready();
        assert.equal(usage(), n + 3);
      }
      assert.equal(cases[1].body.parent_node_id, cases[0].body.request_id);
      assert.deepEqual(cases[0].response.conditions.current, cases[1].response.conditions.current);
      const priorPosts = posts.length;
      await page.reload({ waitUntil: 'networkidle' });
      await ready();
      assert.equal(posts.length, priorPosts);
    } else {
      const prior = JSON.parse(fs.readFileSync(evidence, 'utf8'));
      const state = await (await page.request.get(origin + '/api/ecommerce/workspace')).json();
      for (const item of prior.cases) assert.deepEqual(state.nodes.find(n => n.node_id === item.body.request_id).result, item.response);
      await page.getByText(/条件复核通过/).waitFor();
      assert.equal(posts.length, 0);
    }
    await page.getByRole('table', { name: '分析结果表' }).waitFor();
    await page.locator('.vega-embed svg').first().waitFor();
    await page.screenshot({ path: `.local/verification/v1-team-${phase}.png`, fullPage: true });
    assert.deepEqual(errors, []);
    fs.writeFileSync(`docs/verification/V1-team-${phase}-ui.json`, JSON.stringify({ errors, posts: posts.length, passed: true }, null, 2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exitCode = 1; });
