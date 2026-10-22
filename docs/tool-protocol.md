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
| Issue | 查询、搜索、创建、编辑、状态、指派、父子关系、阶段、排序、执行历史、关联 PR |
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

查询大结果时可用 `fields` 选取顶层字段。超过 25,000 字符的结果返回 `result_id` 和 `next_offset`，通过 `multica_result` 分页读取，最多每次 40,000 字符。

## 超时、重复和费用

- `GBB_WAIT` 默认 3600 秒（1 小时），包含整个多轮流程，而非每轮分别等待 1 小时。
- `GBB_MAX_ROUNDS` 默认 8，允许 1–32；每一轮都是一次真实 Grok webhook 请求，受现有 gbb 每小时额度限制。达到最后一轮时仅允许最终回答，不执行新的工具批次。
- 每批最多 8 个调用，顺序执行；失败后剩余调用不执行。需要上一步结果的调用分到下一轮。
- 本任务内相同调用 ID 和相同写入内容会复用结果；同一任务重启时也读取本地记录。不同任务之间仍须先查询去重，不能承诺跨任务恰好一次。
- 写操作超时、进程中断或响应损坏时可能已经生效。先回读状态，再决定下一步；包装不会自动重试写入或 webhook 请求。
- 本地记录位于 gbb 数据目录的 `multica-runs/<task-id>/journal.jsonl`，权限 0600，用于检查已完成或未确认的操作。记录和发送给 Grok 的结果会遮盖任务 token、已知 webhook 凭证及敏感配置字段。

## 安装与验证

安装和升级统一使用仓库根目录的 `install.sh runtime` / `install.sh reply`（不带参数进入交互菜单；`install.sh ssh` 可独立配置免密连接），参数及多用户示例见 [README](../README.md)。已有 gbb 配置的 Linux/macOS 操作者也可以直接运行独立 `gbb-multica-setup.sh`；需要明确传入对应的 `--instance-id`、`--reply-route`、`--agent-name`，并让 PATH 能找到本次固定安装的 gbb。

工具测试使用真实 CLI 的参数帮助与隔离数据，覆盖多轮创建、编辑、回读、权限错误、超时后的写入确认、重复调用、任务重启、结果分页和凭证保护。网络测试使用环回 webhook。安装测试使用真实离线 npm 安装及模拟 SSH，验证两个消息目录之间不会串写。真实 Bot 执行环境的授权与完整问答请按 README 验收。
