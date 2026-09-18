import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ fetch: vi.fn(), embed: vi.fn() }));
vi.mock('../../src/app/utils', () => ({ fetchWithIdentity: mocks.fetch }));
vi.mock('vega-embed', () => ({ default: mocks.embed }));
import { EcommerceResults, EcommerceWorkspace } from '../../src/views/EcommerceWorkspace';
import { AnalysisResponse, resultRows, salesSpec } from '../../src/views/ecommerce';

const values = { order_count: 7, sales_amount: '1234.50', average_order_amount: null };
const success: AnalysisResponse = { state: 'success', result: { state: 'success', values, groups: [] } };
afterEach(() => { cleanup(); vi.clearAllMocks(); });
mocks.embed.mockResolvedValue({ finalize: vi.fn() });

describe('V2 malformed and failed responses', () => {
    it.each([
        { state: 'success', result: { state: 'success', values: { sales_amount: '30.00' } } },
        { state: 'success', result: { state: 'success', current: { state: 'success', values }, changes: {} } },
    ])('shows an explicit incomplete-data message for missing fields', response => {
        render(<EcommerceResults response={response as AnalysisResponse} />);
        expect(screen.getByText(/结果数据不完整/)).toBeInTheDocument();
    });
    it('shows unknown status without claiming verified results', () => {
        render(<EcommerceResults response={{ state: 'future-status', result: success.result }} />);
        expect(screen.getByRole('alert')).toHaveTextContent('未知');
        expect(mocks.embed).not.toHaveBeenCalled();
    });
    it.each(['sync', 'async'])('keeps the table after %s chart failure', async kind => {
        if (kind === 'sync') mocks.embed.mockImplementationOnce(() => { throw new Error('chart'); });
        else mocks.embed.mockRejectedValueOnce(new Error('chart'));
        render(<EcommerceResults response={success} />);
        expect(await screen.findByText(/图表暂不可用/)).toBeInTheDocument();
        expect(screen.getByRole('table', { name: '分析结果表' })).toHaveTextContent('1,234.50');
    });
    it('uses safe fallback for unknown error and renders long text as text', () => {
        render(<EcommerceResults response={{ state: 'failed', error: { code: 'UNKNOWN' },
            explanation: { summary: '<script>' + '长'.repeat(10000) } }} />);
        expect(screen.getByRole('alert')).toHaveTextContent('没有自动重试');
        expect(document.querySelector('script')).toBeNull();
    });
    it('keeps unsaved calculation visible when history recovers as interrupted', async () => {
        let submitted = false;
        let requestId = '';
        mocks.fetch.mockImplementation((url: string, options?: RequestInit) => {
            if (url.endsWith('/catalog')) return Promise.resolve({ ok: true, json: async () => ({ snapshots: [] }) });
            if (url.endsWith('/workspace')) return Promise.resolve({ ok: true, json: async () => ({ schema_version: 1,
                orchestrator: 'v1', nodes: submitted ? [{ node_id: requestId, question: 'x', conditions: {},
                    status: 'interrupted', result: { state: 'interrupted' } }] : [] }) });
            submitted = true;
            requestId = JSON.parse(options!.body as string).request_id;
            return Promise.resolve({ ok: false, json: async () => ({ state: 'failed', result: success.result,
                error: { code: 'WORKSPACE_UNAVAILABLE' } }) });
        });
        render(<EcommerceWorkspace />);
        await waitFor(() => expect(screen.getByRole('button', { name: '开始分析' })).not.toBeDisabled());
        fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
        expect(await screen.findByText(/保存失败/)).toBeInTheDocument();
        expect(screen.getByRole('table', { name: '分析结果表' })).toBeInTheDocument();
    });
});

describe('verified result presentation', () => {
    it('uses identical group rows for the table and chart, preserving null averages', async () => {
        const response: AnalysisResponse = { state: 'success', result: { state: 'success', values,
            groups: [{ ...values, key: 'UNKNOWN' }], truncated: true, total_groups: 201 } };
        render(<EcommerceResults response={response} />);
        expect(screen.getByRole('table', { name: '分析结果表' })).toHaveTextContent('未知地区');
        expect(screen.getByRole('table', { name: '分析结果表' })).toHaveTextContent('1,234.50');
        expect(screen.getByRole('table', { name: '分析结果表' })).toHaveTextContent('—（不适用）');
        expect(screen.getByText(/分组已截断/)).toBeInTheDocument();
        await waitFor(() => expect(mocks.embed).toHaveBeenCalled());
        expect(mocks.embed.mock.calls[0][1].data.values).toEqual(salesSpec(resultRows(response.result!)).data.values);
    });
    it.each(['empty_result', 'outside_coverage'])('does not chart %s as business zero', state => {
        render(<EcommerceResults response={{ state, result: { state, values: { ...values, order_count: 0 } } }} />);
        expect(screen.queryByRole('table', { name: '分析结果表' })).not.toBeInTheDocument();
        expect(mocks.embed).not.toHaveBeenCalled();
    });
    it('keeps failed partial results visibly incomplete and shows budget errors', () => {
        render(<EcommerceResults response={{ state: 'failed', error: { code: 'BUDGET_EXHAUSTED' }, partial_result: success.result }} />);
        expect(screen.getByRole('alert')).toHaveTextContent('预算已用尽');
        expect(screen.getByRole('alert')).toHaveTextContent('分析闭环未完成');
    });
    it('renders clarification text as text, never HTML', () => {
        render(<EcommerceResults response={{ state: 'clarification_required', question: '<script>bad()</script>' }} />);
        expect(screen.getByRole('alert')).toHaveTextContent('<script>bad()</script>');
        expect(document.querySelector('script')).toBeNull();
    });
});

describe('submission boundaries', () => {
    it('restores V1 nodes, branches explicitly, and replays the same parent and ID', async () => {
        const conditions = { current: { start: '2018-01-01', end: '2018-02-01' }, regions: [], group_by: null };
        const nodes: any[] = [{ node_id: 'v1-parent-001', question: '父问题', status: 'success',
            conditions, result: success }];
        const bodies: any[] = [];
        mocks.fetch.mockImplementation((url: string, options?: RequestInit) => {
            if (url.endsWith('/catalog')) return Promise.resolve({ ok: true, json: async () => ({ snapshots: [] }) });
            if (url.endsWith('/workspace')) return Promise.resolve({ ok: true, json: async () => ({ schema_version: 1, orchestrator: 'v1', nodes }) });
            const body = JSON.parse(options!.body as string); bodies.push(body);
            if (bodies.length === 1) nodes.push({ node_id: body.request_id, parent_node_id: body.parent_node_id,
                question: body.user_question, conditions, status: 'success', result: success });
            return Promise.resolve({ ok: true, json: async () => success });
        });
        render(<EcommerceWorkspace />);
        await screen.findByRole('table', { name: '分析结果表' });
        fireEvent.click(screen.getByRole('button', { name: '基于此分析继续追问' }));
        fireEvent.change(screen.getByLabelText('分析问题'), { target: { value: '按日显示' } });
        fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
        await screen.findByText('父分析：父问题');
        await waitFor(() => expect(screen.getByRole('button', { name: '开始分析' })).not.toBeDisabled());
        fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
        await waitFor(() => expect(bodies).toHaveLength(2));
        expect(bodies[0]).toEqual(bodies[1]);
        expect(bodies[0].parent_node_id).toBe('v1-parent-001');
    });
    it('uses the V1 chart type and chosen measure, and labels partial results', async () => {
        render(<EcommerceResults response={{ state: 'partial', result: success.result,
            chart_spec: { type: 'line', measures: ['order_count'], source: 'verified_result' } }} />);
        expect(screen.getByRole('alert')).toHaveTextContent('部分结果');
        await waitFor(() => expect(mocks.embed).toHaveBeenCalled());
        const spec = mocks.embed.mock.calls[0][1];
        expect(spec.mark.type).toBe('line');
        expect(spec.data.values[0].amount).toBe(values.order_count);
    });
    it('keeps the parent when explicitly retrying a saved V1 failure', async () => {
        const node = { node_id: 'v1-failed-child', parent_node_id: 'v1-root-parent', question: '按日显示',
            status: 'failed', conditions: {}, result: { state: 'failed', error: { code: 'ANALYSIS_FAILED' } } };
        const bodies: any[] = [];
        mocks.fetch.mockImplementation((url: string, options?: RequestInit) => {
            if (url.endsWith('/catalog')) return Promise.resolve({ ok: true, json: async () => ({ snapshots: [] }) });
            if (url.endsWith('/workspace')) return Promise.resolve({ ok: true, json: async () => ({ schema_version: 1, orchestrator: 'v1', nodes: [node] }) });
            bodies.push(JSON.parse(options!.body as string));
            return Promise.resolve({ ok: false, json: async () => node.result });
        });
        render(<EcommerceWorkspace />);
        fireEvent.click(await screen.findByRole('button', { name: '故障已排除，发起新请求' }));
        await waitFor(() => expect(bodies).toHaveLength(1));
        expect(bodies[0].parent_node_id).toBe(node.parent_node_id);
        expect(bodies[0].request_id).not.toBe(node.node_id);
    });
    it('keeps a saved BUSY failure terminal instead of pretending it is running', async () => {
        const response = { state: 'failed', error: { code: 'BUSY' } };
        const nodes: any[] = [];
        mocks.fetch.mockImplementation((url: string, options?: RequestInit) => {
            if (url.endsWith('/catalog')) return Promise.resolve({ ok: true, json: async () => ({ snapshots: [] }) });
            if (url.endsWith('/workspace')) return Promise.resolve({ ok: true, json: async () => ({ schema_version: 1, nodes }) });
            const body = JSON.parse(options!.body as string);
            nodes.push({ node_id: body.request_id, ...body, response });
            return Promise.resolve({ ok: false, json: async () => response });
        });
        render(<EcommerceWorkspace />);
        await waitFor(() => expect(screen.getByRole('button', { name: '开始分析' })).not.toBeDisabled());
        fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
        await screen.findByRole('button', { name: '故障已排除，发起新请求' });
        expect(screen.queryByText(/任务仍在运行/)).toBeNull();
    });
    it('does not apply a stale refresh after selecting another saved node', async () => {
        const node = { node_id: 'saved-node', request_id: 'saved-node', user_question: '分析2018年1月销售额', response: success };
        const other = { ...node, node_id: 'other-node', request_id: 'other-node', user_question: '分析2018年2月销售额' };
        let calls = 0;
        let finish!: (value: unknown) => void;
        mocks.fetch.mockImplementation((url: string) => {
            if (url.endsWith('/catalog')) return Promise.resolve({ ok: true, json: async () => ({ snapshots: [] }) });
            if (++calls === 2) return new Promise(resolve => { finish = resolve; });
            return Promise.resolve({ ok: true, json: async () => ({ schema_version: 1, nodes: [other, node] }) });
        });
        render(<EcommerceWorkspace />);
        await screen.findByRole('table', { name: '分析结果表' });
        fireEvent.click(screen.getByRole('button', { name: '刷新保存状态' }));
        fireEvent.click(screen.getByRole('button', { name: '分析2018年2月销售额 · 完成' }));
        await act(async () => { finish({ ok: true, json: async () => ({ schema_version: 1,
            nodes: [node, { ...other, response: { state: 'failed', error: { code: 'MODEL_FAILED' } } }] }) }); });
        expect(screen.getByLabelText('分析问题')).toHaveValue('分析2018年2月销售额');
        expect(screen.getByRole('table', { name: '分析结果表' })).toBeInTheDocument();
    });
    it('restores saved results on remount without posting analysis', async () => {
        const node = { node_id: 'saved-node', request_id: 'saved-node', user_question: '分析2018年1月销售额',
            response: success, chart: { version: 1, kind: 'sales_bar' } };
        mocks.fetch.mockImplementation((url: string) => Promise.resolve({ ok: true, json: async () =>
            url.endsWith('/workspace') ? { schema_version: 1, nodes: [node] } : { snapshots: [] } }));
        const first = render(<EcommerceWorkspace />);
        await screen.findByRole('table', { name: '分析结果表' });
        expect(screen.getByLabelText('分析问题')).toHaveValue(node.user_question);
        first.unmount();
        render(<EcommerceWorkspace />);
        await screen.findByRole('table', { name: '分析结果表' });
        expect(mocks.fetch.mock.calls.every(([url]) => !url.endsWith('/analyze'))).toBe(true);
    });
    it('reads running then interrupted state without automatically retrying', async () => {
        let state = 'running';
        mocks.fetch.mockImplementation((url: string) => Promise.resolve({ ok: true, json: async () =>
            url.endsWith('/workspace') ? { schema_version: 1, nodes: [{ node_id: 'running-node', request_id: 'running-node',
                user_question: '分析2018年1月销售额', response: { state } }] } : { snapshots: [] } }));
        render(<EcommerceWorkspace />);
        await screen.findByText(/任务仍在运行/);
        expect(screen.getByRole('button', { name: '开始分析' })).toBeDisabled();
        state = 'interrupted';
        fireEvent.click(screen.getByRole('button', { name: '刷新保存状态' }));
        await screen.findByText(/任务已中断/);
        expect(screen.getByRole('button', { name: '故障已排除，发起新请求' })).not.toBeDisabled();
        expect(mocks.fetch.mock.calls.every(([url]) => !url.endsWith('/analyze'))).toBe(true);
    });
    it('blocks submission when history cannot be recovered', async () => {
        mocks.fetch.mockImplementation((url: string) => Promise.resolve({ ok: !url.endsWith('/workspace'),
            json: async () => ({ snapshots: [] }) }));
        render(<EcommerceWorkspace />);
        await screen.findByText(/工作区恢复失败/);
        expect(screen.getByRole('button', { name: '开始分析' })).toBeDisabled();
        expect(mocks.fetch.mock.calls.every(([url]) => !url.endsWith('/analyze'))).toBe(true);
    });
    it('only allocates a new retry ID after an explicit terminal-failure retry', async () => {
        const ids: string[] = [];
        mocks.fetch.mockImplementation((url: string, options?: RequestInit) => {
            if (url.endsWith('/workspace')) return Promise.resolve({ ok: true, json: async () => ({ schema_version: 1, nodes: [] }) });
            if (url.endsWith('/catalog')) return Promise.resolve({ ok: true, json: async () => ({ snapshots: [] }) });
            ids.push(JSON.parse(options!.body as string).request_id);
            return Promise.resolve({ ok: false, json: async () => ({ state: 'failed', error: { code: 'MODEL_DISABLED' } }) });
        });
        render(<EcommerceWorkspace />);
        await waitFor(() => expect(screen.getByRole('button', { name: '开始分析' })).not.toBeDisabled());
        fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
        fireEvent.click(await screen.findByRole('button', { name: '故障已排除，发起新请求' }));
        await waitFor(() => expect(ids).toHaveLength(2));
        expect(ids[1]).not.toBe(ids[0]);
    });
    it('prevents duplicate in-flight sends and reuses the ID after an uncertain network failure', async () => {
        const bodies: string[] = [];
        let reject!: (reason?: unknown) => void;
        mocks.fetch.mockImplementation((url: string, options?: RequestInit) => {
            if (url.endsWith('/workspace')) return Promise.resolve({ ok: true, json: async () => ({ schema_version: 1, nodes: [] }) });
            if (url.endsWith('/catalog')) return Promise.resolve({ ok: true, json: async () => ({ snapshots: [] }) });
            bodies.push(options!.body as string);
            if (bodies.length === 1) return new Promise((_, fail) => { reject = fail; });
            return Promise.resolve({ ok: true, json: async () => success });
        });
        render(<EcommerceWorkspace />);
        const button = screen.getByRole('button', { name: '开始分析' });
        await waitFor(() => expect(button).not.toBeDisabled());
        fireEvent.click(button); fireEvent.submit(button.closest('form')!);
        expect(bodies).toHaveLength(1);
        reject(new Error('network'));
        const retry = await screen.findByRole('button', { name: '查询同一请求' });
        fireEvent.click(retry);
        await screen.findByRole('table', { name: '分析结果表' });
        expect(JSON.parse(bodies[0]).request_id).toBe(JSON.parse(bodies[1]).request_id);
        expect(Object.keys(JSON.parse(bodies[1])).sort()).toEqual(['request_id', 'user_question']);
        fireEvent.change(screen.getByLabelText('分析问题'), { target: { value: '分析2018年1月销售额' } });
        expect(screen.queryByRole('table', { name: '分析结果表' })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
        await waitFor(() => expect(bodies).toHaveLength(3));
        expect(JSON.parse(bodies[2]).request_id).not.toBe(JSON.parse(bodies[1]).request_id);
    });
});
