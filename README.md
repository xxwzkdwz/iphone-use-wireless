# iphone-use-wireless

**通过 Wi-Fi，让 Codex 在你的 iPhone 上完成任务。**

打开 App、查找页面内容、输入文字、点击和滑动，都可以直接告诉 Codex。首次完成 USB 配对、签名和隧道设置后，拔掉数据线，在手机与 Mac 所在的同一网络中无线使用。

[English](README.en.md) · [安装指南](docs/WIRELESS_SETUP.md)

## 能做什么

- **无线操作**：通过 Mac 连接已配对的 iPhone，日常使用无需连接数据线。
- **读懂页面**：读取界面文字和控件，必要时结合截图定位。
- **使用 App**：打开应用、搜索内容、填写文字、点击按钮、滑动页面。
- **完成连续步骤**：交代目标后，由 Codex 观察页面、执行操作并确认结果。
- **接入现有工具**：提供 Codex 插件，也支持标准 MCP 客户端。

## 开始使用

准备一台 Mac、一部 iOS 18+ 的 iPhone、首次配对用的数据线和自己的 Apple Account。把项目链接和下面这段话发给**本机 Codex**，让它检查环境、协助准备 Xcode 等软件并完成设置。

```text
我想使用 iphone-use-wireless 无线操作自己的 iPhone。
项目地址：https://github.com/xxwzkdwz/iphone-use-wireless

请按仓库指南先检查环境，协助准备缺少的软件并运行 setup.sh，
再完成手机配对、WDA 配置和无线隧道设置；已有配置优先复用。
需要我登录 Apple 账户、插拔数据线、信任电脑、开启开发者模式
或输入密码时，告诉我如何在本机操作。最后拔掉 USB，连接并读取当前页面。
```

Codex 可以执行检查、安装和配置命令；Apple 登录、手机上的信任与开发者模式确认、必要的密码输入由你在本机完成。完整 Xcode 是运行要求，可以在设置过程中准备。步骤见[安装指南](docs/WIRELESS_SETUP.md)。

<details>
<summary>手动安装</summary>

在本机终端运行：

```sh
git clone https://github.com/xxwzkdwz/iphone-use-wireless.git
cd iphone-use-wireless
bash scripts/setup.sh --check
bash scripts/setup.sh
```

`--check` 检查准备情况；缺少系统软件时先按提示补齐，仅缺插件依赖时运行 `setup.sh` 安装。安装需要完整 Xcode、uv（准备 Python 3.11+ 环境）、Node.js 22.12+（22.x）或 24+、npm 10+ 和 Codex CLI。`setup.sh` 安装插件运行依赖并注册插件，保留设备配置，不连接手机。

接着按[指南](docs/WIRELESS_SETUP.md)完成签名、配置与无线隧道。安装后新开聊天，运行 `setup` 技能检查实际插件目录。内部安装标识仍为 `iphone-use`。

其他 MCP 客户端可用 `bash` 启动本项目 `scripts/run-server.sh`。在 Codex CLI 中直接注册的命令是：

```sh
codex mcp add iphone_use -- bash '/absolute/path/to/iphone-use-wireless/scripts/run-server.sh'
```

插件安装和手动 MCP 注册选择一种即可；不注册插件时，用 `bash scripts/setup.sh --skip-plugin` 准备依赖。

</details>

## 试着这样用

> 使用 iphone-use-wireless 连接我的手机，告诉我现在是什么页面。

> 在地图里搜索附近的咖啡店，把页面上的几家店整理给我。

> 打开备忘录，帮我写一份周末出行清单。

日常使用时，Mac 保持唤醒、手机解锁并处于同一可达网络，前台无线隧道保持运行。然后直接交代任务；完成后可以让 Codex 断开手机会话。

## 更多

[完整配置与常见问题](docs/WIRELESS_SETUP.md) · [更新记录](CHANGELOG.md) · [MIT License](LICENSE) · [第三方说明](THIRD_PARTY_NOTICES.md)
