# V1 运行与恢复

当前为 V1 模型协作补全版，基于旧确定性标签 `v0.2.0`，适用于 Windows 本机单用户和 local 工作区。V1 使用 LangGraph `1.2.11` 协调三个独立 Qwen Agent；详见 [协作说明](V1_MODEL_TEAM.md)。V0 的 Qwen + 单 AnalystAgent 路径保留，且仍为默认模式。

## 准备

已有验收环境无需安装或转换数据。全新机器的 Python 3.11、Node/Yarn、锁定依赖、前端构建和合法 Olist v2 数据准备，沿用 [V0 指南的准备步骤](ECOMMERCE_V0.md)，使用当前 `uv.lock` / `yarn.lock`。本机已验收 Python 3.11.16、Node 24.16.0、Yarn 1.22.22；没有重新执行新机器安装验收。

原始 CSV、派生 Parquet、工作区、密钥和费用账目不在源码中。数据许可、固定指纹及取得方式见 [来源与许可](../THIRD_PARTY_NOTICES.md) 和 V0 指南。V1 新分析需要自己的服务端 Qwen 凭据与完整有效费用账目。已有项目不得重建或删除账目；全新独立使用者的账目建立限制沿用 V0 指南。

## 显式启动 V1

在项目根目录的 PowerShell 中执行：

```powershell
$env:ECOMMERCE_ANALYSIS_ORCHESTRATOR = 'v1'
.\.venv\Scripts\python.exe -m devtools.run_ecommerce --qwen
```

打开 http://127.0.0.1:5567，Ctrl+C 停止。启动器不自动读取 `.env`，只修改 `.env.example` 不会切换模式。前端须已构建；仅前端代码改变后需要重新运行 `node node_modules/vite/bin/vite.js build`。不使用上游自由执行、通用 Docker 或桌面入口代替受限启动器。

服务端环境变量选择同一个受限 `/api/ecommerce/analyze`、`/workspace` 的 V0/V1 实现。页面从工作区响应识别模式；V1 出现“基于此分析继续追问”和“开始独立分析”。

不带 `--qwen` 可恢复已有结果，新分析返回模型未启用。历史确定性节点仍可读取，不会自动升级或产生费用。正常任务最多三次模型调用、预留 0.06 元，整个项目仍使用同一 10 元累计预算；失败不自动重试。

## 操作示例

1. 提交 `比较2018年2月与2018年1月销售额、订单数、客单价`。
2. 点击“基于此分析继续追问”，输入 `按地区分组销售金额减少最多前3`，继承父期间并按两期差额排名。
3. 再选原父问题，继续追问 `按日显示`，建立另一分支。
4. 点击“开始独立分析”后输入 `分析2018年1月销售额按地区分组销售额最高前3`，不继承父条件。

只支持订单数、商品金额、客单价，delivered 状态、下单时间、不含运费的商品价格。期间左闭右开；比较前者为当前期。未知筛选、冲突维度、模糊年月及不支持指标需要澄清；不会依据机器当前日期猜历史期间。独立问题若尚未形成完整条件，澄清时应补交完整问题；已有父条件则保留。条件、结果表与图表同源，数字由固定工具产生。

## 保存与故障恢复

V1 使用 `.local/runtime/users/<本机身份>/workspaces/ecommerce-v0/v1-session-state.json`，与 V0 的 `session_state.json` 分离。保留 `ecommerce-v0` 工作区目录名是兼容设计，不表示运行了 V0。

刷新只读取保存结果，不再次运行图或模型；恢复最近创建节点，草稿和最后浏览节点不保存。活跃任务保持 running，进程退出后的未完成任务标为 interrupted，不自动续跑。同 ID 回放原响应；改变问题或父节点需新请求。排除故障后显式发起新请求，仍保留原父节点；旧失败/中断节点保留。

工作区最多 128 节点/20 MiB，固定执行审计最多 128 条且有 20 MiB 上限。只读 worker 限时 15 秒并受进程资源限制。排序最多读取 200 个分组，集合不完整时拒绝全局排名；无排序通常最多展示 100 组。无排序的 Top N 按组键顺序；增减排名需两期，缺少可比值不填零。均价排名按未舍入值，展示值按指标规则舍入。

空记录不代表真实业务为零，覆盖外不会裁剪日期；解释/制图失败时保留已验证部分结果并标记整体失败。币种、时区和月份完整覆盖未证实。没有因果分析、多用户授权、自由 SQL/Python、自动续跑或生产级部署保证；保留既有 Flask-Session 和 Vite 构建警告。

遇到 BUSY/账目不可用时，先停止本项目服务，再核对 `.local/runtime` 的审计与 `.local/verification` 的累计费用。不得删旧执行/预算锁或清账来强行恢复。备份这两个目录时保持服务停止，备份不提交 Git。

## 回到 V0

停止 V1 服务后，在同一终端显式切回：

```powershell
$env:ECOMMERCE_ANALYSIS_ORCHESTRATOR = 'v0'
.\.venv\Scripts\python.exe -m devtools.run_ecommerce
```

这可离线浏览原 V0 工作区；V0 新模型分析按 [V0 指南](ECOMMERCE_V0.md) 配置自己的 Qwen 密钥、保留有效账目后加 `--qwen`。已有项目不得重新运行首次账目探针。当前版本内切换模式不需要检出旧提交或改依赖。

若需源码级回退，停止服务、备份私有数据并保留本地改动，在独立目录检出 `v0.1.0`；旧确定性 V1 固化点为本地 `v0.2.0`；它不是当前模型协作版。不要 reset 当前工作区或把 V1 文件覆盖为 V0 的 `session_state.json`。标签未推送，远端无法使用本地标签直到另行发布。

## 验证范围

V1-15 已完成 264 项相关后端、15 项前端、类型检查、构建、13 个 V1 浏览器场景、9 个原始 CSV 复算场景及两项真实 V0 对照。这些属于旧确定性版本；本次模型协作新增验收见 V1_MODEL_TEAM.md。摘要见 [RELEASE_V1.md](RELEASE_V1.md) 和 [validation/V1-summary.json](validation/V1-summary.json)。

已有环境需要离线回归时可运行 `python -m devtools.v1_regression` 和 `node node_modules/vitest/vitest.mjs run tests/frontend/ecommerce.test.tsx`。后端脚本使用新临时目录，不删除旧缓存。`devtools.v1_check`、`v1_reference`、`v1_verify` 是验收机工具，依赖私有历史/证据、指定位置的 Playwright Core 与 Edge，不能作为新克隆通用初始化步骤。付费 V0 对照必须显式使用 `--qwen-baseline`，不作为恢复会话的必要操作。
