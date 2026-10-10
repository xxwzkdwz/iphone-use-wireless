# Third-party notices

This independent release of iphone-use-wireless is distributed under the [MIT License](LICENSE). Its phone-control implementation was extracted from the author's VICO project; compatibility state paths are retained. This release does not change the license of the original VICO application, dependencies, Apple software, or the Codex host.

## Runtime dependencies

| Component | Purpose | Upstream license |
| --- | --- | --- |
| [Appium](https://github.com/appium/appium) | Local WebDriver server | [Apache-2.0](https://github.com/appium/appium/blob/master/LICENSE) |
| [Appium XCUITest Driver](https://github.com/appium/appium-xcuitest-driver) | iOS automation driver | [Apache-2.0](https://github.com/appium/appium-xcuitest-driver/blob/master/LICENSE) |
| [Appium iOS RemoteXPC](https://github.com/appium/appium-ios-remotexpc) | Wireless service access, TCP forwarding, and runner process control for the direct-WDA experiment | [Apache-2.0](https://github.com/appium/appium-ios-remotexpc/blob/main/LICENSE) |
| [WebDriverAgent](https://github.com/appium/WebDriverAgent) | Device-side XCTest automation | [BSD-3-Clause](https://github.com/appium/WebDriverAgent/blob/master/LICENSE) |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MCP protocol implementation | [MIT](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE) |

`bridge/package-lock.json`, `direct/package-lock.json`, and `uv.lock` identify the resolved dependency graphs. The optional direct-WDA experiment installs its own pinned `appium-ios-remotexpc` dependency under `direct/`; it uses that library and WebDriverAgent without running Appium Server or the XCUITest Driver. Transitive dependencies retain their own licenses and notices. This table lists key components; it is not an exhaustive license inventory of an installed environment.

The source ZIP deliberately excludes `node_modules`, Python environments, compiled WebDriverAgent, and signing material. Dependencies are downloaded to the user's machine during setup. Preserve their included copyright, LICENSE, and NOTICE files. If distributing a separate installer or binary that embeds those dependencies, include the applicable upstream notices and license texts for every embedded component.

## Local compatibility patches

The installer applies local compatibility patches to Appium/XCUITest/WebDriverAgent for this bridge. Those changes are represented in `scripts/iphone-control-patches.mjs`; the file and locked dependency versions define their scope. Upstream license obligations continue to apply to patched files. The repository includes [Apache License 2.0](licenses/Apache-2.0.txt) and the [WebDriverAgent license](licenses/WebDriverAgent-LICENSE.txt) for this context; original notices in installed dependencies are preserved.

Appium's upstream license credits OpenJS Foundation and other contributors. The XCUITest driver's upstream license credits JS Foundation and other contributors. WebDriverAgent's upstream notice credits Facebook, Inc., 2015-present. No endorsement by those projects, Apple, or OpenAI is implied.

## Development tools

uv, Python, Node.js, npm, Xcode, and Codex are separately installed development/host tools. They are not redistributed in this source package. Apple signing and device provisioning remain under the device owner's control.
