# cxto：Codex 渠道与会话切换工具

`cxto` 用来管理 **Codex CLI 实际请求哪个模型渠道、使用哪个 API key，以及恢复旧会话时如何避免串到旧渠道**。

它是独立的 Python 命令行工具，不依赖任何业务项目，可安装到其他服务器或电脑。仓库：<https://github.com/qq542793678/cxto>。

## 它解决什么问题

Codex 的一个会话会在 rollout 中保存 `model_provider` 名称。若直接把渠道名当作 provider 名，可能出现下面的情况：

1. 会话最初在渠道 A 创建，保存了 `model_provider = "channel_a"`。
2. 你执行 `cxto use channel_b`，API key 已经变成 B 的 key。
3. 再执行普通 `codex resume <旧会话ID>` 时，Codex 仍按 `channel_a` 找 Base URL。
4. B 的 key 被发到 A，收到 `401 Unauthorized`。

`cxto` 的方案是：从第一次使用新版 `cxto use` 起，让所有**新会话**都使用固定的逻辑身份 `cxto_active`；切换时只更新该身份背后的 Base URL、模型、认证方式和 key。这样同一会话无论恢复多少次，都能使用你当前选中的渠道。

## 它不会做什么

- 不会修改或迁移你的 rollout 会话文件。
- 不会自动切换正在运行的 Codex 进程；切换影响后续启动/恢复的 Codex。
- `cxto use` 和正式的 `cxto resume` 会修改本机 Codex 配置与认证文件，但会先创建备份。
- `cxto resume --dry-run` **不写任何配置、不写 key、不启动 Codex**。

## 安装

要求：Python 3.10+、已安装 Codex CLI。推荐使用 `pipx`，不会污染系统 Python：

```bash
pipx install git+https://github.com/qq542793678/cxto.git
```

本地开发版本可安装为：

```bash
pipx install /path/to/cxto
```

安装后确认：

```bash
cxto --version
```

## 首次配置

推荐配置路径：`${XDG_CONFIG_HOME:-~/.config}/cxto/config.yaml`。可以从示例开始：

```bash
mkdir -p ~/.config/cxto/keys
cp /path/to/cxto/examples/config.example.yaml ~/.config/cxto/config.yaml
chmod 700 ~/.config/cxto ~/.config/cxto/keys
```

编辑 `~/.config/cxto/config.yaml`，为每个渠道配置 Base URL、模型和对应 key 文件：

```yaml
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

每个 `.key` 文件只写一行 key，例如：

```bash
printf '%s\n' '你的真实key' > ~/.config/cxto/keys/channel_a.key
chmod 600 ~/.config/cxto/keys/channel_a.key
```

不要将真实 key、`~/.codex/auth.json`、个人 rollout 或真实渠道配置提交到 Git。

## 日常使用

先查看本机能切换哪些渠道：

```bash
cxto providers
cxto status
```

切换到一个渠道：

```bash
cxto use channel_a
```

这会：

1. 备份并更新 `~/.codex/config.toml`，把实际渠道写到 `cxto_active` provider。
2. 备份并更新 `~/.codex/auth.json` 中的 `OPENAI_API_KEY`（配置了 `auth_token_path` 时）。
3. 在 `~/.config/cxto/active.json` 记录当前渠道名；这里不保存 key。

随后直接新建 Codex 会话：

```bash
codex
```

以后切换到渠道 B，再恢复**由新版 cxto 创建的新会话**：

```bash
cxto use channel_b
codex resume <new-session-id>
```

这就是正常、可随时切换的路径。

## 恢复旧会话

在 `cxto_active` 机制启用前创建的会话保存了历史渠道名，不能只用普通 `codex resume`。请改用：

```bash
cxto resume channel_b <old-session-id> --dry-run
```

先确认输出中：

- `旧会话Provider身份` 是你预期的旧渠道名；
- 命令中的 `base_url` 是目标渠道 `channel_b` 的 URL；
- 输出包含“不会修改配置、不会写入密钥、不会启动Codex”。

确认后正式恢复：

```bash
cxto resume channel_b <old-session-id>
```

正式命令会切到 `channel_b`，然后只为这次 `codex resume` 临时覆盖旧 provider 的 URL、wire API 和认证标志。rollout 文件始终只读。

## 命令速查

```text
cxto status
  查看当前渠道、实际 URL、模型和掩码后的 token。

cxto providers
  列出配置中所有渠道。

cxto use <provider> [model] [--effort low|medium|high|xhigh] [--no-auth]
  切换渠道；之后创建的新会话支持普通 codex resume 切换。

cxto resume <provider> <session-id> [--dry-run]
  切换渠道并恢复旧会话；旧会话优先先 dry-run。

cxto export-env [--provider <provider>] [--format shell|docker] [--mask]
  打印环境变量，供容器或其他工具使用；不会改变当前 shell 的环境变量。

cxto models [filter]
  查看本机 Codex 已缓存的模型目录。

cxto init --print
  根据当前发现的渠道打印可移植配置模板。
```

## 常见问题

### 切换后仍然是旧 Base URL，或收到 401

大概率恢复的是旧会话。不要手改 rollout，运行：

```bash
cxto resume <目标渠道> <旧会话ID> --dry-run
```

确认无误后去掉 `--dry-run`。

### `cxto use` 后 tmux 中已打开的 Codex 没有变

正常。已启动进程不会被工具强制重载。退出该 Codex 后重新启动或恢复会话即可。

### 如何在新服务器迁移

1. 安装 `cxto`。
2. 复制不含密钥的 `config.yaml`。
3. 通过安全渠道单独放置 key 文件并设置 `chmod 600`。
4. 运行 `cxto providers` 和 `cxto status`。
5. 先用 `cxto resume ... --dry-run` 验证旧会话；新会话按 `cxto use` + `codex resume` 使用。

## 开发与发布

```bash
python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --no-build-isolation .
```

项目使用 MIT License。欢迎提交 Issue、PR 和渠道配置兼容建议；任何 Issue、日志和截图都必须先脱敏。
