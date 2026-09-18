# V3 本地使用与验收边界

V3使用管理员分配的账号密码，不启用QQ。原单用户V0/V1入口与历史保留；新用户不会自动继承本机旧节点。HTTP仅127.0.0.1:5567，页面/multiuser，不对外开放。正式入口会复用V1的服务端Qwen配置与传输；网站运行账目写入隔离数据库，不使用调试campaign的10元上限。

## 账号

在项目PowerShell中用已有.venv；密码由交互输入并显示星号，不放命令行或聊天。账号上限10，密码6—30字符；内部UUID稳定。禁用/改密立即撤销旧会话，每账号仅一个有效登录。

```powershell
.\.venv\Scripts\python.exe -m devtools.v3_accounts create alice
.\.venv\Scripts\python.exe -m devtools.v3_accounts password alice
.\.venv\Scripts\python.exe -m devtools.v3_accounts disable alice
```

默认仅写.local/v3/multiuser.sqlite，不修改原.local/runtime与真实账目。此命令不启动服务，不开放注册，不发送邀请。

## 启动真实 Qwen 网站

先在当前用户环境配置 `qwen-api-key`，再创建受邀账号。启动不会自动发起模型请求；只有登录用户提交分析时才调用 Qwen。密钥只注入进程环境，不写入账号库、日志或命令行。

```powershell
.\.venv\Scripts\python.exe -m devtools.v3_accounts create alice
.\.venv\Scripts\python.exe -m devtools.run_v3
```

网站产生的预留、未知费用和实际 usage 写入 `.local/v3/multiuser.sqlite` 的 `website_usage`，与 `.local/verification/qwen-usage.json` 调试账目分开。V3 仍限制单任务调用次数和运行时间，防止单次请求失控；不限制网站累计金额。

## 可复验浏览器出口

先确认5567端口空闲；工具若检测占用会停止，不关闭现有服务。复用已构建的dist及已有Playwright/Edge，不安装依赖。每次使用新的attempt目录；禁止覆盖旧证据。离线浏览器工具仍固定使用OfflineModel，不代表真实Qwen入口。

```powershell
$env:NODE_PATH='C:\Users\Kobe Bryant\AppData\Local\AgentProjectTools\browser\node_modules'
node devtools/v3_browser.cjs docs/verification/V3-S09/browser-new-attempt
```

工具创建两个随机密码的真实本地账号，凭据只通过stdin传给隔离服务，不写日志；两个浏览器context通过实际密码表单登录。模型固定为OfflineModel，实际只读指标worker及原业务图照常运行。使用合成临时预算验证费用逻辑，不能把这个预算视为真实Qwen新campaign；真实247行总账只读。完成后只关闭工具自身服务，保留数据库、日志、截图及失败。重启复用同一隔离库及会话secret，不重跑旧任务。

## 恢复与限制

- 数据库用SQLite backup API备份，不手动复制活动WAL。旧V1迁移只读源→显式owner→临时库→逐节点比对→独立新目标；V0继续原方式恢复。
- 费用副本迁移采用.migrated标记切断旧writer；不得删除标记、真实锁或未知预留。若恢复代码，先关闭收费入口并保留新总账的全部reservation，不能回退到旧额度。
- 任务60秒、每用户1/全局2、队列0；提交每用户6/全局12每分钟；HTTP4线程、accept最多等待1秒、backlog8，属本地验收实现。查询不续租/不重跑；lease过期未知步骤标interrupted。
- 本轮只验收Windows Job资源限制；正式外网域名、HTTPS、反向代理、生产WSGI服务器及部署仍属于V4，尚未验收。
- QQ审核通过后单独适配并绑定已有内部UUID，不按昵称/邮箱自动合并用户，不重建历史或清零账目。本轮不预先实现QQ。
