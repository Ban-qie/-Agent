# 后续开发 TODO：先关闭 V1 验收缺口，再进入 V2—V5

更新：2026-09-18。工程：`C:\Users\Kobe Bryant\Agent Project`。依据：桌面开发文档第 17.7、21.2、21.3 节及本轮实际复核。本文是新任务入口，历史“V1 已完成”不能代替当前工作树验收。

**最新恢复点：V1-R01—R08通过，下一任务V2-01。** 原R06六个失败完整保留；修复后独立20样本全部成功，另有2个定点复验成功。后端315项、前端15项及类型/构建通过；累计247次调用。见 [最新摘要](../validation/V1-team-summary.json)、[R06-fix交接](../verification/V1-R06-fix/handoff.md)、[R07交接](../verification/V1-R07/handoff.md)。本地固定标签为 `v0.2.1-v1-closure`；qwen-flash为provider别名，不能声称固定了provider内部权重版本。

## 本轮任务状态

- [x] 任务一的复核工作：检查实际代码、原始验收记录、固定提交与未提交差异；重新运行相关后端、前端、类型检查和构建；补测真实模型链路。
- [x] 任务一的达标出口：V1收尾通过，真实演示、独立复算、恢复、V0对照和本地固定提交齐备。新V1与旧V0运行时段不同，不声称性能优势。详见 [V1 收尾任务](V1-closure.md)。
- [x] 任务二：完成后续功能与工程工作的小任务拆分，包括前置条件、文件入口、实施范围、验收及停止条件。以下是待执行计划，不代表已经实现。

初次复核的289项及第三场景失败作为历史保留，见 [V1 初次复核报告](V1-review.md)；最新315项及修复后证据以上述摘要为准。

## 顺序与阶段出口

| 顺序 | 任务清单 | 进入条件 | 出口 |
| --- | --- | --- | --- |
| 1 | [V1-R01—R08](V1-closure.md) | 当前恢复点 | 真实三 Agent 完整演示、独立复算、V0 对照、P95/费用和固定版本证据齐备 |
| 2 | [V2-01—V2-22](V2.md) | V1-R08 通过 | 语义版本、分层校验、来源证据、评测可复算；关系检索按门槛启用或有依据跳过 |
| 3 | [V3-01—V3-24](V3.md) | V2-22 通过 | 商品/类别、状态、支付逐模块验收，跨粒度与原有任务兼容；导出按实际需求启用 |
| 4 | [V4-01—V4-26](V4.md) | V3-24 通过 | 身份与对象授权、隔离、并发、恢复、部署目标全部验收 |
| 5 | [V5-01—V5-19](V5.md) | V4-26 通过且有真实新增需求 | 一种新增数据源/刷新可复现；后台任务及通知分别按需求启用 |

共 99 张任务卡。编号顺序为默认执行顺序；每张卡写了额外依赖。`必做`必须通过；`推荐`为本计划建议加入、仍须经过该模块需求卡；`条件`不满足启用条件时记录 `N/A + 原因 + 证据`，不能伪装成已实现。阶段出口只要求必做及已启用模块通过。

## 功能取舍清单

| 功能 | 安排 | 当前决策 |
| --- | --- | --- |
| 真实 Qwen 多角色、分支、基础趋势/比较、表格降级、保存恢复 | V1 已有，R 系列补证据 | 复用，不重新造顶层编排 |
| 指标/术语/时间/状态的版本化、旧节点口径锁定 | V2-02—07 | 必做 |
| 独立参考集、错误分类、分层结果校验 | V2-08—11、20 | 必做 |
| 查询/条件/结果/快照/结论来源关联与证据面板 | V2-12—15 | 必做 |
| 可信关系目录、关系检索、一跳扩表 | V2-16—18 | 只有真实多表失败证据时启用；无向量库默认要求 |
| AST 安全检查、有限修复 | V2-19 | 固定指标路径保持只读；有 SQL 执行路径才接 AST，修复默认 0 次 |
| 同比/环比、自定义比较 | V2-21 | 明确区间的自定义比较复用已有功能；自动同比/环比须证明覆盖完整 |
| 地区差额贡献、订单数×客单价变化拆解 | V3-20 | 推荐；仅计算贡献，不称为原因 |
| 商品类别/商品标识、排行、销售结构、地区×类别 | V3-02—10 | 推荐逐项引入，有独立数据和口径验收 |
| 状态分布/取消状态 | V3-11—13 | 推荐；独立指标，不放宽原 delivered 三指标 |
| 支付方式/支付记录金额分布 | V3-14—16 | 推荐；不等同商品金额、净收入或退款 |
| CSV/结构化结果导出 | V3-17—18 | 有结果交付需求才启用；图片导出单独判断 |
| 分析收藏、简单异常线索 | V3-19、21 | 条件；不自动扩展复杂报表/预测 |
| 服务端身份、对象授权与隔离 | V4-03—11 | 受控上线必做，优先审查已有 Python 认证模块 |
| 并发/限流/幂等/取消/断连恢复、日志/健康/备份 | V4-12—21 | 必做 |
| 工作区角色、只读分享/邀请 | V4-22 | 条件，读写权限矩阵先行 |
| PostgreSQL、Redis、任务队列、进度订阅、检查点 | V4-02、15—18 | 按实测需求决策，不预置全部组件 |
| 新连接器/受限 CSV、快照刷新、质量检查 | V5-01—12 | 先选一种实际需求，再实现 |
| 模板/周期报告/异常通知/MCP | V5-13—18 | 分别启用，无需求就跳过 |
| 利润、退款归因、广告归因、库存决策、预测 | 不进入当前路线 | 缺少数据和定义，不能用现有字段假造 |

## 给后续 GPT-6-astra low 的执行规则

1. 每次只认领一个任务 ID，先读本页、[交接模板](HANDOFF.md)、该任务卡及列出的代码。模型名称和 low 是使用偏好，不需要自动改设置，也不沿用历史“必须切换 high 才继续”的阶段安排。
2. 一张卡只交付一个可单独验证的能力。若预计涉及超过 5 个主要业务文件，先拆成 `ID.a/ID.b`，各写输入、输出与验收，再实施；不把“实现整个 V4”作为一次工作单元。
3. 先检查 `git status --short`，记录已存在差异。本轮开始时已有 4 个未提交文件，不能覆盖、删除或混同为本轮新增贡献。旧标签 `v0.2.0` 是确定性 V1；当前真实三 Agent 基础提交为 `c3f4abec`。
4. 先用离线或合成 fixture 验证逻辑，再按已冻结预算做必要真实调用。现有 `qwen-api-key` 仅服务端读取；账目 `.local/verification/qwen-usage.json` 全程续用。总预算是全项目累计 10 元，未知 usage 继续保守预留。
5. 任务通过后记录改动文件、命令/退出码、证据路径、原始失败、费用增量和下一任务。在已授权阶段内按顺序继续；不要每个小任务都要求重新确认。需要真实外部身份、部署目标或通知收件人时才等待必要输入。
6. 数值错误、条件丢失、授权越界、旧节点不可恢复时禁止进入下一卡。一次修复只针对根因；失败两次后先缩小复现并拆任务，不反复扩大提示词、权限或调用预算。测试断言不能为了通过而降低标准。
7. 每阶段出口要固定提交或标签，并记录**实际代码指纹**。不要用一次旧测试覆盖后来代码变化；未跑的检查必须写“未执行”。不以 README、测试存在或旧版本通过来代替当前验收。
8. 本计划不自动授权远端推送、公开部署、向他人发通知或公开数据；本地设计、可逆实现、复核与文档更新可按用户指定阶段推进。无真实需求的条件模块记录跳过即可。

## 代码定位与复用

以下简称在任务卡中使用，所有路径均相对工程根目录。标注“拟新增”的文件尚不存在，应先按任务创建。

| 简称 | 当前路径与用途 |
| --- | --- |
| E | `py-src/data_formulator/ecommerce/`：受限电商业务；优先在此增加领域模块 |
| F | `src/views/EcommerceWorkspace.tsx`、`src/views/ecommerce.ts`：当前前端/响应与恢复适配 |
| API | `py-src/data_formulator/routes/ecommerce.py`：受限路由 |
| T | `tests/backend/ecommerce/`：相关后端测试 |
| FT | `tests/frontend/ecommerce.test.tsx`：当前前端测试 |
| 工具/指标 | E 下 `contracts.py`、`metrics.py`、`executor.py`、`worker.py`、`snapshot.py` |
| 语义/编排 | E 下 `v1_normalization.py`、`v1_agents.py`、`v1_runtime.py`、`v1_graph.py` |
| 状态 | E 下 `v1_contracts.py`、`v1_service.py`、`v1_workspace.py`；V0 使用 `workspace_state.py` |
| 认证/存储 | `py-src/data_formulator/auth/`、`datalake/`、`workspace_factory.py`；已有能力需核验接入 |
| 边界/预算 | E 下 `policy.py`、`process_limits.py`、`budget.py`；不要另造一套付费账目 |
| 私有证据 | `docs/verification/<任务ID>/`、`.local/`，已经被 Git 忽略 |
| 可分发摘要 | `docs/roadmap/`、`docs/validation/`；不得放密钥、逐订单记录或私有账目 |

## 通用验收命令

均在项目根目录 PowerShell 执行，复用已有 `.venv` / `node_modules`。文档类小任务用链接/编号/差异检查即可，不为文案编写镜像测试。

```powershell
git status --short
git diff --check
# 完整相关离线后端回归；将任务ID替换为本次真实任务编号，防止覆盖旧报告。
.\.venv\Scripts\python.exe -c "from devtools.v1_regression import main; raise SystemExit(main('任务ID'))"
# 前端变化时执行：
node node_modules/vitest/vitest.mjs run tests/frontend/ecommerce.test.tsx
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vite/bin/vite.js build
```

阶段内优先运行改动相关测试；阶段出口再运行完整相关回归。新增测试若不在 `tests/backend/ecommerce/`，同步更新回归入口或显式列出命令，避免“全绿”遗漏新模块。真实浏览器与付费验证先读对应脚本；旧 `v1_check` 面向确定性旧 V1，不能直接用作当前模型成功验收。

## 当前接续提示词

```text
Read docs/roadmap/README.md and V1-closure.md. V1-R01 through R08 passed.
Start V2-01 in docs/roadmap/V2.md, one task card at a time.
Preserve existing modifications, failure evidence, old nodes and cumulative ledger (247 calls).
The old 193-call campaign and new 247-call campaign are exhausted; do not silently reset them.
Offline work first; freeze a justified budget before further paid acceptance within the project limit.
If another reasoning-effort adjustment is necessary, save progress and request manual adjustment.
```
