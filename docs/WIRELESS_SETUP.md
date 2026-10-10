# 从零设置 iphone-use-wireless

[返回 README](../README.md) · [English](WIRELESS_SETUP.en.md)

完成首次 USB 配对和自己的 Apple 签名后，可以拔掉数据线，通过同网络的 Mac 操作 iPhone。本指南使用**预安装 WDA + CoreDevice / RemoteXPC**，要求 iOS 18+。Mac 仍需运行 Codex、Appium 和前台隧道；项目不包含跨网络 Relay。

维护者已在 iPhone 16、iOS 26.6.1、Xcode 26.6 上验证无 USB 会话和界面读取。这不是全新 Mac 的一键安装保证：你的 Apple 账户、配对和本地签名需要自己完成。

## 1. 获取代码与依赖

安装完整 Xcode，启动一次并完成组件安装。在 Xcode 的 **Settings → Locations → Command Line Tools** 中选中该 Xcode。准备 Python 3.11+、[uv](https://docs.astral.sh/uv/getting-started/installation/)、[Node.js](https://nodejs.org/en/download) 和 npm 10+。本项目支持的 Node.js 范围是 `^20.19.0 || ^22.12.0 || >=24.0.0`。

```sh
git clone https://github.com/xxwzkdwz/iphone-use-wireless.git
cd iphone-use-wireless
uv sync --frozen
node scripts/install-bridge.mjs --json
uv run --frozen iphone-use doctor
```

此时 `configured: false` 是正常的，设备尚未配置。`full_xcode` 应为 `true`，所需工具应已找到。安装依赖不会自动连接手机。

以下所有终端命令都从这个仓库根目录运行，除非步骤明确指出使用 Codex 安装副本。不要把真实配置、截图或证书复制到仓库。

## 2. 用 USB 完成 Apple 登录、信任与配对

1. 在 Xcode **Settings → Accounts** 中登录自己的 Apple Account。无需把密码交给 Codex，也无需导出私钥。
2. 用数据线连接 iPhone，解锁，在手机提示中选择“信任这台电脑”，由机主输入手机密码。
3. 打开 Xcode 的设备窗口（**Window → Devices and Simulators**；部分新版称 **Device Hub**）。选择目标手机，等待配对和开发支持准备完成，记录自己的 UDID 与 iOS 版本。
4. 在 iPhone **设置 → 隐私与安全性 → 开发者模式** 中启用 Developer Mode，按手机提示重启并确认。若没有这个选项，先启动 Xcode 配对流程。参见 [Apple Developer Mode 指南](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device)。
5. 为后续无线发现，在 Finder 的设备页面启用 **Show this iPhone when on Wi-Fi / 连接 Wi-Fi 时显示此 iPhone**，按界面提示应用更改。Xcode 若显示 **Connect via network**，也应启用；某些版本不再单独显示这一项。
6. 让 Mac 和 iPhone 连接同一可达网络。先保持 USB，继续完成 WDA 签名。

Apple 的[无线设备说明](https://help.apple.com/xcode/mac/current/en.lproj/dev3e2f4ee6d.html)介绍了先配对再拔线运行的方式；这里不要求关闭或切换你正在使用的网络。

## 3. 用自己的团队构建并安装 WDA

WDA 是安装到手机上的测试辅助 App，必须使用你自己的签名。不要下载或复用别人已经签名的 `.app`、证书或设备配置。

打开本项目锁定依赖附带的 Xcode 工程：

```sh
open bridge/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj
```

在 Xcode 中：

1. 选择 **WebDriverAgentRunner** target，进入 **Signing & Capabilities**，勾选 **Automatically manage signing**，选择自己的 Team。
2. 将该 target 的 bundle identifier 改成你自己可签名且唯一的值，例如 `com.yourname.iphoneuse.WebDriverAgentRunner`；`yourname` 必须替换。后续 `--wda-bundle-id` 使用这个基础 ID。Xcode 的测试 runner 通常会另加 `.xctrunner`，不要把该后缀重复加到配置值中。
3. 如果其他 target 也显示签名错误，在 Xcode 中为相关 target 选择同一团队，处理完所有签名提示。
4. 选择 **WebDriverAgentRunner** scheme，运行目标选你的真实 iPhone，然后执行 **Product → Test**。由机主完成手机上出现的开发者信任或授权提示。
5. 确认测试 runner 已构建、安装并启动。初次签名失败时先修复 Xcode 错误；不要跳过此步直接反复连接插件。成功后停止这次 Xcode 测试，再进行下文的预安装准备。

这是 Appium 的[手动签名流程](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/full-manual-config/)。免费 Personal Team 遇到 bundle ID / provisioning profile 错误时，可参考[基础手动配置](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/basic-manual-config/)。

### 准备供预安装模式使用的 .app

在 Xcode 的产品或构建报告中找到 `WebDriverAgentRunner-Runner.app`，一般在该项目 DerivedData 下的 `Build/Products/Debug-iphoneos/`。使用 **Show in Finder** 定位实际产物，不要复制其他机器的路径。

将自己的构建产物复制到固定的本机私有目录，例如 `~/.local/share/vico-iphone/wda/`。iOS 17+ 的预安装模式需要使用手机内置 XCTest frameworks，因此 `.app/Frameworks/` 中的 `XC*` 框架必须按上游要求处理，并在修改后重新签名。普通 Xcode 产物不能仅凭文件存在就视为已准备好。详见 [Appium 预安装 WDA 说明](https://appium.github.io/appium-xcuitest-driver/latest/guides/run-preinstalled-wda/)。

本项目已验证的 WDA 使用了“移除嵌入 XC* 框架、保留其他必要框架、重新签名”的副本。以下是按该要求整理的本机准备流程，**尚未在另一台全新 Mac 上完成从零构建验收**；最终以 Xcode、签名检查和你的手机连接结果为准。

先将自己的原始构建复制到一个新的私有子目录。替换第一行路径；不要把目标路径设成原始构建目录：

```sh
IPHONE_USE_WDA_SOURCE='/absolute/path/to/original/WebDriverAgentRunner-Runner.app'
IPHONE_USE_STATE_HOME="${VICO_IPHONE_HOME:-$HOME/.local/share/vico-iphone}"
mkdir -p "$IPHONE_USE_STATE_HOME/wda"
IPHONE_USE_WDA_DIR="$(mktemp -d "$IPHONE_USE_STATE_HOME/wda/prepared.XXXXXX")"
IPHONE_USE_WDA_APP="$IPHONE_USE_WDA_DIR/WebDriverAgentRunner-Runner.app"
ditto "$IPHONE_USE_WDA_SOURCE" "$IPHONE_USE_WDA_APP"
```

只处理刚创建的副本中 `Frameworks` 直属的 `XC*` 内容，保留其他框架：

```sh
python3 - "$IPHONE_USE_WDA_APP" <<'PY'
from pathlib import Path
import shutil
import sys

app = Path(sys.argv[1])
if app.name != 'WebDriverAgentRunner-Runner.app' or not app.is_dir():
    raise SystemExit('Expected the copied WDA .app directory')
for item in (app / 'Frameworks').glob('XC*'):
    if item.is_dir() and not item.is_symlink():
        shutil.rmtree(item)
    else:
        item.unlink()
    print('Removed from copy:', item.name)
PY
```

在本机列出可用的代码签名身份：

```sh
security find-identity -v -p codesigning
```

选与自己 Xcode Team 对应的有效 **Apple Development** 身份，将它的完整名称或 SHA-1 标识填入下面变量。证书列表留在本机，不需要公开或发到聊天；这个命令不导出私钥。

```sh
IPHONE_USE_SIGNING_IDENTITY='<YOUR_VALID_APPLE_DEVELOPMENT_IDENTITY>'
python3 - "$IPHONE_USE_WDA_APP" "$IPHONE_USE_SIGNING_IDENTITY" <<'PY'
from pathlib import Path
import subprocess
import sys

app = Path(sys.argv[1])
identity = sys.argv[2]
test_bundles = list((app / 'PlugIns').glob('*.xctest'))
if not test_bundles:
    raise SystemExit('No XCTest bundle found; check the original build product')
for bundle in [*test_bundles, app]:
    subprocess.run(['codesign', '--force', '--sign', identity,
                    '--preserve-metadata=identifier,entitlements', str(bundle)], check=True)
subprocess.run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)], check=True)
print('Prepared WDA:', app)
PY
```

此顺序先重签嵌套 `.xctest` 再签外层 `.app`，保留原有 identifier 和 entitlements；不要用别的团队身份给现有 profile 强行重签。验证失败就停在这里，回到 Xcode 修复原始签名后重新复制准备。记下最后显示的 `.app` 绝对路径，用于下一步；不要提交此包或签名材料到 Git。

## 4. 保存自己的设备配置

用本机实际值替换所有尖括号占位符：

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

`--preinstalled-wda` 启用预安装启动路径。提供 `--prebuilt-wda-path` 时，Appium 会从这个签名包安装 WDA；该文件必须一直存在于稳定的绝对路径。若正确的 WDA 已装到手机上，可省略路径，但 bundle ID 必须仍匹配。

私有配置位于 `~/.local/share/vico-iphone/configuration/iphone-control.json`；`VICO_IPHONE_HOME` 可更改私有目录。改名为 iphone-use-wireless 后保留这些路径和环境变量以兼容已有安装。端口默认是 `127.0.0.1:4737`。`doctor` 是主机检查，不会替你完成手机连接验收。

## 5. 安装 Codex 插件并准备实际运行副本

```sh
bash scripts/install-codex.sh
```

打开新的 Codex 聊天，必要时重启桌面客户端。让 Codex 执行：

> 使用 iphone-use-wireless 的 setup 技能，定位当前实际加载的插件目录，检查并安装该副本缺少的依赖。保留我已有的设备配置，先不要操作手机。

**这一步不能省略。** Codex 通常从缓存副本运行插件，仓库里的 `.venv` 和 `bridge/node_modules` 不保证出现在副本中。setup 应从当前加载的 `skills/setup/SKILL.md` 推导插件根目录，在该目录运行：

```sh
uv sync --frozen
node scripts/install-bridge.mjs --json
uv run --frozen iphone-use doctor
node bridge/cli.mjs status --json
```

不要手填某台开发者机器的缓存路径。仓库目录和安装副本都读取同一个私有配置，且一次只允许一个控制器操作该设备。隧道可以使用你保留的稳定仓库目录中的锁定依赖运行。

## 6. 拔线后启动前台无线隧道

1. 结束自己已有的有线手机会话，停止自己在 Xcode 中运行的 WDA 测试。若之前开过本指南的隧道，在它自己的终端按 `Ctrl+C` 结束。
2. 拔掉 iPhone 与 Mac 之间的 USB 数据线；手机保持解锁，Mac 和 iPhone 仍在同一可达网络。等待 Xcode 设备窗口显示它可无线连接。
3. 先在普通用户终端准备隧道端口指针。若此位置已被另一套 Appium 隧道使用，先核对其用途，不覆盖已有活动服务。下面脚本仅为缺失的目录和文件创建用户拥有的初始内容；已存在时检查权限并保留内容：

```sh
python3 - <<'PY'
from pathlib import Path
import os
import stat

directory = Path.home() / 'Library/Application Support/appium-xcuitest-driver-nodejs/strongbox'
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
details = directory.lstat()
if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
    raise SystemExit('Registry directory must belong to the current user; inspect before repair')
pointer = directory / 'tunnelRegistryPort'
try:
    fd = os.open(pointer, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    pass
else:
    os.close(fd)
details = pointer.lstat()
if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid()
        or details.st_mode & 0o022 or not os.access(pointer, os.R_OK | os.W_OK)):
    raise SystemExit('Registry pointer must be user-owned, readable/writable, and not group/world writable')
print('Registry location prepared; existing contents were not overwritten')
PY
```

4. 在一个单独终端进入源码仓库根目录，执行下列命令。`sudo` 由机主在这个终端交互确认，密码不要发到聊天里：

```sh
IPHONE_USE_ROOT="$(pwd -P)"
sudo --preserve-env=HOME "$(command -v node)" \
  "$IPHONE_USE_ROOT/bridge/node_modules/appium-xcuitest-driver/node_modules/appium-ios-remotexpc/scripts/tunnel-creation.mjs" \
  --udid '<DEVICE_UDID>' \
  --tunnel-registry-port 42314 \
  --reconnect-retries 3
```

保持这个终端运行。它创建本机 TUN/TAP 接口与 RemoteXPC 隧道，属于单独的前台进程；本插件不安装 privileged daemon 或开机自启。正常结束时在这个终端按 `Ctrl+C`，不要用“杀掉所有 Node/Appium”来停止它。

**保留当前 HOME 很关键。** 锁定版本的 Appium 用用户主目录寻找 Strongbox 端口指针；默认 sudo 的 root 主目录可能使隧道和普通用户插件使用不同位置。这里仅保留 HOME，不需要保留整个环境。支持该选项的 `sudo --help` 会列出 `--preserve-env=list`。

这些参数已按锁定的 `appium-ios-remotexpc 5.18.9` 脚本核对。该版本同时发现 USB 和 Network 时会优先 USB，也不包含新版本的 `--watch-devices` 参数，所以请在拔线后重新启动隧道。其帮助文字仍写 USB，但上游支持通过 usbmuxd 的 `ConnectionType: Network` 使用无线设备。参见 [RemoteXPC 无线说明](https://github.com/appium/appium-ios-remotexpc#iphone--ipad-over-wifi-usbmuxd-network-devices) 和 [Appium 隧道说明](https://appium.github.io/appium-xcuitest-driver/latest/guides/remotexpc-tunnels-real-devices/)。

在另一个终端进行只读诊断：

```sh
node scripts/tunnel-diagnostics.mjs --json
```

需要进一步核对隧道登记时，可在本机查看：

```sh
curl --fail --silent --show-error http://127.0.0.1:42314/remotexpc/tunnels
```

登记结果可能包含 UDID 与本机地址，留在本机，不贴到公开 Issue。确认目标项的 `connectionType` 是 `Network`；没有目标项或显示 `USB` 时，不算完成无线验证。若 42314 已被另一套隧道占用，先确认拥有者和用途；不要停止或覆盖别的任务服务。

## 7. 连接并完成拔线验收

对 Codex 说：

> 使用 iphone-use-wireless 连接我的手机。现在 USB 已拔掉，请先检查无线隧道，再读取当前界面组件，完成后告诉我连接和读取是否成功。

工具顺序如下，通常由 Codex 自动执行：

1. `iphone_status`、`iphone_setup(action="status")` 检查状态；需要时 `iphone_setup(action="start")` 启动 Appium。
2. `iphone_session(action="connect")` 建立会话。如果返回 pending，保存私有 `operation_id`，调用 `connect_status` 轮询同一次连接，不能重新发起 connect。
3. 成功后保存 `session_token`，带令牌调用 `iphone_observe(mode="components")`。必须拿到新观察，不能仅凭 Appium ready 宣称手机可控。
4. 需要动作验收时，指定一个你允许的可逆动作，例如打开一个指定 App；动作后再观察确认。截图只在组件不可用或多次有效尝试仍无法定位时回退。
5. 用同一会话令牌断开自己的会话，再连接一次，验证无线重连。

验收标准是：**数据线已拔、目标登记为 Network、真实会话成功、重新读取界面成功**。位置会变化的界面需重新观察；未知结果不要重放。记下具体设备和版本即可，不必保存私人页面截图作为公开证明。

日常使用时重复“手机解锁与同网络 → 启动前台隧道 → 让 Codex 连接”。系统重启、休眠、换网或签名过期后可能需要重新检查，不能保证永久在线。

## 8. 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| 拔线后看不到手机 | 检查双方网络是否互通、Finder Wi-Fi 显示与 Xcode 配对、手机是否解锁；必要时重新接 USB 完成配对，再按第 6 步验证。访客网络的设备隔离可能影响发现。 |
| 有线可用，拔线后断开 | 当前隧道可能选中了 USB。结束自己有线会话和前台隧道，拔线后重建 Network 隧道，不重放未确认动作。 |
| Mac 休眠或网络切换后失联 | 唤醒 Mac、解锁手机，检查同网络和隧道状态；结束自己失效的前台隧道后重新启动。 |
| 显示隧道缺失 / RemoteXPC unavailable | 确认第 6 步终端仍运行，当前版本依赖已经安装，登记中存在目标。诊断命令本身不会启动隧道。 |
| 42314 或 4737 已占用 | 查明服务所属任务，协调使用或由拥有者关闭；不要批量终止服务。 |
| 隧道日志正常，但插件说没有隧道 | 核对启动命令保留了当前 HOME，且普通用户能读取 Strongbox 的 `tunnelRegistryPort`；检查下文的定点权限修复。 |
| WDA cannot launch / invalid signature | 核对 Developer Mode、签名团队、基础 bundle ID、设备 provisioning，以及处理后的 `.app` 是否重新签名。 |
| 免费账户几天后突然不能运行 | Personal Team 的设备安装 provisioning profile 七天到期。重新用 Xcode 构建、安装和准备 WDA，必要时更新本地 `.app` 路径。[Apple 说明](https://developer.apple.com/help/account/basics/about-your-developer-account)。 |
| `prebuilt_wda_path` 指向不存在文件 | 移动仓库、清理 DerivedData 或更新插件后旧路径可能失效。断开自己的会话，用第 4 步完整 configure 命令设置稳定的真实 `.app` 路径。不要只改 JSON 来绕过校验。 |
| 插件更新后提示缺依赖 | 新缓存副本需要自己的依赖。重新运行 setup，按当前技能位置确定根目录，不沿用旧版本缓存路径。 |
| 组件引用失效、点击未发生 | 界面可能已经变动；重新读取组件并重新定位。不能把过去观察的坐标或引用用于新页面。 |
| 动作或连接结果 unknown / uncertain | 暂停后续变更，检查真实界面与工具状态；按受支持的恢复流程处理，不重复点击、发送、connect，也不删除锁文件。 |

重新配置需要先结束自己的设备会话。保留 `VICO_IPHONE_HOME` 及签名副本，不要把旧 VICO 或其他控制器的会话、锁、证书直接搬来。升级依赖前结束设备会话和使用该依赖目录的前台隧道，再运行锁定安装命令。

### 修复自己创建的 Strongbox 权限

仅在确认该目录和指针是本次自己创建、没有其他活动隧道依赖它，并已停止自己的前台隧道后使用。若指针曾因 sudo 变成 root 所有，可以只修复这两个具体路径；不要递归修改整个 `Library`、Appium 状态或别人的服务：

```sh
IPHONE_USE_REGISTRY_DIR="$HOME/Library/Application Support/appium-xcuitest-driver-nodejs/strongbox"
ls -ld "$IPHONE_USE_REGISTRY_DIR" "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
sudo chown "$(id -u):$(id -g)" "$IPHONE_USE_REGISTRY_DIR" "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
chmod 700 "$IPHONE_USE_REGISTRY_DIR"
chmod 600 "$IPHONE_USE_REGISTRY_DIR/tunnelRegistryPort"
```

然后重新执行第 6 步，并在普通用户终端运行诊断。这里修复的是自己的端口指针，不创建守护进程，也不使用原 VICO 的隧道服务。
