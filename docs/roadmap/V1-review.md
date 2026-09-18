# V1 当前目标复核报告

日期：2026-09-18。结论：**V1 已具备主要实现，当前版本尚未满足第 21.2 节的完整阶段出口。** 本轮验收发现可复现的流程断点和跨维度排序继承问题，因此不能直接进入 V2 实现。[收尾 TODO](V1-closure.md) 为下一执行入口；V2—V5 是预先拆好的待启用计划。

## 本次基线

- 分支 `v1/development`；HEAD `c3f4abecc33fa3bb17a4fca3e111277aca62439e`。本轮未提交/打标签/推送。
- `v0.2.0` 仍是旧确定性 V1；当前 production 入口已使用三个独立 Qwen Flash 调用的 Planner、Reviewer、Interpreter，与确定性查询/执行/图表节点组成一个 LangGraph。
- 开始时已有未提交改动：`docs/V1_MODEL_TEAM.md`、E/`v1_normalization.py`、T/`test_v1_agents.py`、T/`test_v1_normalization.py`，共39行，主要是销量歧义拒绝；原样保留。本轮没有修改这些业务文件。
- 运行快照、现有源码和依赖文件 SHA-256 记录于 `docs/verification/V1-readiness-6f8d69ec4d/manifest.json`。HEAD 不能单独代表本轮工作树。

## 目标与证据矩阵

| V1 要求 | 当前证据 | 结论 |
| --- | --- | --- |
| 一个顶层 LangGraph、职责与交接明确 | 当前代码、三角色真实 trace，planner→reviewer→固定工具→interpreter→chart | 已实现；角色之间的安全边界有离线测试 |
| 统一状态、运行/节点/父节点、快照/指标版本 | `v1_contracts.py` / service/workspace / 当前后端测试 | 相关回归通过；V2 再完善语义和来源证据 |
| 两期比较与地区差额排名 | 本轮真实 Edge 两个成功场景，各三次 Qwen 调用，同ID回放零新增调用 | 本轮通过 |
| 日/月趋势、连续追问与分支完整链路 | 日趋势工具执行成功，解释阶段失败；后续脚本因失败停止 | **当前完整链路未通过** |
| 条件继承不引入错误 | SP/期间/指标绑定正确；日维度继承地区差额排序，产生59条空差额排名 | **跨维度排序需修复/明确策略** |
| 澄清、空/失败/中断、只读/预算等边界 | 本轮289项相关后端回归通过；旧浏览器/模型团队证据可作历史参考 | 离线通过；当前完整浏览器补测尚未走完 |
| 刷新与实际重启恢复后继续追问 | 旧模型团队2场景有恢复记录；本轮恢复阶段未到达 | 不能将旧证据称为本轮全链路恢复 |
| 图表协议和降级 | 本轮比较表、地区柱图/差额表成功；日趋势失败无图 | 需完成当前日/月趋势及语义回归 |
| V0 基线与同条件数值比较 | V0路径仍保留；旧V1-15对照是2模型调用对0调用 | 保留实现；当前真实多Agent对照待补 |
| P95、费用、节点轨迹 | 轨迹/费用已记录；当前性能对照阶段未执行 | **P95及可比费用样本未完成** |
| 固定版本和回退说明 | 原V0与旧V1标签存在；当前业务修复仍未提交 | 收尾后须固定真实模型版工作树 |

## 本轮实际执行的检查

- `.\.venv\Scripts\python.exe -c "from devtools.v1_regression import main; raise SystemExit(main('V1-readiness-20260918'))"`：**289 passed**，一条既有 Flask-Session 弃用警告。覆盖电商、预算/Qwen探针及相关认证/工作区兼容；不是上游所有联网连接器测试。
- `node node_modules/vitest/vitest.mjs run tests/frontend/ecommerce.test.tsx --reporter=json --outputFile=.local/verification/v1-readiness-frontend.json`：**15 passed**。
- `node node_modules/typescript/bin/tsc --noEmit`：退出码0。
- `node node_modules/vite/bin/vite.js build`：退出码0，保留既有 eval/动态导入/大包警告。
- `.\.venv\Scripts\python.exe -m devtools.v1_readiness --live`：真实 Edge→受限 Flask→Qwen→LangGraph→固定工具，**退出码1**；不把失败写成验收通过。
- `.\.venv\Scripts\python.exe -m devtools.v1_readiness_reference`：重新读取三张原始CSV，以独立 stdlib/Decimal 验证已执行三个场景的总体、全部分组与变化值一致，地区差额排序一致；不导入生产指标/条件函数。数值正确不能抵消整体流程失败。

## 真实失败与边界

实际操作顺序：

1. `比较2018年2月与2018年1月销售额、订单数、客单价`：成功。
2. 从1追问 `按地区分组销售金额减少最多`：成功。
3. 从2追问 `地区SP按日显示销售额`：工具成功，Interpreter 报 `INVALID_AGENT_OUTPUT`，整体 failed，真实结果保留；chart_spec为空。

原始证据：`docs/verification/V1-readiness-6f8d69ec4d/demo.json`、`reference.json`、`manifest.json`、`summary.json`、服务日志与隔离工作区 `.local/V1-readiness-6f8d69ec4d/`。全部保留在被 Git 忽略的位置，不向公开仓库分发真实结果。

trace 已定位解释节点，但没有保存原始模型输出，**具体是 JSON/schema/事实引用哪种不合规仍待定位**，不能擅自断言“模型编造了某个ID”。失败场景的 sort 仍为 `{field: sales_amount, direction: asc, basis: change}`，daily keys跨两月互不重合，59条ranking.absolute均为null；这是额外的语义/展示缺口，须独立修复。

本轮脚本在首次失败后停止，没有继续重试或跑性能样本。新增验收脚本是可接续的基础，尚未到达的 restore/followup/bench 分支没有运行通过；需要先完成 V1-R04 的接续入口、顺序与全轮预算改善，再做下一次真实验收，不能不加区分整套反复重跑。

## 费用与恢复

- 本轮真实调用开始前账目已为 **53次**，与较早文档的51次不同；中间两次不归因于本轮，保留原记录。
- 本轮新增 **9次**，已知估算 **0.00160740元**，新增保守预留 **0.18元**；累计 **62次**，已知估算 **0.01501245元**，保守预留 **1.19元**。全项目预算仍为10元，估算不等于供应商结算。
- 原账目前缀完全保留；本轮结束指纹 `c65b41c50a3b1db4296ad9e68c05779bf018bc0c6abca67692d14d527e02c817`。没有清账、删除旧锁、重装依赖或修改原始数据。
- 脚本 summary 的 `ports_stopped:false` 是进程终止后立即检查的观测；稍后复查5173/5567均无监听。原summary不覆盖，最终停止状态单独记录；脚本已补等待释放端口，未为此再发模型请求。
- V0默认模式保留。继续开发前先读 [V1-R02](V1-closure.md)，不要把V2任务“已经有计划”当作“已获阶段通过”。

## 下一步

按R02定位解释输出→R03修复跨维度排序继承→R04补完浏览器链路→R05独立复算/恢复→R06可比P95与费用→R07安全封口→R08固定版本。后续功能设计已在 [总入口](README.md)拆好，但阶段出口之前仅作为待执行草案。
