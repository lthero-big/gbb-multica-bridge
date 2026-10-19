# Grok Bot Bridge × Multica

通过 Grok Bot 处理 Multica Agent 的任务，并将回答及查询、创建、编辑结果带回聊天。

- **运行端**：运行 Multica daemon、gbb 和 runtime 包装。
- **回传端**：供 Grok 的电脑/工具执行能力调用回填助手。
- 两端可同机或跨机器部署，通过本机直连或 SSH 回填，无需公网隧道。

当前包装 **0.4.0**，固定安装随仓库提供的 **gbb 0.2.0**。

## 快速开始

```bash
git clone https://github.com/lthero-big/gbb-multica-bridge.git
cd gbb-multica-bridge
bash install.sh
```

按交互菜单完成配置：

1. 准备已有 Grok Bot 的 webhook URL/key，参考 [Bot 提示词](docs/grok-bot-prompt.md)。
2. 选择**运行端**，安装 gbb 并注册 Multica runtime / Agent；按提示输入 URL/key。
3. 选择**回传端**，配置路由，将生成的提示词交给 Bot。
4. 在 Multica 新聊天发送“请只回复 `GBB_RUNTIME_OK`”，确认回答正常返回。

运行端需要 Linux、Python 3.9+、Node.js 20+ / npm，以及已登录且 daemon 运行中的 Multica CLI；请在 daemon 用户的普通终端安装。Multica profile、工作区可使用当前配置。

回传端支持 Linux / macOS，需要 Python 3.9+；SSH 模式另需 OpenSSH。Grok 必须已获准调用这个环境，安装助手本身不会提供执行能力。安装过程不发送 Grok 请求。

## 常用入口

```bash
bash install.sh                        # 交互菜单
bash install.sh runtime --interactive  # 运行端向导
bash install.sh reply --interactive    # 回传端向导
bash install.sh ssh                    # 独立 SSH 免密配置
bash install.sh --help                 # 命令行参数
```

同机同用户选择**本机直连**；跨机器或不同用户选择 **SSH**，同机不同用户可连接 `localhost`。SSH 配置可以单独完成，再由回传端复用。

也支持带参数执行；例如同一 Linux 用户同时承担两种职责：

```bash
bash install.sh runtime --instance-id host-alice --bot bridge
bash install.sh reply --route host-alice --transport local --bot bridge
```

`host-alice` 是自定义实例/路由标识，`bridge` 是已有 Bot 的凭证名称。更新时 `git pull` 后重跑安装向导；旧 `server` / `mac` 命令兼容保留。

## 文档

- [安装、参数与部署示例](docs/installation.md)：SSH、同机/跨机、多用户、升级和检查。
- [Grok Bot 提示词](docs/grok-bot-prompt.md)：webhook 与回填说明。
- [工具协议](docs/tool-protocol.md)：Multica 查询、创建、编辑及手动回填。
- [gbb 0.2.0 来源与校验](vendor/README.md)：随附归档来自已验证的本机安装，不是 npm 官方发布归档。

采用 [MIT 许可证](LICENSE)。上游 [grok-bot-bridge](https://github.com/anup-a/grok-bot-bridge) 的许可证随归档保留。
