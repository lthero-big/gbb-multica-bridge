# Grok Bot Bridge × Multica

通过 Grok Bot 处理 Multica Agent 的任务，并将回答及查询、创建、编辑结果带回聊天。

- **运行端**：运行 Multica daemon、gbb 和 runtime 包装。
- **回传端**：供 Grok 的电脑/工具执行能力调用回填助手。
- 两端可同机或跨机器部署，通过本机直连或 SSH 回填，无需公网隧道。

当前包装 **0.5.3**，固定安装随仓库提供的 **gbb 0.2.0**。
整次任务默认等待 **3600 秒（1 小时）**，安装时默认发送限额为 **每个 Bot 每小时 120 条**。
配置回传路由后，每次任务只触发一次 Webhook。Bot 在同一次运行中通过回填助手持续交换工具调用与新增结果，后续不重复发送原始任务或累计上下文。0.5.3 起可从回传端通过 `update --all` 协同更新两端；Bot 一次安装稳定引导，后续从完整 inbox 获取当前协议；无回传路由的独立 gbb 保留逐轮 Webhook 兼容模式。
Issue 任务切换到此 Bridge runtime 的 Agent 后，首轮自动附上最近一次执行的平台结论、执行来源与补读入口（不预取完整日志），并提供补读入口；这不恢复旧 Bot 会话或未提交文件。交接限制与缺口标记见[工具协议](docs/tool-protocol.md#换-agent-后的交接上下文)。

## 快速开始

```bash
git clone https://github.com/lthero-big/gbb-multica-bridge.git
cd gbb-multica-bridge
bash install.sh
```

按交互菜单完成配置：

1. 准备已有 Grok Bot；首次填写凭证时，脚本会显示可复制的 webhook 配置模板。
2. 同机同用户选择**本机部署（无需 SSH）**，一次配置运行端和回传端；跨机则分别配置这两端。
3. 点击 Bot 的 **Created routine → Webhook**，从 **POST to / key** 复制 URL/key 填回终端；配置完成后，再复制终端显示的完整运行提示词交给 Bot，助手路径和路由已自动填好。
4. 在 Multica 新聊天发送“请只回复 `GBB_RUNTIME_OK`”，确认回答正常返回。

运行端支持 Linux / macOS，需要 Python 3.9+、Node.js 20+ / npm，以及已登录且 daemon 运行中的 Multica CLI；请在 daemon 用户的普通终端安装。Multica profile、工作区可使用当前配置。

回传端支持 Linux / macOS，需要 Python 3.9+；SSH 模式另需 OpenSSH。Grok 必须已获准调用这个环境，安装助手本身不会提供执行能力。安装过程不发送 Grok 请求。

## 常用入口

```bash
bash install.sh                        # 交互菜单（首项为本机部署）
bash install.sh local                  # 本机部署向导，无需 SSH
bash install.sh runtime --interactive  # 运行端向导
bash install.sh reply --interactive    # 回传端向导
bash install.sh ssh                    # 独立 SSH 免密配置
bash install.sh update --check         # 检查远端 main 和本机安装版本
bash install.sh update                 # 快进更新，复用配置重装本机组件
bash install.sh update --all --check   # 回传端：预检查全部已配置运行端
bash install.sh update --all           # 回传端：同一提交升级各端
bash install.sh --help                 # 命令行参数
bash scripts/restore-reply.sh --ssh-host YOUR_HOST --ssh-user YOUR_USER --route YOUR_ROUTE  # 回传端一键恢复
```

同机同用户选择**本机直连**；跨机器或不同用户选择 **SSH**，同机不同用户可连接 `localhost`。SSH 配置可以单独完成，再由回传端复用。

也支持带参数执行；例如本机同时运行 Grok Bot 和 Multica：

```bash
bash install.sh local --instance-id host-alice --bot bridge
```

`host-alice` 是自定义实例/路由标识，`bridge` 是已有 Bot 的凭证名称。完成 0.5.2 安装后，也可直接使用 `~/.local/bin/gbb-multica update --check` 和 `~/.local/bin/gbb-multica update`。管理入口依赖原 Git 克隆目录，请保留它；旧安装首次升级仍需 `git pull --ff-only` 后重跑原安装命令。旧 `server` / `mac` 命令兼容保留。

更新命令面向 daemon 用户的普通终端：只接受干净的 `main`，不会覆盖本地修改或切换开发分支；运行中的 Bridge 任务会阻止替换程序。跨机部署从回传端执行 `update --all`；各端先具备 0.5.3 的管理入口，首次迁移仍需分别更新一次。旧 Bot 例程需一次性粘贴稳定引导，此后协议随每次完整 inbox 提供。安装器不直接修改云端 Bot。这里更新本项目的 Bridge；Multica 官方 CLI 使用自己的 `multica update` 入口。

已验证 Multica CLI 0.6.3、0.6.5。Multica 的 JSON 输出结构经统一的宽容解析处理，并通过 `--help` 检测参数能力而非固定版本；遇到无法识别的新结构会报出命令名和顶层键名后停止，详见[Multica CLI 兼容性](docs/installation.md#multica-cli-兼容性)。

## 文档

- [安装、参数与部署示例](docs/installation.md)：SSH、同机/跨机、多用户、升级和检查。
- [回传端一键恢复](docs/installation.md#回传端一键恢复)：Grok Bot 电脑被清空后重建回填助手和路由。
- [Grok Bot 运行提示词模板](docs/grok-bot-prompt.md)：由脚本填入配置后在终端显示。
- [工具协议](docs/tool-protocol.md)：Multica 查询、创建、编辑及手动回填。
- [gbb 是否需要替换](docs/dependency-assessment.md)：当前用途、限制与替换边界。
- [gbb 0.2.0 来源与校验](vendor/README.md)：随附归档来自已验证的本机安装，不是 npm 官方发布归档。

采用 [MIT 许可证](LICENSE)。上游 [grok-bot-bridge](https://github.com/anup-a/grok-bot-bridge) 的许可证随归档保留。
