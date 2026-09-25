# V4 故障处理指南

先保存时间、容器 ID/镜像/restart count、`docker compose ps`、health/readiness、磁盘/内存和脱敏日志。不要删除容器 volume、任务、锁、unknown usage、失败证据或防重放标记，也不要用新 request ID 重试可能已经送达供应商的请求。

## 网站不可访问或 readiness 失败

1. 从服务器本机检查 `curl --resolve ecominsight.cn:443:127.0.0.1 https://ecominsight.cn/healthz` 和 `/readyz`。
2. 检查 Caddy/app/PostgreSQL/Redis 是否 healthy，端口 80/443 是否监听，磁盘是否已满。
3. `healthz` 成功而 `readyz` 失败时，按返回项依次检查 config、database、ledger、snapshot；readiness 不会调用 Qwen。
4. 持续失败时关闭公网入口，保存日志，切勿扩大 timeout 或跳过账目/快照检查。

## 登录失败

确认账号未禁用、用户名符合 3-32 字符规则、密码符合 6-30 字符规则。登录频率上限为全站 60/分钟、单来源 10/分钟、单用户名 5/分钟；429 时等待窗口自然到期，不清空整个 Redis namespace。Redis 不可用时认证会 fail closed，先恢复 Redis，再重新登录；旧 session 不应人工恢复。

## 分析 BUSY、超时或失败

- 全局只允许 1 个分析任务，没有队列；第二个提交返回 429 是预期保护。
- 单任务最多 3 次 Qwen dispatch、自动重试 0、总时限 60 秒。供应商超时或失败后保留 task 与 usage。
- 若 `website_usage` 新增 unknown，立即停止同一请求的重试，核对供应商控制台和任务状态。不能把估算 0 写成费用 0。
- 任务显示终态后仍持续 BUSY 属于回归；保存 task ID 和时间，执行 task/slot 定向测试，不直接提高并发。

## PostgreSQL 或 Redis 故障

PostgreSQL 是账号、workspace、task、node、结果和 usage 的唯一持久真相。Redis 只保存 session、限流和协调状态。任一依赖不可用时应用返回脱敏 503。恢复 PostgreSQL 前不得接受写请求；Redis 恢复使用新/空 session 状态，不从备份恢复旧登录。数据库锁、连接池耗尽或 Redis timeout 解除后，用离线 stub 验证一次正常任务，真实模型调用必须为 0。

## 内存、磁盘或容器退出

主机可用内存低于 300 MiB、swap 持续增长、app 超过约 800 MiB，或根磁盘达到 85% 时，关闭分析入口并停止新任务。保存 OOM/restart 证据；只清理已证明可重建的镜像缓存和临时文件。不能删除 named volume、备份、旧镜像、失败日志或账目。进程重启后旧 accepted/running 任务应变为 interrupted，自动模型重放必须为 0。

## TLS、DNS 或代理异常

确认 DNS A 记录仍指向生产 IP，腾讯云防火墙只开放必要的 22/80/443，Caddy volume 和 80/443 可达。错误 Host/Origin、伪造代理头、缺 CSRF、超大 body 分别应被 421/403/403/413 拒绝。不要将 Gunicorn 5567 暴露公网来绕过 Caddy。

同一根因经过两次有依据修复仍失败时，保留复现、两次补丁、全部日志、费用增量和恢复步骤，停止继续修改并申请人工提高思考强度。
