# cxto

一个用于 **切换 Codex 渠道 Base URL、API key、模型与推理强度** 的命令行工具。

把多个渠道集中配置在一个目录中，然后通过一条命令完成切换：

```bash
cxto use channel_b
```

它会更新 Codex 需要的配置和认证信息；你不必再手动编辑 `config.toml`、复制 API key，或记忆不同渠道的 URL。

## 安装

要求：Python 3.10+、已安装 Codex CLI。

推荐使用 [pipx](https://pipx.pypa.io/) 安装：

```bash
pipx install git+https://github.com/qq542793678/cxto.git
```

确认安装成功：

```bash
cxto --version
```

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
```

每个 key 文件只有一行对应渠道的 API key：

```bash
printf '%s\n' 'YOUR_API_KEY' > ~/.config/cxto/keys/channel_a.key
chmod 600 ~/.config/cxto/keys/channel_a.key
```

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

### 查看当前状态

```bash
cxto status
```

输出当前渠道、Base URL、模型和掩码后的 key，便于确认是否已切换成功。

## 恢复已有会话

切换渠道后，正常恢复由 cxto 管理的会话：

```bash
codex resume <session-id>
```

如果某个历史会话是在安装/使用 cxto 之前创建的，可使用 cxto 直接选择渠道并恢复：

```bash
cxto resume channel_b <session-id> --dry-run
cxto resume channel_b <session-id>
```

建议先执行 `--dry-run`；它只展示将要执行的命令，不会修改配置、写入 key 或启动 Codex。

## 命令参考

```text
cxto providers
  列出所有渠道。

cxto status
  查看当前正在使用的渠道和配置状态。

cxto use <provider> [model] [--effort low|medium|high|xhigh] [--no-auth]
  切换 Codex 使用的渠道、模型和 API key。

cxto resume <provider> <session-id> [--dry-run]
  使用指定渠道恢复已有会话。

cxto export-env [--provider <provider>] [--format shell|docker] [--mask]
  输出指定渠道的环境变量，用于容器或其他脚本。

cxto models [filter]
  查看本机 Codex 已缓存的模型目录。

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

## 安全与行为

- `cxto use` 会先备份，再更新 `~/.codex/config.toml` 与 `~/.codex/auth.json`。
- API key 只从本地 key 文件读取，不会显示在 `status` 输出中。
- 已启动的 Codex 进程不会被强制重启；切换后重新启动或恢复会话即可。
- `cxto` 不修改会话 rollout 文件。

## 开发

```bash
python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --no-build-isolation .
```

项目采用 MIT License。提交 Issue、日志或截图前请先移除 API key 和其他敏感信息。
