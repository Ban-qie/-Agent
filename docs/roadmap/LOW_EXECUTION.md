# 小步执行协议（V2—V4共用）

本协议配合22张任务卡使用，不新增业务范围。目标是让gpt-6-astra low有足够明确的输入、步骤和判定标准；不能保证任何任务仅靠某个思考强度一定完成。所有卡先读本协议，遇到下述升级条件必须暂停。

## 1. 每次只做一个可验收步骤

1. 读取README、当前卡、上一卡handoff；`git status --short`，记录已有差异。不要全库扫描、重装依赖或启动付费campaign。
2. 找出当前卡“先读”函数及测试；先运行相关已有检查或使用同指纹已通过证据，写下缺口。已有实现足够时只记录复用，不为了任务存在而重构。
3. 卡内a/b/c为顺序子步骤，不是同时开工的清单。先定schema/纯逻辑，再接服务，最后页面；每步最多5个主要业务文件，超出就进一步拆分并列出输入/输出/测试。
4. 一次只修一个根因；测试应验证外部行为，不能复制实现或降低断言。新schema/错误码先写一个正常例和一个反例再接下一层。
5. 验收通过→记录短交接→继续已授权下一步；没有新信息不重复跑相同检查。通过卡勾选需本卡所有子步骤及前置依赖都通过。
6. 新文件均标“拟新增”，实施时才创建；优先放E与T，避免污染上游通用模块。文档中的建议名称可等价调整一次，并在core-release记录映射，后续沿用。

## 2. 明确的失败计数与人工升级

- 初次复现失败是基线，不算修复尝试；“一次修复尝试”=记录根因假设、完成针对性代码修改、跑同一验收反例。
- **同一阻塞连续两次有依据的修复仍未通过，立即暂停该卡及依赖任务，申请人工把思考强度从low调至medium。** 不发起第三轮试改、不通过新增prompt/权限/预算绕过；medium仍两次失败时再申请更高强度。
- 如果尚未尝试就发现无法说明认证信任链、账目迁移守恒、并发竞态或备份回滚安全性，可以提前暂停申请调整，不必为了凑次数冒险。
- 环境/网络/凭据缺失单独记阻塞，不反复调用模型验证同一外部故障；说明缺少的输入。调整思考强度不能替代服务器/凭据/用户业务决策。
- 暂停前写 `docs/verification/<ID>/handoff.md`：复现命令、失败断言、两次假设/补丁、日志路径、当前差异、调用增量及账目hash、未处理风险、恢复第一步。保留所有失败文件；不得reset、删除锁、清账、扩大权限或覆盖旧节点。
- 向用户简短说明：“<ID>在<验收点>连续两次修复未通过，已保留<交接路径>。请将思考强度调整为medium后回复继续<ID>。”用户明确调整后才恢复，不自行切模型或启动更高强度子Agent。
- 计数跨会话保留，不能换线程/重启就归零。仅当原阻塞确已验收通过才关闭该计数。

## 3. 通用契约与实施顺序

以现有代码为准，未修改的V0/V1协议继续兼容；新字段只在明确需要时增加。

| 对象 | 必须先冻结的内容 |
| --- | --- |
| 可信身份 | 服务端验证的subject/provider；客户端owner/header不能覆盖；多用户模式无匿名fallback |
| 工作区/节点 | 服务端owner/workspace、node/parent、状态、规范条件、快照/指标版本、完整旧响应；未知旧版本拒绝破坏性迁移 |
| 幂等 | 唯一键=(owner, workspace, request_id)；另存input_fingerprint并比较，**不能把fingerprint放进唯一键后允许同键不同输入重复创建** |
| 任务 | task_id、owner/workspace、request_id、input_fingerprint、status、version、lease_owner/expiry、cancel_requested、时间与错误/结果引用 |
| 预算 | 保留历史总额/未知预留，新记录带owner/workspace/task；同一reservation只能结算一次，重复回调不重复计费 |
| 错误 | 可显示code/message + request/task ID；JSON结构稳定，无原始异常/密钥/内部路径；现有业务422不伪装成200成功 |

V3-S01先定存储及认证方案；S03先用repository接口/fake验证授权策略，S04才实现真实持久化，不能提前假设owner表已存在。S06接原子任务；S07接真实原子配额，S08接取消/恢复。S02—S08仅在隔离开发环境实现，**S09之前多用户分析入口不得对外开放**。

建议最小模型：每用户一个私有工作区，公共只读样例快照，单实例；先不做队列，满载明确拒绝。后台运行只负责执行同一业务图，不是新增Agent编排。S06需保证取消/状态查询能被独立HTTP请求及时处理，不占住唯一服务线程。

任务建议状态：accepted→running→success/empty_result/waiting_clarification/failed/cancelled/interrupted。cancel_requested可独立标记，已终态不可改；“澄清后补问”是有明确父条件的新请求，不重开旧终态。已有partial需显式映射为失败但保留结果，禁止丢失。执行器持有lease token和version；过期旧worker不能覆盖新状态，未知收费步骤不自动重放。

## 4. 代码核对事实与默认边界

已在2026-09-18读取核对，实施前确认是否变化：

- E/`policy.py:install_profile`要求loopback/local身份，API白名单和8192字节请求上限；多用户要新增明确profile分支，不能删除限制或通配开放所有auth/API路由。
- E/`v1_service.py:analyze_v1`校验request/parent ID为8—64字符，问题UTF-8最多2048字节；保留这些已有上限。空白、异常Unicode编码等缺口需测试，不能只数JS字符串长度。
- E/`v1_workspace.py:V1WorkspaceStore`及E/`executor.py:MetricExecutor.execute`要求local身份；当前workspace最多128节点/20MiB，执行审计另有限额，迁移时分别处理，不能无限增长或串用户。
- E/`budget.py:UsageLedger`为累计文件账目及排他文件锁；多用户原子预留需要真实存储/进程验证。当前项目上限10元、每调用保守预留0.02元，V1最多3调用/任务、输出768token、任务60秒；不要用新用户重置历史支出。
- E/`process_limits.py:constrain_process`只支持Windows Job Object（worker默认512MiB），其他OS明确拒绝。选Linux必须新增并实测等效内存/超时/子进程清理，不可跳过该调用上线；建议作为V4-S01.a独立步骤。
- `devtools/run_local.py:configure_offline`和现有run_ecommerce面向本机开发；不可直接用来生产启动或真实认证。OIDC验证fixture在离线配置之后显式注入测试provider，不访问真实IdP。
- `devtools/v1_regression.py`未覆盖整个auth目录；新增OIDC/多用户测试必须显式加入阶段命令或更新回归入口，不能用“旧315项通过”代替新增功能验收。

上限/时间基于现有契约，服务容量和部署阈值在V3-S01冻结。建议首轮验证用两账号、每用户1运行任务、全局2运行任务、无等待队列；这是待实测建议，不是性能承诺。生产HTTP线程/进程需留出状态/取消/健康请求容量。

## 5. 离线命令模板

在项目根目录PowerShell使用项目.venv；不要用系统Python，也不要用Linux heredoc。下列命令是执行模板，本次规划不运行测试。

**T：相关后端**，将files替换为本卡列出的已有测试及实际新增测试；每次唯一临时目录：

```powershell
.\.venv\Scripts\python.exe -c "from devtools.run_local import configure_offline, ROOT; configure_offline(); import pytest, uuid; files=['tests/backend/ecommerce/test_v1_service.py']; raise SystemExit(pytest.main(files+['-q','-p','no:cacheprovider','--basetemp',str(ROOT/'.local'/('pytest-'+uuid.uuid4().hex))]))"
```

测试必须使用tmp_path/临时存储、stub模型和假凭据，注入fixture不得写真实用户工作区或真实账目。针对provider的HTTP调用mock；不得把真实token粘进命令。

**F：前端**：`node node_modules/vitest/vitest.mjs run tests/frontend/ecommerce.test.tsx`；新增前端测试不在该文件时显式追加；有前端改动再跑 `node node_modules/typescript/bin/tsc --noEmit`、`node node_modules/vite/bin/vite.js build`。

**R：阶段后端回归**：使用README命令，stage值需唯一如V2-S06-attempt1；脚本当前同stage会覆盖报告，重跑必须递增attempt。改动涉及auth时追加实际provider测试，未来新增跨进程/部署测试也必须加入。同一业务指纹已通过且无后续改动时不重复执行。

**D：文档/配置静态检查**：`git diff --check`、编号/依赖/链接核对；配置另用所选服务的validate/check命令，不启动付费模型。

**B：浏览器/HTTP行为**：参考现有 `devtools/v1_campaign_browser.cjs` 的选择器与断言，但新多用户测试使用独立runtime/证据目录、两浏览器context、真实服务或清楚标明stub。旧campaign已耗尽且面向local，不得直接重新运行它获得新调用额度。只有明确标记的live步骤启用真实Qwen。

Shell逐条执行，上一命令失败先读退出码和日志，不用分号串成失败后仍继续付费的链路。

## 6. 通用验收记录

每条验收记录至少有case_id、输入/故障（脱敏）、预期HTTP/业务状态、实际结果、模型/工具调用次数、持久化变化、后续正常请求结果。不是每卡都要建设报告器：现有pytest/JSON/截图足够时直接使用。

- 单元测试优先写合成数据，网络故障mock；竞争测试用barrier/event协调，断言数据库/账目及副作用次数，不靠随机sleep。
- 校验非法请求/越权时必须断言模型和执行工具调用均为0；可合法澄清的自然语言问题按既有模型预算判定，不承诺所有语义歧义都0调用。
- 崩溃/磁盘满在独立进程和临时目录模拟，不能杀用户原服务或填满实际磁盘。恢复前记录文件hash，恢复后检查身份、数值与账目。
- 核心样例：A“比较2018年2月与2018年1月销售额、订单数、客单价”→“按地区分组销售金额减少最多前3”→“地区SP按日显示”；回原根节点“按地区比较订单数”。B独立“分析2018年1月销售额”；两账号历史不互见。
- “销量”“最新两个完整月”、2020年覆盖外、合法空期、非法事实引用分别保持歧义/覆盖外/空/失败含义，不因测试方便改为成功。

每张卡的“下一步”只在本卡通过时执行；有依赖阻塞就保存交接。思考强度升级不授权扩大项目范围、额度或公网权限。
