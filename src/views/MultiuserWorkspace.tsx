import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Box, Button, CssBaseline, Paper, Stack, TextField, ThemeProvider, Typography, createTheme } from '@mui/material';
import { EcommerceWorkspace } from './EcommerceWorkspace';
import { PasswordSession, TaskResponse, taskFinished, taskLabels } from './ecommerce';
import { SessionApi } from './multiuserApi';

export function MultiuserWorkspace() {
    const api = useMemo(() => new SessionApi(), []);
    const [identity, setIdentity] = useState<PasswordSession>();
    const [ready, setReady] = useState(false);
    const [busy, setBusy] = useState(false);
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [task, setTask] = useState<TaskResponse>();
    const [revision, setRevision] = useState(0);
    const polling = useRef<string>();
    const theme = useMemo(() => createTheme(), []);
    const clearVisible = () => { setIdentity(undefined); setTask(undefined); polling.current = undefined; setPassword(''); };
    api.onExpired = () => { clearVisible(); setError('登录已失效，请重新登录。'); };
    useEffect(() => {
        let active = true;
        api.json('/api/ecommerce/auth/status').then(({ data }) => {
            if (active) { api.csrf = data.csrf_token; setIdentity(data); setReady(true); }
        }).catch(() => { if (active) { setError('登录服务不可用，请刷新重试。'); setReady(true); } });
        return () => { active = false; api.reset(); };
    }, [api]);
    const signIn = async (event: React.FormEvent) => {
        event.preventDefault(); setBusy(true); setError('');
        api.reset();
        try {
            const status = await api.json('/api/ecommerce/auth/status');
            api.csrf = status.data.csrf_token;
            const { data } = await api.json('/api/ecommerce/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) });
            api.csrf = data.csrf_token; setIdentity({ ...data, authenticated: true }); setPassword(''); setTask(undefined);
        } catch (e) { setError(e instanceof Error ? e.message : '登录失败'); }
        finally { setBusy(false); }
    };
    const logout = async () => {
        setBusy(true); api.reset(); clearVisible(); setError('');
        try {
            const { data } = await api.json('/api/ecommerce/auth/logout', { method: 'POST', body: '{}' });
            api.csrf = data.csrf_token;
        } catch { setError('退出未确认，请刷新检查登录状态。'); }
        finally { setBusy(false); }
    };
    const request = async (url: string | URL, options?: RequestInit) => {
        const path = String(url);
        const { data, status } = await api.json(path, options);
        if (status === 202) {
            polling.current = data.task_id;
            try {
                const result = await api.poll(data, setTask);
                return new Response(JSON.stringify(result), { status: 200, headers: { 'Content-Type': 'application/json' } });
            } finally { polling.current = undefined; }
        }
        if (path.endsWith('/workspace') && Array.isArray(data.tasks)) {
            const pending = data.tasks.find((value: TaskResponse) => !taskFinished(value));
            if (pending && polling.current !== pending.task_id) {
                polling.current = pending.task_id;
                const generation = api.generation;
                void api.poll(pending, setTask).then(() => {
                    if (generation === api.generation) setRevision(value => value + 1);
                }).catch(e => { if (generation === api.generation) setError(e.message); })
                    .finally(() => { if (generation === api.generation) polling.current = undefined; });
            }
        }
        return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
    };
    const cancel = async () => {
        if (!task) return;
        try { const { data } = await api.json(`/api/ecommerce/tasks/${task.task_id}/cancel`, { method: 'POST', body: '{}' }); setTask(data); }
        catch (e) { setError(e instanceof Error ? e.message : '取消失败'); }
    };
    return <ThemeProvider theme={theme}><CssBaseline />
        <Stack spacing={1} sx={{ p: 2 }}>
            {error && <Alert severity="error" sx={{ overflowWrap: 'anywhere' }}>{error}</Alert>}
            {!identity?.authenticated ? <Paper sx={{ p: 3, maxWidth: 480, mx: 'auto', width: '100%' }}>
                <Typography component="h1" variant="h5">登录电商分析</Typography>
                <Typography sx={{ my: 1 }}>使用管理员分配的受邀账号登录。</Typography>
                <Stack component="form" onSubmit={signIn} spacing={2}>
                    <TextField label="账号" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} disabled={busy} />
                    <TextField label="密码" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} disabled={busy} />
                    <Button type="submit" variant="contained" disabled={!ready || busy || !username || !password}>登录</Button>
                </Stack>
            </Paper> : <>
                <Box><Typography component="span">当前账号：{identity.username}</Typography><Button onClick={logout}>退出登录</Button></Box>
                {task && <Box role="status">任务状态：{taskLabels[task.status] || '未知状态'}
                    {!taskFinished(task) && <Button onClick={cancel} disabled={task.cancel_requested}>取消任务</Button>}</Box>}
                <EcommerceWorkspace key={`${identity.user_id}:${revision}`} request={request} />
            </>}
        </Stack>
    </ThemeProvider>;
}
