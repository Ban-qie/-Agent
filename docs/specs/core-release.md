# 核心发布范围与验收

V2仅核心可靠性；尚未验收多用户或部署。基线a7b53dfd，V1业务manifest全部匹配（V2-S01/a-attempt1.json）。下表“已有且验证”仅指同指纹历史证据，V2新增边界仍需逐卡验收。

E=`py-src/data_formulator/ecommerce/`；T=`tests/backend/ecommerce/`；F=`src/views/EcommerceWorkspace.tsx`。

| 能力 | 当前文件/函数 | 测试/证据 | 缺口 | 后续卡 |
| --- | --- | --- | --- | --- |
| 明确期间三指标 | E/metrics.py，v1_normalization.py | T/test_metrics.py；R05独立CSV | 已有且验证；补手算边界核对 | S03a |
| 两期比较 | E/metrics.py | T/test_metrics.py；R06-fix 20/20 | 已有且验证；单侧空待核对 | S03a |
| 日/月趋势 | E/v1_normalization.py；v1_runtime.py | T/test_v1_group_transition.py；R04 day/month | 已有且验证 | S03b |
| 地区拆分/Top N | E/v1_runtime.executor_handler | T/test_v1_runtime.py；R05独立排序 | 已有且验证；同分待核对 | S03a |
| 连续追问/父条件 | E/v1_service.analyze_v1 | T/test_v1_normalization.py；R04 day | 已有且验证 | S03b |
| 澄清/销量歧义 | E/v1_agents.py | T/test_v1_agents.py；R04 clarify/quantity | 已有且验证 | S03c |
| 历史兄弟分支 | E/v1_workspace.py | T/test_v1_service.py；R04 branch | 已有且验证 | S03b |
| 图表/表格 | F；src/views/ecommerce.ts | tests/frontend/ecommerce.test.tsx；R04 390px/SVG | 已有且验证；缺字段白屏风险待反例 | S05a/b |
| 保存/刷新/重启 | E/v1_workspace.py；F | T/test_v1_workspace.py；R05 11节点/0 POST | 已有且验证；未知版本待核对 | S05c |
| 失败/部分结果/中断 | E/v1_graph.py；v1_service.py | T/test_v1_final.py；R05原失败节点 | 已有且验证；保存故障/deadline待核对 | S04 |
| V0回退 | E/orchestrator.py；workspace_state.py | R05 v0-restore，两结果/0 POST/0调用 | 已有且验证 | S06 |
| 指标版本/快照绑定 | E/metrics.py；contracts.parse_request | T/test_execution.py；R07 manifest | 直接复用固定版本和快照 | S03 |
| 合法事实白名单 | E/v1_agents.verified_facts/interpreter | T/test_v1_interpreter_output.py；R06-fix | 直接复用；拒绝未知/重复/超量 | S03c |
| 请求结构/不受信文本 | E/contracts.py；policy.py；routes/ecommerce.py | T/test_profile.py；test_v1_final.py | question孤立代理字符可能500；仅检查声明body大小；需反例 | S02 |
| 只读/工具白名单/资源 | E/executor.py；process_limits.py | T/test_execution.py；test_profile.py | 复用Windows Job限制；故障恢复需核对 | S04 |
| 累计预算/未知费用 | E/budget.py | T/test_budget.py；S01实读账目 | 复用10元上限；阻塞网络deadline需证明 | S04 |

原R06：V0 20/20、V1 14/20，六个失败原样保留；R06-fix：另外20/20成功，不能合并抹除失败。性能为历史本机小样本，非生产承诺。

当前账目只读核对为247次、已知估算0.04992810元、预留4.89元、未知usage 2笔；旧campaign额度耗尽。V2默认新增真实调用0，任何真实调用须先冻结独立小预算，不清账。

不支持/不实施：商品、支付、状态扩展、上传、自由代码、动态SQL、关系检索、自动同比环比等归V5未启用池。认证、跨用户隔离、原子配额与生产迁移属于V3/V4，本窗口不实施。

## S02a 冻结请求契约

- /analyze：对象，必需request_id、user_question；V1可选parent_node_id（null或ID），V0不接受parent。ID为8—64个ASCII字母/数字/_/-，parent不能等于request；问题为非空白字符串，UTF-8最多2048字节，孤立代理字符拒绝；保留原字符串，不静默修改语义。
- /query：仅request_id、snapshot_id、metric_version、operation、current、baseline、regions、group_by、limit。前五必需；快照64位小写hex；固定指标版本；summarize/compare；日期对象只允许start/end，YYYY-MM-DD左闭右开最多1096天；compare必有baseline；地区最多28个合法州码；分组null/region/day/month；limit整数1—200（bool拒绝）。
- 两入口body最多8192实际字节；未知键、null/数组、异常UTF-8、过深JSON与字段类型非法为400 INVALID_REQUEST；超body为413 RESOURCE_LIMIT。结构合法但语义不支持沿用clarification_required/原业务状态；业务失败422不改200。
- 应用仅能检查WSGI暴露的请求流，代理/服务器对伪造Content-Length及请求走私的完整处置由V4复验；有终止标记的流读取8193字节探测超限，绝不无界读取。

## 当前验收进度

- [x] S01a 工作树/manifest与账目核对
- [x] S01b 核心能力证据表
- [x] S01c 缺口归S02—S05，未支持功能归V5
- [x] S02a 冻结请求契约
- [x] S02b 非法结构与实际body上限拒绝（61项相关组、26项补充组通过；首轮失败保留）
- [x] S02c 同client错误后正常stub成功、非法输入不创建节点
- [x] S03a 手算/快照/日期/零分母/Top N同分（35项通过）
- [x] S03b 父条件/分组切换/兄弟分支（17项通过）
- [x] S03c 模型条件/引用边界（38项通过）
- [x] S04a 模型故障：27项通过，真实子进程补充7项通过；原22通过/1失败保留
- [x] S04b 执行/保存故障：59项通过，原25通过/1失败保留
- [x] S04c 资源恢复与阻塞网络总deadline：53项通过
- [x] S05a 异常响应样本：基线17通过/5失败保留
- [x] S05b 页面降级：22项通过，类型/构建通过
- [x] S05c 历史恢复：后端64项+版本4项；隔离服务实际重启恢复12节点/390px/0 POST/0新增usage
- [x] S06a 汇总证据：清单逐项对应S02—05和未变更的R05独立参考
- [x] S06b 出口完整回归：后端355通过；前端22通过，类型/构建退出0
- [x] S06c 摘要及本地恢复点：V2-core-summary.json；本地v0.3.0-v2-core（最终提交见私有release.json）

S04阻塞V2-S04-DEADLINE已在恢复后首次修复通过。新增E/model_transport.py与model_worker.py：父进程在请求前预留，独立进程只负责网络、不持有账目；Windows Job 512MiB/最多两个进程（含venv trampoline）；连接/读取timeout=min(10,剩余任务秒)，父进程绝对60秒任务截止控制阻塞网络并关闭整个Job，最多2秒回收；返回后再次检查绝对期限。请求通过stdin传入，凭据不在命令行/日志，响应上限512KiB。V0内部stream有界缓冲后返回；未知usage/超时保留预留，禁止自动重放。生产provider在远端是否仍计算不可由本地kill证明，故不撤销未知费用。模型提示/指标协议未变；本轮没有真实provider调用，不宣称新的真实模型成功率或性能。

保存失败映射：HTTP500 + state=failed/WORKSPACE_UNAVAILABLE，当前响应保留已计算result；磁盘故障期间不承诺持久化，恢复将running标interrupted，原ID不自动执行。前端保留本页结果并提示保存失败；缺字段/未知状态明确降级，图表同步或异步失败时表格可读。

矩阵中的“待核对”是S01起始缺口；其关闭证据分别是S03a手算/Top N、S04a/c期限/费用、S04b保存故障、S05b异常页面、S05c未知版本拒绝且原文件字节不变。数值参考R05及真实模型R06-fix仅复用未改变的计算/提示路径历史证据；不冒充本轮live验收。
