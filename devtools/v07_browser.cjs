const { chromium } = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '..');
  const out = path.join(root, '.local', 'verification');
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  // Stable IDs let repeated verification reuse the backend's existing task cache.
  await page.addInitScript(() => {
    let next = 0;
    crypto.randomUUID = () => `v07-browser-${String(++next).padStart(3, '0')}`;
  });
  const errors = [], requests = [], cases = [];
  const allowed = new Set(['/api/app-config', '/api/auth/info', '/api/agent/list-global-models', '/api/ecommerce/catalog', '/api/ecommerce/analyze']);
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) requests.push(url.pathname);
  });
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    return ['127.0.0.1', 'localhost'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)
      ? route.continue() : route.abort();
  });
  try {
    await page.goto('http://127.0.0.1:5567', { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: '分析你的电商数据' }).waitFor();
    const questions = [
      '分析2018年1月销售额、订单数、客单价按地区分组',
      '比较2018年2月与2018年1月销售额、订单数、客单价',
      '分析2016年11月销售额、订单数、客单价',
      '分析2020年1月销售额',
      '最近两个完整月销售额',
    ];
    for (const [index, question] of questions.entries()) {
      await page.getByLabel('分析问题').fill(question);
      const responseReady = page.waitForResponse(r => r.url().endsWith('/api/ecommerce/analyze'), { timeout: 90000 });
      await page.getByRole('button', { name: '开始分析', exact: true }).click();
      const response = await responseReady;
      const data = await response.json();
      cases.push({ question, status: response.status(), response: data });
      assert.equal(response.status(), 200);
      const state = ['success', 'success', 'empty_result', 'outside_coverage', 'clarification_required'][index];
      assert.equal(data.state, state);
      if (index < 2) {
        await page.getByRole('table', { name: '分析结果表' }).waitFor();
        await page.locator('.vega-embed svg').waitFor();
        const rows = await page.getByRole('table', { name: '分析结果表' }).innerText();
        assert(rows.includes(index === 0 ? '368,611.43' : '826,437.13'));
        if (index === 0) {
          assert.equal(data.result.groups.length, 27);
          await page.screenshot({ path: path.join(out, 'v07-groups.png'), fullPage: true });
          await page.setViewportSize({ width: 390, height: 844 });
          await page.screenshot({ path: path.join(out, 'v07-mobile.png'), fullPage: true });
          await page.locator('.vega-embed').scrollIntoViewIfNeeded();
          await page.screenshot({ path: path.join(out, 'v07-mobile-chart.png'), fullPage: true });
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
          await page.setViewportSize({ width: 1440, height: 1000 });
        } else {
          assert.equal(data.result.current.values.order_count, 6555);
          assert.equal(data.result.baseline.values.order_count, 7069);
          assert((await page.getByRole('table', { name: '期间变化' }).innerText()).includes('-3.6126%'));
          await page.getByText('计算依据与数据来源', { exact: true }).click();
          await page.screenshot({ path: path.join(out, 'v07-compare.png'), fullPage: true });
        }
      } else {
        await page.getByRole('alert').first().waitFor();
        assert.equal(await page.getByRole('table', { name: '分析结果表' }).count(), 0);
        assert.equal(await page.locator('.vega-embed svg').count(), 0);
      }
    }
    await page.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle', timeout: 60000 });
    await page.getByRole('heading', { name: '分析你的电商数据' }).waitFor();
    assert.deepEqual(errors, []);
    assert(requests.every(url => allowed.has(url)), 'Unexpected legacy API request');
    const legacy = await page.request.post('http://127.0.0.1:5567/api/agent/analyst-streaming', { data: {} });
    assert.equal(legacy.status(), 403);
    const report = { status: 'passed', cases, page_errors: errors, api_paths: [...new Set(requests)],
      built_and_dev_loaded: true, mobile_no_page_overflow: true, legacy_route_status: legacy.status() };
    fs.writeFileSync(path.join(root, 'docs/verification/V0-7-browser.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ status: 'passed', cases: cases.length, page_errors: errors, api_paths: report.api_paths }));
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'v07-failed.png'), fullPage: true });
    fs.writeFileSync(path.join(out, 'v07-failed.json'), JSON.stringify({ errors, requests,
      text: await page.locator('body').innerText(), charts: await page.locator('.vega-embed').evaluateAll(nodes => nodes.map(node => node.outerHTML.slice(0, 1200))) }, null, 2));
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error('V0-7 browser verification failed:', error.message); process.exitCode = 1; });
