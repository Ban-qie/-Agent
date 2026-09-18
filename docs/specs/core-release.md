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

## V3-S01 冻结方案（本机隔离开发）

本节开始V3，前文“本窗口不实施”仅指V2历史记录。用户已明确Windows本机服务器、http://127.0.0.1:5567/，并授权先切换网站账号密码，QQ审核通过后另行修改。本轮不依赖外部IdP，不开放外网，不推送/部署。暂按10个受邀账号作为工程容量上限（非用户已经确认的实际人数），每人1运行、全局2运行、无排队；实测后才声明容量验收。正式域名、代理和公网TLS留V4，不阻塞本地V3。

### 六类决策

| 类别 | 候选/理由 | 待输入或实际缺口 | 验证方式 |
| --- | --- | --- | --- |
| identity | 管理员创建受邀账号；Werkzeug scrypt哈希，Flask-Session服务端会话；数据库不可变UUID作为owner，禁用匿名/local回退 | 拟新增E/password_auth.py及认证存储；无公开注册，不启用QQ/OIDC，禁用默认密码 | S02真实密码正反例、会话过期/退出/撤销、CSRF、登录限流；S09两个真实本地账号登录，stub仅替换模型 |
| store | 单实例本地磁盘SQLite，工作区、节点、任务、reservation同库；复用V1响应/条件协议，不用上游通用文件工厂作为事务存储 | 目标磁盘/容量；禁止网络共享盘或多实例。事务与迁移尚未实现 | 唯一约束、外键、短事务BEGIN IMMEDIATE、版本CAS；独立进程竞争、失败注入、backup API恢复 |
| API | 保留本机V0/V1同步协议；多用户/analyze返回202任务，终态回放保留原业务响应；/workspace仅本人；多用户/query关闭 | 同源loopback 5567；登录/状态/退出精确路径见下，设计路径尚未开放 | S03授权先于副作用，S05路由白名单，S09类型/浏览器 |
| task | 单个既有业务图，有界后台executor；唯一(owner,workspace,request_id)，fingerprint独立比较 | lease、状态、slot需S06—08证明，不复用文件锁冒充事务 | 双进程barrier同键只执行一次；终态/过期worker CAS；未知收费步骤不重跑 |
| budget | 项目绝对累计10元；legacy只读导入同库，保守消耗按每行max(reserved,estimated)；新reservation同时检查项目/全局/用户上限 | 用户预算是总预算内分配，不增加总额；尚无新真实调用授权。旧写者切断和恢复必须演练 | S07副本迁移行数/hash/金额守恒、两进程临界争抢、重复结算、重启/新账号不清零 |
| deployment | Windows本机单实例隔离loopback，公共快照只读、每用户一个私有workspace、受控账号 | 暂定10账号/2并发工程上限；尚无公网域名/代理/TLS，外网保持关闭。SQLite置独立.local/v3目录，禁止指向原runtime | S09仅本地出口；公网代理/TLS在V4验证，本轮不部署 |

用户明确授权采用受控本地密码账号替代OIDC。密码12—128字符、UTF-8最多512字节；哈希仅用已安装Werkzeug scrypt。用户名只作登录标识，owner由数据库UUID生成。密码更新/禁用账号递增auth_version，旧会话立即失效；会话绝对30分钟，登录轮换session ID。cookie HttpOnly/SameSite=Lax；仅显式loopback开发允许HTTP与Secure=false，任何外部部署必须Secure=true及HTTPS。所有写请求含同源Origin和会话CSRF token，登录也校验；凭据不入日志或命令行。按用户名、直接来源及全局限制登录尝试，拒绝统一响应，不暴露账号存在性。QQ审核完成不会自动切换：以后需显式绑定已有内部UUID，禁止按昵称/邮箱自动合并、复制或清零账目。

### 对象与事务边界草案

| 对象 | 权限与约束 | 原子边界 |
| --- | --- | --- |
| user/workspace | 服务端验证账号密码及有效会话后读取不可变owner；客户端owner/header不能授权；每用户一个私有工作区 | owner+workspace复合主键，创建/容量检查同事务 |
| node/result | 父节点、结果及间接引用必须同owner/workspace；未找到和越权统一404；列表只返回本人 | parent复合外键；完整响应、条件、快照/指标版本与task终态同事务保存；版本CAS |
| task | 本人提交/读取/取消；request_id不是授权凭据或路径 | 唯一(owner,workspace,request_id)，同指纹回放、异指纹409；claim/lease/version控制唯一执行 |
| snapshot | 仅固定accepted_catalog允许的公共只读快照；服务端解析路径 | 不允许用户路径；哈希检查保持；worker无模型凭据 |
| reservation | legacy不归任意新用户；新记录绑定owner/workspace/task/dispatch序号 | 检查用户与全局累计额、写reservation同事务；重复finish不重复记账；未知用量保留预留 |

迁移候选：旧文件只读备份并记录hash；显式owner映射；只读解析已知V0/V1版本到临时库，比对节点、状态、金额、条件和完整结果。未知版本拒绝。SQLite使用backup API，不复制活动WAL。激活前停止旧账目写者、核对基座hash并原子切换配置。失败留原路径不变。新库产生费用后不能直接恢复旧账目写入：须关闭收费入口，保留新库及新增reservations并核对守恒后才能恢复服务；不能靠回退代码丢费用。S04/S07实际行为测试前不宣称迁移/回滚安全。

### S01b API契约

所有业务路径前缀为/api/ecommerce。多用户模式由服务端身份定位workspace，不接受body.owner。

认证路径：GET /api/ecommerce/auth/status返回authenticated与csrf_token（已登录再含本人user_id/username）；POST /api/ecommerce/auth/login仅接受username/password并要求X-CSRF-Token，成功200返回本人身份；POST /api/ecommerce/auth/logout同样校验CSRF并撤销当前会话。未登录业务请求401，CSRF错误403，凭据错误统一401，限流429。初次status只生成匿名CSRF会话，不形成可信身份。禁止用X-Identity-Id授权。

```json
{"request_id":"v3-demo-0001","user_question":"分析2018年1月销售额","parent_node_id":null}
```

POST /analyze首次接收或同键运行中返回202：

```json
{"task_id":"server-generated-id","request_id":"v3-demo-0001","status":"accepted","poll_after_ms":1000}
```

GET /tasks/<task_id>只读本人状态，200含task_id/request_id/status/result/error；POST /tasks/<task_id>/cancel返回200当前状态，已终态幂等。GET /workspace返回本人原节点响应；同键终态回放返回原业务HTTP状态和结果，不再次执行。400输入非法、401未认证、404不存在或越权、409不同输入或版本冲突、413请求过大、429容量/预算拒绝、503存储暂不可用；原业务422保持。错误仅含安全code/message及本人request/task ID。

任务状态accepted→running→success/empty_result/waiting_clarification/failed/cancelled/interrupted；既有partial映射failed并保留完整partial结果。cancel_requested为独立标志；终态不可改；澄清补问创建新请求并指定父节点。本机local仍用既有同步响应；多用户/query关闭以避免治理旁路。

### S01c 容量参数（待行为实测、非生产承诺）

- 总受邀账号上限10，隔离验收两账号并另测账号容量拒绝。每用户1运行、全局2、等待0，超额429；executor最多2任务，HTTP单进程4线程以服务状态/取消；本机拟使用已有waitress，若不可用仅依赖fixture直至另定有界服务，禁止用无限线程伪称4线程。
- 沿用body 8192字节、问题2048 UTF-8字节、ID 8—64 ASCII、每workspace 128节点/20MiB，审计独立有界；任务记录拟上限128/用户且不自动删除历史；容量满明确拒绝。
- 任务绝对60秒；工具min(15秒,剩余任务时间)；模型连接/读取min(10秒,剩余任务时间)，输出768 token、V1最多3调用、进程清理最多2秒；HTTP请求预算拟65秒，状态查询数据库busy timeout拟1秒。
- lease拟75秒、不自动重放；超过lease将未确认任务标interrupted；旧worker写入必须校验lease及version。轮询1秒、连续5次网络失败或90秒停止并提供重新查询原ID；401和终态立即停止。
- 提交速率每用户6次/分钟、全局12次/分钟；登录每账号5次/分钟、每来源10次/分钟及全局60次/分钟，来源取直接连接，未来只信指定代理。实际负载变化另行调整后复测，不当作当前实现。
- 默认真实调用预算0。可用项目余量不是调用授权；将来用户额度必须在全局/项目剩余额内分配。调用预留0.02元沿用，不能降低旧行预留或用已知费用替代保守支出。

### S01d 缺输入登记

OS/服务器/当前地址/认证来源均已由用户明确；总人数未明确，采用上述保守工程上限并公开注明。S09账号在隔离数据库受控创建，密码由测试进程临时生成并仅在内存使用，不硬编码产品账号、不要求用户发送密码。生产域名/公网访问/TLS留V4。本轮真实Qwen预算0，S09使用真实本地密码登录与离线模型，不冒充QQ认证。现有迁移/账目/回滚方案须在S04/S07落实行为测试后才能标为实现通过。

S02实现映射：E/account_store.py持久账号/登录限流/有效会话，E/password_auth.py适配Flask-Session，E/multiuser_app.py独立Flask工厂，policy.py精确multiuser分支。原上游global app和OIDC保持原状，不用于V3身份验证。每账号一个有效会话，新登录撤销同账号旧会话；不同账号互不影响。退出删除SQLite有效会话，迟到请求即使恢复文件cookie也不能恢复认证。缺库/无账号/弱secret/非loopback origin拒绝启动。此阶段业务API全部关闭；后续S05逐项接入。

### S03 对象动作矩阵

| 对象/动作 | 本人 | 他人/不存在 | 副作用顺序 |
| --- | --- | --- | --- |
| workspace read/create/list | 允许 | 统一404 | 先取仅元数据授权，再列本人数据 |
| node read/create/parent/replay | 允许同workspace | 统一404，包括兄弟用户同ID | 先授权父节点/所属workspace，再读取结果或执行 |
| task read/cancel/replay | 允许 | 统一404 | 先授权，再读取结果/取消；越权reserve/graph/worker均0 |
| snapshot read | A/B均可读批准只读快照 | 未批准404，写操作拒绝 | 固定catalog，不接受路径 |

授权策略E/authorization.py输入仅由服务端认证形成的Principal；repository返回owner/workspace元数据，不加载敏感payload。S03采用fake验证策略及stub服务，S04接实际SQLite范围查询；不同用户同ID时查本人范围，不泄露他人对象存在性。客户端owner不是Principal。

S07迁移协议：保留legacy每行及hash，在旧账排他锁内备份/导入并写不可覆盖.migrated标记；旧文件writer见标记拒绝，新SQLite writer必须核对标记目标和源hash。没有标记不激活新writer；标记写入失败时保留导入库但禁用预留。真实账本只读，本轮在副本演练；恢复不能删除标记或清空新账。项目10元、新真实调用额度0；用户配额是已有全局额度内限制，未授权时默认为0。金额纳元整数保守转换，legacy不归新用户；未知预留不可释放。

本机HTTP验收实现：已有Werkzeug基础WSGI server+固定4线程池，accept侧最多等待1秒，socket backlog=8，每连接65秒timeout；没有waitress依赖不安装。此连接等待与分析队列分开，分析仍每用户1/全局2/等待0。仅供隔离验收，非公网生产服务器。

## V3 本轮出口

S01—S09在Windows本机隔离、受邀密码账号、离线模型范围完成。最终后端429/429、前端29/29、类型检查/构建退出0；两真实本地密码身份的独立浏览器context完成核心比较、地区Top3、SP日趋势、兄弟订单分支与各自历史；跨task/取消/parent/owner/query反例拒绝，同request不同用户独立任务；退出换人、刷新、断连、实际服务重启0新增分析POST。原账247次、0.04992810元已知估算、4.89元预留、未知2笔，hash不变、新增真实调用0。12历史节点只读副本迁移守恒，真实历史/账目未迁移。

详见[公开验收摘要](../validation/V3-multiuser-summary.json)、[本地说明](V3-local-run.md)及私有docs/verification/V3-S09/handoff.md。前端HTTP请求另有10秒超时；轮询1秒、90秒总等待检查/连续5次错误停止，未完成任务只查询原ID。既有Flask-Session弃用及构建eval/chunk警告保留。QQ审核后另行变更，不预先切换；未推送V3、未部署、不进入V4/V5。
