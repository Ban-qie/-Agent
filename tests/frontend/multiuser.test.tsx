import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('vega-embed', () => ({ default: vi.fn(async () => ({ finalize: vi.fn() })) }));
vi.mock('../../src/app/utils', () => ({ fetchWithIdentity: vi.fn() }));
import { MultiuserWorkspace } from '../../src/views/MultiuserWorkspace';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const response = (data: any, status = 200) => new Response(JSON.stringify(data), { status });
const workspace = (question?: string) => ({ schema_version: 1, orchestrator: 'v1', tasks: [],
    nodes: question ? [{ node_id: 'node-001', question, status: 'interrupted', conditions: {}, result: { state: 'interrupted' } }] : [] });

async function signIn(name: string) {
    fireEvent.change(await screen.findByLabelText('账号'), { target: { value: name } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'test-password' } });
    await waitFor(() => expect(screen.getByRole('button', { name: '登录' })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: '登录' }));
    await screen.findByText('当前账号：' + name);
}

it('clears A screen and rejects a late history response after B login', async () => {
    let owner = '';
    let finishA!: (value: Response) => void;
    const fetcher = vi.fn(async (path: string, options?: RequestInit): Promise<Response> => {
        if (path.endsWith('/auth/status')) return response({ authenticated: false, csrf_token: 'csrf' });
        if (path.endsWith('/auth/login')) { owner = JSON.parse(options!.body as string).username;
            return response({ user_id: owner, username: owner, csrf_token: 'csrf' }); }
        if (path.endsWith('/auth/logout')) { owner = ''; return response({ logged_out: true, csrf_token: 'csrf' }); }
        if (path.endsWith('/catalog')) return response({ snapshots: [] });
        if (path.endsWith('/workspace')) {
            if (owner === 'alice') return new Promise(resolve => { finishA = resolve; });
            return response(workspace('B-only'));
        }
        throw new Error(path);
    });
    vi.stubGlobal('fetch', fetcher);
    render(<MultiuserWorkspace />);
    await signIn('alice');
    await waitFor(() => expect(finishA).toBeDefined());
    fireEvent.click(screen.getByRole('button', { name: '退出登录' }));
    await signIn('bobby');
    await screen.findByRole('button', { name: /B-only/ });
    await act(async () => { finishA(response(workspace('A-secret'))); });
    expect(screen.queryByText(/A-secret/)).toBeNull();
    expect(screen.getByRole('button', { name: /B-only/ })).toBeInTheDocument();
});

it('restores original running task and cancels without an analysis POST', async () => {
    let cancelled = false;
    const task = { task_id: 'a'.repeat(32), request_id: 'same-request', status: 'running', cancel_requested: false };
    const fetcher = vi.fn(async (path: string): Promise<Response> => {
        if (path.endsWith('/auth/status')) return response({ authenticated: true, user_id: 'A', username: 'alice', csrf_token: 'csrf' });
        if (path.endsWith('/catalog')) return response({ snapshots: [] });
        if (path.endsWith('/workspace')) return response({ ...workspace(), tasks: cancelled ? [] : [task] });
        if (path.endsWith('/cancel')) { cancelled = true; return response({ ...task, status: 'cancelled', cancel_requested: true }); }
        if (path.includes('/tasks/')) return response({ ...task, status: cancelled ? 'cancelled' : 'running' });
        throw new Error(path);
    });
    vi.stubGlobal('fetch', fetcher);
    render(<MultiuserWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: '取消任务' }));
    expect(await screen.findByText(/任务状态：已取消/)).toBeInTheDocument();
    expect(fetcher.mock.calls.some(([path]) => path.endsWith('/analyze'))).toBe(false);
});

it('shows wrong-password error without entering the workspace', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => path.endsWith('/status') ?
        response({ authenticated: false, csrf_token: 'csrf' }) : response({ error: { code: 'LOGIN_REJECTED' } }, 401)));
    render(<MultiuserWorkspace />);
    fireEvent.change(await screen.findByLabelText('账号'), { target: { value: 'alice' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'wrong-password' } });
    await waitFor(() => expect(screen.getByRole('button', { name: '登录' })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: '登录' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('账号或密码不正确');
    expect(screen.queryByText(/当前账号/)).toBeNull();
});
