# V4 生产运行指南

适用范围：腾讯云香港 `ecominsight-hk-prod`，Ubuntu 24.04.4 LTS，公网入口 `https://ecominsight.cn`。仅受邀密码账号可登录。应用源码提交为 `6bb52720fa05b771eeda98a0b8014fc707267b19`，当前生产镜像为 `sha256:9db574eb3bd69b82f2587c83471257dda02b7d51fe4295a7a7f046c149f1dd2a`。

## 每日检查

登录服务器后，从发布目录执行：

```bash
cd ~/ecominsight-release/ecominsight-v4-5706462f1407/deploy/ecommerce
docker compose --env-file .env.v4.private -f compose.yml ps
curl --silent --show-error --fail https://ecominsight.cn/healthz
curl --silent --show-error --fail https://ecominsight.cn/readyz
df -h /
free -h
```

四个容器都应为 `healthy`，`healthz.status` 应为 `ok`，`readyz.status` 应为 `ready`，且 config/database/ledger/snapshot 四项均为 `true`。5432、6379 和 5567 不应发布到公网。根磁盘达到 70% 开始处理，达到 85% 停止发布；不得删除 PostgreSQL volume、未知费用记录或证据。

## 启动与停止

启动前先校验配置，命令不会显示私有值：

```bash
docker compose --env-file .env.v4.private -f compose.yml config --quiet
docker compose --env-file .env.v4.private -f compose.yml up -d --wait --wait-timeout 90
```

计划停机先关闭公网分析入口并确认没有 `accepted`/`running` 任务，再停止 Caddy 和 app。日常重启使用 `restart`；禁止执行 `docker compose down -v`。

```bash
docker compose --env-file .env.v4.private -f compose.yml stop caddy app
docker compose --env-file .env.v4.private -f compose.yml restart
```

## 当前容量

- 主机：2 vCPU、约 1.9 GiB RAM、1.9 GiB swap、40 GiB 磁盘。
- 目标：最多 10 个登录会话、全局 1 个分析任务、排队数 0；第二个分析快速返回 429/忙碌。
- Gunicorn：1 个 gthread worker、4 threads、backlog 8、worker timeout 30 秒。
- 单分析任务：总时限 60 秒、最多 3 次 Qwen dispatch、自动重试 0。
- 生产账号没有累计 Qwen 次数或金额配额；开发/发布 smoke 的 10 元上限不作用于网站用户。

更改并发、worker、容器内存或模型边界前，必须重新执行 2 GiB 压力测试、两账号授权矩阵、费用守恒和回滚验收。完整限制见 `docs/roadmap/V4-STABILITY-LIMITS.md`。

## 数据与账号

生产只展示固定 Olist 快照的汇总分析，不展示原始订单明细。来源为 Kaggle Brazilian E-Commerce Public Dataset by Olist v2，许可为 CC BY-NC-SA 4.0，仅作非商业演示。账号只能由管理员通过服务器端工具创建，不开放注册；密码、API Key、session secret 和数据库凭据不得进入 Git、备份、日志或聊天。

## 变更规则

每次发布先固定源码提交、镜像摘要和回滚镜像，检查 active task 为 0，停止 app 后做 PostgreSQL 一致性备份，验证 TOC 与 SHA-256，并复制到实例外。仅重建 app 时也必须确认 PostgreSQL、Redis、Caddy 容器 ID 未变化，部署前后 9 张表指纹和 usage 账目一致。发布失败时保持入口关闭并按恢复指南处理。
