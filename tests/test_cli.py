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
        (self.home / ".codex" / "auth.json").write_text('{"OPENAI_API_KEY":"old"}\n', encoding="utf-8")
        (self.home / ".cxto.local.yaml").write_text(
            'providers:\n  alpha:\n    model: "gpt-alpha"\n    reasoning_effort: "high"\n    base_url: "https://alpha.invalid/v1"\n    wire_api: "responses"\n    requires_openai_auth: true\n    auth_token_path: "~/.cxto/keys/alpha.key"\n  beta:\n    model: "gpt-beta"\n    reasoning_effort: "xhigh"\n    base_url: "https://beta.invalid/v1"\n    wire_api: "responses"\n    requires_openai_auth: true\n    auth_token_path: "~/.cxto/keys/beta.key"\n  deepseek:\n    model: "deepseek-v4-flash"\n    reasoning_effort: "high"\n    base_url: "https://api.deepseek.com/"\n    wire_api: "responses"\n    preferred_auth_method: "apikey"\n    forced_login_method: "api"\n    model_catalog_json: "~/.codex/models.json"\n    auth_token_path: "~/.cxto/keys/deepseek.key"\n',
            encoding="utf-8",
        )
        (self.home / ".cxto" / "keys" / "alpha.key").write_text("alpha-key\n", encoding="utf-8")
        (self.home / ".cxto" / "keys" / "beta.key").write_text("beta-key\n", encoding="utf-8")
        (self.home / ".cxto" / "keys" / "deepseek.key").write_text("sk-deepseek-test\n", encoding="utf-8")
        self.env = os.environ | {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, encoding="utf-8", env=env or self.env, check=False)

    def make_rollout(self) -> None:
        path = self.home / ".codex" / "sessions" / "2026" / "07" / "19"
        path.mkdir(parents=True)
        (path / f"rollout-{SESSION_ID}.jsonl").write_text(json.dumps({"type": "session_meta", "payload": {"session_id": SESSION_ID, "model_provider": "legacy"}}) + "\n", encoding="utf-8")

    def test_use_creates_stable_provider_without_touching_legacy_definition(self) -> None:
        result = self.run_cli("use", "beta")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model_provider = "cxto_active"', config)
        self.assertIn("[model_providers.cxto_active]", config)
        self.assertIn('base_url = "https://beta.invalid/v1"', config)
        self.assertIn("[model_providers.legacy]", config)
        self.assertEqual(json.loads((self.home / ".codex" / "auth.json").read_text(encoding="utf-8"))["OPENAI_API_KEY"], "beta-key")
        self.assertEqual(json.loads((self.home / ".config" / "cxto" / "active.json").read_text(encoding="utf-8"))["provider"], "beta")

    def test_legacy_resume_dry_run_is_read_only(self) -> None:
        self.make_rollout()
        before_config = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        before_auth = (self.home / ".codex" / "auth.json").read_text(encoding="utf-8")
        result = self.run_cli("resume", "beta", SESSION_ID, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("旧会话Provider身份: legacy", result.stdout)
        self.assertIn("model_providers.legacy.base_url", result.stdout)
        self.assertIn("https://beta.invalid/v1", result.stdout)
        self.assertEqual((self.home / ".codex" / "config.toml").read_text(encoding="utf-8"), before_config)
        self.assertEqual((self.home / ".codex" / "auth.json").read_text(encoding="utf-8"), before_auth)

    def test_legacy_resume_executes_only_a_fake_codex(self) -> None:
        self.make_rollout()
        fake_bin = self.home / "bin"
        fake_bin.mkdir()
        capture = self.home / "arguments"
        fake = fake_bin / "codex"
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n', encoding="utf-8")
        fake.chmod(0o755)
        result = self.run_cli("resume", "beta", SESSION_ID, env=self.env | {"PATH": f"{fake_bin}:{self.env.get('PATH', '')}", "CAPTURE": str(capture)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('model_provider = "cxto_active"', (self.home / ".codex" / "config.toml").read_text(encoding="utf-8"))
        arguments = capture.read_text(encoding="utf-8")
        self.assertIn("model_providers.legacy.base_url=\"https://beta.invalid/v1\"", arguments)
        self.assertIn(SESSION_ID, arguments)

    def test_use_deepseek_writes_bearer_token_and_extra_top_keys(self) -> None:
        result = self.run_cli("use", "deepseek")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model_provider = "cxto_active"', config)
        self.assertIn('model = "deepseek-v4-flash"', config)
        self.assertIn('preferred_auth_method = "apikey"', config)
        self.assertIn('forced_login_method = "api"', config)
        self.assertIn('model_catalog_json = "~/.codex/models.json"', config)
        # provider 块写入了 experimental_bearer_token，且不再有 requires_openai_auth
        active_block = config.split("[model_providers.cxto_active]")[1]
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
        # deepseek 的顶层额外键被清理
        self.assertNotIn("preferred_auth_method", config)
        self.assertNotIn("forced_login_method", config)
        self.assertNotIn("model_catalog_json", config)
        # provider 块的 experimental_bearer_token 残留被清理
        self.assertNotIn("experimental_bearer_token", config)
        self.assertIn('requires_openai_auth = true', config)


if __name__ == "__main__":
    unittest.main()
