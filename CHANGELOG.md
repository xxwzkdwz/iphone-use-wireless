# Changelog

## 0.1.2 — Guided wireless setup

- One setup command checks Mac prerequisites, prepares this copy's runtime dependencies, and registers the Codex plugin. A read-only check and an installed-cache mode are included.
- A WDA preparation helper works on a fresh copy of the owner's signed build, removes embedded XCTest frameworks and verifies the prepared signature.
- A foreground tunnel launcher reads the saved device configuration, checks wireless discovery and existing services, and guides local administrator authentication when needed.
- Tunnel diagnostics inspect the local registry and distinguish wireless, USB, missing and unreachable states without starting a phone session.
- Chinese and English guides now use the same assisted setup flow and explain which first-time steps require the owner.

## 0.1.1 — Wireless name

- Display name: **iphone-use-wireless**.
- GitHub repository: **xxwzkdwz/iphone-use-wireless**; installation links and guides updated.
- Existing plugin, marketplace, MCP and CLI identifiers remain compatible.

## 0.1.0 — First public preview

- Independent Codex plugin named **iphone use**, extracted from VICO's phone-control implementation.
- Same-network wireless iPhone control through a Mac after initial pairing, signing, and RemoteXPC tunnel setup.
- Component-first observation and actions, with screenshots available when necessary.
- Owner-controlled setup, isolated local runtime, exclusive device sessions, and recoverable disconnect.
- Automatically rebuilds Appium's generated driver registration when an installed plugin moves to a new path.
- Chinese and English installation, wireless preparation, verification, and troubleshooting guides.

Tested on an iPhone 16 running iOS 26.6.1 with Xcode 26.6. Wireless connection, component observation, app navigation, scrolling and selection were exercised on the real device. Automated checks use fixtures and isolated runtimes; they do not replace device acceptance. Fresh-machine setup and other iPhone/iOS/Xcode combinations have not yet been verified end to end.

The Mac must remain available and the paired phone reachable on the same network. Initial setup may need USB; expired signing profiles may need renewal. This release does not provide cross-network relay access or run a phone agent independently of the Mac.
