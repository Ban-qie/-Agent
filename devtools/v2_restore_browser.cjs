const { chromium } = require('playwright-core');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const directory = process.argv[2];
const expected = JSON.parse(fs.readFileSync(path.join(directory, 'expected.json'), 'utf8'));
(async () => {
    const browser = await chromium.launch({ channel: 'msedge', headless: true });
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [], posts = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/analyze')) posts.push(r.url()); });
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    try {
        await page.goto('http://127.0.0.1:5567', { waitUntil: 'networkidle' });
        const labels = { success: '完成', empty_result: '空结果', failed: '失败', waiting_clarification: '待澄清' };
        for (const node of expected.nodes) {
            const matches = [...expected.nodes].reverse().filter(n => n.question === node.question && n.status === node.status);
            const index = matches.findIndex(n => n.node_id === node.node_id);
            assert(index >= 0);
            await page.getByRole('button', { name: node.question + ' · ' + labels[node.status], exact: true }).nth(index).click();
            assert.equal(await page.getByLabel('分析问题').inputValue(), node.question);
            if (node.conditions.current) {
                assert((await page.locator('main').innerText()).includes(node.conditions.current.start));
            }
            const result = node.result.result;
            if (result && result.state === 'success') {
                await page.getByRole('table', { name: '分析结果表', exact: true }).waitFor();
                const values = (result.current || result).values;
                if (values) assert((await page.locator('main').innerText()).includes(Number(values.sales_amount).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })));
            }
        }
        await page.reload({ waitUntil: 'networkidle' });
        assert.deepEqual(await (await page.request.get('http://127.0.0.1:5567/api/ecommerce/workspace')).json(), expected);
        assert.equal(posts.length, 0);
        assert.deepEqual(errors, []);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ path: path.join(directory, 'mobile.png'), fullPage: true });
        fs.writeFileSync(path.join(directory, 'browser.json'), JSON.stringify({ nodes: expected.nodes.length, posts, errors, width: 390, passed: true }), { flag: 'wx' });
    } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
