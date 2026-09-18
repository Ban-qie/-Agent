import { describe, expect, it, vi } from 'vitest';
import { SessionApi } from '../../src/views/multiuserApi';
import { taskFinished } from '../../src/views/ecommerce';

describe('multiuser API boundaries', () => {
    it('aborts an unresponsive HTTP request within ten seconds', async () => {
        vi.useFakeTimers();
        try {
            const api = new SessionApi(vi.fn((_path, options) => new Promise((_resolve, reject) => {
                options.signal.addEventListener('abort', () => reject(new Error('aborted')));
            })) as any);
            const pending = expect(api.json('/api/ecommerce/workspace')).rejects.toThrow('aborted');
            await vi.advanceTimersByTimeAsync(10000);
            await pending;
        } finally { vi.useRealTimers(); }
    });
    it('discards a late response after identity generation changes', async () => {
        let complete!: (value: any) => void;
        const api = new SessionApi(vi.fn(() => new Promise(resolve => { complete = resolve; })) as any);
        const pending = api.json('/api/ecommerce/workspace');
        api.reset();
        complete({ ok: true, status: 200, json: async () => ({ nodes: ['A-private'] }) });
        await expect(pending).rejects.toThrow('Session changed');
    });
    it('stops and expires on 401 without retrying a POST', async () => {
        const fetcher = vi.fn(async () => ({ ok: false, status: 401, json: async () => ({ error: { code: 'AUTH_REQUIRED' } }) }));
        const api = new SessionApi(fetcher as any);
        api.onExpired = vi.fn();
        await expect(api.json('/api/ecommerce/tasks/abc')).rejects.toThrow('登录');
        expect(api.onExpired).toHaveBeenCalledOnce();
        expect(fetcher).toHaveBeenCalledOnce();
    });
    it('maps terminal cancellation without polling or charging', async () => {
        const fetcher = vi.fn();
        const api = new SessionApi(fetcher);
        const task = { task_id: 'x', request_id: 'y', status: 'cancelled' as const, cancel_requested: true };
        expect(taskFinished(task)).toBe(true);
        expect(await api.poll(task, vi.fn())).toEqual({ state: 'cancelled' });
        expect(fetcher).not.toHaveBeenCalled();
    });
});
