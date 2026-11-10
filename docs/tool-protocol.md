# Multica 工具能力与回填协议

包装版本 0.5.7；固定 gbb 0.2.0。安装方式见仓库 README。以下 ROUTE、MESSAGE_ID、request_id 均须替换为本轮真实值。回传端通过路由选择本机直连或 SSH；运行端手动验证可使用同用户同 GBB_HOME 的 gbb answer。

## 使用

Multica 调用是无人值守的后台任务。0.5.7 起每轮 `REQUEST_JSON` 明确带 `interaction_mode=non_interactive`。Bot 不应在 Grok 聊天中弹出提问/选项并等待点击；格式选择、生成正文文件和已确认未执行的参数错误应在现有授权范围内自行处理。确实需要用户决策或缺少输入时，立即通过当前 `answer` 回传 `final`，写明阻塞原因及精确问题/选项，不能把澄清说成任务已完成；该运行结束，用户在 Multica 回答后启动新任务。真正的权限或连接阻塞用当前 `fail` 回传具体要求，不绕过权限，不以沉默代替同意。现有超时仍是兜底；Bridge 看不到 Grok 原生提问卡片，无法强制拦截，指令更新不能证明模型永不偏离。

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

Bridge 的 Multica 工具不提供任意 shell、读取主机文件、登录切换、daemon 控制、跨工作区操作或明文凭证管理。此边界不代表整个任务禁止使用已获授权的 Grok 原生本机工具。模型选择和会话恢复仍不支持；Agent 的模型、自定义启动参数、思考档位和服务档位应留空。

## 首轮执行环境与任务指令

0.5.5 起，完整 inbox 的首轮 `REQUEST_JSON` 包含 `execution_context`：运行端系统主机名、目标本机设备名、操作系统、包装的实际 `working_directory`，以及当前目录 `.multica/project/resources.json` 中的项目和资源信息。单个本地目录资源另提供 `project.local_path` 和 `execution_mode`；多个资源保留列表，不猜测当前使用哪一个。资源指针和 `worktree` 配置都不是实际检出、构建成功或产物存在的证据。

目标设备名默认采用运行端 `socket.gethostname()`，不会查询 Grok 的设备注册表；若 Grok 显示名不同，可在对应 Agent 的自定义环境中设置 `GBB_LOCAL_DEVICE_NAME`。它只是设备选择提示，不是 Grok machine ID、连接状态或权限凭据，`local_execution_status=unverified` 明确保留这一限制。跨机 SSH 回填路由也不会因此授予 Grok 对运行端的原生访问权。Bot 须通过原生工具核对实际设备和目录；连接或权限失败时报告具体错误，不换成云端同名路径，也不修改项目绑定。`read_only=true` 的恢复模式仍禁止原生任务工具。首轮上下文（`AGENTS.md` 与项目资源）单文件上限 128 KiB、合计 160 KiB，超限时该项标记为 `unavailable`，不转发部分内容；`resource_ref` 仅转发 `local_path`/`execution_mode`/`url`/`ref`/`branch`/`name`，并对 GitHub/AWS/Slack 令牌、Bearer 与 URL 用户信息脱敏。

`runtime_instructions` 携带当前任务目录 `AGENTS.md` 全文；该文件缺失时兼容读取 `.github/copilot-instructions.md`。只读取当前目录的固定入口，不向父目录搜索，不读取资源路径指向的业务文件，不展开技能目录。文本及资源信息经过既有凭据脱敏，再随首轮发送。任务指令不能覆盖 Bridge 协议或扩大授权。

各文件提供 `source` 和 `status`，缺失为 `missing`，无权限、非 UTF-8、非普通文件、符号链接逃逸或超过单文件 1 MiB 时为 `unavailable`，并标明缺口，不发送截断内容。后续连续交换只传新增工具结果；兼容模式的新 Webhook 和明确只读恢复的新 Bot 运行重新带上同一启动快照，不重复读取变化中的文件。`cwd` 仍保留在 Webhook 外层，但 Bot 应以完整 inbox 为依据。

## 手动回填协议

每一轮通过回传端助手查询；运行端同用户也可直接使用对应 GBB_HOME 的 gbb inbox：

```bash
"$HOME/.local/bin/gbb-multica-remote" inbox ROUTE
"$HOME/.local/bin/gbb-multica-remote" inbox ROUTE MESSAGE_ID
```

完整问题末尾的 `REQUEST_JSON` 给出本轮 `request_id`、剩余轮数及交互模式。配置回传路由的任务使用 `delivery_mode=continuous`：首轮包含原始任务和工具说明，后续只有本轮新增的 `tool_results`，此前上下文由同一次 Bot 运行保留。`request_id` 是包装生成的校验值；`MESSAGE_ID` 是 gbb 消息 ID，二者不同。

要查询项目，向**本轮**消息回填：

```bash
"$HOME/.local/bin/gbb-multica-remote" exchange ROUTE MESSAGE_ID - <<'GBB_REPLY'
{"gbb_tool_protocol":1,"request_id":"复制本轮 request_id","tool_calls":[{"id":"projects1","name":"multica","arguments":{"args":["project","list"],"fields":["id","title","status"]}}]}
GBB_REPLY
```

`exchange` 原子提交工具调用，并最多等 25 秒。返回 `{"state":"ready","message":{...}}` 时，直接读取该消息 `question` 的 REQUEST_JSON.tool_results，使用新 message.id 和 request_id 继续交换。返回 `{"state":"pending","message_id":"..."}` 时，运行以下命令继续等同一条消息的结果，保持本次 Bot 运行，不再触发 Webhook，也不重新提交工具：

```bash
"$HOME/.local/bin/gbb-multica-remote" wait ROUTE MESSAGE_ID
```

`state=closed` 表示任务已结束、取消或超时，停止处理。`exchange` 的同内容重复回填会读取同一个后继结果，不重复执行工具；不同内容被拒绝。最终回答仍通过 `answer ROUTE 新MESSAGE_ID -` 回填：

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

`multica_help` 的操作目录与执行白名单共用同一来源；精确操作的帮助只展示 Bridge 可用的长参数，原生 CLI 示例和全局/输出参数不直接转发。原生帮助仍由包装内部用于检测 CLI 能力。

附件下载通过 `{"args":["attachment","download","附件 ID"]}` 调用，使用当前任务凭据。包装在该任务 journal 旁的私有 `downloads/` 中创建独立目录，成功后保留文件并返回 `downloaded_files`（绝对 `path`、`size_bytes`、`sha256`），不回传二进制正文。不能指定 `--output-dir`、覆盖项目文件或回退所有者凭据；失败下载会清理本次目录。此目录是缓存，不是原项目的构建产物位置；使用附件的本机工具仍受任务授权及 `read_only` 限制。`read_only` 只禁止修改 Multica 状态，下载只写本地私有缓存，因此在只读恢复下仍允许（用于读取任务输入）。单次下载上限 512 MiB，每个任务缓存上限 2 GiB，超限时终止下载并清理；超过 7 天的附件副本在下次下载前清理。缓存与任务 journal 一起保留，不因单次工具调用结束而清理。

评论列表未选模式时默认 `--roots-only --summary`；显式 `--thread`、`--since`、`--roots-only` 或 `--recent` 时不追加模式。`--tail` 必须配合 `--thread`，互斥模式在调 CLI 前报出可执行的修正提示。

参数不确定时使用 `multica_help`，如 `{"command":["project","update"]}`；空数组列出支持的操作。命令参数以字符串数组传递，省略 `multica`、`--output` 和全局选项。正文或附件路径只能引用 `files` 中的 `@文件名`，不能读取运行端上的既有文件。评论必须使用 `--content-file`；编辑评论还需要查询结果中的 `--expected-revision`。

重跑指定历史执行使用 `multica_help` 的 `{"command":["issue","rerun-task"]}` 查看当前 CLI 能力，再通过 `multica` 传入 `{"args":["issue","rerun-task","完整 run UUID"]}`。短 run ID 需加 `--issue ISSUE-ID`；当前 CLI 默认在安全时复用原目录和会话，`--fresh` 创建新会话和目录。此操作会启动执行，按写操作记账、防重复，不使用所有者凭据绕过任务权限；失败或超时后先查询执行历史，避免盲重跑。旧 CLI 不提供此命令时仍报能力缺失。

查询大结果时可用 `fields` 选取顶层字段。超过 25,000 字符的结果返回 `result_id` 和 `next_offset`，通过 `multica_result` 分页读取，最多每次 40,000 字符。

## 换 Agent 后的交接上下文

Issue 任务的新包装进程会在第一次请求的 `REQUEST_JSON.prior_context` 中附上最近一次较早执行的结论和日志摘录，包括上一任 Agent 的执行；同 Agent 的新进程也使用这一机制。所属 Issue 通过当前 `MULTICA_AGENT_ID` 的任务列表匹配 `MULTICA_TASK_ID` 取得，不从任务正文中的引用猜测。只读取任务列表最近 20 条；找不到当前 task 时明确报告缺口。普通聊天没有所属 Issue，不读取其他聊天历史。

此功能在新 Agent 使用本 Bridge runtime 时生效；上一任可以使用其他运行端，只要记录已保存到 Multica。切换到原生 Codex、Claude 等其他运行端时，其提示词注入与交接行为由 Multica 自身决定，本仓库的补丁不改变这些运行端。

交接只使用当前任务凭据和 daemon 注入的 `MULTICA_CLI_PATH`（旧环境未注入路径时兼容 PATH），不会切换所有者登录。额外读取共用最多 10 秒预算，计入 `GBB_WAIT`，无自动重试。`status=available` 表示取得摘录，`none` 表示没有较早的已启动执行，`unavailable` 和 `missing` 指出读取失败的环节；失败时保留已读到的结论并继续当前任务。权限、旧 CLI 不支持、未知 JSON 结构或超时都不会被当成“完整历史已恢复”。

交接对象限制在 12,000 个 JSON 字符以内，结论保留约 2,000 字符的首尾摘录，错误约 500 字符；`truncated` 表示摘要本身经过裁剪。0.5.3 不再启动时读取完整 `run-messages`：当前 CLI 只有 `--since`，没有有上限的尾部读取，先全量下载再裁剪会让长日志拖慢启动。`status=summary_only`、`messages_status=not_loaded` 明确表示工具证据尚未读取，不能当作上一任没有工具操作。结论也可能为空（例如上轮中断），此时尤其需要补查当前 Issue 与评论。

`read_more` 提供当前 Issue、评论线程扫描、执行历史和原 run 的 `run-messages` 参数。先用 `multica_help` 核对参数，再通过 `multica` 工具按需补读；若已知序号可用 `--since`。手动补读整份日志仍可能超时；本补丁消除的是启动对完整日志的强制依赖，没有声称 CLI 已提供分页尾读。新任务不能用旧任务的 `multica_result` ID 读取其缓存。任务定位及执行列表读取仍共享最多 10 秒预算，失败则标明缺口并继续任务。

`prior_context` 仅首轮携带，后续连续交换仍只有新增 `tool_results`。它是平台已保存记录的快照：历史内容不能授权新写入，旧工具回填也不会进入新任务的防重 journal。新 Agent 必须读当前 Issue/评论、核实未确认写入的状态，再继续操作。Multica 已放入原始提示词的 handoff note 保留在 `user_task`，不会据此恢复旧 Bot routine、私有对话或上一任未提交的工作目录；更早执行可按 `read_more.runs` 补查。

## 超时、重复和费用

- 新 Agent 仅切换到 Bridge runtime、未设置路由环境变量时，包装读取同系统用户的 `~/.config/gbb-multica/server.json`（或 `GBB_MULTICA_CONFIG_HOME`）作为本机部署默认值，核对工作区和消息目录。显式路由必须同时提供 `GBB_INSTANCE_ID` 和 `GBB_REPLY_ROUTE`；不完整或冲突配置在发送前报错。没有安装配置的独立 gbb 仍兼容原无路由模式。
- 路由与消息 ID 已验证、但 Bot 无法处理时，使用 `gbb-multica-remote fail ROUTE MESSAGE_ID -`，从 stdin 传入失败原因。包装收到后结束并向 Multica 报错；仅在 Bot 聊天中报告不会结束等待。路由不明或 helper 失败时不要猜路由、补答或自动重试。
- 回填助手原子保存回复，拒绝过期请求、其他实例、错误 request_id 和已有不同回复。同一回复重复提交只确认，不覆盖第一次结果；新回复不再通过会提前退出的 gbb CLI 输出管道保存。已有旧消息没有截止时间元数据时保持兼容。
- 包装监听消息目录变化，回复原子落盘后即可唤醒；文件监听不可用、报错或漏通知时仍由 1 秒轮询兜底。转发耗时与 Bot 思考及工具执行耗时分别计算。
- `GBB_WAIT` 默认 3600 秒（1 小时），包含整个多轮流程，而非每轮分别等待 1 小时。
- `GBB_MAX_ROUNDS` 默认 8，允许 1–32；连续模式正常任务只有一次 Webhook，后续轮次是本机/SSH 工具结果交换。达到最后一轮时仅允许最终回答，不执行新的工具批次。无路由兼容模式仍使用逐轮 Webhook、累计 history 和 answer 回填。
- 每次包装启动使用独立会话标识；重启后不会接续旧 Bot 运行，但同 task-id 的写操作仍由原 journal 防重。正常结束、错误及 SIGTERM 取消会关闭会话；SIGKILL 或整个进程组被强杀时，独立会话守护进程通过唯一文件锁发现所有者退出并关闭会话；endpoint 即使在守护进程落盘前也拒绝无锁所有者的旧请求。掉电/系统未调度时无法承诺清理时延，恢复后仍会拒绝已失去所有者的会话。两端可通过回传端的 `update --all` 协同升级；旧 Bot 例程先安装一次稳定引导，此后工具协议随首轮完整 inbox 提供。`REQUEST_JSON.bridge_version` 与 `protocol_revision` 标识当前版本与指令摘要，`reply_commands` 由回传助手填入本机实际路径，不从运行端猜测路径。
- 每批最多 8 个调用，顺序执行；失败后剩余调用不执行。需要上一步结果的调用分到下一轮。
- 本任务内相同调用 ID 和相同写入内容会复用结果；同一任务重启时也读取本地记录。不同任务之间仍须先查询去重，不能承诺跨任务恰好一次。
- 写操作超时、进程中断或响应损坏时可能已经生效。先回读状态，再决定下一步；包装不会自动重试写入或不确定是否送达的 webhook 请求。
- 本地记录位于 gbb 数据目录的 `multica-runs/<task-id>/journal.jsonl`，权限 0600，用于检查已完成或未确认的操作。记录和发送给 Grok 的结果会遮盖任务 token、已知 webhook 凭证及敏感配置字段。

### 无进展检测和恢复

连续模式在 Webhook 成功返回或下一轮结果发布后开始计时，只计算等待 Bot 的时间，不将本地 Multica 工具运行时间算作无进展。`GBB_IDLE_WARN=300` 时向 Multica 输出等待提示，`GBB_IDLE_TIMEOUT=600` 时结束等待并关闭会话（退出码 3）。两个值均为秒，要求 `0 < GBB_IDLE_WARN < GBB_IDLE_TIMEOUT`。更短的 `GBB_WAIT` 总预算优先，退出码 2。无回填不等于模型一直在思考，也不能证明云端任务已停止。

长任务在真实步骤完成后、尚未回填的当前消息上执行：

```sh
gbb-multica-remote progress ROUTE MESSAGE_ID
```

进展序号原子更新并与回复共用写锁；不接受过期、已回答或已关闭的消息。它只重置无进展计时，不延长 `GBB_WAIT`。`inbox` 读取、`wait` 轮询、HTTP 成功和无人值守心跳不能证明任务进展，均不自动续期。长时间不可分割的原生调用需在任务开始前调大无进展上限，或使用能查询真实进度的后台任务。超时/取消撤销 Bridge 会话并停止接收后续工具调用，不能强行终止 Grok 云端推理或已经启动的本机原生操作。

默认不自动重发。只有调用方明确保证整个任务只读、且授权恢复时，才可设置 `GBB_RETRY_READ_ONLY=1`。该模式在执行 CLI 前拒绝 Multica 写命令，指令限定 Bot 只使用 Bridge 工具和回填助手；它不能从技术上撤销 Grok 原生工具权限，不能用于“可能写入”的任务。仅无进展超时、没有任何写入 journal、尚未重试且剩余总预算大于无进展上限时，先关闭旧会话，再触发一次新会话。恢复首轮提供原任务及本次已完成的结果；正常后续轮仍只发送新增结果。轮数和总预算不重置。发送失败、明确处理失败、总超时、取消和结果不确定的写入均不自动重试；先回读本机/平台状态，再由用户决定。

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
| `task_budget_expired` | 整个任务绝对预算耗尽，由包装终止等待进程 |
| `answer_timeout` / `transport_error` | 等待超时或传输异常；异常只记录阶段及安全错误码 |
| `continuation_published` | 新增结果已落盘并链接到上一轮；附 `previous_message_id`，不发送 Webhook |
| `exchange_wait_start` / `exchange_wait_end` | 回填助手等待下一批结果；结束含 `state` 和等待耗时 |
| `session_closed` | 包装或独立守护进程关闭本次会话；含任务退出码、会话和关闭原因 |
| `bot_wait_start` | HTTP 确认/结果发布后，包装开始监测等待 |
| `bot_idle_warning` / `bot_idle_timeout` | 无进展提示/截止；含 `idle_ms` |
| `bot_progress` / `bot_progress_observed` | endpoint 接受真实进展、包装观察到序号更新 |
| `read_only_retry` | 显式只读恢复，`attempt=1` |

用 `webhook_send_end → inbox_start` 查看触发确认到读取命令开始的间隔，`inbox_end → reply_start` 查看 Bot 处理区间，`reply_saved → answer_received` 查看本地回填唤醒延迟。多次读取可按同一 `message_id` 分别检查。`webhook_send_end` 仅表示 HTTP 确认，不能证明 Bot 已开始执行；Bot 也可能在 HTTP 返回前读取消息。
连续模式后续轮次可用 `tool_end → continuation_published → exchange_wait_end(state=ready)` 分别查看结果发布与助手交付耗时，再用下一轮的 `reply_start` 查看 Bot 继续处理的区间。`exchange_wait_end` 是运行端输出完成，不代表云端已接收到输出。

`inbox_end → reply_start` 仍包含 Bot 思考、Bot 内部工具和本地命令调度/连接等待，不能全部标成模型思考。此日志无法取得 Grok 云端排队、token 生成及 Bot 内部工具的独立时间。`elapsed_ms` 使用同进程单调时钟；跨进程节点间隔由 `ts` 计算，受系统校时及毫秒精度影响。并发追加的行顺序不保证等于事件时间顺序；错误或取消可能留下未配对的开始节点，以 `task_end` 的退出码判断结束。进程被强制杀死或主机掉电时也可能没有结束节点。升级只覆盖新启动的包装/endpoint，已有包装进程继续旧代码。

## 安装与验证

安装和升级统一使用仓库根目录的 `install.sh runtime` / `install.sh reply`（不带参数进入交互菜单；`install.sh ssh` 可独立配置免密连接），参数及多用户示例见 [README](../README.md)。已有 gbb 配置的 Linux/macOS 操作者也可以直接运行独立 `gbb-multica-setup.sh`；需要明确传入对应的 `--instance-id`、`--reply-route`、`--agent-name`，并让 PATH 能找到本次固定安装的 gbb。

工具测试使用真实 CLI 的参数帮助与隔离数据，覆盖多轮创建、编辑、回读、权限错误、超时后的写入确认、重复调用、任务重启、结果分页和凭证保护。网络测试使用环回 webhook。安装测试使用真实离线 npm 安装及模拟 SSH，验证两个消息目录之间不会串写。真实 Bot 执行环境的授权与完整问答请按 README 验收。
