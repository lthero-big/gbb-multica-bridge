# 安装与配置参考

[返回首页](../README.md)。首次使用推荐在仓库目录执行 `bash install.sh`，按交互菜单配置。

不带参数进入菜单；`runtime --interactive`、`reply --interactive`、`ssh` 可直接进入对应向导。向导复用已有配置，执行前展示设置并确认；按 Ctrl+C 或输入 `n` 取消。可选项留空采用显示的默认值，输入 `-` 清空。

带参数执行仍受支持。非交互环境须明确指定模式及必填参数；已有 webhook 和 SSH 认证配置可复用。URL/key、密码不通过命令参数传入。

## 角色与准备条件

| 入口 | 职责 | 条件 |
| --- | --- | --- |
| `runtime` 运行端 | 安装固定版本 gbb、包装、endpoint，注册/复用 Multica runtime 和 Agent | 当前支持 Linux；Python 3.9+、Node.js 20+、npm；已登录 Multica CLI 且 daemon 运行 |
| `reply` 回传端 | 安装回填助手、维护路由、生成 Bot 提示词 | Linux 或 macOS；Python 3.9+；SSH 路由另需 OpenSSH；Grok 能调用这个环境 |
| `ssh` 连接配置 | 单独生成/复用密钥、追加公钥、检查免密连接 | Linux 或 macOS；Python 3.9+、OpenSSH；不需要 Multica、gbb 或 endpoint |

运行端安装必须在 **daemon 用户已登录 Multica 的普通终端**执行，不能借用聊天任务注入的 `mat_` token。已验证 Multica CLI 0.6.3、Node 22.22.0。回传端无需 Multica 登录凭证；SSH 回传端也不需要本地 gbb。

旧命令 `server` 是 `runtime` 的兼容别名，`mac` 是 `reply` 的兼容别名；原命令和已有路由可继续使用。为兼容已安装的 endpoint，运行端配置文件仍叫 `server.json`，无需手动迁移。

## 选择连接方式

| 部署情况 | 回传方式 | 配置 |
| --- | --- | --- |
| 同一台机器、同一系统用户 | `local`，直接调用本机 endpoint | 无需 SSH、SSH key 或 SSH 服务 |
| 同一台机器、不同系统用户 | `ssh`，登录运行端用户 | SSH 目标可用 `localhost`，需要该机 SSH 服务 |
| 不同机器，例如 Mac → Linux、Linux → Linux | `ssh` | 使用已有免密连接，或先运行独立 SSH 向导 |

本机模式使用**当前用户**的运行端配置，不自动切换账号，也不放宽 `/root` 权限。运行端当前支持 Linux，因此同机部署也以 Linux 为前提。

## 安装流程

### 1. 准备已有 Bot 的 webhook

把 [Bot 提示词模板](grok-bot-prompt.md) 交给已有 Grok Bot，创建或更新 webhook routine，从面板取得 URL/key。已有 Bot 和 routine 可复用；不必再创建。名称可用 `bridge-host-alice` 方便识别。两端安装后，再把回传端生成的实际提示词交给 Bot，替换模板占位符。

### 2. 配置运行端

推荐菜单选择“运行端”。命令行等价示例：

```bash
bash install.sh runtime --instance-id host-alice --bot bridge-host-alice
```

只有平时使用命名 Multica 登录配置时才加 `--profile`；不需要创建一个叫 alice 的 profile。要指定工作区，可运行 `multica workspace get --output json` 查 `id`，然后传入真实 UUID：

```bash
bash install.sh runtime \
  --instance-id host-alice \
  --bot bridge-host-alice \
  --profile alice-login \
  --workspace-id 实际工作区UUID
```

脚本校验归档并离线安装 gbb 0.2.0 到 `~/.local/share/gbb-multica/npm`，复用或交互保存 URL/key，安装包装和 endpoint，然后注册/复用 profile、Agent。不会发送 Grok 请求或创建 Grok Bot。URL/key 通过标准输入交给 gbb 的凭证存储，不放入进程参数。

默认 Agent 名为 `Grok Bot Bridge · host-alice`。升级已有 Agent 时，用 `--agent-name` 填原名称；先结束正在运行的请求。安装脚本拒绝修改仍有任务的 Agent。

### 3. 配置回传端

菜单选择“回传端”，再选择本机或 SSH。如果需要新建 SSH 免密连接，可在向导里选择，也可以提前单独配置。

同机同用户的命令行示例：

```bash
bash install.sh reply --route host-alice --transport local --bot bridge-host-alice
```

跨机器或用户，使用已有免密 SSH alias：

```bash
bash install.sh reply --route host-alice --ssh-alias multica-alice --bot bridge-host-alice
```

脚本发现 endpoint 的绝对路径，验证目标实例、gbb 0.2.0 和配置可读状态，然后合并保存路由，安装 `~/.local/bin/gbb-multica-remote`，生成 `~/.config/gbb-multica/grok-bot-prompt.md`。把该提示词交给对应 Bot，并让它的工具执行环境能够运行这个助手。

默认 `route` 与运行端实例名相同。运行端使用不同的 `--reply-route` 时，回传端须同时设置 `--route 回填路由名 --instance-id 实际实例名`。

### 4. 验证完整问答

在 Multica 为该 Agent 新开聊天，只发送一次：

> 请只回复 `GBB_RUNTIME_OK`。

Grok 根据本轮 `next`，在回传端执行类似命令，使用实际助手路径、route 和消息 ID：

```bash
"$HOME/.local/bin/gbb-multica-remote" answer host-alice 本轮消息ID - <<'GBB_REPLY'
GBB_RUNTIME_OK
GBB_REPLY
```

Multica 显示回答且运行成功结束，才代表完整打通。`--check` 成功只能证明配置链路；Bot 的实际执行授权需要完成这次问答验证。之后可以请求“列出当前工作区项目”；创建、编辑时应明确目标和字段，由运行端包装使用当前任务身份执行。

默认整次任务等待 900 秒，最多 8 次 Grok 请求。请在本轮任务仍等待时回填；超时后补写答案不会恢复已结束的任务，不要盲目重发请求。

## 独立 SSH 入口

无需先安装任何一端，直接运行：

```bash
bash install.sh ssh
```

按提示输入连接别名、目标域名/IP、系统用户名、端口和本机私钥路径。也支持命令行：

```bash
bash install.sh ssh \
  --ssh-alias multica-alice \
  --ssh-host your-host.example \
  --ssh-user alice \
  --ssh-port 22
```

流程为：生成/复用专用密钥 → `ssh -i` 尝试连接 → 必要时输入一次目标账号密码追加公钥 → 验证免密 → 保存独立连接。密码由 SSH 自己从终端读取，不进入脚本变量、参数或配置文件。首次连接时仍需核对并确认主机指纹。保留其他 `authorized_keys` 条目，按公钥内容去重；免密已可用时不会再次要求密码。

私钥默认 `~/.ssh/gbb_multica_别名_ed25519`，新建时为无口令 Ed25519，权限 600，供自动回填使用；私钥只留在发起 SSH 的这台机器。指定已有私钥可加 `--ssh-key "$HOME/.ssh/id_ed25519"`。已有加密私钥请先加载到 `ssh-agent`；安装时从私钥核对公钥也可能要求密钥口令，这与目标登录密码分开。

目标需允许密码认证且使用 `~/.ssh/authorized_keys`。不支持密码认证、使用 MFA 或其他公钥路径的环境，应自行配置免密 alias 后使用。脚本不会修改目标的密码认证策略。

专用配置保存于 `~/.ssh/gbb-multica/别名.conf`，连接目录保存于 `~/.config/gbb-multica/ssh.json`。回传端通过 `--ssh-alias` 自动复用；原有 `~/.ssh/config` 保留。手动登录及只读检查：

```bash
ssh -F "$HOME/.ssh/gbb-multica/multica-alice.conf" multica-alice
bash install.sh ssh --ssh-alias multica-alice --check
```

已有 `~/.ssh/config` alias 也可以直接用于回传端，不必导入。例如：

```sshconfig
Host multica-alice
    HostName your-host.example
    User alice
    IdentityFile ~/.ssh/id_ed25519_multica_alice
    IdentitiesOnly yes
```

需要在配置回传端时一起引导 SSH，保留了这个入口：

```bash
bash install.sh reply --route host-alice --setup-ssh --ssh-host your-host.example --ssh-user alice
```

连接使用系统 OpenSSH；参见 [ssh 的 -i/-F 参数](https://man.openbsd.org/ssh.1) 与 [SSH 配置说明](https://man.openbsd.org/ssh_config.5)。

## 参数说明

未指定参数通常复用上次设置；首次使用的默认值见下表。`alice`、`host-alice` 都是示例名称，不要求创建同名系统用户、profile 或 Bot。

| 参数 | 用于 | 含义与首次默认值 |
| --- | --- | --- |
| `--interactive` / `-i` | 三个入口 | 逐项询问和确认，可与预填参数一起使用 |
| `--check` | 三个入口 | 只读检查，不发 Grok 请求；runtime 连接 Multica；reply 检查 endpoint；ssh 仅验证免密登录 |
| `--dry-run` | 三个入口 | 仅展示步骤，不连接或写文件 |
| `--instance-id host-alice` | runtime / reply | 部署稳定标识；runtime 默认“主机短名-用户名”，reply 默认 route；不是 profile、工作区或 Bot ID |
| `--bot bridge-host-alice` | runtime / reply | runtime 的 gbb 凭证 alias/请求目标；reply 的 Bot 名称标注；默认 `bridge-实例名`；已有名称是 `bridge` 时填 `bridge` |
| `--profile alice-login` | runtime | 已有 Multica 登录配置名；默认 CLI 当前配置，不创建新配置，也不是系统用户名 |
| `--workspace-id UUID` | runtime | 真实工作区 UUID；默认读取当前工作区，并核对本机 daemon 关联 |
| `--reply-route ROUTE` | runtime | 请求发给 Grok 的回填路由；默认 instance-id，必须对应 reply 的 route |
| `--agent-name NAME` | runtime | 要创建/复用的 Agent；默认 `Grok Bot Bridge · 实例名`；升级旧 Agent 用原名称 |
| `--gbb-home DIR` | runtime | 当前 daemon 用户的 gbb 数据目录；默认 `~/.grok-bot-bridge` |
| `--route ROUTE` | reply | 回填路由，必须对应 runtime 的 reply-route；也可用 instance-id 提供同名路由，否则询问 |
| `--transport local/ssh` | reply | 同机同用户直连或 SSH；命令行首次默认 ssh，后续复用路由设置；向导会让用户选择 |
| `--endpoint PATH` | reply | **运行端** endpoint 绝对路径；默认发现其 `~/.local/bin/gbb-multica-endpoint`，不是回传端任意路径 |
| `--ssh-alias HOST` | ssh / reply | 连接别名；可复用独立 SSH 配置或 `~/.ssh/config`；回填目标用户必须是 daemon 用户 |
| `--setup-ssh` | reply | 配回传路由前先引导 SSH；独立 ssh 入口不需要此参数；不能与 check/local 同用 |
| `--ssh-host HOST` | ssh / reply 的 setup-ssh | 目标域名/IP，不含用户名或端口；同机其他用户可填 localhost；首次询问 |
| `--ssh-user USER` | 同上 | 目标系统用户，回填时为 daemon 用户；不是 Multica profile；首次询问 |
| `--ssh-port PORT` | 同上 | SSH 端口，1–65535；默认 22 |
| `--ssh-key PATH` | 同上 | 发起 SSH 机器上的私钥路径，等价于 ssh -i；默认专用密钥，缺失则生成 |

实例名、route、SSH alias 只允许字母、数字、点、下划线和连字符，长度 1–80，以字母或数字开头。Agent、Bot 名称包含空格时需在命令中加引号。多行命令需要行末 `\`，且后面不能有空格。

| 运行端设置 | 回传端设置 | 对应规则 |
| --- | --- | --- |
| `--instance-id host-alice` | `--route host-alice` | 默认路由和实例同名 |
| `--instance-id host-alice --reply-route alice-reply` | `--route alice-reply --instance-id host-alice` | 路由对应路由，实例对应实例 |
| `--bot bridge-host-alice` | `--bot bridge-host-alice` | 提示词标注一致；回填目标仍由 route 决定 |
| 系统用户 alice 运行 daemon | 本机同用户，或 SSH 的 User alice | 必须读写运行端同一份 gbb 消息 |

## 示例：Linux 运行端 + Mac 回传端

以 Linux 运行 Multica、Mac 登录 Grok Bot、已有 `bridge` webhook 为例。运行端使用当前 Multica 登录配置，无需额外创建 profile：

```bash
# 运行端：实际 daemon 用户的普通终端；升级原测试 Agent
bash install.sh runtime --instance-id linux-root --bot bridge --agent-name 'Grok Bot Bridge 测试'

# 回传端：这个示例在 Mac；也可以是具备执行能力的 Linux 环境
bash install.sh reply --route linux-root --ssh-alias multica-root --bot bridge
```

`multica-root` 是连接实际 daemon 用户的 SSH alias。这里仅保留场景和示例名，没有真实主机地址、工作区 UUID、邮箱、凭证或运行日志。不是 root 时请使用实际用户，不要复制 `/root` 路径或放宽权限。

**两种职责都在同一台 Linux、同一用户时**，可直接：

```bash
bash install.sh runtime --instance-id host-alice --bot bridge
bash install.sh reply --route host-alice --transport local --bot bridge
```

仍须让 Grok 能在这台机器执行回传助手。如果同机不同用户，则在 SSH 向导选择 `localhost` 和实际运行端用户名。

## 多用户与多个 Bot

不强制一人一个 Bot；独立使用时建议按用户/实例分别命名 Bot 和 webhook，例如 `bridge-host-alice`、`bridge-host-bob`，便于查看和维护。共用 Bot 时必须按请求的 `reply_route` 回填，不能根据显示名称猜测用户。

每个 Linux 系统用户一套 gbb 实例、daemon 和数据目录，可各自使用 Multica 的默认 profile，也可以选已有命名 profile。安装器匹配 runtime profile ID、当前 daemon ID 和 online 状态，Agent 则保存对应的实例、路由、GBB_HOME。回传端可以保存多条路由，添加时会保留其他路由。

命名 profile 或 Bot 名称不提供系统隔离。此安装器按**每个 Linux 用户一个实例**设计；同用户切换实例时会停止提示核对。需要账号隔离时使用独立系统用户、容器或 VM。共用 Bot/执行环境也会共享一部分访问能力。

## 检查、升级与手动回填

```bash
bash install.sh runtime --check
bash install.sh reply --route host-alice --check
bash install.sh ssh --ssh-alias multica-alice --check
bash install.sh runtime --dry-run
bash install.sh reply --dry-run
bash install.sh ssh --dry-run
"$HOME/.local/bin/gbb-multica-remote" check host-alice
"$HOME/.local/bin/gbb-multica-remote" inbox host-alice
```

升级时 `git pull` 后重跑安装向导或原命令。已有资源复用，变更的配置文件先备份；不会自动发测试消息。`runtime --check` 返回 2 表示 Multica 配置尚需修正，其他非零状态表示检查/权限/连接失败。

先手动验证也可以：在运行端同一用户、同一 GBB_HOME 下查询 inbox，并对**本轮新消息**回填：

```bash
"$HOME/.local/share/gbb-multica/npm/bin/gbb" inbox
"$HOME/.local/share/gbb-multica/npm/bin/gbb" answer 本轮消息ID - <<'GBB_REPLY'
GBB_RUNTIME_OK
GBB_REPLY
```

使用自定义 GBB_HOME 时需在这个终端先设置为安装时的值。工具能力、回填 JSON 和权限边界见 [工具协议](tool-protocol.md)。

## 目录与版本来源

| 路径 | 内容 |
| --- | --- |
| `install.sh` / `install.py` | 交互菜单与命令行安装入口 |
| `ssh_setup.py` | 通用 SSH 专用密钥、公钥追加及免密验证 |
| `gbb-multica-agent` | Copilot JSONL 协议的 Multica runtime 包装 |
| `gbb-multica-setup.sh` | 独立 runtime/Agent 注册脚本，内嵌同版本包装 |
| `gbb-multica-endpoint` | 运行端固定 check/inbox/answer 入口 |
| `gbb-multica-remote` | 回传路由助手，支持本机或 SSH |
| `vendor/` | gbb 0.2.0 归档、校验值及上游许可证 |

gbb 0.2.0 归档来自本机已安装、通过联调的程序执行 `npm pack --ignore-scripts`，**不声称是 npm 官方 0.2.0 发布归档**。包含编译 JS、示例和文档，无运行时 npm 依赖，无本机配置、凭证或消息。上游为 [anup-a/grok-bot-bridge](https://github.com/anup-a/grok-bot-bridge)，按 MIT 保留归属。

## 验证

在 Linux、Node.js 20+、Python 3.9+ 环境执行：

```bash
python3 test_gbb_multica_compat.py
python3 test_gbb_multica_agent.py
python3 test_gbb_multica_setup.py
python3 test_gbb_multica_install.py
python3 test_ssh_setup.py
# 工具测试还需要 Multica CLI，用于真实 --help 参数契约；业务读写为隔离模拟
python3 -m unittest -v test_gbb_multica_tools.py
```

测试使用临时数据、环回 webhook、模拟 Multica API 和 SSH，不发生产 Grok 请求。安装测试覆盖离线 npm、命令别名、交互菜单/确认/取消、本机回填、独立 SSH 连接复用和实例校验；通过真实 PTY 检查无参数菜单能在终端显示。

SSH 测试覆盖追加、去重、配置保留、权限、失败不重试及专用配置传递。具备 root、sshd 和 `/run/sshd` 时会启动仅监听环回地址的临时 sshd，以临时初始密钥代替密码验证真实公钥复制和后续登录；不修改宿主账号 authorized_keys。真实 Bot 执行授权、Mac 客户端和密码输入仍需部署后完成完整问答验收。
