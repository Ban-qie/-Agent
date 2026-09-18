import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, Divider, Paper, Stack, Table, TableBody,
    TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from '@mui/material';
import embed from 'vega-embed';
import dfLogo from '../assets/df-logo.svg';
import { fetchWithIdentity } from '../app/utils';
import { AnalysisNode, AnalysisResponse, MetricResult, ResultRow, errorMessage, examples, formatAmount, periodLabel,
    resultRows, salesSpec, workspaceNodes, ChartPlan, Values, metricLabels, plannedSpec } from './ecommerce';

function SalesChart({ rows, plan, measure = 'sales_amount' }: { rows: ResultRow[]; plan?: ChartPlan; measure?: keyof Values }) {
    const host = useRef<HTMLDivElement>(null);
    const [error, setError] = useState(false);
    useEffect(() => {
        if (!host.current) return;
        let disposed = false;
        let view: Awaited<ReturnType<typeof embed>> | undefined;
        setError(false);
        embed(host.current, plan ? plannedSpec(rows, plan, measure) : salesSpec(rows), { actions: false, renderer: 'svg' }).then(result => {
            if (disposed) result.finalize(); else view = result;
        }).catch(() => { if (!disposed) setError(true); });
        return () => { disposed = true; view?.finalize(); };
    }, [rows, plan, measure]);
    return <Box sx={{ overflowX: 'auto' }}>
        {error && <Alert severity="warning">图表暂不可用，以下结果表仍可查看。</Alert>}
        <Box ref={host} role="img" aria-label={`${metricLabels[measure]}${plan?.type === 'line' ? '趋势图' : '柱状图'}，与结果表使用相同数据`}
            sx={{ width: '100%', minWidth: Math.max(280, rows.length * 24 + 150) }} />
    </Box>;
}

function Summary({ title, result }: { title: string; result: MetricResult }) {
    const values = result.values;
    if (result.state === 'empty_result') return <Alert severity="info">{title}：快照中无符合条件的记录，不能推断真实业务为零；客单价不适用。</Alert>;
    if (!values) return null;
    return <Paper variant="outlined" sx={{ p: 2, flex: 1 }}>
        <Typography variant="subtitle2" color="text.secondary">{title}</Typography>
        <Typography variant="h5" sx={{ my: 1 }}>{formatAmount(values.sales_amount)}</Typography>
        <Typography variant="body2">商品金额 · 源数据金额单位</Typography>
        <Typography variant="body2" sx={{ mt: 1 }}>已交付订单 {values.order_count.toLocaleString('zh-CN')} 单 · 客单价 {formatAmount(values.average_order_amount)}</Typography>
    </Paper>;
}

export function EcommerceResults({ response }: { response: AnalysisResponse }) {
    const failed = response.state === 'failed';
    const result = failed ? response.partial_result || (response.result?.state !== 'failed' ? response.result : undefined) : response.result;
    const conditions = response.conditions?.current ? { ...result?.conditions, ...response.conditions } : result?.conditions;
    const rows = useMemo(() => result ? resultRows(result) : [], [result]);
    const periods = result?.current ? [result.current, result.baseline!] : result ? [result] : [];
    const truncated = periods.some(period => period.truncated);
    return <Stack spacing={2} aria-live="polite">
        {response.state === 'clarification_required' && <Alert severity="info">需要澄清：{response.question}</Alert>}
        {response.state === 'running' && <Alert severity="info">任务仍在运行。刷新状态只读取记录，不会重新执行。</Alert>}
        {response.state === 'interrupted' && <Alert severity="warning">任务已中断，未自动重新执行。请检查服务与费用记录，确认后可发起新请求。</Alert>}
        {response.state === 'partial' && <Alert severity="warning">以下仅为已验证的部分结果，分析尚未全部完成。</Alert>}
        {failed && <Alert severity="error">{errorMessage(response.error?.code || response.result?.error?.code)}{result && ' 以下仅为已验证的部分结果，分析闭环未完成。'}</Alert>}
        {response.explanation?.summary && <Typography variant="body2">{response.explanation.summary}</Typography>}
        {conditions && <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle2">本次分析条件</Typography>
            <Typography variant="body2">当前期：{periodLabel(conditions.current)}</Typography>
            {conditions.baseline && <Typography variant="body2">基准期：{periodLabel(conditions.baseline)}</Typography>}
            <Typography variant="body2">地区：{conditions.regions.length ? conditions.regions.join('、') : '全部地区（包括未知地区）'} · 分组：{({ region: '地区', day: '天', month: '月' } as Record<string, string>)[conditions.group_by || ''] || '不分组'}</Typography>
            {conditions.metrics && <Typography variant="body2">所选指标：{conditions.metrics.map(metric => metricLabels[metric]).join('、')}</Typography>}
            {conditions.sort && <Typography variant="body2">排序：{metricLabels[conditions.sort.field]} · {conditions.sort.direction === 'asc' ? '升序' : '降序'}{conditions.top_n ? ` · 前 ${conditions.top_n} 组` : ''}</Typography>}
        </Paper>}
        {result?.state === 'outside_coverage' ? <Alert severity="warning">日期超出快照观测范围，未裁剪日期或放宽条件；没有生成图表。</Alert> : result && <>
            <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
                <Summary title="当前期" result={result.current || result} />
                {result.baseline && <Summary title="基准期" result={result.baseline} />}
            </Stack>
            {result.changes && <TableContainer component={Paper} variant="outlined"><Table size="small" aria-label="期间变化">
                <TableHead><TableRow><TableCell>指标</TableCell><TableCell>绝对变化</TableCell><TableCell>相对变化</TableCell></TableRow></TableHead>
                <TableBody>{(['order_count', 'sales_amount', 'average_order_amount'] as const).map(key => <TableRow key={key}>
                    <TableCell>{{ order_count: '订单数', sales_amount: '商品金额', average_order_amount: '客单价' }[key]}</TableCell>
                    <TableCell>{result.changes![key].absolute == null ? '—（不适用）' : key === 'order_count'
                        ? Number(result.changes![key].absolute).toLocaleString('zh-CN') : formatAmount(result.changes![key].absolute)}</TableCell>
                    <TableCell>{result.changes![key].percent == null ? '—（基准为零或不适用）' : `${result.changes![key].percent}%`}</TableCell>
                </TableRow>)}</TableBody>
            </Table></TableContainer>}
            {rows.length > 0 && <>
                {truncated && <Alert severity="warning">分组已截断：每期最多显示 {conditions?.top_n || 100} 组。图表与表格仅含显示的分组，上方总计仍覆盖全部选定记录。</Alert>}
                {response.chart_spec?.type !== 'table' && (response.chart_spec?.measures || ['sales_amount' as const]).map(measure =>
                    <Paper key={measure} variant="outlined" sx={{ p: 2 }}><Typography variant="subtitle1">{metricLabels[measure]}对照</Typography>
                    <Typography variant="caption" color="text.secondary">宽分组图和结果表可横向滚动查看。</Typography><SalesChart rows={rows} plan={response.chart_spec} measure={measure} /></Paper>)}
                <TableContainer component={Paper} variant="outlined"><Table size="small" aria-label="分析结果表"
                    sx={{ minWidth: 560, '& .MuiTableCell-root': { whiteSpace: 'nowrap' } }}>
                    <TableHead><TableRow>{['期间', '分组', '已交付订单数', '商品金额', '客单价'].map(label => <TableCell key={label}>{label}</TableCell>)}</TableRow></TableHead>
                    <TableBody>{rows.map((row, i) => <TableRow key={i}><TableCell>{row.period}</TableCell><TableCell>{row.label}</TableCell>
                        <TableCell>{row.order_count.toLocaleString('zh-CN')}</TableCell><TableCell>{formatAmount(row.sales_amount)}</TableCell><TableCell>{formatAmount(row.average_order_amount)}</TableCell></TableRow>)}</TableBody>
                </Table></TableContainer>
            </>}
        </>}
        {result && <Box component="details" sx={{ p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 1, overflowWrap: 'anywhere' }}>
            <summary>计算依据与数据来源</summary>
            <Typography variant="body2" sx={{ mt: 1 }}>只统计 delivered 已交付订单，按下单日期筛选；商品金额为订单商品价格合计，不含运费。订单粒度去重，零价订单计入分母；客单价＝商品金额÷订单数。比较差额使用未舍入值计算，基准为零时增长率不适用。</Typography>
            <Typography variant="body2">固定指标工具计算，无模型生成 SQL。金额为源数据金额单位，币种和时间时区未声明；观测范围不证明月份完整。</Typography>
            <Typography variant="body2">Olist · CC BY-NC-SA 4.0</Typography>
            <Typography variant="caption" component="p">快照：{result.conditions?.snapshot_id || conditions?.snapshot_id} · 指标版本：{result.conditions?.metric_version || conditions?.metric_version}</Typography>
            <Typography variant="caption" component="p">结果编号：{result.result_id || '未生成'}</Typography>
        </Box>}
    </Stack>;
}

export function EcommerceWorkspace() {
    const [question, setQuestion] = useState(examples[0]);
    const [response, setResponse] = useState<AnalysisResponse>();
    const [busy, setBusy] = useState(false);
    const [networkError, setNetworkError] = useState(false);
    const [catalog, setCatalog] = useState<{ snapshots: { observed_dates: { observed_purchase_min: string; observed_purchase_max: string } }[] }>();
    const [catalogError, setCatalogError] = useState(false);
    const [nodes, setNodes] = useState<AnalysisNode[]>([]);
    const [loaded, setLoaded] = useState(false);
    const [restoreError, setRestoreError] = useState(false);
    const [v1, setV1] = useState(false);
    const [selected, setSelected] = useState<AnalysisNode>();
    const [parent, setParent] = useState<AnalysisNode>();
    const pending = useRef(false);
    const last = useRef<{ question: string; id: string; parentId?: string | null }>();
    const selectNode = (node: AnalysisNode) => {
        last.current = { question: node.user_question, id: node.request_id, parentId: node.parent_node_id };
        setSelected(node); setParent(undefined);
        setQuestion(node.user_question); setResponse(node.response); setNetworkError(false);
    };
    const loadWorkspace = async (restore = false) => {
        const selectedId = last.current?.id;
        const res = await fetchWithIdentity('/api/ecommerce/workspace');
        if (!res.ok) throw new Error();
        const state = await res.json();
        const savedNodes = workspaceNodes(state);
        setV1(state.orchestrator === 'v1'); setNodes(savedNodes); setLoaded(true); setRestoreError(false);
        const node = restore ? savedNodes.at(-1) : savedNodes.find(n => n.request_id === selectedId);
        if (node && selectedId === last.current?.id && (restore || last.current?.question === node.user_question)) selectNode(node);
    };
    useEffect(() => {
        let active = true;
        fetchWithIdentity('/api/ecommerce/catalog').then(async res => {
            if (!res.ok) throw new Error();
            const data = await res.json();
            if (active) setCatalog(data);
        }).catch(() => { if (active) setCatalogError(true); });
        return () => { active = false; };
    }, []);
    useEffect(() => {
        let active = true;
        fetchWithIdentity('/api/ecommerce/workspace').then(async res => {
            if (!res.ok) throw new Error();
            const state = await res.json();
            const savedNodes = workspaceNodes(state);
            if (active) {
                setV1(state.orchestrator === 'v1'); setNodes(savedNodes); setLoaded(true);
                const node = savedNodes.at(-1);
                if (node) selectNode(node);
            }
        }).catch(() => { if (active) setRestoreError(true); });
        return () => { active = false; };
    }, []);
    const submit = async (event: React.FormEvent) => {
        event.preventDefault();
        if (pending.current || !loaded || !question.trim() || response?.state === 'running') return;
        if (new TextEncoder().encode(question).length > 2048) {
            setResponse({ state: 'clarification_required', question: '问题过长，请缩短至 2048 字节以内。' }); return;
        }
        pending.current = true; setBusy(true); setNetworkError(false); setResponse(undefined);
        if (last.current?.question !== question) last.current = { question, id: crypto.randomUUID(), parentId: parent?.node_id };
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 90000);
        try {
            const res = await fetchWithIdentity('/api/ecommerce/analyze', { method: 'POST',
                headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
                body: JSON.stringify({ request_id: last.current!.id, user_question: question,
                    ...(v1 && last.current!.parentId ? { parent_node_id: last.current!.parentId } : {}) }) });
            const data = await res.json();
            if (!['success', 'empty_result', 'outside_coverage', 'failed', 'partial', 'clarification_required', 'running', 'interrupted'].includes(data.state)) throw new Error();
            setResponse(data);
            const saved = await fetchWithIdentity('/api/ecommerce/workspace');
            if (!saved.ok) throw new Error();
            const state = await saved.json();
            const savedNodes = workspaceNodes(state);
            setNodes(savedNodes); setRestoreError(false);
            const node = savedNodes.find(n => n.request_id === last.current?.id);
            if (node) selectNode(node);
        } catch { setNetworkError(true); }
        finally { clearTimeout(timeout); pending.current = false; setBusy(false); }
    };
    const dates = catalog?.snapshots?.[0]?.observed_dates;
    return <Box sx={{ height: '100dvh', overflow: 'auto', bgcolor: 'background.default' }}>
        <Box component="header" sx={{ px: 3, py: 2, display: 'flex', gap: 2, alignItems: 'center', borderBottom: '1px solid', borderColor: 'divider' }}>
            <Box component="img" src={dfLogo} alt="" sx={{ width: 30, height: 30 }} />
            <Typography variant="h6">Data Formulator</Typography><Chip size="small" label="电商运营分析" />
        </Box>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '300px minmax(0, 1fr)' }, gap: 3, p: { xs: 2, md: 3 }, maxWidth: 1500, mx: 'auto' }}>
            <Paper component="aside" variant="outlined" sx={{ p: 2, alignSelf: 'start' }}>
                <Typography variant="subtitle1">Olist 订单快照</Typography>
                <Typography variant="body2" sx={{ my: 1 }}>商品销售额 · 已交付订单数 · 客单价</Typography>
                {dates && <Typography variant="body2" color="text.secondary">观测日期：{dates.observed_purchase_min.slice(0, 10)} 至 {dates.observed_purchase_max.slice(0, 10)}</Typography>}
                {catalogError && <Alert severity="error">数据目录加载失败，请检查服务后刷新。</Alert>}
                <Typography variant="body2" color="text.secondary" sx={{ my: 2 }}>金额单位和时区未声明；观测区间不代表完整月份。</Typography>
                <Divider sx={{ my: 2 }} /><Typography variant="subtitle2">试试这些问题</Typography>
                <Stack spacing={1} sx={{ mt: 1 }}>{examples.map(text => <Button key={text} disabled={busy || !loaded} variant="text" sx={{ textAlign: 'left', whiteSpace: 'normal', justifyContent: 'flex-start' }} onClick={() => { last.current = undefined; setSelected(undefined); setParent(undefined); setQuestion(text); setResponse(undefined); setNetworkError(false); }}>{text}</Button>)}</Stack>
                <Divider sx={{ my: 2 }} /><Typography variant="subtitle2">已保存的分析</Typography>
                <Typography variant="caption">任务和结果自动保存；选择历史问题后可修改条件继续分析。</Typography>
                <Stack spacing={1}>{[...nodes].reverse().map(node => <Button key={node.node_id} disabled={busy || !loaded}
                    sx={{ justifyContent: 'flex-start', textAlign: 'left', whiteSpace: 'normal', overflowWrap: 'anywhere', minWidth: 0 }} onClick={() => selectNode(node)}>
                    {node.user_question} · {({ success: '完成', empty_result: '空结果', outside_coverage: '覆盖外', failed: '失败',
                        clarification_required: '待澄清', running: '运行中', interrupted: '中断', partial: '部分完成' } as Record<string, string>)[node.response.state] || '未知状态'}
                </Button>)}</Stack>
            </Paper>
            <Stack component="main" spacing={2} sx={{ minWidth: 0 }}>
                <Typography variant="h5" component="h1">分析你的电商数据</Typography>
                {!loaded && !restoreError && <Alert severity="info">正在恢复工作区…</Alert>}
                {restoreError && <Alert severity="error">工作区恢复失败，未覆盖已保存记录。请检查服务后重试。</Alert>}
                <Button disabled={busy} onClick={() => { void loadWorkspace(!loaded).catch(() => setRestoreError(true)); }}>刷新保存状态</Button>
                <Typography variant="body2" color="text.secondary">指定明确期间；比较时先写当前期，再写基准期。可附加“地区SP”或“按地区分组”。</Typography>
                {v1 && <Stack spacing={1}>
                    {selected?.parent_node_id && <Typography variant="body2">父分析：{nodes.find(n => n.node_id === selected.parent_node_id)?.user_question || '未找到'}</Typography>}
                    {parent && <Alert severity="info">继承条件自：{parent.user_question}。仅修改本次明确指定的条件。</Alert>}
                    <Box>
                        <Button disabled={busy || !loaded || !selected || selected.response.state === 'running'} onClick={() => {
                            setParent(selected); setQuestion(''); setResponse(undefined); last.current = undefined; setNetworkError(false);
                        }}>基于此分析继续追问</Button>
                        <Button disabled={busy || !loaded} onClick={() => {
                            setParent(undefined); setSelected(undefined); setQuestion(''); setResponse(undefined); last.current = undefined; setNetworkError(false);
                        }}>开始独立分析</Button>
                    </Box>
                </Stack>}
                <Box component="form" onSubmit={submit}>
                    <TextField label="分析问题" fullWidth multiline minRows={2} value={question} disabled={busy || !loaded}
                        onChange={e => { last.current = undefined; setQuestion(e.target.value); setResponse(undefined); setNetworkError(false); }} />
                    <Button type="submit" variant="contained" disabled={busy || !loaded || response?.state === 'running' || !question.trim() || !catalog} sx={{ mt: 1 }}>
                        {busy ? <><CircularProgress size={16} sx={{ mr: 1 }} />正在分析…</> : networkError ? '查询同一请求' : '开始分析'}
                    </Button>
                    {['failed', 'interrupted'].includes(response?.state || '') && <Button type="button" disabled={busy} sx={{ mt: 1, ml: 2 }}
                        onClick={event => {
                            last.current = { question, id: crypto.randomUUID(), parentId: last.current?.parentId || parent?.node_id };
                            void submit(event);
                        }}>
                        故障已排除，发起新请求
                    </Button>}
                </Box>
                {busy && <Alert severity="info">正在读取已绑定条件的结果，请勿重复提交。</Alert>}
                {networkError && <Alert severity="error">连接中断或等待超时，服务器可能仍在处理。可查询同一请求；不会自动新建任务。</Alert>}
                {response ? <EcommerceResults response={response} /> : !busy && !networkError && <Paper variant="outlined" sx={{ p: 4, textAlign: 'center' }}>
                    <Typography color="text.secondary">提交问题后，这里将显示已核验的指标、结果表和图表。</Typography>
                </Paper>}
            </Stack>
        </Box>
    </Box>;
}
