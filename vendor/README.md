# 固定 gbb 0.2.0 安装包

`grok-bot-bridge-0.2.0.tgz` 来自部署机器上已经安装、通过真实问答验证的 grok-bot-bridge 0.2.0，执行 `npm pack --ignore-scripts` 生成；不是从 npm 官方仓库下载的 0.2.0 发布包。构建源代码未随这个包提供；包内保留上游 README、MIT LICENSE、package.json、已编译的 dist、示例与 skill，共 15 个文件。

上游：<https://github.com/anup-a/grok-bot-bridge>。作者 Anup Aglawe，MIT。许可证见相邻的 `LICENSE.grok-bot-bridge`，归档内部也含 LICENSE。

SHA-256：

```text
f5f2815bbaa272f34e0115b86b2344d7d60a0ce2546b8ee37765637b735e1b79  grok-bot-bridge-0.2.0.tgz
```

安装使用 `npm install --global --prefix 用户目录 --offline --ignore-scripts 安装包`，不依赖 registry 的 latest，不运行安装生命周期脚本。Node.js 要求 >=20。
