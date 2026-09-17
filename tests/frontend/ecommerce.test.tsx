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
