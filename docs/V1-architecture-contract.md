# V1 LangGraph 迁移边界与契约

状态：V1-01 至 V1-05 设计基线。本文不表示 LangGraph 运行时已经接入。

## 基线

- V0 基线：标签 `v0.1.0`，提交 `700b2c0ee8d9d0fb0a6e6137c1158d24768778ca`。
- 底座：Data Formulator 0.8b1，继续使用 Python/Flask、React/TypeScript、Parquet/DuckDB 和本地单用户工作区。
- 运行时模型：继续使用百炼 `qwen-flash`。它与 Codex 的代码思考强度是两个独立配置。
- V1 只新增一个顶层 LangGraph。V0 单 Agent 保留为可复现回退基线。

## 图状态与历史边界

LangGraph 状态只描述一次运行；Data Threads/工作区节点保存用户可见历史。两者通过 `run_id`、`node_id` 和 `parent_node_id` 关联。

最小状态字段如下：

```json
{
  "state_version": 1,
  "run_id": "run_...",
  "workspace_id": "ecommerce-v0",
  "snapshot_id": "64位十六进制快照指纹",
  "metric_version": "...",
  "node_id": "node_...",
  "parent_node_id": null,
  "user_question": "...",
  "normalized_conditions": {},
  "plan": {},
  "selected_sources": [],
  "query": {},
  "verified_result": {},
  "chart_spec": {},
  "explanation": {},
  "status": "running",
  "error": null,
  "budget": {},
  "trace": []
}
```

允许的终态为 `success`、`empty_result`、`partial`、`failed` 和 `interrupted`；`waiting_clarification` 不得触发查询执行。状态转换表在 `v1_contracts.py` 中冻结：运行中可以进入澄清或任一终态，澄清后只能重新运行或被中断，终态不可重新打开（中断任务由用户发起新运行）。刷新页面不得重新调用模型，服务重启后未完成的运行标为 `interrupted`。

## 角色与交接

角色契约已在 `data_formulator.ecommerce.v1_contracts` 中冻结：

1. `planner`：解析问题、规范化条件和分析计划；它是唯一可提出条件变更的角色。
2. `source_selector`：选择固定订单粒度数据和已确认关系。
3. `query_generator`：生成结构化查询请求。
4. `query_validator`：程序执行只读、白名单、结果规模、超时和条件一致性检查。
5. `executor`：调用受限指标工具并分类结果状态。
6. `interpreter`：只解释已验证结果，不生成数字或因果结论。
7. `chart_planner`：生成结构化图表规格。

计算、权限、指标口径和安全校验由确定性程序掌握；角色数量不因“多 Agent”目标而无限增加。

## V1 不在本阶段引入

- RAG、向量数据库或自动关系检索；固定订单粒度数据先使用已确认映射。
- Redis、PostgreSQL、令牌轮换和多用户授权。
- 商品类别、支付、退款、利润、广告、库存、预测等扩展指标。
- 第二套顶层 Agent 循环或任意模型生成的 SQL/React/HTML。

## 下一阶段入口

V1-06/07 已搭建 `langgraph==1.2.11` 的单顶层图骨架，并提供 `ECOMMERCE_ANALYSIS_ORCHESTRATOR` 选择器。默认值为 `v0`，因此现有 API 仍走 V0 `run_analysis`；显式选择 `v1` 才进入骨架入口。当前骨架只记录节点遍历，不发布业务结果，真实节点处理留给 V1-08 以后。

进入 V1-08 前，需保持本文件、`v1_contracts.py` 和 `v1_graph.py` 的静态检查，并将当前 V0 回归结果作为对照样本。
