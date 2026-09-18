// Offline model only; real HTTP password authentication in independent contexts.
const { chromium } = require('playwright-core');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const net = require('node:net');
const directory = path.resolve(process.argv[2]);
const origin = 'http://127.0.0.1:5567';
const credentials = { secret: randomBytes(32).toString('hex'), accounts: {
    alice: randomBytes(18).toString('hex'), bobby: randomBytes(18).toString('hex') } };
const errors = [], matrix = [], allPosts = [];
let server, browser, logs = 0;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function start() {
    const log = fs.openSync(path.join(directory, `server-${++logs}.log`), 'wx');
    server = spawn(path.resolve('.venv/Scripts/python.exe'), ['-m', 'devtools.v3_server', path.join(directory, 'runtime')],
        { windowsHide: true, stdio: ['pipe', log, log] });
    server.stdin.end(JSON.stringify(credentials) + '\n');
    for (let i = 0; i < 120; i++) {
        if (server.exitCode !== null) throw new Error('Server exited; inspect log');
        try { const r = await fetch(origin + '/api/ecommerce/auth/status'); if (r.ok) return; } catch {}
        await delay(250);
    }
    throw new Error('Server startup timeout');
}
async function stop() {
    if (server && server.exitCode === null) {
        const killer = spawn('taskkill.exe', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
        await new Promise(resolve => killer.on('exit', resolve));
        await delay(300);
    }
}
async function login(page, name) {
    await page.goto(origin + '/multiuser');
    await page.getByLabel('账号', { exact: true }).fill(name);
    await page.getByLabel('密码', { exact: true }).fill(credentials.accounts[name]);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.getByText('当前账号：' + name, { exact: true }).waitFor();
    await page.getByRole('button', { name: '开始分析', exact: true }).waitFor();
}
async function api(page, method, url, body) {
    return page.evaluate(async ({ method, url, body }) => {
        const status = await (await fetch('/api/ecommerce/auth/status')).json();
        const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': status.csrf_token },
            ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
        return { status: r.status, body: await r.json() };
    }, { method, url, body });
}
async function analyze(page, question) {
    await page.getByLabel('分析问题', { exact: true }).fill(question);
    const post = page.waitForResponse(r => r.url().endsWith('/analyze') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '开始分析', exact: true }).click();
    const response = await post;
    assert.equal(response.status(), 202);
    const task = await response.json();
    await page.getByRole('table', { name: '分析结果表', exact: true }).waitFor({ timeout: 30000 });
    await page.getByRole('button', { name: '开始分析', exact: true }).waitFor();
    matrix.push({ case: 'own-analysis', task_id: task.task_id, request_id: task.request_id, status: 202 });
    return task;
}
(async () => {
    fs.mkdirSync(directory, { recursive: false });
    const occupied = await new Promise(resolve => { const socket = net.connect(5567, '127.0.0.1');
        socket.on('connect', () => { socket.destroy(); resolve(true); }); socket.on('error', () => resolve(false)); });
    assert.equal(occupied, false, 'Existing server occupies 5567; never stop it');
    await start();
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const contexts = await Promise.all([browser.newContext({ viewport: { width: 390, height: 844 } }), browser.newContext()]);
    const pages = await Promise.all(contexts.map(c => c.newPage()));
    for (const page of pages) {
        page.on('pageerror', e => errors.push(e.message));
        page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/analyze')) allPosts.push(r.url()); });
        await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    }
    const [a, b] = pages;
    await login(a, 'alice'); await login(b, 'bobby');
    const root = await analyze(a, '比较2018年2月与2018年1月销售额、订单数、客单价');
    await a.getByRole('button', { name: '基于此分析继续追问', exact: true }).click();
    await analyze(a, '按地区分组销售金额减少最多前3');
    await a.getByRole('button', { name: '基于此分析继续追问', exact: true }).click();
    await analyze(a, '地区SP按日显示');
    await a.getByRole('button', { name: '比较2018年2月与2018年1月销售额、订单数、客单价 · 完成', exact: true }).click();
    await a.getByRole('button', { name: '基于此分析继续追问', exact: true }).click();
    await analyze(a, '按地区比较订单数');
    const bRoot = await analyze(b, '分析2018年1月销售额');
    const bHistory = await api(b, 'GET', '/api/ecommerce/workspace');
    assert.equal(bHistory.body.nodes.length, 1);
    for (const [method, url, body] of [
        ['GET', `/api/ecommerce/tasks/${root.task_id}`],
        ['POST', `/api/ecommerce/tasks/${root.task_id}/cancel`, {}],
        ['POST', '/api/ecommerce/analyze', { request_id: 'forbidden-parent', user_question: '按日显示', parent_node_id: root.request_id }],
        ['POST', '/api/ecommerce/analyze', { request_id: 'forged-owner', user_question: '分析2018年1月销售额', owner: 'alice' }],
        ['POST', '/api/ecommerce/query', {}],
    ]) {
        const result = await api(b, method, url, body);
        assert([400, 403, 404].includes(result.status));
        matrix.push({ case: 'cross-user-or-closed', method, url, status: result.status });
    }
    // Same request ID belongs independently to B, never replays A's response.
    const same = await api(b, 'POST', '/api/ecommerce/analyze', { request_id: root.request_id, user_question: '分析2018年1月销售额' });
    assert.equal(same.status, 202); assert.notEqual(same.body.task_id, root.task_id);
    for (let i = 0; i < 100; i++) {
        const r = await api(b, 'GET', `/api/ecommerce/tasks/${same.body.task_id}`);
        if (r.body.status === 'success') break;
        if (i === 99) throw new Error('Same-ID task timeout');
        await delay(200);
    }
    const beforeRestart = await api(b, 'GET', '/api/ecommerce/workspace');
    await a.screenshot({ path: path.join(directory, 'alice-mobile.png'), fullPage: true });
    const postsBefore = allPosts.length;
    await a.getByRole('button', { name: '退出登录', exact: true }).click();
    await login(a, 'bobby');
    assert.equal(await a.getByRole('button', { name: '比较2018年2月与2018年1月销售额、订单数、客单价 · 完成', exact: true }).count(), 0);
    await a.reload();
    await a.getByText('当前账号：bobby', { exact: true }).waitFor();
    await contexts[0].setOffline(true); await contexts[0].setOffline(false);
    await stop(); await start();
    await a.reload();
    await a.getByText('当前账号：bobby', { exact: true }).waitFor();
    assert.deepEqual((await api(a, 'GET', '/api/ecommerce/workspace')).body, beforeRestart.body);
    assert.equal(allPosts.length, postsBefore);
    assert.deepEqual(errors, []);
    await a.screenshot({ path: path.join(directory, 'bob-after-restart.png'), fullPage: true });
    fs.writeFileSync(path.join(directory, 'summary.json'), JSON.stringify({ passed: true, real_password_accounts: 2,
        model: 'offline-stub', real_provider_calls: 0, matrix, reload_restart_analysis_posts: 0, errors,
        b_task: bRoot.task_id }, null, 2), { flag: 'wx' });
})().catch(async error => {
    console.error(error); console.error('Page errors:', errors);
    if (browser) for (const [i, context] of browser.contexts().entries()) {
        const page = context.pages()[0];
        if (page) {
            console.error('Page URL/text:', page.url(), (await page.locator('body').innerText()).slice(0, 2000));
            await page.screenshot({ path: path.join(directory, `failure-${i}.png`) });
        }
    }
    process.exitCode = 1;
})
    .finally(async () => { if (browser) await browser.close(); await stop(); });
