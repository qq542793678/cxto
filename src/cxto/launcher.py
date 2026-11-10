"""Bind each Codex process to its launch-time channel and install a reversible entry."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
import tempfile


MARKER = b"# cxto managed Codex launcher v1"
SESSION_TOKEN_ENV = "CXTO_SESSION_API_KEY"
VALUE_OPTIONS = {"-c", "--config", "-m", "--model", "-p", "--profile", "-C", "--cd",
                 "-a", "--ask-for-approval", "-s", "--sandbox", "-i", "--image",
                 "--remote", "--remote-auth-token-env", "--local-provider", "--add-dir",
                 "--enable", "--disable", "--listen"}
COMMANDS = {"agents", "exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server",
            "remote-control", "completion", "update", "doctor", "sandbox", "debug", "apply", "a",
            "resume", "queue", "archive", "delete", "migrate-rollouts", "migrate-nfs-db", "unarchive",
            "fork", "cloud", "exec-server", "features", "help"}
SESSION_COMMANDS = {None, "exec", "e", "review", "resume", "fork", "app-server"}


def _cli():
    if __package__:
        from . import cli
    else:
        import cli
    return cli


@contextlib.contextmanager
def file_lock(path: Path):
    # flock releases on process exit, including a failed activation.
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def channel_lock(config: Path):
    return file_lock(config.with_name(".cxto-channel.lock"))


def atomic_write(path: Path, text: str, mode: int | None = None):
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    permissions = mode if mode is not None else stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.cxto-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), permissions)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def inspect_arguments(arguments: list[str]):
    operation = None
    saw_positional = False
    overrides = {}
    profile = None
    passthrough = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            break
        option, equal, attached = argument.partition("=")
        if option in {"--remote", "--oss", "--local-provider", "--help", "-h", "--version", "-V"}:
            passthrough = True
        if option in VALUE_OPTIONS:
            value = attached if equal else arguments[index + 1] if index + 1 < len(arguments) else ""
            if option in {"-c", "--config"} and "=" in value:
                key, _, raw = value.partition("=")
                overrides[key.strip()] = raw
            if option in {"-p", "--profile"}:
                profile = value
            index += 1 if equal else 2
            continue
        if argument.startswith("-c") and not argument.startswith("--") and len(argument) > 2 and "=" in argument[2:]:
            key, _, raw = argument[2:].partition("=")
            overrides[key.strip()] = raw
        elif not argument.startswith("-") and not saw_positional:
            saw_positional = True
            operation = argument if argument in COMMANDS else None
        index += 1
    if operation not in SESSION_COMMANDS:
        passthrough = True
    if operation == "app-server" and any(value in arguments for value in ("daemon", "proxy", "generate-ts", "generate-json-schema", "--print-startup-config")):
        passthrough = True
    return operation, overrides, profile, passthrough


def _metadata_path(entry: Path) -> Path:
    return entry.with_name(entry.name + ".cxto-launcher.json")


def _is_wrapper(entry: Path) -> bool:
    try:
        with entry.open("rb") as stream:
            return MARKER in stream.read(2048)
    except OSError:
        return False


def real_command(command: str | None) -> str:
    found = command or shutil.which("codex")
    if not found:
        raise SystemExit("找不到 Codex CLI；请先安装 Codex 或通过 --codex 指定真实入口")
    entry = Path(found).expanduser().absolute()
    if _is_wrapper(entry):
        try:
            metadata = json.loads(_metadata_path(entry).read_text(encoding="utf-8"))
            entry = Path(metadata["original"])
        except (OSError, ValueError, KeyError):
            raise SystemExit("cxto 启动器缺少原 Codex 入口记录，请检查安装状态") from None
    if not entry.is_file() or not os.access(entry, os.X_OK) or _is_wrapper(entry):
        raise SystemExit(f"Codex 原入口不可执行: {entry}")
    return str(entry)


def launch_configuration(state: dict, arguments: list[str], executable: str):
    cli = _cli()
    operation, overrides, profile, passthrough = inspect_arguments(arguments)
    if passthrough:
        return [], {}
    with channel_lock(state["config"]):
        top = cli.parse_codex_top(state["config"])
        providers = cli.parse_codex_providers(state["config"])
        if profile:
            profile_path = state["config"].parent / f"{profile}.config.toml"
            if profile_path.exists():
                top.update(cli.parse_codex_top(profile_path))
                for name, values in cli.parse_codex_providers(profile_path).items():
                    providers.setdefault(name, {}).update(values)
        selected = str(cli.parse_scalar(overrides["model_provider"]) if "model_provider" in overrides else top.get("model_provider", ""))
        if not selected.startswith(cli.CODEX_PROVIDER_PREFIX):
            return [], {}
        provider = providers.get(selected)
        explicit_block = any(key == "model_providers" or key.startswith("model_providers.") for key in overrides)
        if provider is None and not explicit_block:
            raise SystemExit(f"渠道 provider 配置缺失: {selected}；请先执行 cxto use <渠道>")
        pins = []
        for key in ("model_provider", "model", "review_model", "model_reasoning_effort", "model_catalog_json"):
            value = selected if key == "model_provider" else top.get(key)
            if value is not None:
                pins.extend(["-c", f"{key}={cli.quote_toml(value)}"])
        if "model_reasoning_effort" not in top:
            catalog_path = cli.expand_path(top.get("model_catalog_json"))
            catalog = cli.read_model_catalog(catalog_path) if catalog_path else cli.codex_bundled_model_catalog(executable)
            model = str(top.get("model", ""))
            effort = next((item.get("default_reasoning_level") for item in (catalog or {}).get("models", []) if item.get("slug") == model), None) or "medium"
            pins.extend(["-c", f"model_reasoning_effort={cli.quote_toml(effort)}"])
        environment = {}
        if provider is not None:
            provider = dict(provider)
            for key in cli.PROVIDER_KEYS | cli.EXTRA_PROVIDER_KEYS:
                override = f"model_providers.{selected}.{key}"
                if override in overrides:
                    provider[key] = cli.parse_scalar(overrides[override])
            token = provider.get("experimental_bearer_token")
            if provider.get("env_key"):
                token = os.environ.get(str(provider["env_key"]))
                if not token and not explicit_block:
                    raise SystemExit(f"当前渠道所需环境变量未设置: {provider['env_key']}")
            if not token and provider.get("requires_openai_auth") and not explicit_block:
                token = cli.token_for(provider, cli.auth_path(state))
                if not token:
                    raise SystemExit("当前渠道缺少可绑定的 API key，请配置独立 auth_token_path 或 env_key")
            values = {key: provider[key] for key in cli.PROVIDER_KEYS | cli.EXTRA_PROVIDER_KEYS if key in provider}
            values["name"] = selected
            if token:
                environment[SESSION_TOKEN_ENV] = str(token)
                values.update(env_key=SESSION_TOKEN_ENV, experimental_bearer_token="", requires_openai_auth=False)
            block = "{ " + ", ".join(f"{key} = {cli.quote_toml(value)}" for key, value in sorted(values.items())) + " }"
            key = f"model_providers.{selected}" if re.fullmatch(r"[A-Za-z0-9_]+", selected) else "model_providers"
            value = block if key != "model_providers" else "{ " + cli.quote_toml(selected) + " = " + block + " }"
            pins.extend(["-c", f"{key}={value}", "-c", 'forced_login_method="api"'])
        if operation != "app-server" and "--no-daemon" not in arguments:
            pins.insert(0, "--no-daemon")
        return pins, environment


def command_run(args):
    cli = _cli()
    arguments = args.codex_arguments
    if arguments and arguments[0] == "--":
        arguments = arguments[1:]
    executable = real_command(args.codex)
    if inspect_arguments(arguments)[3]:
        os.execve(executable, [executable, *arguments], os.environ.copy())
    state = cli.load_state(args)
    if os.environ.get("CODEX_HOME"):
        codex_home = Path(os.environ["CODEX_HOME"]).expanduser()
        state["config"] = codex_home / "config.toml"
        state["top"]["codex_auth_path"] = str(codex_home / "auth.json")
    pins, environment = launch_configuration(state, arguments, executable)
    os.execve(executable, [executable, *pins, *arguments], os.environ | environment)


def _installation(entry: Path):
    metadata_path = _metadata_path(entry)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit(f"启动器安装记录不可读: {metadata_path}") from None
    expected_backup = entry.with_name(entry.name + ".cxto-original")
    if metadata.get("original") != str(expected_backup) or not expected_backup.exists():
        raise SystemExit("启动器原入口备份缺失或记录不匹配，请手动检查")
    if hashlib.sha256(entry.read_bytes()).hexdigest() != metadata.get("wrapper_sha256"):
        raise SystemExit("启动器文件已有其他修改，保留现有文件；请先检查改动")
    return metadata


def command_install(args):
    cli = _cli()
    entry = Path(args.path).expanduser().absolute() if args.path else Path(shutil.which("codex") or "")
    if not entry.is_file():
        raise SystemExit("Codex 入口不存在；请用 --path 指定已安装的 codex 可执行文件或符号链接")
    if _is_wrapper(entry):
        _installation(entry)
        print(f"cxto 启动器已安装: {entry}")
        return
    backup = entry.with_name(entry.name + ".cxto-original")
    if backup.exists() or backup.is_symlink() or _metadata_path(entry).exists():
        raise SystemExit(f"备份或安装记录已存在，未覆盖: {backup}")
    if args.dry_run:
        print(f"dry-run：将备份 {entry} 到 {backup} 并安装进程隔离启动器；未修改文件")
        return
    wrapper = "#!/bin/sh\n" + MARKER.decode() + "\nexec " + shlex.join([
        sys.executable, str(Path(cli.__file__).resolve()), "run", "--codex", str(backup), "--",
    ]) + ' "$@"\n'
    metadata = {"original": str(backup), "wrapper_sha256": hashlib.sha256(wrapper.encode()).hexdigest()}
    with file_lock(entry.with_name(entry.name + ".cxto-install.lock")):
        if backup.exists() or backup.is_symlink():
            raise SystemExit(f"备份已存在，未覆盖: {backup}")
        entry.rename(backup)
        try:
            atomic_write(_metadata_path(entry), json.dumps(metadata, indent=2) + "\n", 0o600)
            atomic_write(entry, wrapper, 0o755)
        except BaseException:
            backup.replace(entry)
            _metadata_path(entry).unlink(missing_ok=True)
            raise
    print(f"已安装 cxto 进程隔离启动器: {entry}\n原入口备份: {backup}")


def command_uninstall(args):
    entry = Path(args.path).expanduser().absolute() if args.path else Path(shutil.which("codex") or "")
    if not _is_wrapper(entry):
        print("当前入口不是 cxto 启动器，无需恢复")
        return
    _installation(entry)
    if args.dry_run:
        print(f"dry-run：将恢复 {entry} 的原 Codex 入口；未修改文件")
        return
    with file_lock(entry.with_name(entry.name + ".cxto-install.lock")):
        metadata = _installation(entry)
        Path(metadata["original"]).replace(entry)
        _metadata_path(entry).unlink()
    print(f"已恢复原 Codex 入口: {entry}")
