# V4 生产运行限制与稳定性维护基准

更新日期：2026-09-24

适用版本：V4 香港生产部署

生产入口：`https://ecominsight.cn`

目标环境：腾讯云香港，2 vCPU、约 1.92 GiB RAM、40 GiB 磁盘
预期负载：约 1-10 个登录用户，同时执行 1 个分析任务

本文记录当前生产代码和部署配置中实际生效的限制，并说明限制目的、达到上限后的行为和调整要求。本文不代替故障与恢复步骤；具体命令见 [`deploy/ecommerce/OPERATIONS.md`](../../deploy/ecommerce/OPERATIONS.md)。

## 1. 最容易混淆的两类限制

### 1.1 网站用户没有累计 Qwen 次数或金额配额

生产环境的 `website_usage` 只负责在每次真实模型请求前预留记录，并在响应后保存 token 和估算费用。它不会因为某个用户累计调用次数或累计金额而拒绝后续分析。

- 用户累计模型调用次数上限：无。
- 用户累计模型费用上限：无。
- 账号用完 3 次后是否永久停用：否。
- 每个新分析问题是否重新获得任务调用额度：是。

历史调试账目的 10 元绝对上限和本次 S06c 的 0.10 元预算，只约束 Codex 执行的开发、发布验收和 smoke，不是网站用户配额。

### 1.2 每个分析任务最多 3 次内部模型 dispatch

这是线上长期生效的单任务安全边界。一个用户问题会建立一个任务，该任务最多调用 Qwen 3 次，用于规划、复核和结果解释。达到 3 次后任务停止，用户仍可提交新的分析问题。

保留该边界的原因：

- 防止异常提示或模型行为形成无限循环。
- 防止一个任务长期占用 2 GB 服务器唯一的分析槽位。
- 保证每次可能收费的请求先记账，且失败后不隐式重试。
- 让任务在 60 秒总时限内有界结束。

不建议直接删除此限制。若复杂分析确实需要更多轮次，应先扩展测试矩阵，再重新验证费用、60 秒时限、内存、取消和未知 usage 行为。

## 2. 用户和认证限制

| 项目 | 当前限制 | 达到限制后的行为 | 目的 |
| --- | --- | --- | --- |
| 注册方式 | 仅管理员创建受邀账号，无开放注册 | 公网页面不能自行注册 | 控制访问范围 |
| 账号总数 | 最多 10 个 | 创建第 11 个账号失败 | 符合当前 1-10 人目标负载 |
| 用户名 | 3-32 字符，只允许字母、数字、`_`、`-` | 创建或登录输入被拒绝 | 避免歧义和异常键值 |
| 密码 | 6-30 个字符，UTF-8 编码不超过 512 bytes | 创建、改密或登录输入被拒绝 | 限制哈希输入成本和异常载荷 |
| 登录会话 | 每个账号同时仅 1 个有效会话 | 新设备登录会撤销该账号旧会话 | 限制 session 数量并支持快速撤销 |
| 会话有效期 | 登录后绝对 30 分钟 | 到期后重新登录 | 降低遗留 session 风险 |
| 登录频率 | 全站 60 次/分钟、单来源 IP 10 次/分钟、单用户名 5 次/分钟 | HTTP 429 | 限制密码猜测和 scrypt 计算压力 |
| Cookie | `Secure`、`HttpOnly`、`SameSite=Lax`，服务端 Redis session | 浏览器脚本不能读取，HTTP 明文不发送 | 降低 cookie 泄漏风险 |
| 写请求 | 必须携带正确 Origin 和 CSRF token | HTTP 403 | 防止跨站写入 |
| 身份来源 | 只信任服务端 session，不信任 owner/header | 伪造身份不改变对象归属 | 保证对象授权可信 |

登录频率中的“次数”包含成功和失败尝试。账号禁用或改密会递增认证版本，使已有 session 失效。

来源：`account_store.py`、`password_auth.py`、`postgres_store.py`、`redis_runtime.py`。

## 3. HTTP、代理和接口边界

| 项目 | 当前限制 | 行为 |
| --- | --- | --- |
| 唯一生产 Origin | `https://ecominsight.cn` | 其他 Host、Origin 或非 HTTPS 应用请求返回 403 |
| 可信代理 | 只接受 `172.29.0.10/32` 的 1 跳 Caddy 转发 | 非可信来源或异常转发头返回 403 |
| 公网端口 | 网站开放 80/443；SSH 管理端口 22；5432/6379/5567 不公开 | 数据库、Redis 和应用端口不能从公网直连 |
| 请求体 | Caddy 和 Flask 均限制为 8 KiB | 超限返回 413 |
| Caddy 读 header | 5 秒 | 慢速 header 连接终止 |
| Caddy 读 body | 10 秒 | 慢速 body 连接终止 |
| Caddy 写响应 | 70 秒 | 超时连接终止 |
| Caddy 空闲连接 | 30 秒 | 空闲连接关闭 |
| 反向代理连接应用 | 2 秒 | 无法连接应用时快速失败 |
| 等待应用响应 header | 65 秒 | 超时返回代理错误 |
| API 范围 | 只开放认证、catalog、workspace、analyze、task 状态和取消 | 旧上游 API、任意 agent API 和未知 API 返回 403 |
| API 缓存 | 所有 `/api/` 响应 `Cache-Control: no-store` | 浏览器和中间缓存不应保存认证响应 |

Caddy admin API 已关闭。HTTP 80 只用于跳转 HTTPS 和证书流程，业务访问使用 HTTPS。

来源：`deploy/ecommerce/Caddyfile`、`deployment.py`、`password_auth.py`、`policy.py`。

## 4. 分析提交、并发和任务状态

| 项目 | 当前限制 | 达到限制后的行为 |
| --- | --- | --- |
| 用户问题 | 非空 UTF-8 文本，最多 2048 bytes | HTTP 400 |
| `request_id` | 8-64 字符，只允许字母、数字、`_`、`-` | HTTP 400 |
| 单用户提交频率 | 6 个新任务/分钟 | HTTP 429 `RATE_LIMIT` |
| 全站提交频率 | 12 个新任务/分钟 | HTTP 429 `RATE_LIMIT` |
| 同时活动分析 | 全站 1 个，单用户也只能占 1 个 | 第二个任务快速返回 HTTP 429，不进入等待队列 |
| 后台分析线程 | 1 个 | 不并行执行两个模型/Parquet 分析 |
| 单任务总时限 | 60 秒 | 任务失败或中断，不自动重放 |
| accepted lease | 75 秒 | 过期任务转为 `interrupted` |
| 单账号持久任务 | 最多 128 条 | HTTP 429 `RESOURCE_LIMIT` |
| 单账号 workspace | 固定 1 个 `ecommerce-v0` | 不创建任意多个 workspace |
| 单 workspace 节点 | 最多 128 个 | HTTP 429 `RESOURCE_LIMIT` |
| workspace 节点总载荷 | 最多 20 MiB | HTTP 429 `RESOURCE_LIMIT` |

同一 owner、workspace 和 `request_id` 具有唯一约束：

- 相同输入重复提交返回原任务，不重复执行或计费。
- 相同 `request_id` 搭配不同输入返回 HTTP 409。
- parent node 必须属于当前登录用户；跨用户读取、取消或作为 parent 使用均返回 404。
- 应用启动时，旧的 `accepted` 或 `running` 任务统一改为 `interrupted`，不会自动调用 Qwen。

### 4.1 128 条任务/节点是容量上限

这不是费用配额，但会影响长期使用。当前没有自动删除、归档或滚动保留机制。一个成功任务通常同时新增 1 条 task 和 1 个 node，因此账号接近 128 条时必须先做备份和保留策略评审，不能直接删除生产历史来腾位置。

来源：`task_store.py`、`postgres_store.py`、`task_service.py`、`workspace_repository.py`、`v1_workspace.py`。

## 5. Qwen 模型调用边界

| 项目 | 当前限制 | 目的 |
| --- | --- | --- |
| 模型 | 固定 `qwen-flash` | 保持能力、价格和验收基线一致 |
| API | 固定 DashScope OpenAI-compatible endpoint | 防止静默切换供应商或地域 |
| 单任务 dispatch | 最多 3 次 | 防止循环和失控收费 |
| 自动重试 | 0 次 | 避免未知结果被重复收费 |
| 单次上游超时 | 最多 15 秒，并受 60 秒任务总时限约束 | 有界等待供应商；S06c 首次香港真实调用约 10 秒超时后调整 |
| 单次最大输出 | 768 tokens | 限制延迟、费用和响应体 |
| 发送给模型的消息 | JSON 序列化后最多 16 KiB | 限制 token 和内存 |
| 模型传输结果 | 最多 512 KiB | 防止子进程输出失控 |
| temperature | 0 | 提高稳定性和可复验性 |
| thinking | 关闭 | 控制延迟和费用 |
| streaming | 关闭 | 使用完整且可核对的 usage |
| 任意工具调用 | 禁止 | 只运行代码绑定的固定分析图 |
| ping/探测调用 | 禁止 | health/readiness 不产生模型费用 |

每次 dispatch 在向供应商发送请求前写入 `website_usage`。获得 usage 后保存输入/输出 token 和费用估算；异常或缺失 usage 记为 unknown，不能删除，也不能用新 `request_id` 盲重试。

生产用户没有累计调用或金额配额。S06c 的 0.10 元新增费用上限、最多 1 个问题和项目调试累计 10 元上限，只适用于发布验收人员。

来源：`governed_client.py`、`website_usage.py`、`qwen_client.py`、`model_transport.py`。

## 6. 数据分析功能和输出限制

当前网站不是任意数据分析或任意代码执行平台。生产分析只允许已审核的 Olist 汇总指标：

- 只读取固定 hash 的 Olist Parquet 快照。
- 只统计 `delivered` 订单。
- 时间字段固定为下单时间 `purchase_at`。
- 销售额为商品价格合计，不含运费，不代表付款额、净收入或会计利润。
- 支持订单数、销售额、客单价，以及按日、月、地区分组和两个期间比较。
- 不允许 SQL、Python、任意文件路径、写操作、退款、利润、产品筛选或因果结论。
- 不公开原始订单明细，仅展示汇总分析。
- 数据仅用于非商业演示，保留 Kaggle Olist v2 来源和 `CC BY-NC-SA 4.0` 许可说明。

具体输入上限：

| 项目 | 当前限制 |
| --- | --- |
| 单个日期区间 | 最长 1096 天，开始日必须早于结束日 |
| 地区列表 | 最多 28 个合法州代码 |
| 分组类型 | 无、地区、日、月 |
| 分组返回数量 | 1-200，默认 100；总数仍基于完整选中范围 |
| 快照压缩文件 | 最多 32 MiB |
| 快照解压元数据估算 | 最多 128 MiB |
| 快照行数 | 最多 200,000 行 |
| worker 输入 | 最多 16 KiB |
| worker 输出 | 最多 128 KiB |
| worker 运行时间 | 最多 15 秒 |
| worker 地址空间 | 最多 512 MiB |

快照路径、schema 或 SHA-256 不符时 fail closed，不尝试读取其他文件。worker 使用隔离 Python 进程，不继承 Qwen Key、数据库密码或用户环境，并禁用 core dump。

来源：`metrics.py`、`contracts.py`、`executor.py`、`worker.py`、`process_limits.py`。

## 7. 应用、数据库、Redis 和代理资源限制

### 7.1 Docker 容器预算

| 服务 | 内存上限 | 内存预留 | CPU 上限 | PID 上限 | 其他限制 |
| --- | ---: | ---: | ---: | ---: | --- |
| app | 1024 MiB | 512 MiB | 1.0 | 64 | 只读根文件系统，`/tmp` 32 MiB tmpfs，非 root，删除全部 capabilities |
| PostgreSQL | 256 MiB | 128 MiB | 0.75 | 64 | 持久 volume，`no-new-privileges` |
| Redis | 96 MiB | 32 MiB | 0.25 | 32 | 持久 AOF，`no-new-privileges` |
| Caddy | 64 MiB | 32 MiB | 0.25 | 32 | 只读根文件系统，`/tmp` 16 MiB，只保留绑定低端口能力 |

这些上限为约 2 GiB 主机保留了操作系统和 Docker 开销空间。直接提高任一容器内存或并发前，必须重新执行 2 GiB 压力测试并检查 swap、OOM、容器重启和 P95 延迟。

### 7.2 Gunicorn

| 项目 | 当前值 |
| --- | ---: |
| worker | 1 |
| worker 类型 | `gthread` |
| HTTP threads | 4 |
| socket backlog | 8 |
| worker timeout | 30 秒 |
| graceful timeout | 10 秒 |
| keepalive | 5 秒 |
| 每 worker 最大请求 | 1000，加 0-50 随机抖动后回收 |

HTTP thread 数不等于分析并发数。多个用户可以并发登录、读取 workspace 或轮询任务，但后台分析槽位仍固定为 1。

### 7.3 PostgreSQL

| 项目 | 当前值 |
| --- | ---: |
| PostgreSQL `max_connections` | 20 |
| 应用连接池 | 最多 5 |
| 建连超时 | 2 秒 |
| statement timeout | 3 秒 |
| lock timeout | 1.5 秒 |
| 获取应用连接池槽位 | 1 秒 |
| `shared_buffers` | 64 MiB |
| `work_mem` | 4 MiB |
| `maintenance_work_mem` | 32 MiB |

关键状态转换使用 PostgreSQL advisory transaction lock 串行化。数据库连接、锁或存储故障返回清理后的 503，不向客户端泄露 DSN、schema 或密码。

### 7.4 Redis

| 项目 | 当前值 |
| --- | ---: |
| 容器内存上限 | 96 MiB |
| Redis `maxmemory` | 64 MiB |
| 淘汰策略 | `noeviction` |
| 持久化 | AOF 开启 |
| 应用连接/命令超时 | 0.5 秒 |
| 单 session 载荷 | 最多 64 KiB |
| session TTL | 30 分钟 |
| Redis 单响应 | 最多 1 MiB 或 1024 个数组元素 |
| 协调锁 TTL | 默认 5 秒；允许范围 0.1-60 秒 |

`noeviction` 的含义是 Redis 满时写操作失败，而不是静默删除 session、限流键或协调锁。应用会 fail closed 并返回 503。禁止 namespace 范围的无差别 session 清空。

来源：`deploy/ecommerce/compose.yml`、`gunicorn.conf.py`、`postgres_store.py`、`redis_runtime.py`。

## 8. 网络、容器和秘密保护

- PostgreSQL 和 Redis 只在 Docker 私有 internal network 中通信。
- app 同时加入私有网络和出站网络；生产代码只允许固定 Qwen 配置，不接受客户端指定 endpoint、模型或 Key。
- Caddy 是唯一发布 80/443 的容器，应用 5567 不映射到宿主机。
- app 以 UID/GID 10001 的非 root 用户运行，根文件系统只读，数据快照和配置不可写。
- app、Caddy 删除 capabilities；Caddy 仅增加 `NET_BIND_SERVICE`。
- 所有容器启用 `no-new-privileges`。
- 私有 `.env.v4.private` 权限应为 600，不进入 Git、发布包、备份、日志或聊天。
- `QWEN_API_KEY` 仅注入 app，分析 worker 环境使用 allowlist，不继承 provider key。
- 生产工厂缺少任一密码、session secret、账目 hash、快照或 Qwen Key 时拒绝启动。
- session secret 必须是 64-128 个十六进制字符；PostgreSQL、Redis 和 Qwen 凭据长度至少 16。

## 9. 健康检查、日志和故障行为

### 9.1 健康检查

- `/healthz` 只证明 HTTP 进程存活，不访问 Qwen。
- `/readyz` 检查配置、PostgreSQL、固定快照和历史账目归档。
- readiness 任一项失败返回 503，不输出内部路径或秘密。
- PostgreSQL、Redis、app 和 Caddy 都配置 Docker healthcheck。
- 容器使用 `restart: unless-stopped`；进程退出会重启，但单纯 health 状态变为 unhealthy 不等同于 Docker 自动重启。

### 9.2 日志

- 每个容器使用 Docker `json-file` 日志驱动。
- 单个日志文件最多 10 MiB，保留 3 个文件。
- Gunicorn access log 当前关闭，只保留 error log 和应用明确输出。
- 生产日志不得包含密码、Qwen Key、完整 cookie、Authorization、原始订单或私有环境文件。

关闭 access log 有利于减少个人数据和磁盘占用，但也意味着不能依赖容器日志获得完整逐请求访问审计。公网流量分析需要另行设计经过脱敏和轮转的访问日志，不能直接打开无限日志。

### 9.3 故障与重启

- Redis 或 PostgreSQL 不可用时认证和业务 API fail closed，返回清理后的 503。
- 模型超时、错误或坏响应不会自动重试。
- 任务取消、lease 失效或进程重启后，旧任务不能覆盖新的终态。
- 应用启动会把遗留 `accepted/running` 标记为 `interrupted`，模型 dispatch 增量必须为 0。
- 模型和数据 worker 退出时杀死其进程组，避免遗留子进程。

## 10. 数据保留、备份与恢复限制

- PostgreSQL named volume 是线上账号、workspace、task、node、结果和 `website_usage` 的持久来源。
- Redis 保存 session、限流和协调状态；业务恢复时重建 Redis namespace，不把旧 session 恢复为有效登录。
- Caddy volume 保存证书和运行配置。
- 禁止执行 `docker compose down -v`，因为它会删除持久 volume。
- 例行备份目标为 RPO 24 小时、RTO 30 分钟；计划发布切换在停止写入后备份，目标 RPO 为 0。
- 一致性备份前关闭公网分析入口并停止 app/Caddy，确认没有 `accepted/running` 任务。
- 备份必须包含数据库 dump、快照、manifest 和 SHA-256；校验通过后才可复制到实例外。
- 恢复始终写入新数据库和新 runtime，不覆盖原生产数据库。
- 回滚失败时保持公网入口关闭，保留新旧数据库、备份、日志和 receipt。
- 旧 V1/V2 单用户业务历史不迁移线上，但本地文件、失败证据、标签和账目不能删除。
- 247 条历史调试记录、已知费用、预留费用及 2 条未知 usage 必须通过归档 hash 守恒。

当前运维流程是人工执行，尚未部署自动定时备份、离机备份调度或自动恢复。不能把“文档规定每日备份”理解成服务器已经自动完成每日备份。

## 11. 建议监控阈值

以下是运维建议，不是当前代码自动执行的限制：

| 指标 | 建议预警 | 建议动作 |
| --- | --- | --- |
| 单账号 task/node | 达到 100 条 | 评审备份、导出和保留策略，禁止直接删库 |
| 单 workspace 载荷 | 达到 16 MiB | 检查节点大小和历史增长 |
| 根磁盘 | 使用率达到 70% 预警，85% 停止发布 | 清理可重建镜像缓存和已确认的临时文件；不删 volume/证据 |
| Redis | 接近 48 MiB 使用量 | 检查 session、限流键和异常增长；不改为淘汰策略来掩盖问题 |
| app 内存 | 持续超过 800 MiB | 暂停新分析，检查任务、worker 和 swap |
| 主机可用内存 | 低于 300 MiB 或 swap 持续增长 | 关闭分析入口并诊断，不提高并发 |
| 容器重启 | 任一非计划重启 | 保存日志和计数，确认没有自动模型重放 |
| readiness | 连续失败 | 关闭公网分析入口，按数据库、快照、账目顺序诊断 |
| unknown website usage | 新增任意 1 条 | 暂停相同 request 的重试，先核对供应商和持久任务 |
| TLS | 证书到期前 21 天仍未续期 | 检查 Caddy volume、DNS 和 80/443 可达性 |
| 备份 | 超过 24 小时没有新验证备份 | 暂停非必要变更并补做一致性备份 |

这些阈值需要后续接入监控或由管理员定期检查；当前没有自动告警服务。

## 12. 哪些限制可以调整

### 12.1 只改配置仍需验证

下列项目主要位于 Compose、Caddy 或 Gunicorn 配置中：容器 CPU/内存、日志轮转、Caddy 超时、Gunicorn threads/backlog、PostgreSQL内存和连接数。修改后至少需要：

1. Compose 配置校验。
2. 2 GiB 资源压力测试。
3. 两账号并发读取和单分析验收。
4. OOM、swap、重启次数和 P95 延迟检查。
5. 备份及回滚验证。

### 12.2 改代码并需完整回归

下列项目是数据或安全不变量：账号数、登录限流、session 模型、任务/节点 128 上限、任务时限、模型 3 次 dispatch、请求和响应 byte 上限、数据行数、workspace 20 MiB、日期/地区/分组范围、可信代理和 API allowlist。

修改这些值必须重新运行相关后端测试、A/B 授权矩阵、并发/取消/重启、账目守恒、Linux worker 和 2 GiB 容量验收。不能仅修改常量后直接重启生产。

### 12.3 扩容时的顺序

如果未来增加用户或并发，应按以下顺序处理：

1. 先建立监控、自动一致性备份和任务保留策略。
2. 将服务器内存提高到至少 4 GiB，再测 PostgreSQL、Redis 和 app 的新资源预算。
3. 验证全局任务协调后再增加分析槽位和 worker。
4. 根据实测调整连接池、Gunicorn 和数据库连接，而不是只提高 HTTP threads。
5. 重新做真实公网和小预算 Qwen smoke。

当前 2 GiB 部署的稳定目标仍是 1-10 个登录用户和 1 个同时执行的分析任务。

## 13. 配置与代码来源索引

- 容器资源、网络、volume、healthcheck、日志：`deploy/ecommerce/compose.yml`
- TLS、请求体和代理超时：`deploy/ecommerce/Caddyfile`
- WSGI worker、threads、backlog、timeout：`deploy/ecommerce/gunicorn.conf.py`
- 生产配置与 readiness：`py-src/data_formulator/ecommerce/production_app.py`
- 认证、session、CSRF：`password_auth.py`、`account_store.py`
- Host/Origin/可信代理：`deployment.py`
- API allowlist：`policy.py`
- 任务容量、限流、幂等和状态机：`task_store.py`、`task_service.py`、`postgres_store.py`
- Redis session、限流和锁：`redis_runtime.py`
- 模型次数、时限和记账：`governed_client.py`、`website_usage.py`、`qwen_client.py`、`model_transport.py`
- workspace 容量：`workspace_repository.py`、`v1_workspace.py`
- 指标、快照和 worker 限制：`metrics.py`、`contracts.py`、`executor.py`、`worker.py`、`process_limits.py`
- 备份、恢复与回滚：`deploy/ecommerce/OPERATIONS.md`

本文记录的是 V4 当前已部署基线。V5 功能、开放注册、OIDC、多实例分析扩容、自动归档和监控告警均不应被视为已经启用。
