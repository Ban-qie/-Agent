# V4-S07 交接

状态：通过。日期：2026-09-25。V4 已完成并停止，未进入 V5，未推送远端、未发送通知、未部署第二目标。

## 发布状态

- 香港生产入口 `https://ecominsight.cn` 正常，health 为 `ok`，readiness 四项全 true。
- 应用源码提交 `6bb52720fa05b771eeda98a0b8014fc707267b19`，生产镜像 `sha256:9db574eb3bd69b82f2587c83471257dda02b7d51fe4295a7a7f046c149f1dd2a`，镜像 label 和两个变更文件 hash 与提交一致。
- 可重建归档 `.local/v4-release/ecominsight-v4-6bb52720fa05.tar.gz`，450 个文件，SHA-256 `c9b31ce8c25000b32fcba56d0d7597095130ab3fe740595979f4cc5c1d31e3d3`，manifest SHA-256 `6603a1643a500919e0ea85accd00f11ed37a297b11d459e24826cb11b46f9b97`。
- 后端最终 425 passed，TypeScript 和前端生产构建通过；Compose、Caddy、Gunicorn 配置通过。运行、故障、预算、恢复指南与 `docs/validation/V4-release-summary.json` 已生成。

## S07 发现并修复的竞态

完整回归 attempt1 为 424 passed / 1 failed：失败任务终态已可见，但本机 semaphore 尚未释放，下一提交短暂返回 BUSY。第一次修复把释放移到终态事务前，受 SQLite `BEGIN IMMEDIATE` 轮询竞争影响未通过；相关 setup 权限、冷导入和 faulthandler 崩溃日志也保留。第二次修复通过 store 的 `before_commit` 回调，在终态事务持锁时释放槽，保证外部只能在“槽已释放且终态已提交”后观察终态。定向 11 项和完整 425 项通过，断言未放宽。

修复已免费部署。远端断网探针通过，维护 8.9 秒，真实模型调用 0、费用增量 0。部署前后 9 张表指纹、12 条 usage、1218450 估算 units、3 条生产 unknown、旧账目 hash、Attempt6 task/node 和两账号授权完全一致。PostgreSQL、Redis、Caddy 容器未重建；回滚镜像和离机 dump 均保留。

## 账目与停点

历史归档保留 247 次、已知估算 0.04992810 元、预留 4.89 元、2 条 unknown；生产保留 12 条 website usage、估算 0.00121845 元、3 条 unknown、0 unsettled。供应商最终账单未查询，不能把本地估算当结算值。Attempt6 防重放标记继续保留，V4 不再需要真实 Qwen 调用。

下一步为空。只有用户在新任务中明确选择 V5 的一个模块后才能开始；不得自动进入 V5。
