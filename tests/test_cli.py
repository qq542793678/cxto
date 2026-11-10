from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src" / "cxto" / "cli.py"
SESSION_ID = "019f69e0-a1ca-7f53-8c0f-d60c2d796f8f"


class CxtoCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        (self.home / ".codex").mkdir(parents=True)
        (self.home / ".cxto" / "keys").mkdir(parents=True)
        (self.home / ".codex" / "config.toml").write_text(
            'model_provider = "legacy"\nmodel = "gpt-old"\n\n[model_providers.legacy]\nname = "legacy"\nbase_url = "https://old.invalid/v1"\nwire_api = "responses"\nrequires_openai_auth = true\n',
            encoding="utf-8",
        )
        (self.home / ".codex" / "model_catalog.json").write_text(
            json.dumps({"models": [{"slug": "gpt-5.5", "display_name": "GPT-5.5"}]}),
            encoding="utf-8",
        )
        (self.home / ".codex" / "gamma-models.json").write_text(
            json.dumps({"models": [{"slug": "gamma-one", "display_name": "Gamma One"}]}),
            encoding="utf-8",
        )
        (self.home / ".codex" / "auth.json").write_text('{"OPENAI_API_KEY":"old"}\n', encoding="utf-8")
        (self.home / ".cxto.local.yaml").write_text(
            'providers:\n  alpha:\n    model: "gpt-alpha"\n    reasoning_effort: "high"\n    base_url: "https://alpha.invalid/v1"\n    wire_api: "responses"\n    requires_openai_auth: true\n    auth_token_path: "~/.cxto/keys/alpha.key"\n  beta:\n    model: "gpt-beta"\n    reasoning_effort: "xhigh"\n    base_url: "https://beta.invalid/v1"\n    wire_api: "responses"\n    requires_openai_auth: true\n    auth_token_path: "~/.cxto/keys/beta.key"\n  deepseek:\n    model: "deepseek-v4-flash"\n    reasoning_effort: "high"\n    base_url: "https://api.deepseek.com/"\n    wire_api: "responses"\n    preferred_auth_method: "apikey"\n    forced_login_method: "api"\n    model_catalog_json: "~/.codex/models.json"\n    auth_token_path: "~/.cxto/keys/deepseek.key"\n',
            encoding="utf-8",
        )
        (self.home / ".cxto.team.yaml").write_text(
            'providers:\n  gamma:\n    model: "gamma-one"\n    base_url: "https://gamma.invalid/v1"\n    wire_api: "responses"\n    requires_openai_auth: true\n    model_catalog_json: "~/.codex/gamma-models.json"\n',
            encoding="utf-8",
        )
        (self.home / ".cxto" / "keys" / "alpha.key").write_text("alpha-key\n", encoding="utf-8")
        (self.home / ".cxto" / "keys" / "beta.key").write_text("beta-key\n", encoding="utf-8")
        (self.home / ".cxto" / "keys" / "deepseek.key").write_text("sk-deepseek-test\n", encoding="utf-8")
        self.env = os.environ | {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")}
        self.env.pop("CXTO_CONFIG", None)
        self.env.pop("CXTO_RESUME_API_KEY", None)
        # Never depend on, or invoke, the developer machine's real Codex in CLI tests.
        self.make_fake_codex()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, encoding="utf-8", env=env or self.env, check=False)

    def test_source_checkout_entrypoint_uses_standalone_project(self) -> None:
        entry = ROOT / "bin" / "cxto"
        result = subprocess.run([str(entry), "--version"], capture_output=True, text=True, env=self.env, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cxto 0.4.0", result.stdout)

        invalid = subprocess.run([str(entry), "status", "--unknown-option"], capture_output=True, text=True, env=self.env, check=False)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("unrecognized arguments", invalid.stderr)

    def test_root_help_documents_all_commands_and_common_options(self) -> None:
        result = self.run_cli("-h")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("providers", "status", "use", "resume", "models", "export-env", "init"):
            self.assertIn(f"cxto {command}", result.stdout)
        for option in ("--config", "--profile", "--effort", "--no-auth", "--no-restart", "--dry-run", "--format", "--mask", "--force", "--refresh"):
            self.assertIn(option, result.stdout)
        self.assertIn("120 秒", result.stdout)

    def make_rollout(self, provider: str = "legacy") -> Path:
        path = self.home / ".codex" / "sessions" / "2026" / "07" / "19"
        path.mkdir(parents=True, exist_ok=True)
        rollout = path / f"rollout-{SESSION_ID}.jsonl"
        rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": SESSION_ID, "model_provider": provider}}) + "\n", encoding="utf-8")
        return rollout

    def state_files(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.home)): path.read_bytes()
            for root in (self.home / ".codex", self.home / ".config")
            for path in root.rglob("*") if path.is_file()
        }

    def capture_resume(self, exit_code: int = 0) -> tuple[Path, dict[str, str]]:
        capture = self.home / "resume-arguments.json"
        fake = self.home / "bin" / "codex"
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "if sys.argv[1:] == ['debug', 'models', '--bundled']:\n"
            "    print(os.environ.get('BUNDLED_MODELS', '{}'))\n"
            "else:\n"
            "    pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
            "    pathlib.Path(os.environ['CAPTURE'] + '.token').write_text(os.environ.get('CXTO_RESUME_API_KEY', ''))\n"
            f"    sys.exit({exit_code})\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        return capture, self.env | {"CAPTURE": str(capture)}

    def provider_block(self, config: str, name: str) -> str:
        """取 config.toml 里某个 [model_providers.<name>] 块的内容（到下一个表头为止）。"""
        marker = f"[model_providers.{name}]"
        if marker not in config:
            return ""
        return config.split(marker, 1)[1].split("\n[", 1)[0]

    def test_use_pins_a_dedicated_provider_block(self) -> None:
        result = self.run_cli("use", "beta")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model_provider = "cxto_beta"', config)
        self.assertIn("[model_providers.cxto_beta]", config)
        self.assertIn('base_url = "https://beta.invalid/v1"', self.provider_block(config, "cxto_beta"))
        # 遗留别名同步指向当前渠道，让升级前启动的老会话仍能解析
        self.assertIn('base_url = "https://beta.invalid/v1"', self.provider_block(config, "cxto_active"))
        # 原有 provider 块一个字节都不碰
        self.assertIn("[model_providers.legacy]", config)
        self.assertIn('base_url = "https://old.invalid/v1"', self.provider_block(config, "legacy"))
        self.assertEqual(json.loads((self.home / ".codex" / "auth.json").read_text(encoding="utf-8"))["OPENAI_API_KEY"], "beta-key")
        self.assertEqual(json.loads((self.home / ".config" / "cxto" / "active.json").read_text(encoding="utf-8"))["provider"], "beta")
        self.assertIn(f'model_catalog_json = "{self.home / ".codex" / "cxto_model_catalog.json"}"', config)
        catalog = json.loads((self.home / ".codex" / "cxto_model_catalog.json").read_text(encoding="utf-8"))
        slugs = {model["slug"] for model in catalog["models"]}
        self.assertIn("gpt-5.5", slugs)
        self.assertIn("deepseek-v4-flash", slugs)
        models = self.run_cli("models")
        self.assertEqual(models.returncode, 0, models.stderr)
        self.assertIn("gpt-5.5", models.stdout)
        self.assertIn("deepseek-v4-flash", models.stdout)

    def test_switching_channels_leaves_other_channel_blocks_untouched(self) -> None:
        self.run_cli("use", "alpha")
        alpha_before = self.provider_block((self.home / ".codex" / "config.toml").read_text(encoding="utf-8"), "cxto_alpha")
        self.run_cli("use", "beta")
        config = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model_provider = "cxto_beta"', config)
        # alpha 那块一字未改 —— 钉在它上面的会话不会被这次切换带跑
        self.assertEqual(self.provider_block(config, "cxto_alpha"), alpha_before)
        self.assertIn('base_url = "https://alpha.invalid/v1"', self.provider_block(config, "cxto_alpha"))

    def test_no_restart_flag_skips_the_daemon_bounce(self) -> None:
        """切换默认不刷新；显式刷新时才重启 daemon。"""
        daemon = self.make_running_daemon()
        calls = self.make_fake_codex()
        result = self.run_cli("use", "deepseek", "--no-restart")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cxto models --refresh", result.stdout)
        self.assertFalse(calls.exists())
        self.run_cli("use", "deepseek")
        self.assertFalse(calls.exists())
        refreshed = self.run_cli("models", "--refresh")
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        self.assertEqual(calls.read_text(encoding="utf-8").count("app-server daemon restart"), 1)
        self.assertIsNone(daemon.poll())

    def make_running_daemon(self) -> subprocess.Popen[bytes]:
        """写一个 daemon.pid 指向真实存在的进程，验证切换不会杀掉 daemon。"""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        path = self.home / ".codex" / "app-server-daemon"
        path.mkdir(parents=True, exist_ok=True)
        (path / "daemon.pid").write_text(json.dumps({"pid": proc.pid}), encoding="utf-8")
        return proc

    def make_fake_codex(self) -> Path:
        """假 codex 放 PATH 最前，记录 daemon restart 调用。"""
        bin_dir = self.home / "bin"
        bin_dir.mkdir(exist_ok=True)
        calls = self.home / "calls.txt"
        stub = bin_dir / "codex"
        stub.write_text(
            '#!/bin/sh\n'
            'if [ "$*" = "debug models --bundled" ]; then\n'
            '  printf "%s\\n" "$BUNDLED_MODELS"\n'
            '  exit 0\n'
            'fi\n'
            'printf "%s\\n" "$*" >> "$CALLS"\n'
            'exit 0\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        bundled = {"models": [
            {"slug": "gpt-5.5", "display_name": "GPT-5.5", "default_reasoning_level": "medium"},
            {"slug": "gpt-5.6-terra", "display_name": "GPT-5.6 Terra", "default_reasoning_level": "medium"},
            {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "default_reasoning_level": "high"},
        ]}
        self.env = self.env | {
            "PATH": f"{bin_dir}:{self.env.get('PATH', '')}",
            "CALLS": str(calls),
            "BUNDLED_MODELS": json.dumps(bundled),
        }
        return calls

    def test_each_channel_block_carries_its_own_key(self) -> None:
        """每个渠道块必须自带自己的 key，否则会话会拿「自己的地址 + 别人的 key」去请求而 401。"""
        self.run_cli("use", "alpha")
        self.run_cli("use", "beta")
        config = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('experimental_bearer_token = "alpha-key"', self.provider_block(config, "cxto_alpha"))
        self.assertIn('experimental_bearer_token = "beta-key"', self.provider_block(config, "cxto_beta"))

    def test_catalog_change_is_deferred_until_explicit_refresh(self) -> None:
        daemon = self.make_running_daemon()
        calls = self.make_fake_codex()
        first = self.run_cli("use", "alpha")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("cxto models --refresh", first.stdout)
        self.assertFalse(calls.exists())
        merged = json.loads((self.home / ".codex" / "cxto_model_catalog.json").read_text(encoding="utf-8"))
        slugs = {model["slug"] for model in merged["models"]}
        self.assertIn("gpt-5.6-terra", slugs)
        self.assertIn("gpt-6.1-sol", slugs)
        self.run_cli("use", "deepseek")
        self.assertFalse(calls.exists())
        refreshed = self.run_cli("models", "--refresh")
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        self.assertIn("模型列表已刷新", refreshed.stdout)
        self.assertEqual(calls.read_text(encoding="utf-8").count("app-server daemon restart"), 1)
        self.assertIsNone(daemon.poll(), "渠道切换不能强杀正在承载会话的 daemon")

    def test_custom_catalog_addition_is_merged_and_refreshed_explicitly(self) -> None:
        daemon = self.make_running_daemon()
        calls = self.make_fake_codex()
        self.run_cli("use", "alpha")
        source = self.home / ".codex" / "gamma-models.json"
        source_data = {"models": [
            {"slug": "gamma-one", "display_name": "Gamma One"},
            {"slug": "gamma-two", "display_name": "Gamma Two"},
        ]}
        source.write_text(json.dumps(source_data), encoding="utf-8")

        switched = self.run_cli("use", "gamma")
        self.assertEqual(switched.returncode, 0, switched.stderr)
        self.assertFalse(calls.exists(), "渠道切换不能等待模型目录刷新")
        combined = json.loads((self.home / ".codex" / "cxto_model_catalog.json").read_text(encoding="utf-8"))
        self.assertIn("gamma-two", [model["slug"] for model in combined["models"]])
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), source_data)

        listed = self.run_cli("models")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("gamma-two", listed.stdout)
        refreshed = self.run_cli("models", "--refresh")
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        self.assertEqual(calls.read_text(encoding="utf-8").count("app-server daemon restart"), 1)
        self.assertIsNone(daemon.poll())

    def test_missing_native_catalog_falls_back_to_channel_catalog(self) -> None:
        (self.home / ".codex" / "model_catalog.json").unlink()
        self.env["BUNDLED_MODELS"] = ""
        result = self.run_cli("use", "deepseek")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model_catalog_json = "~/.codex/models.json"', config)
        self.assertFalse((self.home / ".codex" / "cxto_model_catalog.json").exists())

    def test_bundled_catalog_works_without_a_native_snapshot(self) -> None:
        (self.home / ".codex" / "model_catalog.json").unlink()
        result = self.run_cli("models", "--refresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gpt-6.1-sol", result.stdout)
        self.assertIn("gpt-5.6-terra", result.stdout)
        self.assertIn("deepseek-v4-flash", result.stdout)
        self.assertIn("Codex CLI 内置目录", result.stdout)

    def test_bundled_catalog_updates_metadata_and_preserves_local_only_models(self) -> None:
        local_path = self.home / ".codex" / "model_catalog.json"
        local_catalog = {"models": [
            {"slug": "gpt-5.5", "display_name": "Old metadata", "default_reasoning_level": "low"},
            {"slug": "legacy-local", "display_name": "Local gateway model"},
        ]}
        local_path.write_text(json.dumps(local_catalog), encoding="utf-8")
        result = self.run_cli("models", "--refresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        merged = json.loads((self.home / ".codex" / "cxto_model_catalog.json").read_text(encoding="utf-8"))
        entries = {model["slug"]: model for model in merged["models"]}
        self.assertEqual(entries["gpt-5.5"]["default_reasoning_level"], "medium")
        self.assertIn("legacy-local", entries)
        self.assertEqual(json.loads(local_path.read_text(encoding="utf-8")), local_catalog)

    def test_unsupported_bundled_command_falls_back_to_local_catalog(self) -> None:
        self.env["BUNDLED_MODELS"] = "unsupported command"
        result = self.run_cli("models", "--refresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gpt-5.5", result.stdout)
        self.assertIn("deepseek-v4-flash", result.stdout)
        self.assertNotIn("gpt-6.1-sol", result.stdout)

    def test_explicit_native_catalog_path_is_merged_with_bundled_catalog(self) -> None:
        config = self.home / ".cxto.team.yaml"
        config.write_text('model_catalog_path: "~/.codex/model_catalog.json"\n' + config.read_text(encoding="utf-8"), encoding="utf-8")
        result = self.run_cli("models", "--refresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gpt-5.5", result.stdout)
        self.assertIn("gpt-6.1-sol", result.stdout)
        self.assertIn("Codex CLI 内置目录 +", result.stdout)

    def test_same_catalog_family_does_not_restart_daemon(self) -> None:
        self.make_running_daemon()
        calls = self.make_fake_codex()
        self.run_cli("use", "alpha")
        self.run_cli("use", "beta")  # 两者都用内置目录，签名没变 → 不该碰 daemon
        self.assertFalse(calls.exists())

    def test_daemon_restart_skipped_when_no_daemon_running(self) -> None:
        calls = self.make_fake_codex()
        result = self.run_cli("use", "alpha")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("没有运行中的 Codex daemon", result.stdout)
        self.assertFalse(calls.exists())

    def test_legacy_resume_dry_run_is_read_only(self) -> None:
        self.make_rollout()
        before_config = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        before_auth = (self.home / ".codex" / "auth.json").read_text(encoding="utf-8")
        result = self.run_cli("resume", "beta", SESSION_ID, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("旧会话Provider身份: legacy", result.stdout)
        self.assertIn("model_providers.legacy={", result.stdout)
        self.assertIn("https://beta.invalid/v1", result.stdout)
        self.assertIn('model_provider="legacy"', result.stdout)
        self.assertNotIn("beta-key", result.stdout)
        self.assertEqual((self.home / ".codex" / "config.toml").read_text(encoding="utf-8"), before_config)
        self.assertEqual((self.home / ".codex" / "auth.json").read_text(encoding="utf-8"), before_auth)

    def test_legacy_resume_executes_only_a_fake_codex(self) -> None:
        self.make_rollout()
        fake_bin = self.home / "bin"
        fake_bin.mkdir(exist_ok=True)
        capture = self.home / "arguments"
        fake = fake_bin / "codex"
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n', encoding="utf-8")
        fake.chmod(0o755)
        result = self.run_cli("resume", "beta", SESSION_ID, env=self.env | {"PATH": f"{fake_bin}:{self.env.get('PATH', '')}", "CAPTURE": str(capture)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('model_provider = "cxto_beta"', (self.home / ".codex" / "config.toml").read_text(encoding="utf-8"))
        arguments = capture.read_text(encoding="utf-8")
        self.assertIn('model_provider="legacy"', arguments)
        self.assertIn('model_providers.legacy={', arguments)
        self.assertIn('base_url = "https://beta.invalid/v1"', arguments)
        self.assertIn(SESSION_ID, arguments)

    def test_resume_automatically_uses_original_provider_without_global_writes(self) -> None:
        self.run_cli("use", "alpha")
        self.make_rollout("cxto_alpha")
        self.run_cli("use", "beta")
        before = self.state_files()
        capture, env = self.capture_resume()
        result = self.run_cli("resume", SESSION_ID, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(capture.read_text(encoding="utf-8"))
        self.assertEqual(arguments, ["resume", "-c", 'model_provider="cxto_alpha"', SESSION_ID])
        self.assertEqual(self.state_files(), before)
        self.assertIn("cxto_alpha", result.stdout)

    def test_automatic_resume_dry_run_is_read_only_and_keeps_plain_provider(self) -> None:
        self.make_rollout()
        self.run_cli("use", "beta")
        before = self.state_files()
        capture, env = self.capture_resume()
        result = self.run_cli("resume", SESSION_ID, "--dry-run", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('model_provider="legacy"', result.stdout)
        self.assertNotIn("beta-key", result.stdout)
        self.assertFalse(capture.exists())
        self.assertEqual(self.state_files(), before)

    def test_automatic_resume_rejects_missing_or_ambiguous_provider(self) -> None:
        self.run_cli("use", "beta")
        capture, env = self.capture_resume()
        for provider in ("removed_provider", "cxto_active"):
            with self.subTest(provider=provider):
                self.make_rollout(provider)
                before = self.state_files()
                result = self.run_cli("resume", SESSION_ID, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cxto resume <渠道>", result.stderr)
                self.assertFalse(capture.exists())
                self.assertEqual(self.state_files(), before)

    def test_resume_failure_keeps_global_state_and_exit_code(self) -> None:
        self.make_rollout()
        before = self.state_files()
        capture, env = self.capture_resume(exit_code=42)
        result = self.run_cli("resume", SESSION_ID, env=env)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertTrue(capture.exists())
        self.assertEqual(self.state_files(), before)

    def test_resume_unknown_session_and_invalid_id_do_not_activate_channel(self) -> None:
        before = self.state_files()
        capture, env = self.capture_resume()
        for session_id in (SESSION_ID, "*", "not-a-session"):
            with self.subTest(session_id=session_id):
                result = self.run_cli("resume", "beta", session_id, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(capture.exists())
                self.assertEqual(self.state_files(), before)

    def test_explicit_resume_replaces_old_auth_settings_and_quotes_provider_name(self) -> None:
        self.make_rollout("cxto_old.channel")
        capture, env = self.capture_resume()
        result = self.run_cli("resume", "deepseek", SESSION_ID, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(capture.read_text(encoding="utf-8"))
        self.assertIn('model_provider="cxto_old.channel"', arguments)
        block = next(arg for arg in arguments if arg.startswith('model_providers={ "cxto_old.channel" ='))
        self.assertIn('name = "cxto_old.channel"', block)
        self.assertIn('experimental_bearer_token = "sk-deepseek-test"', block)
        self.assertNotIn(".base_url=", block)
        self.assertEqual(Path(str(capture) + ".token").read_text(encoding="utf-8"), "sk-deepseek-test")

    def test_resume_uses_target_environment_key_and_masks_preview(self) -> None:
        config = self.home / ".cxto.local.yaml"
        with config.open("a", encoding="utf-8") as stream:
            stream.write('  env_channel:\n    base_url: "https://env.invalid/v1"\n    env_key: "TARGET_CHANNEL_KEY"\n')
        self.make_rollout()
        before = self.state_files()
        capture, env = self.capture_resume()
        missing = self.run_cli("resume", "env_channel", SESSION_ID, env=env)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("TARGET_CHANNEL_KEY", missing.stderr)
        self.assertFalse(capture.exists())
        self.assertEqual(self.state_files(), before)
        env["TARGET_CHANNEL_KEY"] = "synthetic-env-key"
        preview = self.run_cli("resume", "env_channel", SESSION_ID, "--dry-run", env=env)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertNotIn("synthetic-env-key", preview.stdout)
        self.assertEqual(self.state_files(), before)
        resumed = self.run_cli("resume", "env_channel", SESSION_ID, env=env)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(Path(str(capture) + ".token").read_text(encoding="utf-8"), "synthetic-env-key")

    def test_resume_reads_metadata_after_malformed_lines_and_legacy_id(self) -> None:
        rollout = self.make_rollout()
        metadata = json.loads(rollout.read_text(encoding="utf-8"))
        metadata["payload"]["session_id"] = metadata["payload"].pop("id")
        rollout.write_text('broken-json\n[]\n' + json.dumps(metadata) + '\n', encoding="utf-8")
        result = self.run_cli("resume", SESSION_ID, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('model_provider="legacy"', result.stdout)

    def test_explicit_resume_requires_credentials_before_replacing_old_env_auth(self) -> None:
        config = self.home / ".codex" / "config.toml"
        with config.open("a", encoding="utf-8") as stream:
            stream.write('env_key = "OLD_CHANNEL_KEY"\n')
        with (self.home / ".cxto.local.yaml").open("a", encoding="utf-8") as stream:
            stream.write('  public_channel:\n    base_url: "https://public.invalid/v1"\n')
        self.make_rollout()
        before = self.state_files()
        capture, env = self.capture_resume()
        result = self.run_cli("resume", "public_channel", SESSION_ID, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("auth_token_path", result.stderr)
        self.assertFalse(capture.exists())
        self.assertEqual(self.state_files(), before)

    def test_explicit_resume_can_use_target_global_auth_fallback(self) -> None:
        self.make_rollout()
        capture, env = self.capture_resume()
        result = self.run_cli("resume", "gamma", SESSION_ID, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(str(capture) + ".token").read_text(encoding="utf-8"), "old")

    def test_use_deepseek_writes_bearer_token_and_extra_top_keys(self) -> None:
        result = self.run_cli("use", "deepseek")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model_provider = "cxto_deepseek"', config)
        self.assertIn('model = "deepseek-v4-flash"', config)
        self.assertIn('preferred_auth_method = "apikey"', config)
        self.assertIn('forced_login_method = "api"', config)
        self.assertIn(f'model_catalog_json = "{self.home / ".codex" / "cxto_model_catalog.json"}"', config)
        # provider 块写入了 experimental_bearer_token，且不再有 requires_openai_auth
        active_block = self.provider_block(config, "cxto_deepseek")
        self.assertIn('experimental_bearer_token = "sk-deepseek-test"', active_block)
        self.assertNotIn("requires_openai_auth", active_block)
        # models.json 被写入
        self.assertTrue((self.home / ".codex" / "models.json").exists())
        catalog = json.loads((self.home / ".codex" / "models.json").read_text(encoding="utf-8"))
        self.assertIn("deepseek-v4-flash", [m["slug"] for m in catalog["models"]])

    def test_switch_away_from_deepseek_cleans_extra_top_keys(self) -> None:
        self.run_cli("use", "deepseek")
        result = self.run_cli("use", "beta")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        # deepseek 的顶层额外键被清理（它们必须跟着顶层走）
        self.assertNotIn("preferred_auth_method", config)
        self.assertNotIn("forced_login_method", config)
        self.assertIn(f'model_catalog_json = "{self.home / ".codex" / "cxto_model_catalog.json"}"', config)
        # beta 自己那块带自己的 key，不再依赖全局 auth.json
        self.assertIn('experimental_bearer_token = "beta-key"', self.provider_block(config, "cxto_beta"))
        self.assertIn('requires_openai_auth = true', self.provider_block(config, "cxto_beta"))
        # deepseek 那块保留自己的凭证
        self.assertIn('experimental_bearer_token = "sk-deepseek-test"', self.provider_block(config, "cxto_deepseek"))


if __name__ == "__main__":
    unittest.main()
