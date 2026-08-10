# Changelog

本项目的所有重要变更都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

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
