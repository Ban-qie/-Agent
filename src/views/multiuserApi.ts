import { AnalysisResponse, TaskResponse, taskFinished, errorMessage } from './ecommerce';

export class SessionApi {
    csrf = '';
    generation = 0;
    controller = new AbortController();
    onExpired: () => void = () => {};
    constructor(private transport: typeof fetch = (...args) => fetch(...args)) {}
    reset() {
        this.controller.abort();
        this.controller = new AbortController();
        this.generation++;
    }
    async json(path: string, options: RequestInit = {}) {
        const generation = this.generation;
        const controller = new AbortController();
        const sessionSignal = this.controller.signal;
        const abort = () => controller.abort();
        sessionSignal.addEventListener('abort', abort, { once: true });
        const timer = setTimeout(abort, 10000);
        let response: Response;
        let data: any;
        try {
            response = await this.transport(path, { ...options, credentials: 'same-origin',
                signal: controller.signal, headers: { 'Content-Type': 'application/json',
                    'X-CSRF-Token': this.csrf, ...options.headers } });
            data = await response.json();
        } finally {
            clearTimeout(timer); sessionSignal.removeEventListener('abort', abort);
        }
        if (generation !== this.generation) throw new Error('Session changed');
        if (response.status === 401 && !path.endsWith('/auth/login')) {
            this.reset(); this.onExpired();
        }
        if (!response.ok && !data.state) throw new Error(errorMessage(data.error?.code));
        return { data, status: response.status };
    }
    async pause(milliseconds: number) {
        const signal = this.controller.signal;
        if (signal.aborted) throw new Error('Session changed');
        await new Promise<void>((resolve, reject) => {
            const cancel = () => { clearTimeout(timer); reject(new Error('Session changed')); };
            const timer = setTimeout(() => { signal.removeEventListener('abort', cancel); resolve(); }, milliseconds);
            signal.addEventListener('abort', cancel, { once: true });
        });
    }
    async poll(task: TaskResponse, update: (value: TaskResponse) => void): Promise<AnalysisResponse> {
        const generation = this.generation;
        const deadline = Date.now() + 90000;
        let errors = 0;
        while (!taskFinished(task)) {
            update(task);
            if (Date.now() >= deadline || errors >= 5) throw new Error('等待已停止，请刷新保存状态查询原任务。');
            await this.pause(1000);
            try {
                const value = await this.json('/api/ecommerce/tasks/' + encodeURIComponent(task.task_id));
                task = value.data; errors = 0;
            } catch (error) {
                if (generation !== this.generation) throw error;
                errors++;
            }
        }
        if (generation !== this.generation) throw new Error('Session changed');
        update(task);
        return task.response || { state: task.status };
    }
}
