# iphone-use-wireless

**通过 Wi-Fi，让 Codex 在你的 iPhone 上完成任务。**

打开 App、查找页面内容、输入文字、点击和滑动，都可以直接告诉 Codex。首次完成 USB 配对和签名配置后，拔掉数据线，手机与 Mac 在同一网络下即可无线使用。

[English](README.en.md) · [安装指南](docs/WIRELESS_SETUP.md)

## 能做什么

- **无线操作**：通过 Mac 连接已配对的 iPhone，日常使用无需连接数据线。
- **读懂页面**：读取界面文字和控件，必要时结合截图定位。
- **使用 App**：打开应用、搜索内容、填写文字、点击按钮、滑动页面。
- **完成连续步骤**：交代目标后，由 Codex 观察页面、执行操作并确认结果。
- **接入现有工具**：提供 Codex 插件，也支持标准 MCP 客户端。

## 开始使用

准备一台装有完整 Xcode 的 Mac、一部 iOS 18+ 的 iPhone，以及首次配对用的数据线和自己的 Apple 签名账户。无线操作时，Mac 保持唤醒，手机解锁并与 Mac 连接同一网络。

可以把下面这段话发给 Codex，让它带你完成配置：

```text
我想使用 iphone-use-wireless 无线操作自己的 iPhone。
项目地址：https://github.com/xxwzkdwz/iphone-use-wireless

请按仓库的无线设置指南，帮我准备本机依赖、安装插件，
完成手机配对、WDA 配置和无线隧道设置；已有配置优先复用。
遇到需要我登录 Apple 账户、确认手机提示或输入管理员密码的步骤，
告诉我在本机如何操作。最后在拔掉 USB 的情况下连接手机并读取当前页面。
```

首次设置包括设备配对、WDA 签名和无线隧道准备，具体步骤见[完整安装指南](docs/WIRELESS_SETUP.md)。插件内的 `setup` 技能负责配置引导，`iphone` 技能负责日常操作。

<details>
<summary>手动安装</summary>

先准备可在终端运行 `codex` 的 Codex CLI，并安装 Python 3.11+、[uv](https://docs.astral.sh/uv/getting-started/installation/)、Node.js 22.12+（22.x）或 24+、npm 10+，然后运行：

```sh
git clone https://github.com/xxwzkdwz/iphone-use-wireless.git
cd iphone-use-wireless
uv sync --frozen
node scripts/install-bridge.mjs --json
bash scripts/install-codex.sh
```

按[安装指南](docs/WIRELESS_SETUP.md)完成手机签名、配置和无线隧道准备。安装后新开 Codex 聊天，运行 `setup` 技能检查实际插件目录。命令中的插件标识沿用 `iphone-use`，显示名称为 **iphone-use-wireless**。

其他 MCP 客户端可用 `bash` 启动本项目 `scripts/run-server.sh`。在 Codex CLI 中直接注册的命令是：

```sh
codex mcp add iphone_use -- bash '/absolute/path/to/iphone-use-wireless/scripts/run-server.sh'
```

插件安装和手动 MCP 注册选择一种即可。

</details>

## 试着这样用

> 使用 iphone-use-wireless 连接我的手机，告诉我现在是什么页面。

> 在地图里搜索附近的咖啡店，把页面上的几家店整理给我。

> 打开备忘录，帮我写一份周末出行清单。

日常使用时保持无线隧道运行，然后直接交代任务。完成后可以让 Codex 断开手机连接。

## 更多

[完整配置与常见问题](docs/WIRELESS_SETUP.md) · [更新记录](CHANGELOG.md) · [MIT License](LICENSE) · [第三方说明](THIRD_PARTY_NOTICES.md)
