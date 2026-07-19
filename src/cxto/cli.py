#!/usr/bin/env python3
"""Portable Codex provider, credential, and session switcher.

The tool intentionally uses only the Python standard library so it can be
installed with pipx and moved between developer machines without a runtime
dependency lock-in.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any


VERSION = "0.1.0"
ACTIVE_PROVIDER = "cxto_active"
TOP_LEVEL_KEYS = {"model_provider", "model", "review_model", "model_reasoning_effort"}
PROVIDER_KEYS = {"name", "base_url", "wire_api", "requires_openai_auth"}


def home() -> Path:
    return Path.home()


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", home() / ".config"))


def default_cxto_configs() -> list[Path]:
    """XDG location is preferred but the original cxto locations keep working."""
    return [
        home() / ".cxto.team.yaml",
        home() / ".cxto.local.yaml",
        xdg_config_home() / "cxto" / "config.yaml",
    ]


def default_codex_config() -> Path:
    return home() / ".codex" / "config.toml"


def default_codex_auth() -> Path:
    return home() / ".codex" / "auth.json"


def default_active_state() -> Path:
    return xdg_config_home() / "cxto" / "active.json"


def strip_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    result: list[str] = []
    for char in line:
        if escaped:
            result.append(char)
            escaped = False
        elif char == "\\":
            result.append(char)
            escaped = True
        elif char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
            result.append(char)
        elif char == "#" and quote is None:
            break
        else:
            result.append(char)
    return "".join(result).rstrip()


def parse_scalar(value: str) -> Any:
    value = strip_comment(value).strip()
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        if value[0] == '"':
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        return value[1:-1]
    return value


def quote_toml(value: Any) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else json.dumps(str(value), ensure_ascii=False)


def expand_path(value: Any | None) -> Path | None:
    return Path(os.path.expandvars(os.path.expanduser(str(value)))) if value else None


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_yaml(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Parse the deliberately small YAML subset used by cxto configuration."""
    top: dict[str, Any] = {}
    providers: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return top, providers
    in_providers = False
    current: str | None = None
    for raw in read_text(path).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = strip_comment(raw)
        root = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if root and not raw.startswith(" "):
            key, value = root.groups()
            in_providers = key == "providers"
            current = None
            if not in_providers:
                top[key] = parse_scalar(value)
            continue
        provider = re.match(r"^\s{2}([A-Za-z0-9_.-]+)\s*:\s*$", line)
        if in_providers and provider:
            current = provider.group(1)
            providers[current] = {"_source": str(path)}
            continue
        field = re.match(r"^\s{4}([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if in_providers and current and field:
            key, value = field.groups()
            providers[current][key] = parse_scalar(value)
    return top, providers


def provider_section(line: str) -> str | None:
    match = re.match(r'^\s*\[model_providers\.("([^"]+)"|([^\]]+))\]\s*$', line)
    return (match.group(2) or match.group(3).strip()) if match else None


def parse_codex_top(path: Path) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if not path.exists():
        return values
    for line in read_text(path).splitlines():
        if line.lstrip().startswith("["):
            break
        match = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.+?)\s*$", line)
        if match and match.group(1) in TOP_LEVEL_KEYS:
            values[match.group(1)] = parse_scalar(match.group(2))
    return values


def parse_codex_providers(path: Path) -> dict[str, dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return providers
    current: str | None = None
    for line in read_text(path).splitlines():
        section = provider_section(line)
        if section is not None:
            current = section
            providers[current] = {"_source": str(path)}
        elif line.lstrip().startswith("["):
            current = None
        elif current:
            match = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.+?)\s*$", line)
            if match and match.group(1) in PROVIDER_KEYS:
                providers[current][match.group(1)] = parse_scalar(match.group(2))
    return providers


def config_paths(args: argparse.Namespace) -> list[Path]:
    if args.config:
        return [Path(args.config).expanduser()]
    if args.profile:
        profile = Path(args.profile)
        return [profile.expanduser()] if profile.suffix or "/" in args.profile else [home() / f".cxto.{args.profile}.yaml"]
    environment_path = os.environ.get("CXTO_CONFIG")
    return [Path(environment_path).expanduser()] if environment_path else default_cxto_configs()


def load_state(args: argparse.Namespace) -> dict[str, Any]:
    top: dict[str, Any] = {}
    providers: dict[str, dict[str, Any]] = {}
    paths = config_paths(args)
    loaded = False
    for path in paths:
        loaded = loaded or path.exists()
        file_top, file_providers = parse_yaml(path)
        top.update(file_top)
        providers.update(file_providers)
    config_path = expand_path(top.get("codex_config_path")) or default_codex_config()
    if not loaded:
        providers = parse_codex_providers(config_path)
    return {"top": top, "providers": providers, "paths": paths, "config": config_path}


def auth_path(state: dict[str, Any]) -> Path:
    return expand_path(state["top"].get("codex_auth_path")) or default_codex_auth()


def sessions_path(state: dict[str, Any]) -> Path:
    return expand_path(state["top"].get("codex_sessions_path")) or state["config"].parent / "sessions"


def active_path(state: dict[str, Any]) -> Path:
    return expand_path(state["top"].get("cxto_active_state_path")) or default_active_state()


def active_provider(state: dict[str, Any]) -> str | None:
    path = active_path(state)
    try:
        value = json.loads(read_text(path)).get("provider") if path.exists() else None
    except json.JSONDecodeError:
        value = None
    return str(value) if value else None


def write_active_provider(state: dict[str, Any], provider: str) -> Path:
    path = active_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps({"provider": provider, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def resolved_current_provider(state: dict[str, Any]) -> str | None:
    configured = parse_codex_top(state["config"]).get("model_provider")
    if configured != ACTIVE_PROVIDER:
        return str(configured) if configured else None
    provider = active_provider(state)
    return provider if provider in state["providers"] else None


def backup(path: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{counter}")
        counter += 1
    shutil.copy2(path, candidate)
    return candidate


def replace_top(lines: list[str], key: str, value: Any) -> None:
    replacement = f"{key} = {quote_toml(value)}\n"
    first_section = next((index for index, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    for index in range(first_section):
        if re.match(rf"^\s*{re.escape(key)}\s*=", lines[index]):
            lines[index] = replacement
            return
    lines.insert(first_section, replacement)


def provider_block(lines: list[str], provider: str) -> tuple[int, int] | None:
    start: int | None = None
    for index, line in enumerate(lines):
        if provider_section(line) == provider:
            start = index
        elif start is not None and line.lstrip().startswith("["):
            return start, index
    return (start, len(lines)) if start is not None else None


def replace_provider(lines: list[str], name: str, provider: dict[str, Any]) -> None:
    values = {key: provider[key] for key in PROVIDER_KEYS if key in provider}
    values["name"] = name
    block = provider_block(lines, name)
    order = ("name", "base_url", "wire_api", "requires_openai_auth")
    if block is None:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.append(f"[model_providers.{name}]\n")
        lines.extend(f"{key} = {quote_toml(values[key])}\n" for key in order if key in values)
        return
    _, end = block
    seen: set[str] = set()
    for index in range(block[0] + 1, end):
        match = re.match(r"^\s*([A-Za-z0-9_]+)\s*=", lines[index])
        if match and match.group(1) in values:
            key = match.group(1)
            lines[index] = f"{key} = {quote_toml(values[key])}\n"
            seen.add(key)
    for key in order:
        if key in values and key not in seen:
            lines.insert(end, f"{key} = {quote_toml(values[key])}\n")
            end += 1


def write_codex_config(state: dict[str, Any], provider: dict[str, Any], model: str, effort: str | None) -> Path:
    path: Path = state["config"]
    if not path.exists():
        raise SystemExit(f"Codex配置不存在: {path}")
    lines = read_text(path).splitlines(keepends=True)
    replace_top(lines, "model_provider", ACTIVE_PROVIDER)
    replace_top(lines, "model", model)
    if effort:
        replace_top(lines, "model_reasoning_effort", effort)
    replace_provider(lines, ACTIVE_PROVIDER, provider)
    backup_path = backup(path)
    path.write_text("".join(lines), encoding="utf-8")
    return backup_path


def token_for(provider: dict[str, Any], auth: Path) -> str | None:
    token_path = expand_path(provider.get("auth_token_path"))
    if token_path:
        return read_text(token_path).strip() if token_path.exists() else None
    json_path = expand_path(provider.get("auth_json_path")) or auth
    if not json_path.exists():
        return None
    try:
        return json.loads(read_text(json_path)).get("OPENAI_API_KEY")
    except json.JSONDecodeError:
        return None


def write_auth(auth: Path, token: str) -> Path | None:
    auth.parent.mkdir(parents=True, exist_ok=True)
    backup_path = backup(auth) if auth.exists() else None
    try:
        payload = json.loads(read_text(auth)) if auth.exists() else {}
    except json.JSONDecodeError:
        payload = {}
    payload["OPENAI_API_KEY"] = token
    auth.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return backup_path


def provider_or_exit(state: dict[str, Any], name: str) -> dict[str, Any]:
    provider = state["providers"].get(name)
    if not provider:
        raise SystemExit(f"未知provider: {name}\n可用provider: {', '.join(sorted(state['providers'])) or '无'}")
    return provider


def activate(state: dict[str, Any], name: str, model_override: str | None = None, effort_override: str | None = None, write_key: bool = True) -> dict[str, Any]:
    provider = provider_or_exit(state, name)
    current = parse_codex_top(state["config"])
    model = str(model_override or provider.get("model") or current.get("model") or "gpt-5.5")
    effort = effort_override or provider.get("reasoning_effort") or current.get("model_reasoning_effort")
    config_backup = write_codex_config(state, provider, model, str(effort) if effort else None)
    auth = auth_path(state)
    key = token_for(provider, auth)
    auth_backup = write_auth(auth, str(key)) if key and write_key else None
    return {"provider": provider, "model": model, "effort": effort, "config_backup": config_backup, "auth_backup": auth_backup, "state": write_active_provider(state, name)}


def session_provider(state: dict[str, Any], session_id: str) -> str:
    candidates = sorted(sessions_path(state).rglob(f"*{session_id}*.jsonl"))
    for path in candidates:
        try:
            for line in read_text(path).splitlines():
                record = json.loads(line)
                payload = record.get("payload", {})
                if record.get("type") == "session_meta" and str(payload.get("session_id") or payload.get("id") or "") == session_id and payload.get("model_provider"):
                    return str(payload["model_provider"])
        except (OSError, json.JSONDecodeError):
            continue
    raise SystemExit(f"未找到会话 {session_id} 的 session_meta 或 model_provider")


def config_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_]+", name) else quote_toml(name)


def resume_arguments(old_provider: str, target: dict[str, Any], session_id: str) -> list[str]:
    base_url = str(target.get("base_url", "")).strip()
    if not base_url:
        raise SystemExit("目标provider缺少base_url")
    prefix = f"model_providers.{config_key(old_provider)}"
    command = ["codex", "resume", "-c", f"{prefix}.base_url={quote_toml(base_url)}"]
    for key in ("wire_api", "requires_openai_auth"):
        if key in target:
            command.extend(["-c", f"{prefix}.{key}={quote_toml(target[key])}"])
    return command + [session_id]


def masked(value: Any) -> str:
    text = str(value or "").strip()
    return "-" if not text else "*" * len(text) if len(text) <= 8 else text[:4] + "*" * max(4, len(text) - 8) + text[-4:]


def command_status(args: argparse.Namespace) -> None:
    state = load_state(args)
    name = resolved_current_provider(state)
    provider = state["providers"].get(name, {})
    current = parse_codex_top(state["config"])
    print(f"当前渠道: {name or '-'}")
    if current.get("model_provider") == ACTIVE_PROVIDER:
        print(f"Codex会话Provider身份: {ACTIVE_PROVIDER}")
    print(f"Base URL: {provider.get('base_url', '-')}")
    print(f"模型: {current.get('model', provider.get('model', '-'))}")
    print(f"Token: {masked(token_for(provider, auth_path(state)))}")


def command_providers(args: argparse.Namespace) -> None:
    state = load_state(args)
    current = resolved_current_provider(state)
    for name in sorted(state["providers"]):
        provider = state["providers"][name]
        print(f"{'*' if name == current else '-'} {name}\tmodel={provider.get('model', '-')}\turl={provider.get('base_url', '-')}")


def command_use(args: argparse.Namespace) -> None:
    state = load_state(args)
    result = activate(state, args.provider, args.model, args.effort, not args.no_auth)
    print(f"已切换到渠道: {args.provider}")
    print(f"Codex会话Provider身份: {ACTIVE_PROVIDER}")
    print(f"配置备份: {result['config_backup']}")
    if result["auth_backup"]:
        print(f"认证备份: {result['auth_backup']}")


def command_resume(args: argparse.Namespace) -> None:
    state = load_state(args)
    target = provider_or_exit(state, args.provider)
    old = session_provider(state, args.session_id)
    command = resume_arguments(old, target, args.session_id)
    if args.dry_run:
        print(f"旧会话Provider身份: {old}")
        print(f"目标渠道: {args.provider}")
        print("dry-run：不会修改配置、不会写入密钥、不会启动Codex。")
        print(shlex.join(command))
        return
    activate(state, args.provider)
    print(f"旧会话Provider身份: {old}；正在恢复到渠道: {args.provider}", flush=True)
    os.execvp(command[0], command)


def command_export_env(args: argparse.Namespace) -> None:
    state = load_state(args)
    name = args.provider or resolved_current_provider(state)
    if not name:
        raise SystemExit("请指定有效provider")
    provider = provider_or_exit(state, name)
    current = parse_codex_top(state["config"])
    values = {
        "OPENAI_BASE_URL": str(provider.get("base_url", "")),
        "OPENAI_API_KEY": token_for(provider, auth_path(state)) or "",
        "OPENAI_MODEL": str(provider.get("model") or current.get("model") or "gpt-5.5"),
        "CODEX_MODEL_PROVIDER": ACTIVE_PROVIDER,
    }
    if args.format == "shell":
        for key, value in values.items():
            shown = masked(value) if args.mask and key == "OPENAI_API_KEY" else value
            print(f"export {key}={json.dumps(shown, ensure_ascii=False)}")
    else:
        print(" ".join(f"-e {key}={masked(value) if args.mask and key == 'OPENAI_API_KEY' else value}" for key, value in values.items()))


def command_models(args: argparse.Namespace) -> None:
    state = load_state(args)
    catalog = expand_path(state["top"].get("model_catalog_path")) or home() / ".codex" / "model_catalog.json"
    if not catalog.exists():
        raise SystemExit(f"模型目录不存在: {catalog}")
    needle = (args.filter or "").lower()
    for item in json.loads(read_text(catalog)).get("models", []):
        slug = str(item.get("slug", ""))
        if not needle or needle in slug.lower() or needle in str(item.get("display_name", "")).lower():
            print(f"{slug}\tdefault_effort={item.get('default_reasoning_level', '-')}")


def command_init(args: argparse.Namespace) -> None:
    """Generate a portable XDG config template from the currently known providers."""
    state = load_state(args)
    lines = [
        "# cxto configuration. Keep real keys in local key files, never in this YAML.",
        'codex_config_path: "~/.codex/config.toml"',
        'codex_auth_path: "~/.codex/auth.json"',
        "",
        "providers:",
    ]
    for name in sorted(state["providers"]):
        provider = state["providers"][name]
        lines.append(f"  {name}:")
        for key in ("model", "reasoning_effort", "base_url", "wire_api", "requires_openai_auth"):
            if key in provider:
                value = "true" if provider[key] is True else "false" if provider[key] is False else json.dumps(str(provider[key]), ensure_ascii=False)
                lines.append(f"    {key}: {value}")
        lines.append(f'    # auth_token_path: "~/.config/cxto/keys/{name}.key"')
    rendered = "\n".join(lines) + "\n"
    target = Path(args.output).expanduser() if args.output else xdg_config_home() / "cxto" / "config.yaml"
    if args.print:
        print(rendered, end="")
        return
    if target.exists() and not args.force:
        raise SystemExit(f"配置已存在: {target}；如需覆盖请加 --force")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    print(f"已生成配置: {target}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cxto", description="可移植的 Codex 渠道与会话切换工具")
    parser.add_argument("--config", help="指定 YAML 配置；也可通过 CXTO_CONFIG 指定")
    parser.add_argument("-p", "--profile", help="读取 ~/.cxto.<profile>.yaml")
    parser.add_argument("--version", action="version", version=f"cxto {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="查看当前渠道").set_defaults(func=command_status)
    commands.add_parser("providers", help="列出可用渠道").set_defaults(func=command_providers)
    use = commands.add_parser("use", help="切换渠道；新会话可安全恢复并再次切换")
    use.add_argument("provider")
    use.add_argument("model", nargs="?")
    use.add_argument("--effort", choices=["low", "medium", "high", "xhigh"])
    use.add_argument("--no-auth", action="store_true", help="不写 ~/.codex/auth.json")
    use.set_defaults(func=command_use)
    resume = commands.add_parser("resume", help="切换渠道并兼容恢复旧会话")
    resume.add_argument("provider")
    resume.add_argument("session_id")
    resume.add_argument("--dry-run", action="store_true")
    resume.set_defaults(func=command_resume)
    export = commands.add_parser("export-env", help="输出当前渠道的环境变量")
    export.add_argument("--provider")
    export.add_argument("--format", choices=["shell", "docker"], default="shell")
    export.add_argument("--mask", action="store_true")
    export.set_defaults(func=command_export_env)
    models = commands.add_parser("models", help="列出 Codex 本地模型目录")
    models.add_argument("filter", nargs="?")
    models.set_defaults(func=command_models)
    init = commands.add_parser("init", help="根据当前渠道生成可移植配置模板")
    init.add_argument("--print", action="store_true", help="只打印，不写文件")
    init.add_argument("-o", "--output", help="输出配置路径")
    init.add_argument("--force", action="store_true", help="覆盖已有配置")
    init.set_defaults(func=command_init)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
