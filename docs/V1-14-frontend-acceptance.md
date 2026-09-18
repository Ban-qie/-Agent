# V1-14 受限前端验收

2026-09-18，high。受限 `/api/ecommerce/workspace` 返回 `orchestrator: v1` 后适配 V1 节点；同一受限 analyze API 由服务端显式环境变量选择 V1，默认仍为 V0。新增独立分析、显式父节点追问、父节点说明、条件/排序/Top N 展示和后端图表规格适配。表格/柱状/趋势图只使用已验证结果；失败部分结果和 partial 均不显示为完整成功。

集成检查修复了 V1 读取 running 节点即中断的缺陷：使用每请求 OS lease；活跃读取保留 running，重复请求不执行图，进程退出后读取标记 interrupted；原 ID 不自动重跑。图异常脱敏并保存为失败。未修改原 V0 工作区格式。

验收：前端 14 passed；V1 服务/工作区/路由 12 passed；TypeScript 和生产构建通过，保留上游 eval/大包/动态导入警告。真实 Edge 六项业务场景：期间比较、两个独立父分支（地区与按日趋势）、空、覆盖外、澄清；同 ID 重复提交逐项一致，刷新零分析 POST，实际服务重启后的读取恢复零分析 POST。V0 页面恢复既有真实结果并同 ID 回放通过。浏览器注入的预算失败部分结果与 partial 仅证明展示行为，不称为真实业务故障。

本地详细证据：`docs/verification/V1-14-browser-*.json`、`V1-14-usage.json`、`V1-14-integrity.json`；截图 `.local/verification/V1-14-*.png`。浏览器页面错误 0，390px 无整页横向溢出。运行 `python -m devtools.v1_check V1-14` 可复查（创建新离线历史节点，不删除旧记录）。

新增模型调用 0；账目字节保持不变。当前账目哈希以 V1-14-usage 为准，不沿用较早 V0 文档的历史哈希。原始三 CSV/Parquet 哈希未变，密钥精确扫描 0 命中，依赖无改动，5173/5567 已停止。V1 当前业务图是确定性处理器，尚未接入 Qwen 角色推理，不能宣称已完成多模型 Agent 协同。

V1-14 完成后按同强度规则直接进入 high / V1-15；V1-16 要求 low，届时停止等待确认。未推送远端。
