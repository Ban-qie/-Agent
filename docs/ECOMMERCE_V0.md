# V0 运行与复核

## 已验收环境

Windows、Python 3.11.16、Node 24.16.0、Yarn 1.22.22。应用基于 Data Formulator `0.8b1` 固定提交；使用原 `pyproject.toml` / `uv.lock` / `package.json` / `yarn.lock`。本版验证本机单用户和 local 工作区，不是公网部署方案。

已有环境跳过安装。仅全新机器首次准备依赖时，在项目根目录执行：

```powershell
uv sync --locked --python 3.11
corepack yarn install --frozen-lockfile
node node_modules/vite/bin/vite.js build
New-Item -ItemType Directory -Force .local/verification,docs/verification | Out-Null
```

不要执行 `pip install data_formulator` 来代替本仓库；那会取得上游包。不要删除现有依赖锁、费用账目或工作区来排除故障。本次发布没有重新安装依赖；新机器的网络源可用性不属于已运行的本机验收。

## 数据准备

先阅读 [来源与许可](../THIRD_PARTY_NOTICES.md)。需合法取得 Olist v2，仅用三张文件：olist_orders_dataset.csv、olist_order_items_dataset.csv、olist_customers_dataset.csv。数据放在 `data/raw/olist-v2/`，不提交 Git。

首次下载和准备（已有相同数据时无需重复）：

```powershell
.\.venv\Scripts\python.exe -m devtools.fetch_olist
.\.venv\Scripts\python.exe -m devtools.prepare_olist
```

下载器使用官方 Kaggle API，检查版本/许可、下载大小和完整压缩包 SHA-256；不匹配就停止，不能绕过指纹或覆盖不同原文件。需要官方访问授权时先在 Kaggle 完成授权，不使用其他来源静默替换。

固定压缩包 SHA-256：`967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784`。派生快照 ID：`55d83079902eb6387b886abac02a38d22148ea24a4e9cc384dca0b0094f18d3f`；orders.parquet SHA-256：`00dd35e2b5491739f634bcc861d0905646161a7d3f7cd84657700ec2653ac95a`。转换脚本在 `data/processed/olist/<snapshot_id>/` 保存清单和质量信息。不同序列化环境若产生不同 Parquet 指纹，应复核，不能自行修改服务端允许目录来跳过验证。

## 离线启动

先完成前端构建，再执行：

```powershell
.\.venv\Scripts\python.exe -m devtools.run_ecommerce
```

打开 http://127.0.0.1:5567。离线模式可恢复已有工作区并浏览已保存结果；新分析显示模型未启用。Ctrl+C 停止，无 debugger/reloader。需要前端开发时另开终端执行 `node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort`，后端仍用上述受限入口。

## 显式启用模型

将 `qwen-api-key` 设置为服务器进程环境变量或 Windows 用户环境变量，值是自己的私有密钥。不要写进源码或浏览器；启动器不读取 `.env`，`.env.example` 仅说明配置。模型和地址固定为 Qwen Flash / 百炼北京地域；启动时才把密钥映射到服务端配置。

已有项目必须保留 `.local/verification/qwen-usage.json` 的完整累计账目，再执行：

```powershell
.\.venv\Scripts\python.exe -m devtools.run_ecommerce --qwen
```

累计预算 10 元；单任务最多 2 次模型请求、1 次固定指标工具，单次输入 16384 UTF-8 字节、输出 768 token；隐式重试/ping 关闭，调用前预留 0.02 元，未知结果不退预留。账目缺失或损坏拒绝调用。金额估计不是供应商最终账单。

**全新独立使用者的费用账目**：公开仓库不附带维护者账目。当前客户端要求有效历史至少 5 行；没有无调用的账目初始化入口。只有确实不存在任何项目历史、拥有自己的账号并同意最多 0.06 元的首次探针费用时，才可用现有有界探针建立真实账目：

```powershell
# 仅新独立项目；已有项目禁止删除账目后执行。
.\.venv\Scripts\python.exe -m devtools.qwen_smoke
if ($LASTEXITCODE -ne 0) { throw 'Probe failed; preserve the ledger and inspect the sanitized report.' }
.\.venv\Scripts\python.exe -m devtools.qwen_smoke --tools-only
if ($LASTEXITCODE -ne 0) { throw 'Probe failed; do not reset the ledger.' }
```

正常情况第一步 4 次、第二步 2 次，总上限 6 次，每次预留 0.01 元，计入后续同一累计预算。这不是当前已验收项目的重复验收步骤。本版保留此历史初始化约束；不能复制他人的报告充当自己的真实消费账目。迁移现有项目时私下迁移完整账目和工作区，不在 GitHub 上传。

## 支持的问题与口径

- `分析2018年1月销售额、订单数、客单价`
- `比较2018年2月与2018年1月销售额`（前者为当前期）
- `分析2018年1月销售额地区SP、RJ按地区分组`
- `统计[2018-01-01,2018-02-01)订单数`

只统计 delivered 已交付订单，按下单日期左闭右开筛选，销售额为商品价格合计、不含运费。按订单去重，零价订单计入分母；客单价为商品金额÷订单数。模糊期间、利润/退款等不支持口径和附加未知条件必须澄清。数字来自实际固定工具结果，模型总结不能填数。原始币种/时区和完整月份覆盖未证实；空记录不是业务零，零基准百分比为不适用，无因果归因。

## 工作区、错误与恢复

任务与结果保存在 `.local/runtime/users/<本机身份>/workspaces/ecommerce-v0/session_state.json`。刷新恢复最近创建节点，选择历史问题后可修改继续；草稿和最后浏览节点不保存。工作区上限 128 节点/20 MiB，分析和执行审计也有限额，不自动清空。

同 ID 重复请求返回原结果，条件冲突拒绝。运行中任务按系统锁判断；退出后恢复已提交审计结果，确实未完成的显示中断。反馈轮失败保留部分工具结果但整体失败。旧分析/执行/预算锁不自动删除：先确认本项目服务已停止，再核对审计与费用；不要用删账目/缓存强行解锁。备份 `.local/runtime` 和 `.local/verification` 时保持服务停止，备份不进 Git。失败处理后用户可明确发起新请求，不自动续跑。

60 秒为协作式任务截止，网络 I/O 至多 10 秒、固定 worker 15 秒，不是硬实时总墙钟保证。自由执行、上传、连接器、外部 Host/Origin 和非本机访问关闭。上游通用入口和 Docker/桌面打包不是本版受限启动路径。

## 验证与版本回退

已有依赖的离线回归无需模型密钥：

```powershell
New-Item -ItemType Directory -Force .local/verification,docs/verification | Out-Null
.\.venv\Scripts\python.exe -m devtools.v09_regression
node node_modules/vitest/vitest.mjs run tests/frontend/ecommerce.test.tsx
node node_modules/typescript/bin/tsc --noEmit --pretty false
```

后端测试使用新建项目内临时目录，不删除历史缓存。使用合成数据测试口径/资源限制和假 provider 故障注入；真实 worker、Windows Job、文件锁和 Agent 循环参与对应测试。部分脚本针对验收机私有历史（v06/v07/v08/v09_check、v09_verify），不是新克隆的通用初始化命令。

真实浏览器验收使用额外安装的 Playwright Core 1.58.2 和 Edge，未加入产品依赖锁。完整付费脚本 `devtools.v09_check` 会显式调用模型，不能当普通启动命令。本轮本机 199 项后端、12 项前端和 4 个真实模型样例通过，见 [发布说明](RELEASE_V0.md)。已知 Flask-Session 弃用、Vite 大包/eval/混合导入警告保留。

源码标签为 `v0.1.0`。回退前先停止服务、保留本地改动并备份私有运行数据；在独立目录检出标签，复用或按锁准备环境，再私下迁移兼容的账目/工作区。不得以回退为理由清零预算。V1 范围另行确定。
