# iphone use

**首次完成 USB 配对和签名后，让 Codex 通过同网络的 Mac 无线操作你的 iPhone，运行时可以拔掉数据线。** 支持读取界面组件、打开 App、点击、输入、滑动和返回主屏幕。

Codex 负责理解任务和决策，Appium / XCUITest / WebDriverAgent 负责设备执行。无需启动 VICO 主程序，也无需配置 DeepSeek API。

[从零完成无线设置](docs/WIRELESS_SETUP.md) · [English](README.en.md) · [MIT License](LICENSE) · [第三方说明](THIRD_PARTY_NOTICES.md)

无线运行需要 Mac 保持开机唤醒、手机已配对且可达，并单独启动 RemoteXPC 前台隧道。插件不会自动安装需要管理员权限的隧道守护服务，也不提供跨网络 Relay。完整前提和每一步命令都在[无线设置指南](docs/WIRELESS_SETUP.md)。

## 已验证的范围

维护者于 **2026-10-10** 在 **iPhone 16 / iOS 26.6.1 / Xcode 26.6** 上完成以下验证：

| 场景 | 结果 |
| --- | --- |
| 无 USB 数据连接的无线会话 | 连接成功，读取到 51 个界面组件 |
| 实际 App 操作 | 完成打开 App 和加入购物车流程 |
| 组件与截图 | 过程中使用两次截图回退；一次组件位置变化导致操作未成功 |

这是一次具体设备环境的验证，不能据此保证所有 App、iOS 版本或网络都可用，也不代表每个动作都能仅靠组件完成。新 Mac 仍需使用自己的签名和设备完成设置与拔线验收。

## 需要准备

- 一台 Mac，安装完整 Xcode，并完成首次启动、命令行工具选择和必要组件安装。
- Python 3.11+、[uv](https://docs.astral.sh/uv/getting-started/installation/)。
- Node.js 满足 `^20.19.0 || ^22.12.0 || >=24.0.0`，npm 10+；以 `bridge/package.json` 的 engines 为准。
- 支持本地 MCP 的 Codex。插件安装命令的可用性取决于客户端版本。
- 你拥有或获准控制的 iPhone，以及你自己的 Apple 签名团队。首次使用需要 USB 连接、信任电脑、Developer Mode 和 Xcode 配对。
- 本指南的预安装 WDA 无线路径要求 iOS 18+，同一可达网络，以及由机主在本机终端启动的 RemoteXPC 隧道。

Apple 登录与签名在本机 Xcode 中完成。不要把 Apple 密码、证书、私钥、Cookie 或真实设备配置提交到仓库。

## 安装

第一次使用请按[完整指南](docs/WIRELESS_SETUP.md)依次完成：依赖 → USB 信任与配对 → WDA 签名 → 配置 → Codex 缓存副本准备 → 前台隧道 → 拔线验证。

先获取源码并安装依赖：

```sh
git clone https://github.com/xxwzkdwz/iphone-use.git
cd iphone-use
uv sync --frozen
node scripts/install-bridge.mjs --json
```

`uv sync` 安装 Python 依赖；`install-bridge.mjs` 安装锁定版本的桥接依赖，并应用本项目的兼容补丁。依赖安装需要网络，但不会自动连接或操作手机。

完成指南中的 WDA 签名与预安装准备后，配置自己的设备。以下尖括号内容都是占位符，bundle ID 必须属于你自己的签名配置：

```sh
uv run --frozen iphone-use configure \
  --udid '<DEVICE_UDID>' \
  --team-id '<APPLE_TEAM_ID>' \
  --platform-version '<IOS_VERSION>' \
  --transport coredevice \
  --wda-bundle-id '<YOUR_WDA_BUNDLE_ID>' \
  --preinstalled-wda \
  --prebuilt-wda-path '/absolute/path/to/WebDriverAgentRunner-Runner.app'

uv run --frozen iphone-use doctor
```

设备 UDID 和 iOS 版本可在 Xcode 的设备窗口中核对；签名团队使用自己的 Apple Team ID。`--prebuilt-wda-path` 必须指向已经为该设备签名、完成预安装处理的 `.app`，不是仓库内置的通用安装包。若该 WDA 已正确安装到手机上，可以省略路径并保留 `--preinstalled-wda`。`coredevice` 选择传输路径，实际连接还需要可用的隧道。

将本目录注册并安装为 Codex 插件：

```sh
bash scripts/install-codex.sh
```

脚本调用 `codex plugin marketplace add` 和支持该命令的客户端上的 `codex plugin add`。它修改 Codex 插件配置，不连接手机。若 CLI 只支持注册，按脚本提示到桌面应用的本地 marketplace 安装。也可用 `--register-only` 只登记来源。安装后打开新聊天；必要时重启桌面客户端。

**安装后的 setup 仍然需要执行。** Codex 可能把插件复制到缓存目录，源仓库的 `.venv` 和 `bridge/node_modules` 不保证进入实际运行副本。让 setup 技能根据自身所在位置确定实际插件根目录，在那里检查依赖；缺失时执行该副本的 `uv sync --frozen` 和 `node scripts/install-bridge.mjs --json`。本机私有设备配置不随副本移动，仍从 `VICO_IPHONE_HOME` 读取。

然后对 Codex 说：

> 使用 iphone use 检查配置，连接我的手机，读取当前界面。

> 使用 iphone use 打开我指定的 App，按当前任务逐步操作，每次操作后确认结果。

插件附带 `setup` 和 `iphone` 两个技能。首次使用从 setup 开始。安装插件、启动 MCP、启动 Appium、建立设备会话是不同步骤，只有最后成功才能证明设备已连接。

## 直接作为 MCP 使用

不使用插件目录时，可以注册 stdio MCP：

```sh
codex mcp add iphone_use -- bash '/absolute/path/to/iphone-use/scripts/run-server.sh'
```

请换成实际绝对路径，并避免同时启用插件版与手工注册版。其他 MCP 客户端也可执行同一脚本；脚本内部使用 `uv run --frozen --project <plugin-root> iphone-use-mcp`，无需运行额外模型服务。宿主负责加载技能或采用等效的操作规范。

## 工具与操作方式

| 工具 | 用途 |
| --- | --- |
| `iphone_status` | 查看配置、桥接和控制器状态；不返回会话令牌 |
| `iphone_setup` | `status` / `start` / `stop`，管理本插件桥接；不安装依赖 |
| `iphone_session` | `connect` / `connect_status` / `disconnect`，建立、查询连接进度或释放设备会话 |
| `iphone_observe` | `components` / `screenshot` / `both`，读取当前界面 |
| `iphone_act` | `tap` / `type` / `swipe` / `home`，根据新观察执行一次动作 |
| `iphone_launch` | 用已知 `bundle_id` 打开 App |

首次构建 WDA 可能较慢。`connect` 会先尝试连接，若仍在进行则返回 pending 和 `operation_id`；用 `connect_status` 携带同一 `operation_id` 轮询，不要再次发起 connect。这样每次 MCP 调用保持有界，无需依赖宿主提供超长工具超时。

连接成功返回 `session_token`。当前聊天必须把它原样传给观察、动作、打开 App 和断开连接，避免多个聊天混用同一会话。不要把操作 ID 或令牌贴进文档或分享给别的聊天。

正常顺序是：检查状态 → 启动桥接 → 建立会话 → 观察组件 → 执行一次动作 → 再观察确认 → 完成后断开会话。动作需要当前 `observation_id`；优先使用组件 `element_ref` 定位。

**组件优先。** 先读取组件，结合标签、层级和必要的页面导航定位目标。组件不可用，或经过有意义的再次尝试仍无法定位时，才使用截图和坐标。不会因为一次没找到就立即切截图。

**未知结果不重放。** 若超时或连接中断导致动作结果不明确，先停止后续动作并核对状态；不要再次点击、再次发送或反复连接来猜测上次是否成功。

用户明确交代任务后，技能会连续完成已授权的普通步骤。涉及发送、购买、删除或账户安全等动作时，授权需要覆盖对应行为；宿主本身的工具审批策略仍然有效。

## 本地状态与数据

默认私有目录是 `~/.local/share/vico-iphone`，可用环境变量 `VICO_IPHONE_HOME` 指定其他位置。设备配置保存在该目录的 `configuration/iphone-control.json`。默认 Appium 监听 `127.0.0.1:4737`；本插件使用独立配置和 WDA 标识。为兼容已有连接，改名后保留上述私有目录、环境变量和现有 WDA 配置。

设备标识、观察结果、日志和运行状态应留在私有目录，不放进项目目录。手机界面文字和截图会通过 MCP 返回给当前 Codex 会话，并受你的 Codex 账户或工作区数据政策约束。插件无需单独的模型 API Key。

不要让旧 VICO 控制器、其他 Appium 会话和本插件同时控制同一台 iPhone。会话令牌隔离本插件内的聊天，不代表能协调外部自动化软件。

## 日常无线使用与排查

首次设置完成后，让手机和 Mac 处于同一可达网络、保持 Mac 唤醒并解锁手机。按[指南第 6 步](docs/WIRELESS_SETUP.md#6-拔线后启动前台无线隧道)启动前台隧道，再让 Codex 连接。结束有线会话后再拔线并建立无线隧道；不保证正在执行的有线会话能无缝切换。发现设备或 Appium 健康检查通过，仍需真实会话和新观察验证。

```sh
uv run --frozen iphone-use doctor
node bridge/cli.mjs status --json
node scripts/tunnel-diagnostics.mjs --json
```

这些检查用于定位依赖、桥接、配对、CoreDevice 和隧道层的问题。隧道诊断不会启动隧道或安装守护服务。需要启动或停止本插件 Appium 桥接时：

```sh
node bridge/cli.mjs start --json
node bridge/cli.mjs stop --json
```

出现签名错误时，核对自己的团队、设备授权和 WDA bundle ID。免费 Personal Team 的设备安装描述文件会在七天后过期，需要重新构建与安装；详见 [Apple 说明](https://developer.apple.com/help/account/basics/about-your-developer-account)。常见断线、休眠、旧缓存路径和签名修复见[指南故障排查](docs/WIRELESS_SETUP.md#8-常见问题)。

## 测试与打包

```sh
uv run --frozen pytest -q
node --test tests/bridge.test.mjs
python3 scripts/package.py --check
python3 scripts/package.py
```

打包脚本采用文件白名单，生成 `dist/iphone-use-<version>.zip` 和 SHA-256 校验文件。压缩包包含代码、测试、清单、技能、锁文件、说明和许可证；不包含 `node_modules`、虚拟环境、Git 历史、运行缓存、手机配置、截图或签名材料。`--check` 只验证并列出候选文件，不生成压缩包。

更换 Mac、iPhone、iOS 或 Xcode 后，重新完成签名、连接、组件观察、一个已授权的可逆动作及断开重连验收。上面的真机记录不能替代新环境验收。

## 分发方式

根目录 `plugin.json` / `mcp.json` 使用 Agent Plugins 格式；`.codex-plugin/plugin.json` / `.mcp.json` 为旧 Codex 客户端提供兼容入口。`skills/` 放在插件根目录。本仓库的 `.agents/plugins/marketplace.json` 中 `source.path` 为 `./`，相对于仓库根目录解析。

也可通过 GitHub marketplace 安装，然后运行 setup 技能完成本机准备：

```sh
codex plugin marketplace add xxwzkdwz/iphone-use --ref main
codex plugin add iphone-use@iphone-use-local
```

这里的本地/GitHub 分发不等于上架 OpenAI 官方插件目录。官方目录有独立提交和审核流程，推送 GitHub 不会自动上架。参见 [OpenAI 插件打包文档](https://developers.openai.com/plugins/build/plugins) 和 [提交说明](https://developers.openai.com/plugins/deploy/submission)。

更新时先结束自己的手机会话，在仓库目录更新代码并重新安装锁定依赖，然后重新运行安装脚本；Codex 加载的插件副本可能位于客户端缓存，不一定是正在编辑的工作目录。

许可证为 [MIT](LICENSE)，第三方依赖与补丁范围见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
