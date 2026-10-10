# 从零设置 iphone-use-wireless

[返回 README](../README.md) · [English](WIRELESS_SETUP.en.md)

首次完成 USB 配对和自己的 Apple 签名后，可以拔掉数据线，通过同网络的 Mac 操作 iPhone。默认使用 **直连 WDA + CoreDevice / RemoteXPC**，要求 iOS 18+。Mac 运行 Codex、本插件和前台隧道，直接连接手机上预先安装的 WDA；不运行 Appium Server 或 XCUITest Driver。底层仍使用 Apple XCTest、WebDriverAgent 和 `appium-ios-remotexpc` 无线库。Mac 保持唤醒，手机保持解锁、网络可达。

你可以把本指南交给本机 Codex，让它检查环境、协助准备软件、执行安装与配置命令。你需要提供 Mac、iPhone 和首次配对用的数据线，并亲自完成 Apple 登录、手机信任与开发者模式确认，以及必要的密码输入。Xcode 是运行要求，可以在设置过程中准备，不需要事先手工完成所有软件配置。

已经使用旧版 Appium 后端的用户，先看[升级与兼容说明](DIRECT_WDA_EXPERIMENT.md)；已配置过直连版的用户直接复用现有配置。

## 1. 获取代码并准备软件

在本机终端或让 Codex 执行：

```sh
git clone https://github.com/xxwzkdwz/iphone-use-wireless.git
cd iphone-use-wireless
bash scripts/setup.sh --check
```

`--check` 只读检查系统软件和当前目录中的插件依赖，不安装、不改设备配置、不连接手机。新克隆目录缺少插件依赖是正常的；若缺少系统软件，先按报告的下一步处理：

- **完整 Xcode**：Codex 可检查已有安装并引导下载安装。启动 Xcode 一次，完成组件安装，在 **Settings → Locations → Command Line Tools** 中选中它。需要同意许可或输入管理员密码时，由你在本机完成。
- **运行工具**：[uv](https://docs.astral.sh/uv/getting-started/installation/)、[Node.js](https://nodejs.org/en/download) 和 npm 10+；uv 会准备项目需要的 Python 3.11+ 环境。推荐 Node.js 22.12+（22.x）或 24+；也支持 20.19+（20.x），不支持 21.x 或 23.x。Codex 可协助安装与检查这些工具。
- **Codex CLI**：终端可以运行 `codex`。使用其他 MCP 客户端时可选 `--skip-plugin`，无需为此安装 Codex CLI。

系统软件就绪后运行：

```sh
bash scripts/setup.sh
```

脚本安装锁定的 Python 与 `direct/` 直连依赖，检查运行环境并注册 Codex 插件；保留已有设备配置，不连接或操作手机。它不代装 Xcode、Node.js 等系统软件，缺失时会报告下一步并退出。`bash scripts/setup.sh --skip-plugin` 只准备当前目录的运行依赖，跳过插件注册。

以下命令均从仓库根目录运行，除非明确指出使用 Codex 安装副本。真实设备配置、截图和签名材料保存在本机私有目录，不放进仓库。

## 2. 用 USB 完成 Apple 登录、信任与配对

这些步骤可由 Codex 指引；登录、插线和手机确认由你完成：

1. 在 Xcode **Settings → Accounts** 中登录自己的 Apple Account。无需把密码交给 Codex 或导出私钥。
2. 用数据线连接 iPhone，解锁，在手机提示中选择“信任这台电脑”，在手机上输入密码。
3. 打开 Xcode **Window → Devices and Simulators**（部分新版称 **Device Hub**）。选择目标手机，等待配对和开发支持准备完成，核对自己的 UDID 与 iOS 版本。
4. 在手机 **设置 → 隐私与安全性 → 开发者模式** 中启用 Developer Mode，按提示重启并确认。若未显示该选项，先启动 Xcode 配对流程。参见 [Apple 开发者模式指南](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device)。
5. 在 Finder 的设备页面启用 **Show this iPhone when on Wi-Fi / 连接 Wi-Fi 时显示此 iPhone**，按提示应用。Xcode 若显示 **Connect via network**，也应启用；某些版本不再单独显示这一项。
6. 确保 Mac 和 iPhone 位于同一可达网络，先保持 USB，继续准备 WDA。

参见 [Apple 无线设备说明](https://help.apple.com/xcode/mac/current/en.lproj/dev3e2f4ee6d.html)。不需要为设置插件随意切换你正在使用的网络。

## 3. 用自己的团队构建并准备 WDA

WDA 是安装到手机上的测试辅助 App。它使用你自己的 Apple 签名；无需购买第三方签名，也不要复用别人的签名包、证书或设备配置。可以使用 Xcode 的免费 Personal Team，相关签名到期处理见第 8 步。

默认安装的独立 WDA 依赖锁定为 `appium-webdriveragent 16.12.5`。打开其中的 Xcode 工程：

```sh
open direct/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj
```

在 Xcode 中完成以下设置，Codex 可协助定位和处理构建错误：

1. 选择 **WebDriverAgentRunner** target，进入 **Signing & Capabilities**，勾选 **Automatically manage signing**，选择自己的 Team。
2. 将 bundle identifier 设为自己可签名且唯一的值，例如 `com.yourname.iphoneuse.WebDriverAgentRunner`，替换 `yourname`。后续 `--wda-bundle-id` 使用这个基础 ID；不要重复追加 Xcode 测试 runner 使用的 `.xctrunner` 后缀。
3. 如果其他 target 也出现签名错误，为相关 target 选择同一团队并处理签名提示。
4. 选择 **WebDriverAgentRunner** scheme，运行目标选真实 iPhone，执行 **Product → Test**。由你完成手机上的开发者信任或授权提示。
5. 确认 runner 已构建、安装并启动，然后停止这次 Xcode 测试。如果失败，先解决 Xcode 报错，再继续。

这是 Appium 的[手动签名流程](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/full-manual-config/)。免费团队的 bundle ID 或 provisioning 错误可参考[基础配置](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/basic-manual-config/)。

### 用脚本准备预安装副本

在 Xcode 产品或构建报告中对 `WebDriverAgentRunner-Runner.app` 使用 **Show in Finder**，定位自己的原始构建。它通常在该项目 DerivedData 下的 `Build/Products/Debug-iphoneos/`。

在本机查看可用的签名身份：

```sh
security find-identity -v -p codesigning
```

选与该构建的 Xcode Team 对应的有效 **Apple Development** 身份，以其完整名称或 SHA-1 标识替换下方占位符：

```sh
uv run --frozen python scripts/prepare-wda.py \
  --source '/absolute/path/to/original/WebDriverAgentRunner-Runner.app' \
  --identity '<YOUR_VALID_APPLE_DEVELOPMENT_IDENTITY>'
```

脚本要求原件已经签名且验证通过。它复制到新的私有目录，只移除副本中嵌入的 `XC*` 框架，重签嵌套 XCTest bundle 和外层 App，并检查签名；原件和设备配置保持不变。这遵循 Appium 的[预安装 WDA 要求](https://appium.github.io/appium-xcuitest-driver/latest/guides/run-preinstalled-wda/)。

成功输出中的 `prepared_path` 是下面安装命令所需的 `.app` 绝对路径。默认保存在 `~/.local/share/iphone-use-wireless-direct/wda` 下的新目录，设置 `VICO_IPHONE_HOME` 时则使用该目录下的 `wda/`；可用 `--output-root '/absolute/private/wda'` 指定位置。签名失败时脚本停止并清理本次未完成副本。此步骤仅生成签名包。

### 把准备好的副本安装到手机

保持 USB 连接、手机解锁，并确认 Xcode 的测试已经停止。将设备 ID 和刚才输出的 `prepared_path` 代入：

```sh
xcrun devicectl device install app \
  --device '<DEVICE_UDID>' \
  '/absolute/path/from/prepared_path/WebDriverAgentRunner-Runner.app'
```

按手机提示完成开发者信任；如提示重启，重启后解锁。默认直连的 `connect` 只启动手机上已安装的 WDA，**不会替你安装或重签**。签名到期后也需要重新构建、准备并安装。安装成功之后继续配置和无线连接。

## 4. 保存设备配置

已有直连配置时先检查并复用。旧版配置按[升级说明](DIRECT_WDA_EXPERIMENT.md)迁移；首次设置用本机实际值替换占位符：

```sh
uv run --frozen iphone-use configure \
  --udid '<DEVICE_UDID>' \
  --team-id '<APPLE_TEAM_ID>' \
  --platform-version '<IOS_VERSION>' \
  --transport coredevice \
  --wda-bundle-id '<YOUR_WDA_BUNDLE_ID>' \
  --preinstalled-wda
uv run --frozen iphone-use doctor
```

直连模式使用 `coredevice` 和预安装 WDA；默认已经选择这两项，命令中显式列出便于核对。不要传 `--prebuilt-wda-path`，安装应在第 3 步单独完成。保留签名副本以便重新安装。`doctor` 检查主机与配置，手机连接在第 7 步完成。

默认私有配置为 `~/.local/share/iphone-use-wireless-direct/configuration/iphone-control.json`，本机 WDA 转发端口为 `127.0.0.1:18401`。`VICO_IPHONE_HOME` 优先于默认目录；CLI 名称仍为 `iphone-use`。旧 Appium 后端需要显式选择，见[兼容说明](DIRECT_WDA_EXPERIMENT.md)。

## 5. 准备 Codex 实际运行副本

第 1 步已注册插件。若 CLI 提示使用桌面应用安装，在本地 marketplace 中选择 **iphone-use-wireless**，然后打开新聊天；必要时重启客户端。让 Codex 执行：

> 使用 iphone-use-wireless 的 setup 技能，定位实际加载的插件目录，用 setup.sh --skip-plugin 准备该副本。保留已有设备配置。

Codex 可能从缓存副本运行插件，源码目录里的依赖不保证被复制。setup 技能从自己所在位置确定实际插件根目录，在那里运行：

```sh
bash scripts/setup.sh --skip-plugin
```

这不会再次注册插件或改动设备配置。需要只读检查时，用 `bash scripts/setup.sh --check --skip-plugin`。不要手填旧版本的缓存路径；后续隧道可以从保留的源码目录运行。

## 6. 拔线后启动前台无线隧道

1. 结束自己已有的有线会话和 Xcode WDA 测试。若之前开过自己的前台隧道，在对应终端用 `Ctrl+C` 结束。
2. 拔掉 USB 数据线，手机保持解锁，Mac 与 iPhone 位于同一可达网络。等待 Xcode 显示手机可无线连接。
3. 在普通用户的本机终端进入源码仓库根目录，检查准备情况：

```sh
node scripts/start-tunnel.mjs --check --json
```

`--check` 只读检查配置、无线发现、USB 状态和真实隧道登记，不创建文件、不请求 sudo。按返回的下一步处理问题后，运行：

```sh
node scripts/start-tunnel.mjs
```

脚本读取已有设备配置并使用项目锁定的隧道程序。若已有该设备的可用 Network 隧道，会提示复用；否则确认 USB 已断开、设备通过网络可发现、端口可用后，准备必要的登记文件并启动前台隧道。

**用普通用户运行，不要在命令外加 sudo。** 脚本会在需要时调用 sudo，并保留正确的 HOME；由你直接在终端输入管理员密码。保持新启动隧道的终端运行，结束时在该终端按 `Ctrl+C`。它不安装开机自启服务，不停止其他程序。

在另一个终端可检查：

```sh
node scripts/tunnel-diagnostics.mjs --json
```

目标应有 **Network** 登记且 USB 已断开；仅发现手机或端口正在监听都不足以证明隧道可用。诊断不会创建隧道。由于底层程序在 USB 与 Network 并存时优先 USB，应先结束有线会话、拔线，再新建无线隧道，不依赖运行中无缝切换。参见 [RemoteXPC 无线说明](https://github.com/appium/appium-ios-remotexpc#iphone--ipad-over-wifi-usbmuxd-network-devices) 和 [Appium 隧道说明](https://appium.github.io/appium-xcuitest-driver/latest/guides/remotexpc-tunnels-real-devices/)。

## 7. 连接并使用手机

对 Codex 说：

> 使用 iphone-use-wireless 连接我的手机。USB 已拔掉，请检查无线隧道、读取当前界面组件，并告诉我是否连接成功。

Codex 检查状态，再建立设备会话。默认模式下，`iphone_setup(start)` 只检查本机依赖；`iphone_session(connect)` 才会启动已安装的 WDA 和本机转发，不启动 Appium 服务。连接若返回 pending 和 `operation_id`，用 `connect_status` 查询同一操作，不重复发起 connect。成功后使用该会话的 `session_token` 读取当前组件，再执行你的任务。

每次动作后重新观察确认结果；组件不可用或经过有意义的再次尝试仍无法定位时，再使用截图。结果不明确时先核对状态，不重复发送、点击或购买来试探。任务完成后可断开自己的会话。

一次会话内会缓存屏幕尺寸；旋转手机后先断开并重新连接，再使用坐标操作。

日常顺序是：手机解锁与同网络 → 保持 Mac 唤醒 → 运行 `node scripts/start-tunnel.mjs` → 让 Codex 连接并操作。隧道需持续运行；休眠、重启、换网或签名过期后按下一节恢复。

## 8. 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| setup 提示缺少 Xcode 或运行工具 | 先按输出补齐系统软件；仅缺插件依赖时运行 `bash scripts/setup.sh`。缓存副本使用 `--skip-plugin`。 |
| 拔线后看不到手机 | 检查网络互通、Finder Wi-Fi 显示、Xcode 配对与手机解锁。访客网络隔离可能阻止发现；必要时重新接 USB 配对。 |
| 有线可用，拔线后断开 | 结束自己的有线会话和旧隧道，拔线后重新运行 `start-tunnel.mjs`，确认 Network 登记。 |
| Mac 休眠或换网后失联 | 唤醒 Mac、解锁手机，检查网络并重新启动自己的失效隧道。 |
| RemoteXPC unavailable | 运行 `start-tunnel.mjs --check --json`，按返回的下一步处理；诊断不会自动创建隧道。 |
| 42314 或 18401 已占用 | 查明服务所属任务，协调使用；不要批量终止服务。 |
| 隧道登记权限错误 | 先看诊断输出，再按下方方法核对自己的 Strongbox 路径。 |
| WDA 签名或启动失败 | 核对 Developer Mode、团队、基础 bundle ID 和 provisioning；修复 Xcode 构建后重新运行 `prepare-wda.py`，再用第 3 步命令安装。 |
| 免费账户几天后不能运行 | Personal Team 的设备安装 profile 七天到期。重新构建、准备并安装 WDA；团队或 bundle ID 变更时再更新配置。参见 [Apple 说明](https://developer.apple.com/help/account/basics/about-your-developer-account)。 |
| WDA 未安装或安装的 bundle ID 不匹配 | 结束自己的会话，按第 3 步安装准备好的包，核对第 4 步的基础 bundle ID。 |
| 插件更新后缺依赖 | 在当前加载副本运行 `bash scripts/setup.sh --skip-plugin`，不要沿用旧缓存路径。 |
| 控件位置变动或动作结果不明 | 重新观察和检查状态；不重放不确定动作，不删除会话锁。 |

重新配置或升级依赖前，先结束自己的设备会话和使用该目录的隧道。保留私有配置与签名副本；不要让多个控制器同时操作同一手机。

隧道启动失败时，可在本机查看终端提示的私有日志：`$VICO_IPHONE_HOME/logs/tunnel.log`，默认 `~/.local/share/iphone-use-wireless-direct/logs/tunnel.log`。原始日志可能包含设备 ID 等信息，不要原样贴到公开 Issue 或文档。

### 进阶：自己的 Strongbox 权限

便捷脚本会处理新的登记文件。指针归 root 所有但普通用户可安全读取（例如权限 `0644`）、目录归当前用户且权限安全时，本身不算错误：可以复用已有 Network 隧道，也可在所指端口空闲时重新启动，无需 chown；现有指针由 sudo 隧道程序更新。只有诊断报告不可读、目录所有者异常等问题时，才检查权限；确认相关路径由自己创建、没有他人的活动服务依赖，并停止自己的隧道后，再按需定点修复：

```sh
IPHONE_USE_REGISTRY_DIR="$HOME/Library/Application Support/appium-xcuitest-driver-nodejs/strongbox"
ls -ld "$IPHONE_USE_REGISTRY_DIR" "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
sudo chown "$(id -u):$(id -g)" "$IPHONE_USE_REGISTRY_DIR" "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
chmod 700 "$IPHONE_USE_REGISTRY_DIR"
chmod 600 "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
```

不要递归修改其他 Appium 或 Library 路径。普通用户插件和 sudo 隧道必须使用同一个 HOME 下的登记指针；`start-tunnel.mjs` 会通过 `sudo --preserve-env=HOME` 保持一致。修复后重跑第 6 步的只读检查和启动命令。
