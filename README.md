# cxto

`cxto` 是一个可移植的 Codex 渠道、模型、密钥和历史会话切换工具。它不依赖任何业务仓库、没有第三方运行时依赖，可单独放到任意电脑或服务器使用。

## 为什么需要它

Codex 会把 `model_provider` 名称写进会话 rollout。若把渠道名直接作为 provider 名，恢复旧会话时可能出现“旧 Base URL + 新 API key”，进而收到 401。

`cxto` 固定让 Codex 使用 `cxto_active` 作为 provider 身份，只切换该身份背后的 URL、模型和密钥。之后创建的会话可以在切换渠道后直接恢复。

## 安装

Python 3.10+，推荐使用 `pipx`：

```bash
pipx install /path/to/cxto
```

开发或未安装时可直接运行：

```bash
PYTHONPATH=/path/to/cxto/src python3 -m cxto.cli --help
```

将来发布到 GitHub 后可改为：

```bash
pipx install git+https://github.com/<owner>/cxto.git
```

## 配置

默认按以下顺序读取，后面的同名渠道覆盖前面的值：

1. `~/.cxto.team.yaml`（兼容旧配置）
2. `~/.cxto.local.yaml`（兼容旧配置）
3. `${XDG_CONFIG_HOME:-~/.config}/cxto/config.yaml`（推荐）

也可以使用 `--config /path/to/config.yaml` 或环境变量 `CXTO_CONFIG` 指定配置。密钥不要提交到仓库；下面的密钥路径仅为本机文件。

```yaml
codex_config_path: "~/.codex/config.toml"
codex_auth_path: "~/.codex/auth.json"
# 可选：非默认位置时指定
# codex_sessions_path: "~/.codex/sessions"

providers:
  channel_a:
    model: "gpt-5.5"
    reasoning_effort: "high"
    base_url: "https://example-a.invalid/v1"
    wire_api: "responses"
    requires_openai_auth: true
    auth_token_path: "~/.config/cxto/keys/channel_a.key"
  channel_b:
    model: "gpt-5.5"
    reasoning_effort: "high"
    base_url: "https://example-b.invalid/v1"
    wire_api: "responses"
    requires_openai_auth: true
    auth_token_path: "~/.config/cxto/keys/channel_b.key"
```

每个 `*.key` 文件只放一行 API key，并限制为当前用户可读。

## 日常使用

```bash
# 查看渠道和当前状态
cxto providers
cxto status

# 切换渠道。此后新建的 Codex 会话会使用稳定的 cxto_active 身份。
cxto use channel_a

# 后续切换渠道后，直接恢复新会话即可。
cxto use channel_b
codex resume <new-session-id>
```

## 兼容旧会话

旧会话保存的是历史渠道 provider 名。请用 `cxto resume` 先检查再恢复：

```bash
cxto resume channel_b <old-session-id> --dry-run
cxto resume channel_b <old-session-id>
```

`--dry-run` 不改配置、不写密钥、不启动 Codex。正式执行时，`cxto` 会先备份并更新 Codex 的配置与认证文件，再仅为本次 `codex resume` 注入旧 provider 的 URL、wire API 和认证标志。它不会修改 rollout 文件。

## 命令

```text
cxto status
cxto providers
cxto use <provider> [model] [--effort low|medium|high|xhigh] [--no-auth]
cxto resume <provider> <session-id> [--dry-run]
cxto export-env [--provider <provider>] [--format shell|docker] [--mask]
cxto models [filter]
cxto init --print
```

## 开发与发布

```bash
python3 -m unittest discover -s tests -v
python3 -m build
```

仓库采用 MIT License。发布前请补充 GitHub 地址、维护者信息和版本号；绝不提交真实渠道地址、API key、`auth.json` 或个人 rollout。
