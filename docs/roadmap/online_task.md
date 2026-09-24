# 电商分析平台上线任务总览与续作入口

更新日期：2026-09-23  
当前阶段：V4 真实上线验收
当前停留点：V4-S01—S05、V4-S06a 和 V4-S06b 已通过；香港生产站 `https://ecominsight.cn` 已部署并完成免费公网双账号验收。下一步为 V4-S06c，等待用户确认已冻结的真实 Qwen smoke 预算；随后执行 S06d 重启/回滚和 S07 发布收口。香港目标无需且不能办理中国大陆 ICP 备案。

当前生产运行限制、用户可见上限、资源预算、备份要求和建议监控阈值统一记录在 [`V4-STABILITY-LIMITS.md`](V4-STABILITY-LIMITS.md)。其中明确区分发布 smoke 预算、单任务安全边界和生产用户配额。

## 1. 项目背景

本项目是在 Data Formulator 基础上保留完整电商分析核心的多用户网站。V1、V2 已完成单用户核心可靠性建设，V3 已完成受控账号、多用户对象授权、任务幂等、取消恢复、费用预留和网站 Qwen 调用入口。V4 的目标是把 V3 的本机 loopback 版本变成可以审阅、备份、恢复、回滚并在腾讯云目标服务器上实测的部署包。

上线目标不是只让首页打开，而是同时证明：

- 用户必须通过受控账号登录，不能依靠客户端 `owner`、自定义 header 或匿名回退伪造身份。
- A/B 用户的工作区、节点、任务、结果、取消权限和费用记录互相隔离。
- 重复 `request_id` 不重复执行或重复计费，输入指纹冲突会被拒绝。
- PostgreSQL 保存全部持久业务真相；Redis 只保存共享 session、限流、短期锁和任务协调状态。
- 服务故障、Redis 丢失、进程退出、数据库锁、坏模型响应和重启不会导致未知费用记录丢失或任务无限重跑。
- 域名、HTTPS、反向代理、备份恢复、回滚和两用户远程验收都有真实证据。
- V4 完成后停止，不自动进入 V5。

V1/V2/V3 的本地通过记录不能替代 V4 的真实目标环境验收。当前仍禁止公开多用户分析入口。

## 2. 已冻结的项目与目标环境信息

### 2.1 软件范围

| 项目 | 当前决定 |
|---|---|
| 后端核心 | Python 3.11 项目 `.venv`，Flask/WSGI 应用 |
| 前端依赖 | 使用现有 `node_modules`，不重装依赖 |
| 持久数据库 | PostgreSQL 16 |
| 共享状态 | Redis 8.8.2 |
| 本地依赖启动 | Docker Desktop/Compose |
| 目标服务器运行时 | Ubuntu 24.04.4 LTS + Docker Engine 29.8.1 + Docker Compose v5.5.1 |
| 生产目标云平台 | 腾讯云中国香港轻量应用服务器 `ecominsight-hk-prod`，公网 IP `43.132.124.152` |
| 现有开发实例 | 广州 `gz`，公网 IP `203.195.212.114`；只保留为开发/内部演练环境，不作为最终生产目标 |
| 香港生产目标规格 | 2 核、约 1.9 GiB、40 GiB、1.9 GiB Swap、20 Mbps、512 GB/月、独立公网 IPv4；2026-10-23 到期；全局单分析槽位 |
| 广州实例资源/到期 | 2 核、约 1.9 GiB 内存、40 GiB 根盘；计划 2026-10-22 到期 |
| 时区 | `Asia/Shanghai`，NTP 已同步 |
| 当前域名 | `ecominsight.cn`，已购买并实名通过；最终唯一入口为 `https://ecominsight.cn` |
| 当前备案 | 香港目标为 N/A：腾讯云官方说明中国香港及境外轻量实例无需备案，也不能用于备案 |
| 反向代理 | Caddy 2.10.2-alpine 已在香港目标运行，TLS 在 Caddy 终止，应用仅信任固定容器地址 `172.29.0.10/32` 的一跳代理头 |
| 访问范围 | 公网可达，仅管理员创建的受邀账号可登录，不开放注册 |
| 维护与恢复 | 首次上线允许 30 分钟维护窗口；用户负责腾讯云控制台恢复，应用内恢复步骤由发布说明固定 |

### 2.2 公网边界

最终计划只开放：

```text
22/tcp   SSH（最好限制为管理者公网 IP）
80/tcp   HTTP，用于 HTTPS 跳转和证书申请
443/tcp  HTTPS
```

不得对公网开放：

```text
5567、5432、6379、55432、56379
```

上述边界将应用于未来香港实例。现有广州服务器盘点显示没有容器运行，只有 SSH 22 监听，80/443 可用，应用、PostgreSQL 和 Redis 端口均未监听；该结果不能替代香港目标盘点。

### 2.3 香港路线可行性核查

2026-09-22 已读取腾讯云和阿里云官方文档，结论如下：

- **ICP**：腾讯云“备案云资源”明确写明，中国香港及境外轻量应用服务器无需备案，也不能用于备案；中国大陆实例的包月 3 个月/90 天要求不适用于香港目标。
- **地域**：腾讯云“地域与网络连通性”列出香港一区、二区、三区可新购；实例创建后不能直接更换地域，因此必须新建香港实例，不能把广州 `gz` 原地改成香港。
- **公网能力**：轻量应用服务器分配公网 IP，支持访问公网和被公网访问；公网出流量计入套餐月流量包。
- **端口**：轻量防火墙支持配置 80 和 443。最终只开放 22/80/443，数据库和 Redis 端口保持内部访问。
- **容量**：目标为 2 核 2 GiB、最多 10 个受邀账号/在线会话、全局 1 个分析任务；第二个分析任务必须有界拒绝。2026-09-23 的 PostgreSQL/Redis 完整本地容量闸门已通过，香港实例购买已放行；购买后仍须在真实目标复验。
- **跨境质量**：腾讯云明确提示，中国大陆访问香港可能受运营商线路影响产生较大延迟和丢包。该风险不阻止上线，但必须在购买后从实际用户网络测试 P95 和失败率。
- **Qwen**：当前项目固定使用 `https://dashscope.aliyuncs.com/compatible-mode/v1`。腾讯云香港实例允许除 25 端口外的默认出站访问；阿里云百炼文档也列出中国香港业务空间专属域名并说明现有域名仍可使用。购买后必须先做不计费的 DNS/TLS/HTTP 连通性检查，再按冻结预算做真实 smoke；不能静默切换 API 地域或 API Key。
- **域名**：`.cn` 域名仍必须完成注册信息模板实名认证。域名实名与 ICP 是两个流程；香港路线取消的是 ICP，不取消域名实名认证、DNS、HTTPS 或内容合规要求。

官方依据：

- 腾讯云备案云资源：<https://cloud.tencent.com/document/product/243/18908>
- 腾讯云轻量服务器 ICP 备案：<https://cloud.tencent.com/document/product/1207/45756>
- 腾讯云地域与网络连通性：<https://cloud.tencent.com/document/product/1207/50103>
- 腾讯云实例套餐：<https://cloud.tencent.com/document/product/1207/44755>
- 腾讯云使用限制：<https://cloud.tencent.com/document/product/1207/44376>
- 腾讯云实例防火墙：<https://cloud.tencent.com/document/product/1207/44577>
- 腾讯云域名实名认证：<https://cloud.tencent.com/document/product/242/6707>
- 阿里云百炼 DashScope API：<https://help.aliyun.com/zh/model-studio/qwen-api-via-dashscope>

### 2.4 费用和模型限制

- Codex 调试账目与网站用户使用 Qwen 的账目分开。
- 历史 Qwen 实读累计 247 次，已知估算 `0.04992810` 元、预留 `4.89` 元、2 笔未知 usage；未知记录不能删除或静默忽略。
- 原调试 campaign 额度已耗尽，不能重置账目、换用户清零或静默扩大预算。
- 免费/离线/stub 验收优先；真实 Qwen smoke 只有在目标环境和预算明确后执行。
- 真实 smoke 需先记录当前 usage、问题数、每题最大调用次数和绝对上限；建议最多 4 个问题、每题最多 3 次调用。未知费用不得用新 request_id 盲重试。
- 网站用户是否可以正常调用 Qwen，不等同于 Codex 调试额度；生产环境仍需由账号所有者明确费用承担、密钥注入和业务限额。

## 3. 当前已完成工作

### 3.1 V1/V2/V3 基线

- [x] V1 核心路线已完成并保留历史提交、标签和失败证据。
- [x] V2-S01—S06 已通过：后端 355 项、前端 22 项，类型检查和构建通过。
- [x] V2 隔离服务重启恢复 12 个历史节点，0 次分析 POST，0 次新增真实模型调用。
- [x] V3 多用户本机版本已具备受控密码账号、服务端 session、对象授权、工作区隔离、任务幂等、有限并发、取消和账目预留。
- [x] 账号创建规则已包含合法用户名校验，密码长度为 6—30 字符；网站不是开放注册模式。

对应基线不能被 V4 修改来“凑验收”。原有未提交修改必须保留：

```text
docs/roadmap/HANDOFF.md
docs/roadmap/V1-closure.md
docs/roadmap/V3.md
docs/roadmap/V5.md
```

### 3.2 V4 本地依赖和目标主机

- [x] 创建 V4 专用 Compose：`deploy/ecommerce/compose.yml`。
- [x] 固定镜像：`postgres:16-alpine`、`redis:8.8.2`。
- [x] PostgreSQL 和 Redis 使用独立 named volume 与 `v4_private` 内部网络。
- [x] 本机 PostgreSQL/Redis 已由用户启动并显示 `healthy`。
- [x] 本机端口绑定为 loopback：PostgreSQL `127.0.0.1:55432`，Redis `127.0.0.1:56379`。
- [x] Compose 私有环境文件 `.env.v4.private` 被 Git 忽略；真实密码不读取、不输出、不提交。
- [x] Compose 静态校验退出码为 0；Docker `config.json` 权限警告已记录，不影响校验。
- [x] 腾讯云服务器 SSH 登录成功，`ubuntu` 已加入 Docker 组并重新登录。
- [x] 服务器 OS、CPU、内存、Swap、磁盘、时区、Docker daemon 和监听端口已盘点。

### 3.3 V4-S01 已完成的本地部分

- [x] 新增 production HTTP profile，与 V3 local profile 明确分支。
- [x] production profile 只接受规范 HTTPS authority。
- [x] production profile 要求显式可信代理网段和恰好一跳反向代理。
- [x] 严格校验 `X-Forwarded-For`、`X-Forwarded-Host`、`X-Forwarded-Proto`，拒绝伪造、缺失、链式客户端地址和不受信来源。
- [x] production session cookie 使用 Secure、HttpOnly、SameSite=Lax。
- [x] 新增 `/healthz` 和 `/readyz`；readiness 要求 database、snapshot、ledger 三项检查，失败返回 503。
- [x] 未列入白名单的 `/api` 路由保持拒绝。
- [x] production profile、V3 认证和 profile 回归共 40 项通过，0 项失败。

相关代码和测试当前尚未提交：

```text
py-src/data_formulator/ecommerce/deployment.py
py-src/data_formulator/ecommerce/multiuser_app.py
py-src/data_formulator/ecommerce/password_auth.py
tests/backend/ecommerce/test_deployment_profile.py
```

## 4. 当前未完成和阻塞工作

### 4.1 域名、备案和网络输入

- [x] `ecominsight.cn` 已购买并实名通过。
- [x] 已确认当前免费广州实例不满足腾讯云 ICP 备案云资源条件，不能用它直接提交备案。
- [x] 已选用中国香港生产节点解决 ICP 资源阻塞；官方确认香港实例无需备案，也不能用于备案。
- [x] 已购买香港轻量应用服务器并记录公网 IP、实例名、地域、规格、系统、流量包和到期日。
- [x] 最终唯一访问 URL 冻结为 `https://ecominsight.cn`，当前不启用 `www`。
- [x] 访问范围冻结为公网可达、仅受邀账号登录、不开放注册。
- [x] 反向代理冻结为 Caddy 2.10.2-alpine；TLS 在 Caddy 终止，应用仅信任 `172.29.0.10/32` 的一跳代理头。
- [x] 首次上线维护窗口为 30 分钟，用户负责腾讯云控制台恢复；生产备份目录仍待 S03 固定。
- [x] PostgreSQL 16、Redis 8.8.2 与应用分析进程已通过本地 2 GiB 完整容量闸门；香港 2 核 2 GiB 实例购买已放行，远程容量仍须复验。

### 4.2 V4 代码和数据层

- [x] V3 SQLite 的只读副本已迁移到隔离 PostgreSQL schema，8 张表逐行相等；生产切换和迁移反例仍待完成。旧 V1/V2 单用户历史明确不迁移，原文件和证据保留在本地。
- [x] 生产应用工厂已把共享 session、限流计数、短期锁和任务协调接入 Redis 8.8.2。
- [x] 持久任务、结果和费用以 PostgreSQL 为唯一真相；Redis 不保存这些持久数据。Redis 故障恢复仍待 S02 实际注入验证。
- [x] 隔离迁移已验证旧调试账目原始字节和 hash 守恒；生产切换仍须保留未知 usage 和历史锁。
- [x] Gunicorn worker/thread/backlog/timeout、连接池、请求体上限、Caddy 超时和容器资源预算已冻结；日志轮转和磁盘耗尽行为仍待 S02/S03。
- [x] 生产应用工厂已实现 PostgreSQL/Redis 连接、配置漂移拒绝、启动任务中断恢复和 readiness；完整容器启动仍待本小步验证。

### 4.3 V4-S02—S07

- [x] V4-S01d：锁定生产镜像、完整本地栈、迁移反例、HTTPS 边界和缺依赖行为验收通过；见 `docs/verification/V4-S01/handoff.md`。
- [x] V4-S02：模型慢/拒绝/坏响应、PostgreSQL 锁和连接池耗尽、Redis 不可用、只读目录、ENOSPC、worker 退出的故障隔离与日志脱敏；10 类故障均解除后恢复，387 项后端回归通过。
- [x] V4-S03：PostgreSQL 一致性备份、恢复到新目录/新数据库、Redis 重建策略、损坏备份拒绝和 RPO/RTO 证据均通过。
- [x] V4-S04：两用户并发、异常输入、重复请求、越权、取消、重启、存储失败和预算耗尽组合验收；至少 20 个正常 stub 与 20 个非法/重复/越权样本。
- [x] V4-S05：可审阅发布树、秘密扫描、回滚包、迁移说明和演示材料。
- [ ] V4-S06：使用同一发布 commit 在腾讯云中国香港服务器真实部署；先免费远程验证，再在预算冻结后执行真实 Qwen smoke。
- [ ] V4-S07：生成 V4 release summary、运行/故障/预算/恢复指南和本地 release receipt，然后停止，不进入 V5。

## 5. 上线流程与每个流程的小任务

### 流程 A：域名注册与香港实例购买

1. 实名认证审核通过。
2. 购买 `ecominsight.cn` 一年，核对续费价格和持有人主体。
3. 在腾讯云轻量应用服务器购买页选择“中国香港”，不要选择广州或其他中国内地地域。
4. 按下一节硬性清单逐项确认后才付款。香港路线不提交 ICP 备案，也不需要购买满 3 个月来换取备案资格。
5. 创建后重新盘点目标 OS、IP、CPU/RAM/磁盘、Docker、时区和监听端口，并生成新的 V4 input-freeze attempt。

#### 香港实例购买前硬性清单

- [ ] 产品名称是“轻量应用服务器”，地域明确显示“中国香港”。
- [ ] 2 核 2 GiB；系统盘至少 40 GiB。接受全局 1 个分析任务的容量声明，并确认 PostgreSQL/Redis 接入后的本地 2 GiB 完整容量闸门已经通过。
- [ ] 套餐包含独立公网 IPv4、公网带宽和明确的月流量包；记录流量包大小与超额费用。
- [ ] Linux x86_64，优先 Ubuntu 24.04 LTS；若只有 22.04 LTS，先更新目标冻结再购买/部署。
- [ ] 购买页明确显示价格、续费价、购买时长和退款限制；实例有效期覆盖计划中的开发与验收时间。
- [ ] 当前腾讯云账号允许购买该香港套餐，且库存可用；具体套餐可能售罄，以付款页为准。
- [ ] 可以创建或导入香港地域 SSH 密钥。广州地域密钥不得假定可直接绑定香港实例。
- [ ] 接受中国大陆访问香港可能有跨境延迟和丢包，购买后会从实际用户网络测试。

### 流程 B：域名解析和服务器边界

1. 域名实名认证并购买成功、香港实例创建完成后，添加 DNS A 记录：`@ -> 香港实例公网 IPv4`。
2. 如启用 `www`，添加 `www -> 香港实例公网 IPv4`。
3. 腾讯云防火墙只放行 22、80、443；不放行 5567、5432、6379、55432、56379。
4. 将 SSH 限制为管理者来源 IP，确认密钥登录后再考虑关闭密码登录。
5. 记录 DNS 生效时间、解析结果、访问来源和防火墙规则证据。

### 流程 C：TLS 和反向代理

1. 冻结 Caddy 或其他代理的精确版本。
2. 代理监听 80/443，后端仅监听受控内部地址。
3. 代理负责 TLS 证书申请、HTTP 到 HTTPS 跳转和有限的转发头。
4. 应用只信任冻结的代理网段和一跳转发信息。
5. 验证错误 Host、错误 Origin、伪造代理头、跨站写请求和超大 body 的拒绝行为。

### 流程 D：数据库、Redis 和应用发布

1. 在隔离环境完成 PostgreSQL schema、迁移和旧账目守恒测试。
2. 使用 Docker Compose 启动 PostgreSQL 16 和 Redis 8.8.2；不创建第二套同名容器，不删除卷。
3. 通过私有环境注入数据库密码、Redis 密码、session secret 和 Qwen key；密钥不进仓库、不进聊天、不进日志。
4. 启动生产 WSGI 入口，关闭 debug/reloader，配置健康检查和 readiness。
5. 在新目录验证发布树可重建，记录 commit、配置 hash、镜像 digest、卷名和容器 ID。

### 流程 E：免费远程验收

1. 访问 `https://ecominsight.cn/healthz` 和 `/readyz`，确认不触发模型调用。
2. 使用两个受邀测试账号分别登录，验证 session、CSRF 和登录轮换。
3. 验证 A 不能读取、取消或修改 B 的工作区、节点和任务。
4. 提交一个离线/stub 任务，验证幂等、取消、刷新、重启恢复和 `interrupted` 状态。
5. 验证 PostgreSQL 备份可用，Redis 临时状态丢失不会改变账目和结果。

### 流程 F：真实 Qwen smoke

1. 只有用户明确冻结真实 smoke 预算后执行。
2. 先记录当前 usage、未知 reservation、问题数、每题上限和绝对累计上限。
3. 最多执行冻结的问题数和调用数，失败保留原始 attempt，不用新 request_id 掩盖未知费用。
4. 记录调用增量、账目 hash、任务 ID 和结果摘要，不记录密钥、完整 cookie 或原始私有数据。

### 流程 G：发布、回滚和停止

1. 按 S03 备份，再按 S05 发布包部署。
2. 发生失败时停止收费入口，恢复到明确旧版本，不用旧代码盲读新 schema。
3. 验证回滚后 health/readiness、A/B 隔离、任务状态和账目 hash。
4. 生成 `docs/validation/V4-release-summary.json` 和本地 release receipt。
5. 固定本地提交/标签并停止；不推送、不部署第二个目标、不进入 V5。

## 6. 证据和文件索引

### 已有公开/半公开入口

- `docs/roadmap/README.md`
- `docs/roadmap/LOW_EXECUTION.md`
- `docs/roadmap/HANDOFF.md`
- `docs/roadmap/V4.md`
- `docs/specs/core-release.md`
- `deploy/ecommerce/compose.yml`
- `deploy/ecommerce/.env.v4.example`
- `deploy/ecommerce/README.md`

### 已有忽略证据

- `docs/verification/V4-S01/input-freeze-attempt1.json`
- `docs/verification/V4-S01/input-freeze-attempt2.json`
- `docs/verification/V4-S01/target-docker-access-attempt1.json`
- `docs/verification/V4-S01/target-docker-access-attempt2.json`
- `docs/verification/V4-S01/target-inventory-attempt1.json`
- `docs/verification/V4-S01/s01c-production-attempt1.json`
- `docs/verification/V4-S01/s01c-production-attempt2.json`
- `docs/verification/V4-S01/s01c-compose-config-attempt1.json`
- `docs/verification/V4-S01/icp-resource-attempt1.json`
- `docs/verification/V4-S01/hong-kong-feasibility-attempt1.json`
- `docs/verification/V4-S01/progress.md`

失败 attempt 必须保留，后续使用新 attempt 编号，不覆盖旧日志、锁、账目或恢复证据。

## 7. 新窗口续作提示词

将下面整段复制到新窗口：

```text
你现在只继续 V4，不执行 V5。工作目录是 C:\Users\Kobe Bryant\Agent Project。

先执行：
git status --short

必须保留所有已有修改、旧节点、失败 evidence、累计账目、未知 usage、锁、标签和私有环境文件；禁止 reset、clean、重装依赖、删除 Docker volume、重置账目、推送远端、部署第二个目标或发送通知。使用项目 .venv 和已有 node_modules，不使用系统 Python。

先完整读取：
docs/roadmap/online_task.md
docs/roadmap/README.md
docs/roadmap/LOW_EXECUTION.md
docs/roadmap/HANDOFF.md
docs/roadmap/V4.md
docs/specs/core-release.md
docs/validation/V3-multiuser-summary.json
docs/specs/V3-local-run.md

当前状态：
- 最终生产目标已改为腾讯云中国香港轻量应用服务器，香港实例尚未购买。
- 现有广州 gz（203.195.212.114）只作为开发/内部演练环境，不是生产目标；其 Ubuntu/Docker/端口盘点不能替代香港验收。
- 腾讯云官方确认：中国香港及境外轻量实例无需 ICP 备案，也不能用于备案；3个月/90天规则不适用于香港目标。
- 域名 ecominsight.cn 已申请实名认证，当前等待审核，尚未购买。
- 香港购买规格已由本地容量闸门放行为 2 核 2 GiB、至少 40 GiB 系统盘、独立公网 IPv4，并在付款前确认月流量包、续费价、库存和香港 SSH 密钥。
- 当前没有最终域名/TLS/反向代理/访问范围冻结，不能声称生产上线，也不能开放公网多用户分析入口。
- 本机 Compose 已固定 PostgreSQL 16 和 Redis 8.8.2；PostgreSQL/Redis 本机容器曾健康运行。
- V4-S01 production profile 本地行为测试 40 项通过、0 项失败；但真实 PostgreSQL/Redis 应用迁移和远程目标验收尚未完成。

继续顺序：
1. 先确认域名实名认证状态；通过后购买 ecominsight.cn，无需发起 ICP 备案。
2. 购买香港实例前逐项执行本文“香港实例购买前硬性清单”。产品、地域、2核2GiB、公网IPv4、流量包、系统盘、Ubuntu镜像、续费价、库存和香港SSH密钥任一未确认都先暂停购买。
3. 香港实例创建后先做只读盘点和免费连通性检查，再更新 V4 input-freeze；不能复用广州的 IP、OS、端口或容量验收。
4. 域名解析到香港新公网 IPv4；不把 203.195.212.114 写入生产 DNS。
5. 在域名/TLS/访问范围/回滚窗口明确前，继续完成可独立进行的 V4-S01 小步骤，但每次只做一个子步骤。
6. 对每个小步骤新增 docs/verification/V4-Sxx/<step>-attemptN.log/json/md，不覆盖旧 attempt；证据写开始 commit、git status、命令、退出码、通过/失败数、环境、输入/代码 hash、Qwen 调用增量、账目 hash、产生文件和下一步。
7. PostgreSQL 16 必须成为全部持久业务数据和费用/未知 reservation 的真相源；Redis 8.8.2 只负责共享 session、限流、短期锁和任务协调。不得用 SQLite 多实例写入替代迁移，也不得让 Redis 成为账目或结果唯一来源。
8. 先做离线/stub 测试，禁止直接重跑旧付费 campaign。真实 Qwen smoke 只有在用户明确冻结问题数、每题调用数、绝对累计上限和费用后执行；失败不盲重试，不丢未知 usage。
9. 严格按 V4-S01 到 V4-S07 的 a→b→c→d 顺序执行。S07 完成后立即停止，不进入 V5。

如果同一阻塞经过两次有依据的修复仍失败：保留复现、两次补丁、失败日志、费用增量和恢复步骤，暂停并请求人工提高思考强度；不要盲试第三次或放宽断言。
```

## 8. 当前下一步

### 8.1 2026-09-23 更新：2GB 本地离线容量闸门

上线顺序固定为：

1. 先在本地以 2 GiB 资源上限完成离线压力测试，只使用 stub Qwen，不产生真实模型费用。
2. 只有本地闸门通过后，才购买腾讯云中国香港 2 GiB 实例。
3. 香港实例获得真实公网 IP 后，再执行网络、HTTPS、health/readiness 和任务提交测试。
4. 最后在冻结预算后执行少量真实 Qwen smoke；离线压力测试禁止调用真实 Qwen。

2 GiB 的正式容量声明限定为：最多 10 个模拟在线用户进行登录、读取和任务状态轮询；全局最多 1 个正在执行的分析任务。第二个分析提交必须快速返回 BUSY/429/503，不能排队积压。两个分析任务同时运行不属于 2 GiB 的上线承诺。

离线闸门必须记录请求数量、状态码、p50/p95 延迟、任务完成时间、峰值常驻内存、Swap/OOM/容器重启、代码和配置哈希。只有在 10 用户读请求无异常 5xx、单个 stub 分析在现有 deadline 内完成、第二个分析被有界拒绝、幂等和取消仍正确时才算通过。该证据不代表香港线路或真实 Qwen 可用性，后两项必须在后续阶段单独验收。

2026-09-23 已完成核心预检 `docs/verification/V4-S04/offline-stress-attempt7.json`：2 GiB Windows Job Object 硬限制下，10 个账号、40 次并发 workspace 读取、真实分析图和 Parquet worker、固定本地模型响应均通过；峰值 RSS 294.418 MiB，读取 P95 284.825 ms，第二任务 16.392 ms 返回 429，真实 Qwen 调用 0，历史 Qwen 账目前后哈希一致。该 attempt 使用 SQLite TaskStore，且 Docker Desktop 当时未运行，因此只标记“核心预检通过”，不标记完整 V4-S04 或购买闸门通过。

2026-09-23 已完成完整 PostgreSQL/Redis 容量闸门 `docs/verification/V4-S04/full-offline-stress-attempt4.json`：10 个用户、40 次并发读取，无非预期 5xx；读取 P95 842.626 ms；单分析任务 2.252 秒完成，第二任务 152.729 ms 返回 429；应用 Job 峰值 914.047 MiB。PostgreSQL 和 Redis 的硬限制分别为 256 MiB、96 MiB，测试后均为 healthy、重启 0、OOM 0。真实 Qwen 调用 0，历史账目哈希保持 `0fcdda9608e24c7397414f4bd493f5bde13b6b1b173414f7984214d0482fa0fc`。运行时补充证据见 `full-offline-stress-attempt4-runtime.json`。

香港 2 核 2 GiB 实例购买容量闸门现已通过。该结论仅放行购买，不代表 V4-S04 整卡完成：至少 20 个正常 stub、20 个非法/重复/越权请求，以及取消、重启、存储失败和预算耗尽的完整矩阵仍待执行。

当前下一步已更新为 V4-S06c：S01—S05 与 S06a/S06b 均已完成，DNS、TLS、香港部署和公网双账号隔离已通过。收到用户对 `s06c-budget-proposal-attempt1.json` 的明确确认后，仅执行其中一个固定真实 Qwen smoke；随后完成 S06d 和 S07，并停止在 V4。

### 8.2 2026-09-23 更新：线上历史迁移范围

用户明确选择舍弃旧 V1/V2 单用户历史的线上迁移。这里的“舍弃”只表示不将旧 `local:` 身份的节点和工作区导入香港网站，不删除本机 V1/V2 文件、锁、失败证据或恢复点，也不改写历史提交和标签。

线上迁移范围固定为现有 V3 多用户数据库：受邀账号、服务端 owner、工作区、节点、任务、结果、授权元数据和 `website_usage`。调试 Qwen 总账仍须完整归档并保持旧费用和未知 usage 守恒，但不作为网站用户历史展示。生产切换时旧 session 全部撤销并在 Redis 中重建，不能把迁移副本中的 session 当作有效登录。

本地副本迁移证据 `docs/verification/V4-S01/migration-rehearsal-c9d1e6be8c57.json` 已证明当前 V3 的 8 张表逐行相等、调试账目原始字节相等、源 hash 不变；这仍不是生产切换或香港部署完成。

在上述输入齐全前，项目保持隔离开发状态；不能把当前本机测试结果写成正式上线完成。

### 8.3 2026-09-23 更新：本轮对话与上线续作摘要

本轮从“V4 是否应先本地完成再上线”开始，最终决定采用可重复发布流程：当前不完整的前端和未来核心升级可以在后续版本继续开发，但 V4 先建立一次可回滚、可迁移、可验证的上线基线。每次后续上线都应从固定 commit 构建新镜像，先在隔离环境验收，再备份、迁移并替换生产版本，不能直接在服务器内修改源码。

已完成的人工和环境操作如下：

- [x] 本地 Docker Desktop 已启动 PostgreSQL 16 和 Redis 8.8.2，完整 2 GiB 容量闸门通过；目标负载保持最多 10 个在线会话、全局 1 个分析任务，第二任务快速拒绝。
- [x] 放弃因 ICP 资源期限受阻的广州生产路线，广州 `gz` 只保留为开发/内部演练实例。
- [x] 购买并盘点腾讯云中国香港实例 `ecominsight-hk-prod`：公网 IP `43.132.124.152`、2 核、约 1.9 GiB、40 GiB、1.9 GiB Swap、20 Mbps、512 GB/月、2026-10-23 到期。
- [x] 香港实例已通过 SSH 登录，安装并验证 Docker Engine 29.8.1、Compose v5.5.1、Git 和 rsync；当前尚未运行应用容器。
- [x] `ecominsight.cn` 已购买并实名通过，生产唯一入口冻结为 `https://ecominsight.cn`；香港服务器无需 ICP 备案。
- [x] 公网边界冻结为 22/80/443；PostgreSQL、Redis 和应用 5567 端口不得公开。
- [x] 访问模式冻结为仅受邀账号登录、无开放注册；生产认证暂用站内账号密码，QQ 审核通过后再作为独立后续变更评估。
- [x] Olist 快照获准用于非商业演示，仅展示汇总分析并注明来源和 CC BY-NC-SA 4.0；快照 ID 与 Parquet hash 已冻结。
- [x] 用户明确舍弃旧 V1/V2 单用户线上历史，只迁移现有 V3 多用户账号及工作区数据；本地 V1/V2 文件、锁、失败证据、提交和标签继续保留。
- [x] 全部历史 Qwen 调试账目和 2 笔未知 usage 继续归档并守恒；网站用户使用 Qwen 不继承 Codex 调试额度，但生产仍须保留原子业务配额和费用记录。
- [x] 生产运行方式冻结为 Caddy 2.10.2 + Gunicorn 23.0.0 + Python 3.11，持久业务数据进入 PostgreSQL，Redis 只承担共享 session、限流和任务协调。
- [x] Docker Hub 首次拉取 Python 基础镜像受本机网络影响失败；启用 Docker Desktop Containers proxy 后已成功拉取，失败日志保留。
- [x] 生产镜像未锁依赖版本的构建曾成功，但审计发现没有实际消费 `uv.lock`，因此不能作为发布候选；随后已从 `uv.lock` 导出带 hash 的生产 requirements。
- [x] V4-S01d 已完成。锁定镜像 build attempt4/5 的 sdist/wheel hash 失败均保留，attempt6 通过；完整 HTTPS、生产工厂、cutover attempt4 和本地启动均通过。

严格续作顺序：

1. [x] 修复并验证锁定依赖生产镜像；记录固定依赖版本、非 root 用户、快照 hash、Gunicorn 配置、镜像大小和构建日志。
2. [x] 使用 `compose.yml` 与本地诊断覆盖 `compose.local.yml` 启动完整本地生产栈，完成 S01d 的合法/拒绝 HTTP 矩阵、readiness 和零模型调用验证。
3. [x] 补齐迁移反例与正式切换演练，确认只导入 V3 多用户数据、撤销旧 session、V1/V2 `local:` 历史为 0、旧账目原始字节和未知 usage 守恒。
4. [x] 完成 V4-S02 故障隔离与日志脱敏，再完成 V4-S03 一致性备份和新实例恢复。
5. [x] 完成 V4-S04 的 20 个正常 stub、20 个非法/重复/越权请求及组合故障矩阵；本地容量闸门不能替代整卡。
6. [x] 完成 V4-S05 固定发布包、秘密扫描和回滚演练，生成可传到香港服务器的同一发布制品。
7. [ ] 进入 V4-S06 后才修改 DNS、开放 80/443、部署同一发布 commit、迁移 V3 数据并执行两用户公网验收；真实 Qwen smoke 前另行冻结小预算和绝对累计上限。
8. [ ] 完成 V4-S07 发布摘要、运维/恢复说明和 release receipt，然后停止，不进入 V5。

### 8.4 2026-09-23 最新执行指令：舍弃旧单用户线上历史并继续 V4

用户再次确认：香港生产环境不导入 V1/V2 的旧单用户工作区、节点、任务或结果。此项任务在迁移和恢复验收中的通过标准为生产数据内 `local:` owner 行数为 0；不得为了满足该标准删除或改写本机 V1/V2 数据。

以下内容仍须保留：本地 V1/V2 文件、旧节点、锁、失败 attempt、验证证据、历史提交和标签，以及 247 条 Qwen 调试账目、已知费用和 2 条未知 usage。费用账目属于审计历史，不因舍弃旧单用户业务历史而删除、归零、重新归属或跳过守恒检查。

线上业务迁移只包含 V3 多用户受邀账号及其服务端 owner、工作区、节点、任务、结果、授权元数据和 `website_usage`。旧 session 不迁移为有效登录；切换后由 Redis 重新建立 session。S01 的 cutover attempt4 已在隔离环境证明上述范围，远程生产迁移仍只能在 S06 使用 S05 固定的同一发布包执行。

本轮续作按以下顺序继续：V4-S02a 故障 fixture -> S02b 有界性 -> S02c 逐故障恢复 -> S02d 日志检查与交接 -> S03 -> S04 -> S05 -> S06 -> S07。每个子步骤通过并保存新 attempt 后才进入下一项；S06 前不修改 DNS、不部署远程应用、不开放网站，真实 Qwen 调用保持为 0。S07 完成后停止，不进入 V5。

- [x] V4-S02a：`devtools/service_resilience.py` 和 `test_service_resilience.py` 已建立；实际 fixture attempt1 完成隔离 schema/namespace、独立目录、假密钥和 stub 基线验证，退出后只清理自身资源。证据：`docs/verification/V4-S02/fixture-attempt1.json`。
- [x] V4-S02b：冻结并验证 health、task 查询、取消、恢复和槽满拒绝时限。
- [x] V4-S02c：10 类故障逐项注入和解除后恢复通过；无 running 残留、未结算 usage 或非预期 500。
- [x] V4-S02d：4 类合成秘密命中 0，四个实际容器日志均为 10 MiB x 3 上限；S02 后端回归 387 项通过。
- [x] V4-S03a-d：PostgreSQL 一致性备份、全新数据库恢复、Redis session 撤销、active task 转 interrupted 和 5 类损坏拒绝均通过；旧账目与 2 条未知 usage 守恒，模型 dispatch 为 0。
- [x] V4-S04a-d：双实例 20 个正常任务和 20 个对抗请求通过；全局单槽在 PostgreSQL 中原子执行，第二任务快速 429 且不落任务、不产生 usage。10 类故障复验和 2 GiB 容量闸门通过，最终后端回归 391 项通过；真实 Qwen 0，账目 hash 不变。
- [x] V4-S05a-d 已完成：最终发布 manifest、秘密扫描、回滚演练和演示材料均已通过；文档更新后的最终包 commit 为 `857a9999`。
- [x] V4-S05a-d：最终发布包来源 `857a9999`，归档 SHA-256 为 `bcc62a0dd816ae3cfa805820a60b17c72373c37d6d0749d22bf33743436fcd5f`，镜像 digest、锁依赖、Olist 快照和 Portfolio 均有证据；秘密扫描命中 0；回滚恢复、旧代码兼容边界、失败后入口关闭和 3–5 分钟演示脚本均通过。真实 Qwen 0，账目 hash 不变。
- [ ] 当前下一步：V4-S06c 真实 Qwen smoke。预算提案已经固化；等待用户明确确认后提交一次固定 request_id，不得自动重试或扩大预算。

### 8.5 2026-09-24 用户最终确认：线上舍弃 V1/V2 单用户历史

本轮上线任务和此前关于香港实例、域名、Olist、Qwen 账目及发布流程的对话，以本节为最终输入冻结：

- 线上生产数据库不得导入 V1/V2 单用户 `local:` owner 的工作区、节点、任务、结果或旧 session；这些数据“舍弃”仅针对线上迁移，不是删除本地文件。
- 本地 V1/V2 源码、节点、锁、失败 attempt、恢复点、提交、标签和审计账目继续保留，禁止 reset、clean、覆盖旧证据或重置费用。
- 线上只迁移 V3 多用户受邀账号、服务端 owner、workspace、nodes、tasks、results、授权元数据和 `website_usage`。迁移后所有旧 session 撤销，由 Redis 重新建立登录 session。
- Olist 快照只作非商业汇总演示，保留 Kaggle Olist v2 来源与 `CC BY-NC-SA 4.0` 说明，不展示原始订单明细或将许可范围扩大为商业用途。
- 生产入口固定为 `https://ecominsight.cn`，仅受邀账号加站内密码，无开放注册；QQ/OIDC 暂不接入。公网只开放 22/80/443，PostgreSQL、Redis 和 5567 不公开。
- V4-S01—S05 的本地证据可复用，但不能替代香港目标的 S06 验收。真实 Qwen smoke 只在 S06c 执行，必须先冻结最多 4 个问题、每题最多 3 次调用、预算和绝对累计上限；S01—S05 调用增量为 0，历史 247 条调试记录、2 条未知 usage 及账目 hash `0fcdda9608e24c7397414f4bd493f5bde13b6b1b173414f7984214d0482fa0fc` 必须守恒。
- 完成 S07 后立即停止 V4 工作，不自动进入 V5。

### 8.6 2026-09-24 S06 真实部署续作记录

- [x] S06a：香港目标只读盘点通过；Ubuntu 24.04、2 vCPU、约 1.92 GiB、40 GiB、Docker 29.8.1、Compose 5.5.1，部署目录 700，公网初始仅 SSH 22，DNS 尚未修改。
- [x] S06 迁移输入复核：发现既有 S03 备份是旧快照（`alice/bobby`），与当前本机 V3 PostgreSQL 的 `Ban_qie` 不一致；没有把旧快照当作当前源上线。旧恢复库 `v4_restore_prod_0924` 和 mismatch 证据保留，未公开、未产生模型调用。
- [x] 从当前本机 V3 PostgreSQL 生成最终 `s06-current-attempt2`，manifest hash `fa1fb48cdcafbf49a9c1fb7f619a6e3b2f8961fecab0824ba008072aebfb4745`，只含当前 `Ban_qie`/`bobby` 多用户数据；香港生产库为 `v4_restore_prod_0924c`，内部 health/readiness 通过，session=0、active task=0、`local` owner=0。
- [x] S06b 公网免费验收：DNS 已指向 `43.132.124.152`，Caddy 已签发有效 TLS 证书，HTTP 308 跳转 HTTPS；health/readiness/multiuser 均为 200，5432/6379/5567 均未公开。`Ban_qie`/`bobby` 双账号登录和 workspace 通过，跨用户读/取消/parent 均为 404。生产库计数与 `s06-current-attempt2` 迁移基线相同，模型 dispatch 增量为 0；首次匿名 TLS 超时和后续通过证据分别保留。
- [ ] S06c 真实 Qwen smoke：用户已授权既定预算。attempt1 只提交一次，planner 的第 1 次 dispatch 约 12.3 秒后 `MODEL_FAILED`；新增 1 条 unknown usage，估算增量为 0 不能解释为实际费用为 0。免费探测确认 Key、DNS/TLS、`qwen-flash` 和 chat route 正常。第一次修复把单次上游 timeout 从 10 秒调整为 15 秒，保留 0 自动重试、每任务最多 3 次和 60 秒总时限，并增加不含秘密的错误类别日志；针对性 10 项和 ecommerce 398 项通过。unknown usage 后尚未授权新 request_id，因此未重试真实模型。

### 2026-09-25 S06c Attempt2 与第二诊断补丁

Attempt2 使用固定 request ID `v4-s06c-smoke-20260925-02` 和用户授权的新增费用上限 0.10 元，只提交一次。请求 HTTP 202，任务 `6082ad92e8af4f81973a33d35dc24d9f` 在 planner 阶段经过 1 次模型 dispatch 后失败，自动重试为 0；生产日志类别为 `provider`，没有记录供应商正文或凭据。该 attempt 新增第 2 条 V4 unknown usage：预留 20000000 units、estimated 0、settlement unknown；不能解释为实际费用为 0。生产 `website_usage` 当前共 5 条，旧调试账目 SHA-256 保持不变。

现有 `provider` 桶不足以判断 HTTP 拒绝、权限/配额、服务端异常或响应解析。第二个有依据的补丁只扩展安全诊断分类和离线测试，不改变请求参数、预算、自动重试或业务结果。诊断测试 17 项通过；使用离线 LiteLLM 价格表和受限电商 profile 的完整 ecommerce 回归 407 项通过。Attempt3 未获授权且不得自动执行；第二补丁部署后只能做免费验证。证据见 `docs/verification/V4-S06/s06c-smoke-attempt2.json`、`s06c-fix2-validation-attempt1.json` 与 `handoff.md`。

第二诊断补丁已从提交 `581a90f9` 构建并部署到香港生产 app，镜像 ID 为 `sha256:f861f8fa87f02d810396612b5acf6abde297cb095b7c057b3cdf8e16c77cff8c`。首次构建因一个 wheel 传输不完整触发锁定哈希拒绝，失败日志保留；重新完整下载后的 wheel 与原锁定哈希一致，未修改依赖版本或哈希。部署只重建 app，PostgreSQL、Redis、Caddy 均未重启；四个容器 healthy、重启数 0，公网和内部 health/readiness 均通过，账目仍为 5 条 website usage、2 条 V4 unknown usage，旧调试账目哈希不变。S06c 仍未通过，等待供应商控制台输入及新的 Attempt3 明确授权；不得进入 S06d 或 S07。

用户随后确认默认业务空间未欠费、额度充足、`qwen-flash` 有调用权且无供应商限流，并授权 Attempt3 新增费用最多 0.10 元。固定 request ID `v4-s06c-smoke-20260925-03` 只提交一次，任务 `b7b7670622dd451dbc64f4a1d037a1f1` 在 planner 阶段经过 1 次 dispatch 后仍以 `provider` 类失败，自动重试 0；新分类器未取得 HTTP 状态或白名单错误码。`website_usage` 当前 6 条、V4 unknown usage 3 条，旧调试账目哈希不变，所有容器和公网健康检查正常。由于修复1后 Attempt2 失败、修复2后 Attempt3 仍失败，已达到强制升级门槛：S06c 状态改为“暂停-待人工调整”，必须由用户将思考强度从 low 提升到 medium 后明确回复继续；在此之前不得修改代码、部署、发起 Attempt4 或进入 S06d/S07。

**Authoritative pause record (2026-09-25):** V4-S06c is paused after Attempt3 failed following the second evidence-based fix. No code change, deployment, Attempt4 request, S06d, or S07 work is permitted until the user changes reasoning effort from low to medium and explicitly asks to continue. Any future real request also requires a new request ID and a separately frozen budget.
