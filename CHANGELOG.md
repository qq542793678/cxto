# Changelog

本项目的所有重要变更都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.4.0] - 2026-11-02

### Added

- 新增 `cxto run`：将 provider、URL、凭证、模型和推理强度绑定到启动进程。交互进程独立运行，其他窗口的渠道切换不改变已有进程。
- 新增 `install-launcher` / `uninstall-launcher`：可回滚地接入普通 `codex` 命令，保留原文件或符号链接。管理命令、帮助、远程和 OSS 入口透传，保留 tmux 环境与用户参数。
- 专用进程环境变量固定 API key，新增的启动参数中不含密钥。已有进程无法热补，保护从下一次经启动器进入 Codex 时生效。
- 真实多进程回归覆盖 A 请求进行中时 B/C 并发切换、A 请求重试、同进程重新附着及后续轮次，核对地址、密钥、模型和推理强度保持不变。

### Fixed

- 渠道读写加进程锁，配置与认证原子更新，避免同时切渠道时读取混合状态。保持配置文件符号链接与原权限。
- 修正仅有独立 provider 块就能保证所有运行期场景不受影响的说明；恢复入口兼容继续保留，运行期隔离以启动器为准。

## [0.3.1] - 2026-11-02

### Fixed

- 修复跨渠道恢复缺少顶层 `model_provider` 覆盖而触发 `modelProviderMismatch` 的问题；恢复进程始终使用会话保存的 provider 身份。
- 显式指定目标渠道时，完整替换本次进程的旧 provider 配置，避免残留旧 token、`env_key` 或认证标志。双参数模式继续保持先激活目标渠道的行为。
- `resume --dry-run` 隐藏 bearer token；会话查找使用完整 UUID 并流式读取 metadata。
- 修正普通 `codex resume` 可直接跨 provider 恢复，以及退出重进会改变历史共享 provider 身份的错误说明。

### Added

- `cxto resume <会话ID>`：自动沿用原 provider，不改写全局配置、认证或渠道状态。缺少原 provider 配置或使用历史共享身份时，提示显式指定渠道。
- 新增 CLI 回归及可选真实 Codex 协议测试；使用临时配置和合成会话验证恢复，并向本机 HTTP 桩发送合成请求核验目标密钥，不调用真实渠道。

## [0.3.0] - 2026-10-10

### Changed

- 版本号提升至 `0.3.0`。
- 补全 `cxto -h` 和各子命令的帮助信息，覆盖全部命令、参数说明、常见用法示例及模型目录显式刷新的等待行为，方便查找低频功能。
- **每个渠道有自己固定的 provider 块，切渠道不再影响其它正在跑的会话。**
  - 旧实现把「当前渠道」写进一个共享的 `[model_providers.cxto_active]` 块并原地改写它。Codex 的常驻 app-server daemon 每个 turn 都会重新解析「会话钉住的那个 provider 名」在当前 `config.toml` 里的定义，于是所有会话都钉在同一个可变块上：在一个窗口 `cxto use`，另一个窗口里正在跑的会话下一个请求就被改道（地址和 key 一起变），报「模型找不到」。
  - 现在每个渠道写自己固定的 `[model_providers.cxto_<渠道>]`（各自带 `base_url` / `wire_api` / 凭证），`cxto use` 只改顶层 `model_provider` 这一行，其它块一个字节都不碰：已在跑的会话继续解析到自己的渠道，切换只影响之后新开的会话。
  - 顶层 `model_provider` 现在写 `cxto_<渠道>`（此前恒为 `cxto_active`）。
  - `cxto resume` 除 `base_url` 外，还会把目标渠道的 `wire_api` / 认证方式 / token 一并注入，避免拼出「新地址 + 老 key」。
- **凭证也钉进各自的块。** `requires_openai_auth` 那类渠道默认从**全局** `auth.json` 取 key，而那是所有会话共用的一个文件 —— 切渠道会覆盖它，于是别的会话拿着「自己的地址 + 别人的 key」去请求，报 `401 INVALID_API_KEY`（实测：老会话打 fufuapi 却 401）。现在只要渠道配了 `auth_token_path`，就把 token 写进它的块（实测：块里的 `experimental_bearer_token` 优先于 `requires_openai_auth`），每个会话自给自足。**没配 key 文件的渠道仍然共用 `auth.json`。**

### Fixed

- 修复原生模型目录长期停留在旧 JSON 文件的问题：读取已安装 Codex CLI 的内置模型目录，保留本机额外条目并合并渠道源；升级 CLI 后运行 `cxto models --refresh` 可加载新版本附带的模型，刷新时显示原生目录来源。
- 修正显式配置 `model_catalog_path` 时跳过 Codex CLI 内置模型的问题：该路径现在作为本机补充目录与 CLI 内置目录合并，避免旧快照屏蔽新模型。
- **GPT 与 DeepSeek 模型目录合并，渠道切换始终即时完成。** cxto 把 Codex 原生模型目录和渠道自定义目录合并到固定的 `cxto_model_catalog.json`；切换只更新渠道与默认模型，不会等待 daemon 重启。
  - 新增渠道或模型后，`cxto use` 更新联合目录；`cxto models --refresh` 显式请求 `codex app-server daemon restart` 加载新列表。重启会等待进行中的 turn 完成，最多等待 120 秒，超时不会强杀 daemon。
  - 找不到有效的 Codex 原生目录时，退回按当前渠道使用模型目录，缓存刷新仍由 `cxto models --refresh` 显式触发。

### Removed

- `cxto use` 不会自动刷新 daemon 缓存。`cxto models --refresh` 是显式操作，只在模型目录更新且用户选择刷新时等待任务排空。

### Compatibility

- 升级前启动、`session_meta` 里钉着 `cxto_active` 的会话仍被照顾：每次切换都会把 `[model_providers.cxto_active]` 同步指向当前渠道，它们不会解析到过期地址。但这些老会话钉的仍是共享块，所以**还会跟着切换走**；要彻底隔离需退出重进一次。

## [0.2.0] - 2026-08-10

### Added

- 支持 DeepSeek 渠道接入 Codex（`deepseek-v4-flash`），走 DeepSeek 原生 Responses API，无需中间代理。
  - 切换时自动写入 DeepSeek 官方要求的顶层配置：`preferred_auth_method = "apikey"`、`forced_login_method = "api"`、`model_catalog_json = "~/.codex/models.json"`。
  - provider 块新增 `experimental_bearer_token` 支持，切换时从 `auth_token_path` 指定的 key 文件动态注入，API key 不落入 YAML 配置。
  - 内置 DeepSeek 官方模型目录（`models.json`，含 `deepseek-v4-flash` 与 `deepseek-v4-pro` 的上下文窗口、推理档位、工具格式等元数据），切换时自动写入 `model_catalog_json` 指定路径。
- provider 块新增 `env_key` 字段支持（供通过环境变量注入认证的渠道使用）。

### Changed

- `replace_provider` 改为重建 provider 块内行：可识别并清理不再需要的受管字段（如从 DeepSeek 切回普通渠道时移除残留的 `experimental_bearer_token`）。
- `write_codex_config` 在切换时按当前 provider 写入/清理顶层额外键，避免 DeepSeek 特有配置残留影响 OpenAI 渠道的登录流程。

### Fixed

- 无（本次为功能新增，未修复既有缺陷）。

## [0.1.0] - 2026-07-19

### Added

- 初始发布：可移植的 Codex 渠道 / 认证 / 会话切换 CLI。
- 支持 `use`、`status`、`providers`、`resume`、`export-env`、`models`、`init` 子命令。
- 通过 `~/.cxto.local.yaml` / `~/.cxto.team.yaml` / `~/.config/cxto/config.yaml` 集中配置多个渠道，切换时备份并更新 `~/.codex/config.toml` 与 `~/.codex/auth.json`。
