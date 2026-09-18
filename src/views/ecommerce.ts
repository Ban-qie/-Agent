export interface Values { order_count: number; sales_amount: string; average_order_amount: string | null }
export interface Period { start: string; end: string }
export interface Conditions {
    current: Period; baseline?: Period | null; regions: string[]; group_by: string | null;
    snapshot_id: string; metric_version: string; request_id: string;
    metrics?: (keyof Values)[]; sort?: { field: keyof Values; direction: string; basis?: string } | null; top_n?: number | null;
}
export interface MetricResult {
    state: string; values?: Values | null; groups?: (Values & { key: string })[];
    current?: MetricResult; baseline?: MetricResult;
    changes?: Record<keyof Values, { absolute: string | null; percent: string | null }> | null;
    total_groups?: number; truncated?: boolean; conditions?: Conditions; result_id?: string;
    provenance?: Record<string, string>; error?: { code: string };
    ranking?: { key: string; absolute: string | null }[];
}
export interface AnalysisResponse {
    state: string; question?: string; summary?: string; conditions?: Conditions;
    result?: MetricResult; partial_result?: MetricResult; error?: { code: string };
    limitations?: string[]; agent_completed?: boolean;
    explanation?: { summary?: string }; chart_spec?: ChartPlan;
}
export interface ChartPlan { type: 'table' | 'bar' | 'line'; dimension?: string; measures?: (keyof Values)[]; source?: string }
export interface AnalysisNode {
    node_id: string; request_id: string; user_question: string; response: AnalysisResponse;
    chart: { version: number; kind: string }; created_at: string; updated_at: string;
    parent_node_id?: string | null;
}
export const metricLabels: Record<keyof Values, string> = { sales_amount: '商品金额', order_count: '订单数', average_order_amount: '客单价' };

export function workspaceNodes(state: any): AnalysisNode[] {
    if (state.schema_version !== 1 || !Array.isArray(state.nodes)) throw new Error('Invalid workspace');
    if (state.orchestrator !== 'v1') return state.nodes;
    return state.nodes.map((node: any) => {
        const status = node.status === 'waiting_clarification' ? 'clarification_required' : node.status;
        const response = { ...node.result, state: status,
            conditions: node.result?.conditions || (node.conditions?.current ? node.conditions : undefined),
            error: node.result?.error || node.error, chart_spec: node.result?.chart_spec || node.chart_spec };
        return { ...node, request_id: node.node_id, user_question: node.question, response };
    });
}
export interface ResultRow extends Values { label: string; period: string }
export const examples = [
    '比较2018年2月与2018年1月销售额、订单数、客单价',
    '分析2018年1月销售额、订单数、客单价按地区分组',
    '分析2016年11月销售额、订单数、客单价',
];
export const formatAmount = (value: string | null | undefined) => value == null ? '—（不适用）' :
    Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const periodLabel = (period: Period) => `${period.start} ≤ 下单日期 < ${period.end}`;

export function resultRows(result: MetricResult): ResultRow[] {
    const periods: [string, MetricResult][] = result.current && result.baseline ?
        [['当前期', result.current], ['基准期', result.baseline]] : [['当前期', result]];
    return periods.flatMap(([period, summary]) => {
        if (!summary.values || summary.state === 'outside_coverage' || summary.state === 'empty_result') return [];
        const groups = summary.groups || [];
        if (!groups.length && result.conditions?.group_by) return [];
        return groups.length ? groups.map(group => ({ ...group, label: group.key === 'UNKNOWN' ? '未知地区' : group.key, period })) :
            [{ ...summary.values, label: period, period }];
    });
}

export function salesSpec(rows: ResultRow[]) {
    return {
        $schema: 'https://vega.github.io/schema/vega-lite/v6.json',
        description: '商品金额柱状图，与结果表使用相同数据',
        width: 'container' as const, height: 260,
        data: { values: rows.map(row => ({ ...row, amount: Number(row.sales_amount) })) },
        mark: { type: 'bar' as const, tooltip: true },
        encoding: {
            x: { field: 'label', type: 'nominal' as const, title: '期间 / 分组', sort: null },
            y: { field: 'amount', type: 'quantitative' as const, title: '商品金额（源数据金额单位）', scale: { zero: true } },
            color: { field: 'period', type: 'nominal' as const, title: '期间' },
            xOffset: { field: 'period' },
            tooltip: [{ field: 'label', title: '期间 / 分组' }, { field: 'period', title: '期间' },
                { field: 'sales_amount', title: '商品金额' }, { field: 'order_count', title: '订单数' }],
        },
    };
}

export function plannedSpec(rows: ResultRow[], plan: ChartPlan, measure: keyof Values) {
    const spec = salesSpec(rows);
    return { ...spec, description: `${metricLabels[measure]}图，与结果表使用相同数据`,
        data: { values: rows.map(row => ({ ...row, amount: row[measure] == null ? null : Number(row[measure]) })) },
        mark: { type: plan.type === 'line' ? 'line' as const : 'bar' as const, tooltip: true },
        encoding: { ...spec.encoding,
            y: { ...spec.encoding.y, title: metricLabels[measure] },
            ...(plan.type === 'line' ? { xOffset: undefined } : {}),
        } };
}

export function errorMessage(code?: string) {
    const messages: Record<string, string> = {
        MODEL_DISABLED: '服务端尚未启用分析模型，请由本机管理员启用 Qwen 后再分析。',
        BUDGET_EXHAUSTED: '模型累计预算已用尽，请检查用量账目。',
        BUDGET_UNAVAILABLE: '用量账目不可用，分析已停止。',
        BUSY: '已有任务运行或等待中断检查，请稍后查询同一请求。',
        INTERRUPTED: '上次任务中断，需检查后才能继续。',
        REQUEST_CONFLICT: '请求编号已用于其他条件，请重新发起分析。',
        MODEL_FAILED: '模型请求失败，未自动重试或修改条件。',
        ANALYSIS_TIMEOUT: '分析超时，未修改日期或地区条件。',
        PARENT_NOT_FOUND: '父分析节点不可用，请刷新历史或开始独立分析。',
        RESOURCE_LIMIT: '工作区或结果规模达到限制，请检查保存状态。',
        INVALID_REQUEST: '分析请求无效，请检查问题后重新提交。',
    };
    return messages[code || ''] || '分析未完成。请检查输入条件或服务状态；没有自动重试。';
}
