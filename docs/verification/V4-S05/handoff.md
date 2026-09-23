# V4-S05 交接

状态：通过。日期：2026-09-24。最终发布候选 commit：`857a99990d1a15edf9b09df128e6e9f24e50f1ea`。S06 前未修改 DNS、未远程部署、未开放 80/443、未调用真实 Qwen。

## 结果

- S05a `release-manifest-attempt3.json`：从最终 commit 生成 449 文件归档；attempt3 与 attempt2 的非 manifest 文件 448 个逐文件 hash 一致。Olist snapshot 两文件 hash、固定依赖、许可证、运行版本、V3-only 迁移范围和无凭据声明均记录。
- S05a `release-build-attempt1.json`：首次从全新解压目录完成前端构建、锁 hash wheel 构建和生产镜像导出；镜像约 773,779,053 bytes。
- S05a `release-build-attempt2.json`：加入回滚工具后的候选从新目录构建成功；镜像 digest `sha256:26ab1eadc873cec64892eed0298885d594334538c0ef16ac9225f0222335a1f2`。
- S05a `release-build-attempt3.json`：最终只增加 Portfolio 文档，Docker 输入 hash 不变，因此复用 attempt2 镜像并保留 Docker Hub cache lookup 失败记录。
- S05b `secret-scan-attempt2.json`：发布树与 S01-S05 证据/日志秘密命中 0，私有路径和凭据文件均为 0；历史中的 4 项均为测试假凭据/合成 token，未部署、未记录真实值。因 partial clone 缺失对象，扫描仅覆盖 32 个完整本地 commit，未触发远程 fetch。
- S05c `rollback-attempt3.json`：使用 S03c 已验证备份恢复到新数据库；2 个 active task -> interrupted，session 2 -> 0，损坏备份拒绝，模型 dispatch 0，模拟恢复失败后 public entry 保持关闭。旧 commit `a780bd18` 不含 PostgreSQL store 或 production factory，兼容边界拒绝旧代码盲读新 schema。attempt1/2 的夹具校验失败证据保留。
- S05d `demo-script.md` 和 `docs/PORTFOLIO.md`：演示包含登录、三指标/趋势、追问、失败、刷新、重启和跨用户拒绝；样本范围、来源许可、2 GiB 限制、费用和 V5 未启用均可定位到证据。
- S05a `release-manifest-attempt4.json`：文档决策提交后重新生成 449 文件归档；来源 commit 为 `857a99990d1a15edf9b09df128e6e9f24e50f1ea`，归档 SHA-256 为 `bcc62a0dd816ae3cfa805820a60b17c72373c37d6d0749d22bf33743436fcd5f`，manifest SHA-256 为 `f9573395976b393b175954a4b8fdb7b41d8c573a4560b8402530782b2f85aa8c`。旧 attempt1-3 保留，不覆盖。

## 边界

- 发布包不包含 `.local`、凭据、原始订单、私有工作区、逐次费用或 `docs/verification` 证据。V3 多用户业务备份另行校验和传输。
- 最终包只证明可审阅、可重建和可回滚；不能替代香港公网、DNS/TLS、两用户实际访问或真实 Qwen smoke。
- 历史 Qwen 调试账目仍为 247 条、2 条未知 usage，S01-S05 真实 Qwen 增量为 0，旧账目 hash 未变。

下一步严格为 V4-S06a：在香港目标执行部署前磁盘、权限、依赖、端口、备份和回滚点检查；继续使用该最终 commit/归档，DNS 和公网开放留到免费验证前的明确步骤。
