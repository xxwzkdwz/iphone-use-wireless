# 无线直连 WDA 实验版

实验分支：`experiment/direct-wda`。原版 Appium 后端仍是默认实现。

这个版本通过已有的无线 RemoteXPC 隧道，独立启动已安装的 WDA，再直接请求 WDA 的 HTTP 接口。启动和操作过程都不启动 Appium Server。仍然使用 Apple XCTest / WDA 和 Appium 生态的 RemoteXPC 库。

## 准备

先完成原无线版的配对、签名、安装 WDA 和无线隧道设置。手机需要已解锁、同网络，Mac 保持唤醒。实验版不会自动安装或重签 WDA，也不会创建需要管理员权限的隧道。

在本实验分支目录中运行：

```sh
uv sync --frozen
npm ci --prefix direct --no-audit --no-fund
uv run --frozen iphone-use init-direct
```

`init-direct` 从已有独立插件复制设备配置到新的私有目录 `~/.local/share/iphone-use-wireless-direct`，使用独立端口 `18401`，不复制会话或覆盖旧配置。自定义原状态目录时传 `--from-state`。已有实验配置不会被覆盖。

## 在 Codex 中试用

保留原插件，额外注册一个有区别的 MCP 名称：

```sh
codex mcp add iphone_wireless_direct -- bash '/absolute/path/to/iphone-use-wireless-direct-wda/scripts/run-direct-server.sh'
```

新开聊天后说：

> 用 iphone_wireless_direct 实验版无线连接我的 iPhone，读取当前页面，然后帮我打开设置。完成后断开会话。

实验版的 `iphone_setup(start)` 只检查主机准备情况；实际启动 WDA 在 `iphone_session(connect)` 中完成。工具说明带有 `Wireless direct-WDA experiment` 标识。

同一台手机一次只运行一个控制会话。先让当前会话断开，再切换原版或实验版；两者共用设备占用检查。动作超时不会自动重放。

## 返回原版

让实验会话断开，再使用原来的 `iphone-use-wireless` 插件即可。原目录、设备配置、安装和 GitHub 主分支保持原样。若不想继续加载实验工具：

```sh
codex mcp remove iphone_wireless_direct
```

## 效果比较

连接耗时和已连接后的操作耗时分开记录。使用同一设备、无线网络和页面比较读取组件、截图、点击；工具耗时不包括模型思考。少量真机结果只能说明本次试用情况，不代表所有 App 或网络下都会更快。

参考：[WDA 独立运行](https://appium.github.io/appium-xcuitest-driver/latest/guides/wda-custom-server/) · [Codex MCP 配置](https://developers.openai.com/codex/mcp)
