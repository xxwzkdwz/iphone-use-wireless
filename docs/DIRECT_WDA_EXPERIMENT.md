# 直连版升级与 Appium 兼容说明

[返回 README](../README.md) · [首次安装](WIRELESS_SETUP.md) · [English](#english)

从 0.2.0 起，`iphone-use-wireless` 默认直接连接 WDA。无需运行 Appium Server 或 XCUITest Driver；仍使用 Apple XCTest、WebDriverAgent 和 `appium-ios-remotexpc` 无线库。此页面保留旧文件名，方便已有链接继续访问。

## 从旧 Appium 版升级

先断开自己正在使用的手机会话。在更新后的源码目录运行：

```sh
bash scripts/setup.sh --skip-plugin
bash scripts/install-codex.sh --reinstall
```

`--reinstall` 通过 Codex 的插件移除与安装命令刷新缓存，保留本机私有设备配置。脚本核对 Codex 返回的已安装版本与源码 manifest 版本，版本仍旧时会报错；不能仅凭“已注册”判断升级完成。`setup.sh` 不接受 `--reinstall` 参数。如果使用手动 MCP 注册而非插件，更新其指向的源码目录并重启该 MCP 进程即可。

打开新聊天，用 `setup` 技能定位实际加载副本、确认版本已更新，并在该副本运行 `bash scripts/setup.sh --skip-plugin` 准备依赖。旧配置尚未迁移时，再执行：

```sh
uv run --frozen iphone-use init-direct
uv run --frozen iphone-use doctor
```

`init-direct` 将旧的 `~/.local/share/vico-iphone` 设备配置复制到 `~/.local/share/iphone-use-wireless-direct`，将本机转发端口设为 `18401`，不复制会话、运行进程或连接时自动安装 WDA 的路径。旧配置不变，目标已有配置时拒绝覆盖。**已经使用过直连版，无需再运行迁移**，直接检查并复用原直连配置。

原配置必须是 `coredevice` 和预安装 WDA 模式。使用自定义目录时明确指定来源和目标：

```sh
uv run --frozen iphone-use init-direct \
  --from-state '/absolute/path/to/old-private-state' \
  --state-dir '/absolute/path/to/new-private-state'
```

自定义目标目录还需要给后续命令和 MCP 进程设置同一个 `VICO_IPHONE_HOME`；该变量优先于任何默认目录。不要让旧的全局 `VICO_IPHONE_HOME` 无意中把新后端指向旧配置。

迁移不会安装 WDA。如果手机已有匹配且签名有效的 WDA，可以复用；否则按[安装指南第 3 步](WIRELESS_SETUP.md#3-用自己的团队构建并准备-wda)重新构建、准备，并用 `xcrun devicectl device install app` 安装。默认连接只启动已安装的 WDA，不自动安装或重签。

随后按指南保持手机解锁、Mac 唤醒、USB 拔掉和同网可达，检查并运行前台无线隧道，在新聊天中使用 `iphone-use-wireless` 连接。`iphone_setup(start)` 只检查本机依赖；`iphone_session(connect)` 启动 WDA 和本机转发。旧实验用的 `scripts/run-direct-server.sh` 仍可用，但不要重复注册多个入口控制同一手机。

## 显式使用旧 Appium 后端

旧后端保留用于兼容，需要主动选择；直连失败不会自动回退。结束当前会话后，在要运行旧后端的副本中准备依赖：

```sh
bash scripts/setup.sh --backend appium --skip-plugin
IPHONE_USE_BACKEND=appium uv run --frozen iphone-use doctor
```

该模式默认读取 `~/.local/share/vico-iphone`，Appium 端口仍为 `4737`；`VICO_IPHONE_HOME` 同样优先。启动 MCP 进程时也必须设置 `IPHONE_USE_BACKEND=appium`，仅在安装命令上选择后端不会改变之后的默认值。例如手动注册：

```sh
codex mcp add iphone_use_appium -- env IPHONE_USE_BACKEND=appium bash '/absolute/path/to/iphone-use-wireless/scripts/run-server.sh'
```

旧后端的隧道命令也选择同一配置和依赖：

```sh
node scripts/start-tunnel.mjs --backend appium --check --json
node scripts/start-tunnel.mjs --backend appium
```

不要同时运行两个设备会话，或终止别的任务拥有的 Appium、WDA、隧道进程。默认直连与旧后端共用设备占用检查。一次会话内的屏幕尺寸会缓存；旋转手机后断开并重连，再做坐标操作。

## English

Starting with 0.2.0, `iphone-use-wireless` uses direct WDA by default. It does not run Appium Server or XCUITest Driver. Apple XCTest, WebDriverAgent, and `appium-ios-remotexpc` remain the underlying components. This page keeps its earlier filename for existing links.

### Upgrade an older Appium installation

Disconnect your own active phone session. From the updated source checkout, run:

```sh
bash scripts/setup.sh --skip-plugin
bash scripts/install-codex.sh --reinstall
```

`--reinstall` refreshes the cache using Codex's plugin remove/add commands while retaining private device configuration. The script compares the installed version reported by Codex with the source manifest and fails if it is still outdated. Marketplace registration alone does not establish a successful upgrade. `setup.sh` does not accept `--reinstall`. For manual MCP registration instead of plugin installation, update the checkout it points to and restart that MCP process.

Open a new chat. Use the `setup` skill to locate the actual loaded copy, confirm its version, and prepare dependencies there with `bash scripts/setup.sh --skip-plugin`. If the old configuration has not yet been migrated, run:

```sh
uv run --frozen iphone-use init-direct
uv run --frozen iphone-use doctor
```

Migration copies the old `~/.local/share/vico-iphone` device configuration to `~/.local/share/iphone-use-wireless-direct` and sets local forwarding port `18401`. It does not copy sessions, running processes, or Appium's install-on-connect path. The source is preserved, and an existing destination configuration is never overwritten. **If you already configured the direct backend, skip migration and reuse that configuration.**

The source must use `coredevice` with preinstalled WDA. For custom directories, pass `--from-state '/absolute/old-state' --state-dir '/absolute/new-state'`. Set `VICO_IPHONE_HOME` to the destination for subsequent commands and the MCP process. This variable overrides either backend's default directory; check for a stale global override before switching backends.

Migration does not install WDA. Reuse an already installed matching WDA with valid signing, or follow [step 3 of the setup guide](WIRELESS_SETUP.en.md#3-build-and-prepare-wda-with-your-own-team) to build, prepare, and explicitly install it with `xcrun devicectl device install app`. Direct connect only starts the installed app; it does not install or re-sign it.

Follow the guide to keep the phone unlocked, Mac awake, USB unplugged, and both devices on the same reachable network. Check and run the foreground tunnel, then connect through `iphone-use-wireless` in a new chat. `iphone_setup(start)` checks dependencies; `iphone_session(connect)` starts WDA and local forwarding. The earlier `scripts/run-direct-server.sh` entry point remains usable, but avoid registering duplicate control endpoints for the same device.

### Select the legacy Appium backend explicitly

Disconnect the current session, then run:

```sh
bash scripts/setup.sh --backend appium --skip-plugin
IPHONE_USE_BACKEND=appium uv run --frozen iphone-use doctor
```

Also set `IPHONE_USE_BACKEND=appium` in the MCP process, for example using the `codex mcp add` command above. The installation flag alone does not change future defaults. Legacy mode uses `~/.local/share/vico-iphone` and Appium port `4737`, unless `VICO_IPHONE_HOME` overrides the state directory. Use `--backend appium` for its `start-tunnel.mjs` checks and foreground tunnel as shown above. Direct mode never falls back automatically.

Use one device session at a time and do not stop another task's processes. Both backends share device ownership checks. Screen dimensions are cached within a session; reconnect after rotating the phone before coordinate actions.
