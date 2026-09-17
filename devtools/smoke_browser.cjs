// Run with NODE_PATH pointing at the separately installed playwright-core.
// Uses installed Edge; no browser download and no paid model operations.
const { chromium } = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const root = path.resolve(__dirname, '..');
  const out = path.join(root, '.local', 'verification');
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  const failures = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => {
    if (r.url().startsWith('http://127.0.0.1') && r.status() >= 400)
      failures.push({ url: new URL(r.url()).pathname, status: r.status() });
  });
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (['127.0.0.1', 'localhost'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol))
      return route.continue();
    return route.abort();
  });
  try {
    for (const [label, url] of [['vite', 'http://127.0.0.1:5173'], ['built', 'http://127.0.0.1:5567']]) {
      await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
      await page.waitForFunction(() => {
        const text = document.querySelector('#root')?.innerText || '';
        return text.includes('Stock Prices') && !text.includes('LOADING DATA FORMULATOR');
      }, null, { timeout: 60000 });
      await page.screenshot({ path: path.join(out, `${label}.png`), fullPage: true });
      const result = { url, title: await page.title(), rootText: await page.locator('#root').innerText(), errors, failures };
      fs.writeFileSync(path.join(out, `${label}.json`), JSON.stringify(result, null, 2));
      console.log(JSON.stringify({ label, title: result.title, textLength: result.rootText.length, errors, failures }));
      if (errors.length || failures.length) process.exitCode = 1;
    }
  } finally {
    await browser.close();
  }
})().catch(() => { console.error('Browser smoke check failed; inspect local verification artifacts.'); process.exitCode = 1; });
