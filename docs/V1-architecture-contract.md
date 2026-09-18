# 当前实现补充：Qwen 多 Agent 协作

用户已确认 medium，要求补全真实模型协作。当前生产 V1 路由使用 Planner → Reviewer → 固定选源/查询生成/程序校验/执行 → Interpreter → 固定制图。三个 Agent 分别调用 Qwen，Reviewer 可在执行前否决；同任务共享三调用上限和原费用账目。旧确定性函数仅作离线测试基线，生产失败不回退冒充成功。详见 [V1_MODEL_TEAM.md](V1_MODEL_TEAM.md)。以下保留先前设计和 v0.2.0 历史边界，不将旧验收自动视为当前验收。

# V1 LangGraph 迁移边界与契约

状态：保留 V1-01 至 V1-05 设计基线；2026-09-18 已完成 V1-14 前端接入与 V1-15 验收。真实 LangGraph 业务图已接入；V1-16 按 low 固化为本地 v0.2.0，不远端发布。

## 基线

- V0 基线：标签 `v0.1.0`，提交 `700b2c0ee8d9d0fb0a6e6137c1158d24768778ca`。
- 底座：Data Formulator 0.8b1，继续使用 Python/Flask、React/TypeScript、Parquet/DuckDB 和本地单用户工作区。
- 既有模型配置继续使用百炼 `qwen-flash`，V0 对照调用它。当前 V1 的七个业务节点是确定性处理器，不调用模型；不能将图节点数量称为多个模型 Agent 的协作。运行时模型与 Codex 的代码思考强度是两个独立配置。
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

## 历史阶段入口（保留设计演进，不作为当前恢复点）

V1-06/07 已搭建 `langgraph==1.2.11` 的单顶层图骨架，并提供 `ECOMMERCE_ANALYSIS_ORCHESTRATOR` 选择器。默认值为 `v0`，因此现有 API 仍走 V0 `run_analysis`；显式选择 `v1` 才进入骨架入口。当前骨架只记录节点遍历，不发布业务结果，真实节点处理留给 V1-08 以后。

进入 V1-08 前，需保持本文件、`v1_contracts.py` 和 `v1_graph.py` 的静态检查，并将当前 V0 回归结果作为对照样本。

## 阶段强度衔接规则

- 每个阶段完成后先执行该阶段验收并记录结果，再确定下一阶段建议的 Codex 思考强度。
- 如果下一阶段与当前阶段使用相同强度，验收通过后直接进入下一阶段，不重复询问人工确认。
- 如果下一阶段需要改变强度，完成当前阶段后暂停，明确给出下一阶段强度，收到人工调整确认后再执行。
- 未通过验收时保持当前强度优先修复，不进入下一阶段；不得以阶段衔接规则跳过失败项。

## V1-14/15 已验收的实际边界

- `/api/ecommerce/analyze` 和只读 `/workspace` 继续共用受限路由，服务端显式 `ECOMMERCE_ANALYSIS_ORCHESTRATOR=v1` 选择 V1；工作区响应增加 `orchestrator: v1`。V0 默认、存储格式、预算客户端和回退入口保留。
- V1 节点保存父节点、问题、规范化条件、完整响应与图表规格。前端显式选择父分析后追问；独立分析不继承父条件。重复提交复用原请求及父节点，失败新请求重试仍保留原父节点；读取和刷新不运行图或模型。
- 每请求 OS lease 区分活跃任务与进程退出。活跃任务读取保持 running；进程退出后只标记 interrupted，不复算、不重开原 ID、不自动删旧 execution/budget 锁。终态禁止重写，父引用必须指向更早节点。
- 未识别条件、多于两个期间、冲突分组/排序、重复 Top N 和不明确月份范围进入澄清。澄清保留已有父条件；独立问题若尚未形成完整条件，用户需补交完整问题。
- 排序/Top N 先请求最多 200 个受限分组，只有完整分组集合才排名；超过上限则明确失败，不把截断前缀称为全局排名。“增减最多”要求两期，使用当前减基准的数值差额；缺少可比值保持不适用。金额/订单均价用确定性精确数值排序，图表和表格使用相同结果。比较排名两期展示相同选中分组；无排序的 Top N 按分组键顺序。
- 解释或制图节点失败时保留已验证结果并整体 failed，不能宣称完整成功。前端区分 failed 部分结果与 partial；覆盖外保留原始结果状态和提示，不裁剪日期。
- 分发摘要见 [RELEASE_V1.md](RELEASE_V1.md)，详细阶段报告只保留在本机。V1 是固定快照、本机单用户、有界确定性业务图；未提供自由问数、多模型推理、自动续跑或生产上线验收。
