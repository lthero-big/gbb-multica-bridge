# gbb 的用途与替换边界

核对日期：2026-11-09。结论：目前继续固定使用 gbb 0.2.0 合理，没有必须整套重写的证据；应限制依赖范围，并在需要脱离其内部接口时替换传输适配层。

## 目前从 gbb 复用什么

上游定位是连接 Grok Bot 与本机 CLI Agent，通过 Webhook 发出请求，通过 Grok 桌面端执行能力返回结果。[上游 README](https://github.com/anup-a/grok-bot-bridge#how-it-works) 说明了这条链路；[package.json](https://github.com/anup-a/grok-bot-bridge/blob/main/package.json) 当前仍标为 0.2.0、MIT、Node.js 20+，没有运行时第三方依赖。

本项目的 `gbb-multica-agent` 通过 Node 适配代码复用 `config.js` 的配置与凭证存储、`messages.js` 的消息创建、`webhook.js` 的载荷与发送逻辑。源码直接导入这些内部文件，不能把它们当作上游承诺稳定的库接口；安装器以固定归档及 SHA-256 校验限制变动。macOS Keychain 与已有凭证兼容性仍是保留这部分实现的价值。

Multica 任务凭证、工具白名单、结果脱敏、写入 journal、防重、持续会话、跨 Agent 交接和部署锁均由本项目实现。多机器升级和 Bot 引导也是本项目的安装与协议责任。

## 这次问题是否说明 gbb 不合适

- 超长交接日志：根因是启动时调用没有尾部上限的 Multica `run-messages`，全量读完才裁剪。0.5.3 改为结论与来源先交接，日志按需读取；替换 gbb 不会让 Multica CLI 自动获得分页。
- 两端与提示词不同步：根因是部署入口分散、协议写在云端例程里。0.5.3 增加从回传端按同一提交升级，并把版本变化的协议放入完整 inbox；替换 Webhook 库本身不能修复这些部署流程。
- Webhook 内容裁剪：[上游 buildPayload](https://github.com/anup-a/grok-bot-bridge/blob/main/src/webhook.ts) 会按 `maxSummaryChars` 裁剪。Bridge 通过完整 inbox 读取任务、优先携带路由信息，避免把 webhook 视图当作完整上下文。
- 大消息通过 CLI 管道被截断：本仓库已用 endpoint 直接读同一消息存储，Python 输出完整 JSON 并 flush。这是继续绕开上游 CLI 输出路径的理由，不必因此重写整套任务系统。

## 真正值得继续处理的边界

1. 内部模块耦合：将现有三个模块的调用收敛为可替换适配层。若上游改变文件布局或凭证格式，先替换这一层，继续兼容配置和消息协议。
2. Bot routine 中断：持续模式依赖同一次 routine 存活。当前能标明失败/过期，不能恢复云端模型上下文；要做恢复，需要新会话重放摘要、从 journal 核实写入状态，不能只换 gbb。
3. 缺少 Grok 云端编辑接口：稳定引导消除了每次版本升级都改提示词的需要，但首次迁移、路由和助手路径变化仍需同步。不能把本地生成文件宣称为云端已更新。
4. 多机器更新不是事务：预检查与相同提交减少版本偏差；网络中断仍可能导致部分端已完成。应保留完成记录并核实后继续，不能承诺跨机原子回滚。

只有出现无法通过适配层解决的接口破坏、无法修补的凭证/传输缺陷，或产品明确要求不再安装 Node/gbb 时，才有充分理由实现独立传输。即便如此，替换范围也可限定为 HTTPS 发送、凭证适配、消息持久化和限流，保留已验证的 Multica 工具与防重逻辑。
