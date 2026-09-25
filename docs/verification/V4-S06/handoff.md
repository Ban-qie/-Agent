# V4-S06c 真实 Qwen smoke 交接

## S06c / S06d 完成记录（2026-09-25）

- S06c 在单独授权的 0.10 元新增费用上限内通过真实 Qwen Attempt6。请求 `v4-s06c-smoke-20260925-06` 只提交一次；任务 `07bc9e9fed37497a88aff6a8d47b8e97` 成功，内部 dispatch 3 次、自动重试 0 次，冻结数值全部匹配。Attempt6 新增 426450 估算 units（本地估算 0.00042645 元）；该估算不是供应商最终账单。生产现有 12 条 website usage、1218450 估算 units、3 条保留的 unknown、0 条 unsettled。证据为 `s06c-smoke-attempt6.json` 和 `s06c-budget-authorization-attempt6.json`。
- S06d 在香港目标通过重启、实际回滚和再滚回。重启 7.77 秒恢复且自动重放 0；随后回滚到镜像 `sha256:ac2343ece33408df76ee7727c72f0deb3d22aa98ccfdc89519915794ef30f1d3`，再于 8.82 秒内滚回发布镜像 `sha256:b29cd6ccb4793a66924b9340d7dcc81f4fde4e1ac97d7a03fec8ac8c91b24632`。PostgreSQL、Redis、Caddy 容器身份不变，最终四容器 healthy、重启数 0。
- 9 张表指纹、旧账目 hash、对象归属和 3 条 unknown 均守恒。所有者可读取 Attempt6 task/node，第二账号读取二者均为 `NOT_FOUND`。演练真实模型调用 0、费用增量 0。正式索引为 `s06d-restart-rollback-attempt1.json`；`.local` 私有完整记录和数据库 dump 的 SHA-256 已写入索引。
- S06 已完成。S07 可引用这些目标部署、真实 provider、重启、回滚和授权证据。V4 收口不需要、也未获得新的 Qwen 调用授权。

- 当前状态：2026-09-25 用户明确回复“已切换 medium，继续 V4-S06c 免费诊断”，免费诊断已恢复。下方 low 暂停与 Attempt1—3 记录保留为历史。此次恢复不授权真实 Attempt4；S06c 尚未通过，S06d/S07 不启动。
- Attempt2：`v4-s06c-smoke-20260925-02` 只提交 1 次，HTTP 202，任务 `6082ad92e8af4f81973a33d35dc24d9f` 在 planner 阶段失败；模型 dispatch 1，自动重试 0。
- 生产日志：仅记录 `category=provider`，未记录供应商正文、请求内容、Authorization 或 API Key。
- 账目：生产 `website_usage` 共 5 条、estimated units 418050、V4 unknown usage 2 条。Attempt2 预留 20000000 units、estimated 0、settlement `{"usage":"unknown"}`；estimated 0 不代表供应商费用为 0。旧调试账目 SHA-256 仍为 `0fcdda9608e24c7397414f4bd493f5bde13b6b1b173414f7984214d0482fa0fc`。
- Attempt2 证据：`s06c-smoke-attempt2.json`；客户端原件 SHA-256 `829bc73e54f6171c0ea2479a9ee8956d97646e281d796d35b2c6023db78cb30b`。
- 第二个补丁：只增加安全诊断分类，区分 bad request、认证、权限、限流、配额、服务端、连接、超时和响应解析；HTTP 状态只允许 400–599，供应商错误码只允许固定白名单。异常正文、响应正文、请求和凭据均不进入父进程日志。
- 验收：诊断子进程 17 项通过；首次完整回归的环境型失败保留，设置 `LITELLM_LOCAL_MODEL_COST_MAP=True` 与 `ECOMMERCE_RESTRICTED=true` 后 ecommerce 407 项通过。
- 部署：提交 `581a90f9`，生产镜像 `sha256:f861f8fa87f02d810396612b5acf6abde297cb095b7c057b3cdf8e16c77cff8c`。只重建 app；PostgreSQL、Redis、Caddy 未重启，四个容器均 healthy、重启数 0。公网和内部 health/readiness 均通过，账目和旧 ledger 哈希守恒。
- 恢复：部署前镜像已保留为 `ecominsight:v4-rollback-s06c-fix2-predeploy`，ID `sha256:8416c0fc21705d70cefce56d2af499bf59c2bac9e2b34e40ceee206a2e053149`；更早回滚标签 `ecominsight:v4-rollback-s06c-attempt1` 也继续保留。不删除 volume。
- 下一步：停止在 S06c。先由用户在 DashScope 控制台确认余额/欠费、`qwen-flash` 调用权限、API Key 属于主账号还是子业务空间，以及子业务空间授权；随后才可冻结新的 request ID 与小预算并取得明确授权。不得复用 Attempt1/2 授权，不得进入 S06d/S07。

## Attempt3 与强制暂停

- 用户已确认：未欠费、额度充足、使用默认业务空间、`qwen-flash` 有调用权、不存在供应商限流。
- Attempt3 固定 request ID `v4-s06c-smoke-20260925-03`，新增费用上限 0.10 元，只提交 1 次。
- 任务 `b7b7670622dd451dbc64f4a1d037a1f1`：HTTP 202，planner 阶段失败，模型 dispatch 1，自动重试 0；生产日志仍只有 `category=provider`，没有 HTTP 状态或白名单供应商错误码。
- 账目：`website_usage` 共 6 条，V4 unknown usage 增至 3 条；Attempt3 预留 20000000 units、estimated 0、settlement unknown。旧调试 ledger SHA-256 不变。
- 运行状态：app、PostgreSQL、Redis、Caddy 均 healthy、重启数 0；公网 health/readiness 均为 200。
- 失败链：初次复现 Attempt1；修复1后 Attempt2 失败；修复2后 Attempt3 失败。门槛已满足，必须暂停并请求将思考强度从 low 提升到 medium。
- 恢复步骤：用户明确回复已切换为 medium 并要求继续后，先只读审阅三个 attempt、LiteLLM/OpenAI 兼容异常链及 planner 参数转换；不得直接执行 Attempt4。任何新的真实调用还需单独冻结新 request ID 与预算。

## Authoritative pause record — 2026-09-25

- Status: paused pending manual reasoning-level adjustment.
- Attempt3 failed after the second evidence-based fix, so the mandatory escalation threshold is reached.
- Do not change code, deploy, submit Attempt4, or start S06d/S07.
- Resume only after the user changes reasoning effort from low to medium and explicitly asks to continue.
- A future real Qwen request still requires a new request ID and a separately frozen budget.

## 2026-09-25 medium 恢复与免费诊断（当前有效）

- 用户明确切换 medium 后恢复；上述暂停作为历史保留。仅恢复免费诊断，不授权付费 Attempt4。
- 已复现本地缺陷：生产镜像 + 512 MiB worker 地址空间 + 本地假 HTTP 200 时，LiteLLM 回调线程创建抛 RuntimeError，旧桶记 provider。多余导入链把 pandas 等数据依赖带入模型 worker。旧线上失败缺少异常栈，不能断言三次均同根因。
- medium 修复1：新增无依赖 model_wire 常量模块，parent/worker 各改一个 import；原资源、超时和费用规则保持。统一回归脚本旧代码退出1、补丁后6项通过，真实SDK及有界worker参与，无外部网络。
- 当前补丁尚未部署，不改变生产镜像、回滚点或账目；S06c 不勾选，S06d/S07 不进入。免费诊断 attempt4 是日志序号，不是新真实模型调用。
- 详情与命令：`s06c-medium-diagnosis.md`；可复用回归：`devtools/v4_model_transport_probe.py`。
- 完整 ecommerce 离线回归 407 passed / 26 warnings、退出0；统一旧镜像矩阵最后正常恢复失败（首次正常项可过），补丁后6项全过。指纹/账目证据 `s06c-medium-free-diagnosis-attempt1.json`，补丁 `s06c-medium-fix1-attempt1.patch`。medium 本地修复1通过，生产阻塞未关闭。
- 后续：发布指纹/构建/免费目标验收，再单独授权真实 smoke。保留三个 unknown，不把本地成功当成生产通过。

## 2026-09-25 medium 补丁已部署（最新交接）

- 用户授权继续后，业务提交 `280045f96119391fb06f74a01918e89ea6a78d93` 已在原香港目标部署；镜像 `sha256:4369eda4c5baafa569817ae749808ac584b5eda747c05d0e3a518ce207da74a1`，tag `ecominsight:v4-s06c-medium-fix1`。旧镜像按 digest 固定作为基础，离线 COPY 三个已验证代码文件，依赖/前端无变化，生产源码哈希与提交文件一致。
- 构建/源码证据 `s06c-medium-image-attempt1.json`、`s06c-medium-release-attempt1.json`；部署证据 `s06c-medium-deploy-attempt2.json`。本地和香港断网6项均通过，本机公网 healthz/readyz/auth/status 均200，ready全true。
- attempt1 因检查脚本对 memoryview 使用 str 导致随机内存地址进入哈希而失败，已恢复旧镜像并确认健康。该脚本缺陷在同一数据库行连续读取中复现；随后改为 bytes.hex 序列化，新增实际归档 payload 的 sha256==存储sha256 断言，连续两次9表检查稳定。attempt1日志与原脚本保留，不计作业务 medium 第二次修复失败。
- attempt2 仅重建 app，维护8.89秒；其余3服务ID未变，均healthy/restarts0。部署前后全部9表指纹相同，active0、usage6、estimated418050、unknown3；旧归档原始哈希仍 `0fcdda9608e24c7397414f4bd493f5bde13b6b1b173414f7984214d0482fa0fc`。
- 新备份：服务器 `ecominsight-release/s06c-medium-deploy-attempt2/database.dump`，已检验TOC；本机 `.local/v4-s06c-medium-predeploy-attempt2.dump`，哈希匹配。未恢复/改写生产数据。
- 回滚标签 `ecominsight:v4-rollback-s06c-medium-fix1-predeploy` 对应 `sha256:f861f8fa87f02d810396612b5acf6abde297cb095b7c057b3cdf8e16c77cff8c`，旧标签保留。仅代码补丁，无schema迁移；紧急回滚在原compose目录将旧镜像tag为candidate并 `up -d --no-deps --no-build --pull never --wait app`，核对健康和账目，禁止删volume。
- 本轮真实Qwen0、费用增量0。下一步只在 `s06c-budget-proposal-attempt4.json` 获明确授权后执行固定新ID；S06c尚未通过，不进入S06d/S07。

## 2026-09-25 Attempt4 结果（当前有效）

- 用户已明确授权Attempt4，授权原件 `s06c-budget-authorization-attempt4.json`；预检新ID未使用，当前生产镜像与预算固定镜像相同，active0、usage6/unknown3，保守占用5.05992810元。预检日志按UTC时间戳分别保留。
- 请求ID `v4-s06c-smoke-20260925-04`，task `ce98e5cf84fc4ba4a10061dd14599d2d`。客户端提交1次、HTTP202、轮询3次、dispatch1、自动重试0；终态waiting_clarification，state clarification_required，executed=false。原client failed结果不改写。
- 模型返回455输入/40输出token，estimated128250单位=0.00012825元，小于0.10元；真实账单未查。网站7条usage/estimated546300单位/unknown3条，unknown增量0，历史2条unknown与旧ledger继续保留。原始归档hash仍0fcdda9608e24c7397414f4bd493f5bde13b6b1b173414f7984214d0482fa0fc。
- 四容器healthy，未改代码、未部署、未新增第二分析POST。真实传输问题已获得成功响应证据；S06c整体仍因未完成数值分析而不通过。不能算medium第二次代码修复失败：本轮无新业务补丁；原low失败记录不删除。
- 免费输入复现：原题“已送达订单的”触发严格解析器未消费条件错误，“已交付”可正确解析同一delivered口径。真实模型canonical输出未记录，不能断言其原文；只证明解析缺口和当前澄清终态。
- 证据 `s06c-smoke-attempt4.json` 索引客户端、生产审计、离线诊断和runner SHA-256。密码仅在本机交互窗口输入，不记录；`.submitted` 防重放标记保留，不删除重跑。
- 下一步：免费补充该口径措辞正常例/反例并做最小修复，保持未知条件拒绝；后续新的真实request仍需另行授权，不能继续花Attempt4剩余额度。S06d/S07不开始。

## 2026-09-25 措辞补丁已部署（当前有效）

- 用户继续任务后，仅在normalization词汇表增加“已送达订单”“已送达”，未知/否定/混合口径仍拒绝。新测试先证明3失败，再修复，53定向/424完整回归通过。medium措辞修复第1次本地通过，无付费复验。
- 新业务提交 `73a7ae68b47c3242d1d591b05f72f9f0b503c6bc`；镜像 `sha256:ac2343ece33408df76ee7727c72f0deb3d22aa98ccfdc89519915794ef30f1d3`、tag `ecominsight:v4-s06c-wording-fix1` 已部署。依赖/前端不变，基于上一镜像离线增加一个业务文件，源hash核对通过。
- 本地和目标断网probe：3次fixture模型回复、真实快照/受限工具worker、完整图成功，6个不支持条件拒绝。当前2018-02订单6555/销售额826437.13/客单价126.08；基期2018-01订单7069/销售额924645.00/客单价130.80。变化量和比率亦经独立原始CSV＋Decimal核对；源数据未声明币种，不推断人民币。
- 本轮仅重建app，维护8.78秒；4容器healthy/restarts0，其余服务ID不变，9表内容指纹完全一致，active0/usage7/estimated546300/unknown3，原始ledger哈希不变。公网health/ready/匿名auth均200。真实Qwen增量0/费用0。
- 新备份：服务器 `ecominsight-release/s06c-wording-deploy-attempt1/database.dump`，本机 `.local/v4-s06c-wording-predeploy-attempt1.dump`，SHA256 `32b2eae6e230d619fb6e37764da12f9aff486ff038cb3b078bca5d357dd04d3a`，TOC和离机hash通过。
- 回滚tag `ecominsight:v4-rollback-s06c-wording-fix1-predeploy` 固定原镜像4369eda4…，旧标签不删除。原compose目录把该tag映射candidate后仅 `up -d --no-deps --no-build --pull never --wait app`，再查健康/账目；禁止删除volume。
- 证据 `s06c-wording-validation-attempt1.json`、`s06c-wording-deploy-attempt1.json`、`s06c-wording-image-attempt1.json`、`s06c-wording-release-attempt1.json`，失败baseline日志保留。
- 新只读工具 `devtools.v4_smoke_status` 分别说明登录/模型证据/分析验收，有3项测试；已用Attempt4原证据演示正确输出。旧runner和证据不改写；后续runner须接入该说明。
- 下一步：`s06c-budget-proposal-attempt5.json` 仅提案。收到单独授权后才准备新runner/即时账目预检/密码交互；固定原题、新ID20260925-05、一次提交、至多3dispatch、重试0、新增0.10/累计10元。未经授权不得运行。S06c未通过，不进入S06d/S07。
