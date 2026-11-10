# cxto

一个用于 **切换 Codex 渠道 Base URL、API key、模型与推理强度** 的命令行工具。

把多个渠道集中配置在一个目录中，然后通过一条命令完成切换：

```bash
cxto use channel_b
```

它会更新 Codex 需要的配置和认证信息；你不必再手动编辑 `config.toml`、复制 API key，或记忆不同渠道的 URL。

## 安装

要求：Python 3.10+、已安装 Codex CLI。进程隔离启动器和并发配置锁适用于 Linux/macOS。

推荐使用 [pipx](https://pipx.pypa.io/) 安装：

```bash
pipx install git+https://github.com/qq542793678/cxto.git
```

确认安装成功：

```bash
cxto --version
```

在源码目录中开发而未安装时，也可直接运行 ./bin/cxto --version。这个入口只加载本仓库的 src/cxto，无需其他项目中的兼容脚本。

要继续使用普通 `codex` 命令，并让运行中的会话不受其他窗口切渠道影响，安装一次启动器：

```bash
cxto install-launcher
```

它会将 PATH 中的原 Codex 入口备份为相邻的 `codex.cxto-original`，然后安装可回滚的启动器。终端已有的函数或别名若仍调用同一路径，无需重开 shell。撤销时运行 `cxto uninstall-launcher`。两个命令都支持 `--path` 指定入口和 `--dry-run` 预览。

也可以保持原入口，改用 `cxto run` 启动受保护的 Codex；额外参数写在 `--` 后，例如 `cxto run -- --no-alt-screen`。

## 配置

所有 cxto 配置放在一个目录：

```text
~/.config/cxto/
├── config.yaml          # 渠道、URL、模型等非敏感配置
└── keys/
    ├── channel_a.key    # channel_a 的 API key
    └── channel_b.key    # channel_b 的 API key
```

创建目录和配置文件：

```bash
mkdir -p ~/.config/cxto/keys
touch ~/.config/cxto/config.yaml
chmod 700 ~/.config/cxto ~/.config/cxto/keys
```

编辑 `~/.config/cxto/config.yaml`：

```yaml
# 通常无需修改；仅当 Codex 使用非默认目录时才调整。
codex_config_path: "~/.codex/config.toml"
codex_auth_path: "~/.codex/auth.json"

providers:
  channel_a:
    model: "gpt-5.5"
    reasoning_effort: "high"
    base_url: "https://provider-a.example/v1"
    wire_api: "responses"
    requires_openai_auth: true
    auth_token_path: "~/.config/cxto/keys/channel_a.key"

  channel_b:
    model: "gpt-5.5"
    reasoning_effort: "high"
    base_url: "https://provider-b.example/v1"
    wire_api: "responses"
    requires_openai_auth: true
    auth_token_path: "~/.config/cxto/keys/channel_b.key"

  # DeepSeek 官方接入 Codex（原生 Responses API）
  # 官方文档: https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/codex
  deepseek:
    model: "deepseek-v4-flash"
    reasoning_effort: "high"
    base_url: "https://api.deepseek.com/"
    wire_api: "responses"
    preferred_auth_method: "apikey"
    forced_login_method: "api"
    model_catalog_json: "~/.codex/models.json"
    auth_token_path: "~/.config/cxto/keys/deepseek.key"
```

每个 key 文件只有一行对应渠道的 API key：

```bash
printf '%s\n' 'YOUR_API_KEY' > ~/.config/cxto/keys/channel_a.key
chmod 600 ~/.config/cxto/keys/channel_a.key
```

切换到 DeepSeek 渠道后，cxto 会把内置 DeepSeek 模型目录写入 `model_catalog_json` 指定的源文件，并合并进 Codex 使用的联合目录；key 文件中的 token 会写入 config.toml 的 `experimental_bearer_token` 字段。切回普通渠道时会清理 DeepSeek 认证字段，但联合模型目录继续保留两边的模型。注意目前 DeepSeek 仅 `deepseek-v4-flash` 支持接入 Codex（`deepseek-v4-pro` 预计 2026 年 8 月初支持）。

不要将 `keys/`、`auth.json` 或真实的 `config.yaml` 提交到 Git。

## 使用

### 查看已配置渠道

```bash
cxto providers
```

示例输出：

```text
- channel_a  model=gpt-5.5  url=https://provider-a.example/v1
- channel_b  model=gpt-5.5  url=https://provider-b.example/v1
```

### 切换渠道

```bash
cxto use channel_a
```

切换后新启动的 Codex 会使用 `channel_a` 的 Base URL、API key、模型和推理强度：

```bash
codex
```

切到另一个渠道同样只需：

```bash
cxto use channel_b
codex
```

也可以临时覆盖模型或推理强度：

```bash
cxto use channel_a gpt-5.5 --effort xhigh
```

### 多会话与切渠道

从 0.4.0 起，通过已安装启动器的 `codex` 或 `cxto run` 启动的 cxto 渠道会话，会固定启动时的 provider、Base URL、API key、模型和推理强度。交互进程使用独立 app-server，保留 `TMUX_PANE` 等环境，避免读取其他窗口的共享 daemon 默认配置。

例如 A 窗口先执行 `cxto use aa` 再运行 `codex`，B 窗口执行 `cxto use bb` 再运行 `codex`。之后 B/C 窗口继续切渠道，A 进程的进行中请求、重试、后续轮次和同进程重新附着仍使用启动时的 aa 配置，直到该进程退出。

- `cxto use` 更新以后新启动进程的默认渠道、模型和认证；每个渠道仍保留自己的 `[model_providers.cxto_<渠道>]` 块。
- 启动器把渠道配置作为进程级覆盖传给 Codex，API key 通过该进程的专用环境变量固定，不放入新增的命令行参数。渠道切换不会更改其他进程的环境。
- 配置读取和 `cxto use` 写入使用同一把锁；配置与认证文件原子更新，避免多个窗口同时切换时读到半份状态。
- 用户主动传入的模型或配置参数继续生效；管理命令、帮助、版本、远程连接与 OSS 入口透传给原 Codex。
- 保护从升级后经启动器新启动的进程生效。已运行的旧进程不能补写启动参数，可以在当前任务完成后重新进入。
- 进程退出后，再次打开或恢复会话时按当前配置选择渠道。原生 `codex resume` 的 provider 一致性校验仍适用；需要时先切回原渠道，或使用 `cxto resume`。
- API key 仍可能按现有配置写入 `config.toml`；本地 key 文件和认证文件应限制权限。

模型目录在 app-server 启动时读入并缓存。cxto 会生成一个**固定的联合模型目录**，让 Codex 原生目录和各渠道的自定义模型同时可用：

- 默认读取当前 Codex CLI 随程序附带的模型目录，并保留 `~/.codex/model_catalog.json` 中的本机额外条目；也可用 cxto 的 `model_catalog_path` 指定本机补充目录，该目录会与 CLI 内置目录合并，内置元数据优先。DeepSeek 与各渠道的自定义模型元数据合并写入 `~/.codex/cxto_model_catalog.json`。
- 所有渠道都指向这份联合目录。`cxto use` 会更新联合目录和渠道配置，**切换本身不会重启 daemon，也不会等待**；各会话仍使用自己固定的 provider 和凭证。
- 新增渠道时，在配置中加入渠道及其 `model_catalog_json`；新增模型时更新对应的模型目录源文件。`cxto use <渠道>` 会生成最新联合目录；也可以直接运行 `cxto models --refresh` 重建并刷新，无需切换渠道。
- `cxto models` 查看磁盘上的模型列表。运行中的 daemon 仍缓存旧列表时，执行 `cxto models --refresh` 显式刷新。
- `cxto models --refresh` 从已安装 Codex CLI 读取内置模型目录，再合并本机与渠道目录；官方目录随 Codex CLI 版本更新。需要更新官方模型时先运行 `codex update`。
- 显式刷新会调用 `codex app-server daemon restart`，等待进行中的 turn 完成，最多等待 120 秒，超时不会强杀任务。刷新期间可以继续使用已打开的 Codex 窗口；模型列表在 daemon 刷新后更新。
- 如果找不到有效的 Codex 原生目录，cxto 会退回使用当前渠道自己的模型目录；执行 `cxto models --refresh` 时刷新该目录对应的 daemon 缓存。

首次加载完成后，GPT 与 DeepSeek 的模型会同时显示在列表中。切换渠道不再切换全局模型目录。

注意：从 0.2.x 升级上来时，旧会话保存的可能仍是共享名 `cxto_active`，无法据此确定最初使用的渠道。恢复这类会话时，请用 `cxto resume <目标渠道> <会话ID>` 显式选择渠道；退出重进不会改变 rollout 中的 provider 身份。

### 查看当前状态

```bash
cxto status
```

输出当前渠道、Base URL、模型和掩码后的 key，便于确认是否已切换成功。

## 恢复已有会话

切换渠道后，使用单个完整会话 UUID 自动沿用该会话的 provider：

```bash
cxto resume <session-id>
```

此模式只为恢复进程设置 `model_provider`，不改写 `config.toml`、`auth.json` 或 active 状态。比如全局已切到 B，恢复 A 的会话仍使用已有的 A provider 块。原 provider 块缺失或会话使用历史共享名 `cxto_active` 时，会提示显式选择渠道。

当前 Codex 在恢复时会校验 provider 身份。安装启动器后，普通 `codex resume` 会固定本次启动选定的 provider；它不会自动改选历史会话的渠道，全局默认 provider 与会话不同仍可能报 `modelProviderMismatch`。可以先切回对应渠道，或使用上面的 `cxto resume` 入口。

要让会话改用指定渠道，保留原来的双参数用法：

```bash
cxto resume channel_b <session-id> --dry-run
cxto resume channel_b <session-id>
```

双参数模式沿用旧版行为：先激活目标渠道作为全局默认，再为本次恢复进程选择会话原 provider 身份，并将其 provider 配置完整映射到目标渠道。其他独立 provider 块保持原样；不会修改会话 rollout。

恢复进程会通过专用环境变量传入目标 token，覆盖旧 provider 的环境变量认证。若旧 provider 使用环境变量认证，而目标渠道未提供可用凭证，会在切换前提示配置 `auth_token_path` 或 `env_key`。

两种模式都支持 `--dry-run`：只展示命令并隐藏 bearer token，不会修改配置、写入 key 或启动 Codex。隐藏的 token 仅用于预览，预览命令不适合直接复制执行；实际恢复请去掉 `--dry-run`。

## 命令参考

```text
cxto providers
  列出所有渠道。

cxto status
  查看当前正在使用的渠道和配置状态。

cxto use <provider> [model] [--effort low|medium|high|xhigh] [--no-auth]
  即时切换 Codex 使用的渠道、模型和 API key，不刷新 daemon。

cxto install-launcher [--path <Codex入口>] [--dry-run]
  安装一次，让普通 codex 命令为每个进程固定启动时的渠道。

cxto uninstall-launcher [--path <Codex入口>] [--dry-run]
  恢复备份的原 Codex 文件或符号链接。

cxto run [--codex <真实入口>] -- [Codex参数]
  使用当前渠道的进程专属配置启动 Codex；不改变全局默认渠道。

cxto resume [provider] <session-id> [--dry-run]
  省略 provider 时沿用会话原 provider，不改变全局默认渠道；指定时激活目标渠道后恢复。

cxto export-env [--provider <provider>] [--format shell|docker] [--mask]
  输出指定渠道的环境变量，用于容器或其他脚本。

cxto models [filter]
  查看联合模型目录中的模型。

cxto models --refresh
  重建联合目录并显式刷新 Codex daemon 的模型缓存。

cxto init --print
  根据当前配置生成配置模板。
```

## 配置文件说明

| 字段 | 说明 |
| --- | --- |
| `providers.<name>` | 一个可切换的渠道名称，可自定义。 |
| `base_url` | 该渠道的 OpenAI 兼容 API Base URL。 |
| `auth_token_path` | 存放该渠道 API key 的本地文件。 |
| `model` | 切换该渠道时使用的 Codex 模型。 |
| `reasoning_effort` | 可选，推理强度：`low`、`medium`、`high`、`xhigh`。 |
| `wire_api` | 通常填写 `responses`。 |
| `requires_openai_auth` | 通常填写 `true`。 |
| `preferred_auth_method` | 可选（DeepSeek 等非标准渠道用）。设为 `apikey` 时，切换会改用 `experimental_bearer_token` 认证，并把 key 文件中的 token 写入 config.toml。 |
| `forced_login_method` | 可选，配合 `preferred_auth_method` 使用；DeepSeek 场景填 `api`。 |
| `model_catalog_json` | 可选，指向渠道自定义模型目录源文件；可用时 cxto 会将其并入固定联合目录。 |
| `experimental_bearer_token` | 由 cxto 自动注入，无需在 YAML 中手写；密钥统一放 `auth_token_path` 的 key 文件。 |

## 安全与行为

- `cxto use` 会先备份，再更新 `~/.codex/config.toml` 与 `~/.codex/auth.json`。
- 每个渠道保留独立的 `[model_providers.cxto_<渠道>]` 块；启动器进一步固定进程配置，才构成运行期隔离保证。
- `cxto use` 不重启 daemon；只有显式执行 `cxto models --refresh` 才等待任务结束并刷新模型缓存。详见「多会话与切渠道」。
- API key 只从本地 key 文件读取，不会显示在 `status` 输出中。
- DeepSeek 渠道：token 通过 `experimental_bearer_token` 直接写入 config.toml，本地 key 文件权限请保持 `600`；`auth.json` 仍写入占位 `OPENAI_API_KEY`（Codex 客户端校验需要）。
- 切换渠道时按新渠道重写顶层 `preferred_auth_method` / `forced_login_method`，并让所有渠道共享联合 `model_catalog_json`；每个渠道自己块里的 `experimental_bearer_token` 各自保留，互不干扰。
- 安装/撤销启动器不结束或重启现有 Codex 进程；新启动的受保护进程持续使用自己的渠道快照。
- `cxto` 不修改会话 rollout 文件。

## 开发

```bash
python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --no-build-isolation .
```

真实 Codex 隔离回归可通过 `CXTO_TEST_CODEX=/path/to/codex python3 -m unittest discover -s tests -v` 执行。它使用临时 HOME、合成密钥和本机 HTTP 桩，验证 A/B/C 并行运行、进行中请求、重试、后续轮次与并发切换，不调用真实模型渠道。

项目采用 MIT License。提交 Issue、日志或截图前请先移除 API key 和其他敏感信息。
