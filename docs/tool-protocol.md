# Multica 工具能力与回填协议

包装版本 0.4.0；固定 gbb 0.2.0。安装方式见仓库 README。以下 ROUTE、MESSAGE_ID、request_id 均须替换为本轮真实值。回传端通过路由选择本机直连或 SSH；运行端手动验证可使用同用户同 GBB_HOME 的 gbb answer。

## 使用

安装后新开对应实例 Agent 的聊天，发送例如：

> 列出当前工作区的项目，显示名称、状态和 ID。

> 创建一个名为“Bridge 功能验收”的项目，描述为“验证项目创建和编辑”；创建后将描述更新为“验证完成”，再回读结果。

第二条会真实创建和编辑项目，仅在需要该测试项目时发送。业务操作也是直接描述目标及要修改的字段。

包装会把工具调用和执行结果显示到当前运行中。权限不足会返回 CLI 的错误；不会借用机器所有者登录绕过权限。

## 已实现的操作

| 对象 | 操作 |
| --- | --- |
| 当前工作区、成员 | 查询当前工作区，列出成员 |
| 项目 | 列表、详情、创建、编辑、状态、删除 |
| 项目资源 | 查询、挂载、编辑、移除仓库或目录资源 |
| 仓库登记 | 列出、添加、移除；与项目资源分别管理 |
| Issue | 查询、搜索、创建、编辑、状态、指派、父子关系、阶段、排序、执行历史、重跑指定执行、关联 PR |
| 评论 | 分层查询、创建、编辑、删除、解决和重新打开线程 |
| Issue 标签、属性、元数据 | 读取、添加或设置、移除 |
| 唤醒规则 | 查询、创建、编辑、禁用、删除、触发 |
| Agent | 查询、创建、编辑、复制、归档、恢复、执行历史、绑定技能和已有 MCP |
| Runtime | 查询、活动、用量、重命名；查询 profile |
| 标签、属性定义 | 查询、创建、编辑、删除或归档；以平台权限为准 |
| Squad | 查询、创建、编辑、归档，维护成员和角色 |
| Autopilot | 查询、创建、编辑、删除、手动触发、维护日程或 webhook 触发器 |
| 技能 | 查询列表和详情 |
| 附件 | 将本轮生成的 UTF-8 文本文件上传到当前聊天 |

共映射 109 个当前 CLI 子命令；这是支持的命令数，不代表每个当前任务均有写权限。属性定义等操作仍受平台角色限制。

不提供任意 shell、读取主机文件、登录切换、daemon 控制、跨工作区操作或明文凭证管理。模型选择和会话恢复仍不支持；Agent 的模型、自定义启动参数、思考档位和服务档位应留空。

## 手动回填协议

每一轮通过回传端助手查询；运行端同用户也可直接使用对应 GBB_HOME 的 gbb inbox：

```bash
"$HOME/.local/bin/gbb-multica-remote" inbox ROUTE
"$HOME/.local/bin/gbb-multica-remote" inbox ROUTE MESSAGE_ID
```

完整问题末尾的 `REQUEST_JSON` 给出本轮 `request_id`、当前任务、剩余请求数和此前工具结果。`request_id` 是包装生成的校验值；`MESSAGE_ID` 是 gbb 消息 ID，二者不同。

要查询项目，向**本轮**消息回填：

```bash
"$HOME/.local/bin/gbb-multica-remote" answer ROUTE MESSAGE_ID - <<'GBB_REPLY'
{"gbb_tool_protocol":1,"request_id":"复制本轮 request_id","tool_calls":[{"id":"projects1","name":"multica","arguments":{"args":["project","list"],"fields":["id","title","status"]}}]}
GBB_REPLY
```

包装执行后会发出一条**新消息**。继续读取该消息，在其中查看 `history` 里的真实结果，然后回复最终回答或下一批工具调用：

```json
{"gbb_tool_protocol":1,"request_id":"新消息的 request_id","final":"已查到项目，结果如下……"}
```

普通文字也可作为最终回答；它会结束本次运行，不执行其中出现的命令。

创建和编辑使用同一个 `multica` 工具。例如，先查询现状，再创建 Issue：

```json
{"gbb_tool_protocol":1,"request_id":"本轮 request_id","tool_calls":[{"id":"create_issue1","name":"multica","arguments":{"args":["issue","create","--title","任务名称","--status","backlog","--description-file","@body.md"],"files":{"body.md":"中文正文\n第二行\n保留字面量与引号。"}}}]}
```

拿到真实 Issue ID 后，在下一轮编辑：

```json
{"gbb_tool_protocol":1,"request_id":"下一轮 request_id","tool_calls":[{"id":"edit_issue1","name":"multica","arguments":{"args":["issue","update","实际 Issue ID","--title","修改后的任务名称"]}}]}
```

编辑后再用 `issue get` 回读。不要用示例占位符执行实际写入。

参数不确定时使用 `multica_help`，如 `{"command":["project","update"]}`；空数组列出支持的操作。命令参数以字符串数组传递，省略 `multica`、`--output` 和全局选项。正文或附件路径只能引用 `files` 中的 `@文件名`，不能读取运行端上的既有文件。评论必须使用 `--content-file`；编辑评论还需要查询结果中的 `--expected-revision`。

重跑指定历史执行使用 `multica_help` 的 `{"command":["issue","rerun-task"]}` 查看当前 CLI 能力，再通过 `multica` 传入 `{"args":["issue","rerun-task","完整 run UUID"]}`。短 run ID 需加 `--issue ISSUE-ID`；当前 CLI 默认在安全时复用原目录和会话，`--fresh` 创建新会话和目录。此操作会启动执行，按写操作记账、防重复，不使用所有者凭据绕过任务权限；失败或超时后先查询执行历史，避免盲重跑。旧 CLI 不提供此命令时仍报能力缺失。

查询大结果时可用 `fields` 选取顶层字段。超过 25,000 字符的结果返回 `result_id` 和 `next_offset`，通过 `multica_result` 分页读取，最多每次 40,000 字符。

## 超时、重复和费用

- 新 Agent 仅切换到 Bridge runtime、未设置路由环境变量时，包装读取同系统用户的 `~/.config/gbb-multica/server.json`（或 `GBB_MULTICA_CONFIG_HOME`）作为本机部署默认值，核对工作区和消息目录。显式路由必须同时提供 `GBB_INSTANCE_ID` 和 `GBB_REPLY_ROUTE`；不完整或冲突配置在发送前报错。没有安装配置的独立 gbb 仍兼容原无路由模式。
- 路由与消息 ID 已验证、但 Bot 无法处理时，使用 `gbb-multica-remote fail ROUTE MESSAGE_ID -`，从 stdin 传入失败原因。包装收到后结束并向 Multica 报错；仅在 Bot 聊天中报告不会结束等待。路由不明或 helper 失败时不要猜路由、补答或自动重试。
- 回填助手原子保存回复，拒绝过期请求、其他实例、错误 request_id 和已有不同回复。同一回复重复提交只确认，不覆盖第一次结果；新回复不再通过会提前退出的 gbb CLI 输出管道保存。已有旧消息没有截止时间元数据时保持兼容。
- 包装监听消息目录变化，回复原子落盘后即可唤醒；文件监听不可用、报错或漏通知时仍由 1 秒轮询兜底。转发耗时与 Bot 思考及工具执行耗时分别计算。
- `GBB_WAIT` 默认 3600 秒（1 小时），包含整个多轮流程，而非每轮分别等待 1 小时。
- `GBB_MAX_ROUNDS` 默认 8，允许 1–32；每一轮都是一次真实 Grok webhook 请求，受现有 gbb 每小时额度限制。达到最后一轮时仅允许最终回答，不执行新的工具批次。
- 每批最多 8 个调用，顺序执行；失败后剩余调用不执行。需要上一步结果的调用分到下一轮。
- 本任务内相同调用 ID 和相同写入内容会复用结果；同一任务重启时也读取本地记录。不同任务之间仍须先查询去重，不能承诺跨任务恰好一次。
- 写操作超时、进程中断或响应损坏时可能已经生效。先回读状态，再决定下一步；包装不会自动重试写入或 webhook 请求。
- 本地记录位于 gbb 数据目录的 `multica-runs/<task-id>/journal.jsonl`，权限 0600，用于检查已完成或未确认的操作。记录和发送给 Grok 的结果会遮盖任务 token、已知 webhook 凭证及敏感配置字段。

## 节点计时日志

包装与回填 endpoint 将节点元数据追加到 `$GBB_HOME/timing.jsonl`（默认 `~/.grok-bot-bridge/timing.jsonl`），权限为 0600。每行是独立 JSON，`v=1`，包含 UTC 毫秒时间 `ts`、`event` 和进程 `pid`。通过 `task_id`、每轮 `request_id`、`round` 与 `message_id` 关联同一条链路；工具节点另带 `call_id`。不记录任务正文、工具参数、回复内容、凭据或 webhook 地址。日志写入失败只向 stderr 报警，不阻断转发，也不改变 stdout 协议。

| 节点 | 含义 |
| --- | --- |
| `task_start` / `task_end` | 整个包装任务开始、结束；结束含 `exit_code` 与总耗时 |
| `round_start` / `round_return` | 一轮请求开始、传输返回；返回含 `exit_code` 与本轮耗时 |
| `message_created` | 完整请求已存入 inbox；附正文长度 `request_chars`（UTF-16 单元） |
| `webhook_send_start` / `webhook_send_end` | 发送开始、HTTP 返回；含 `ok`、`http_status` 与发送耗时 |
| `inbox_start` / `inbox_end` | Bot 的完整消息读取命令在运行端开始、输出完成；含 `ok` 与读取耗时 |
| `reply_start` / `reply_saved` / `reply_end` | 回填命令开始、原子落盘、命令完成；含 `action` 或 `ok` 及命令耗时 |
| `reply_duplicate` | 相同回复重复回填，被幂等确认 |
| `answer_received` | 等待进程观察到回复；含 HTTP 返回后的等待耗时 |
| `tool_start` / `tool_end` | 包装本地执行一项 Multica 工具；含 `ok` 与执行耗时 |
| `answer_timeout` / `transport_error` | 等待超时或传输异常；异常只记录阶段及安全错误码 |

用 `webhook_send_end → inbox_start` 查看触发确认到读取命令开始的间隔，`inbox_end → reply_start` 查看 Bot 处理区间，`reply_saved → answer_received` 查看本地回填唤醒延迟。多次读取可按同一 `message_id` 分别检查。`webhook_send_end` 仅表示 HTTP 确认，不能证明 Bot 已开始执行；Bot 也可能在 HTTP 返回前读取消息。

`inbox_end → reply_start` 仍包含 Bot 思考、Bot 内部工具和本地命令调度/连接等待，不能全部标成模型思考。此日志无法取得 Grok 云端排队、token 生成及 Bot 内部工具的独立时间。`elapsed_ms` 使用同进程单调时钟；跨进程节点间隔由 `ts` 计算，受系统校时及毫秒精度影响。并发追加的行顺序不保证等于事件时间顺序；错误或取消可能留下未配对的开始节点，以 `task_end` 的退出码判断结束。进程被强制杀死或主机掉电时也可能没有结束节点。升级只覆盖新启动的包装/endpoint，已有包装进程继续旧代码。

## 安装与验证

安装和升级统一使用仓库根目录的 `install.sh runtime` / `install.sh reply`（不带参数进入交互菜单；`install.sh ssh` 可独立配置免密连接），参数及多用户示例见 [README](../README.md)。已有 gbb 配置的 Linux/macOS 操作者也可以直接运行独立 `gbb-multica-setup.sh`；需要明确传入对应的 `--instance-id`、`--reply-route`、`--agent-name`，并让 PATH 能找到本次固定安装的 gbb。

工具测试使用真实 CLI 的参数帮助与隔离数据，覆盖多轮创建、编辑、回读、权限错误、超时后的写入确认、重复调用、任务重启、结果分页和凭证保护。网络测试使用环回 webhook。安装测试使用真实离线 npm 安装及模拟 SSH，验证两个消息目录之间不会串写。真实 Bot 执行环境的授权与完整问答请按 README 验收。
