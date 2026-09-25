# V4 备份、恢复与回滚指南

## 恢复目标

日常目标 RPO 不超过 24 小时、RTO 不超过 30 分钟。计划发布在停止写入后备份，目标 RPO 为 0。腾讯云控制台恢复由实例所有者执行。PostgreSQL 保存全部持久业务数据；Redis session、限流和协调状态可重建，不是业务备份。

## 一致性备份

1. 关闭公网分析入口，停止 Caddy/app，确认没有 accepted/running 任务。
2. 使用 `devtools/backup_ecommerce.py` 写入一个不存在的新目录；记录代码/schema、Olist manifest/hash、9 张表、website usage/unknown、旧账目归档和 PostgreSQL dump。
3. 验证 `manifest.json`、`manifest.sha256`、每个快照文件和 `database.dump` 的 SHA-256，并用 `pg_restore --list` 检查 TOC。
4. 将完整备份复制到实例外，权限设为仅管理员可读。凭据、`.env.v4.private`、cookie 和 Qwen Key 不进入备份。

最新发布前离机 dump：`.local/v4-s07-slot-predeploy-attempt1.dump`，32915 bytes，SHA-256 `928c34966c16a730456c2a896e712dbac1a9bef73ef59928fd757cb66c6722ca`。它是发布回滚点，不替代每日完整备份。

## 隔离恢复

恢复必须使用 `devtools/restore_ecommerce.py` 指向新的数据库名和新的 runtime 根目录。工具在创建目标库前验证 manifest、archive 和快照 hash，拒绝源库、已有目标、缺 manifest、改字节、缺 dump 或坏快照。恢复后：

- 成功/失败节点、owner、workspace、账目和快照 hash 必须与备份一致。
- 旧 session 全部失效，Redis 使用新 namespace。
- accepted/running 任务变为 interrupted，模型 dispatch 为 0。
- A 用户对象可读，B 用户同一 task/node 返回 404/`NOT_FOUND`。

原生产数据库、runtime、日志和备份在新实例全部验收通过前保持不变。

## 代码回滚

当前回滚标签 `ecominsight:v4-rollback-s07-slot-fix1-predeploy` 指向镜像 `sha256:b29cd6ccb4793a66924b9340d7dcc81f4fde4e1ac97d7a03fec8ac8c91b24632`。回滚步骤：

1. 关闭入口，保存日志并再次备份健康的数据库。
2. 将固定回滚镜像标记为 `ecominsight:v4-candidate`。
3. 只执行 `docker compose ... up -d --no-deps --no-build --pull never --wait app`，不得删除 volume。
4. 验证 health/readiness、两账号归属、9 张表指纹、usage/unknown、旧账目 hash 和自动模型 dispatch 0。
5. 若旧代码与 schema 不兼容，恢复到新数据库，而不是让旧代码盲读新 schema。

S06d 已实际回滚到旧镜像并再滚回，最终恢复 8.82 秒，所有持久指纹不变。任何恢复验收失败都保持公网入口关闭，并保留新旧数据库、镜像、备份、日志和 receipt。
