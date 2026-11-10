from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import test_cli


class LauncherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = test_cli.CxtoCliTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.home = self.fixture.home
        self.env = self.fixture.env | {"TMUX_PANE": "%synthetic-pane"}
        self.env.pop("CXTO_SESSION_API_KEY", None)
        self.real = self.home / "real-codex"
        self.real.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "if sys.argv[1:] == ['debug', 'models', '--bundled']:\n"
            "    print(os.environ.get('BUNDLED_MODELS', '{}'))\n"
            "else:\n"
            "    print(json.dumps({'argv':sys.argv[1:], 'token':os.environ.get('CXTO_SESSION_API_KEY'), 'pane':os.environ.get('TMUX_PANE')}))\n",
            encoding="utf-8",
        )
        self.real.chmod(0o755)
        self.entry = self.home / "bin" / "codex"

    def cli(self, *arguments: str, env=None):
        return self.fixture.run_cli(*arguments, env=env or self.env)

    def install(self):
        self.entry.unlink()
        self.entry.symlink_to(self.real)
        result = self.cli("install-launcher", "--path", str(self.entry))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def invoke(self, *args: str):
        result = subprocess.run([str(self.entry), *args], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_run_binds_channel_without_token_in_arguments_and_keeps_tmux(self):
        switched = self.cli("use", "alpha")
        self.assertEqual(switched.returncode, 0, switched.stderr)
        before = self.fixture.state_files()
        result = self.cli("run", "--codex", str(self.real), "--", "-m", "user-model", "hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(result.stdout)
        arguments = captured["argv"]
        self.assertIn('model_provider="cxto_alpha"', arguments)
        self.assertIn('model="gpt-alpha"', arguments)
        self.assertIn('model_reasoning_effort="high"', arguments)
        self.assertIn("--no-daemon", arguments)
        self.assertNotIn("alpha-key", " ".join(arguments))
        self.assertEqual(captured["token"], "alpha-key")
        self.assertEqual(captured["pane"], "%synthetic-pane")
        self.assertEqual(arguments[-3:], ["-m", "user-model", "hello"])
        # Only a runtime lock may be created; the user's channel files stay intact.
        after = self.fixture.state_files()
        for name, contents in before.items():
            self.assertEqual(after[name], contents)

    def test_installed_entry_uses_latest_channel_for_each_new_process(self):
        self.cli("use", "alpha")
        self.install()
        first = self.invoke()
        self.cli("use", "beta")
        second = self.invoke()
        self.assertIn('model_provider="cxto_alpha"', first["argv"])
        self.assertEqual(first["token"], "alpha-key")
        self.assertIn('model_provider="cxto_beta"', second["argv"])
        self.assertEqual(second["token"], "beta-key")

    def test_install_is_idempotent_and_uninstall_restores_original_symlink(self):
        self.install()
        wrapper = self.entry.read_bytes()
        backup = self.entry.with_name("codex.cxto-original")
        self.assertTrue(backup.is_symlink())
        self.assertEqual(os.readlink(backup), str(self.real))
        repeat = self.cli("install-launcher", "--path", str(self.entry))
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(self.entry.read_bytes(), wrapper)
        result = self.cli("uninstall-launcher", "--path", str(self.entry))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.entry.is_symlink())
        self.assertEqual(os.readlink(self.entry), str(self.real))
        self.assertFalse(backup.exists())
        result = self.cli("uninstall-launcher", "--path", str(self.entry))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_install_dry_run_and_conflicting_backup_do_not_overwrite_files(self):
        before = self.entry.read_bytes()
        preview = self.cli("install-launcher", "--path", str(self.entry), "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(self.entry.read_bytes(), before)
        backup = self.entry.with_name("codex.cxto-original")
        self.assertFalse(backup.exists())
        backup.write_text("existing unrelated backup", encoding="utf-8")
        conflict = self.cli("install-launcher", "--path", str(self.entry))
        self.assertNotEqual(conflict.returncode, 0)
        self.assertEqual(backup.read_text(encoding="utf-8"), "existing unrelated backup")
        self.assertEqual(self.entry.read_bytes(), before)

    def test_uninstall_refuses_to_discard_user_edits(self):
        self.install()
        with self.entry.open("a", encoding="utf-8") as stream:
            stream.write("# user change\n")
        before = self.entry.read_bytes()
        result = self.cli("uninstall-launcher", "--path", str(self.entry))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.entry.read_bytes(), before)
        self.assertTrue(self.entry.with_name("codex.cxto-original").exists())

    def test_management_remote_and_oss_commands_pass_through(self):
        self.cli("use", "alpha")
        self.install()
        cases = [("update",), ("--version",), ("--remote", "unix:///synthetic"),
                 ("--oss",), ("app-server", "daemon", "status"), ("-m", "custom", "doctor")]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                captured = self.invoke(*arguments)
                self.assertEqual(captured["argv"], list(arguments))
                self.assertIsNone(captured["token"])

    def test_unmanaged_provider_is_unchanged(self):
        result = self.cli("run", "--codex", str(self.real), "--", "hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["argv"], ["hello"])

    def test_explicit_provider_and_original_arguments_are_respected(self):
        self.cli("use", "alpha")
        self.cli("use", "beta")
        result = self.cli("run", "--codex", str(self.real), "--", "-c", 'model_provider="cxto_alpha"', "hello update")
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(result.stdout)
        self.assertEqual(captured["token"], "alpha-key")
        self.assertEqual(captured["argv"][-3:], ["-c", 'model_provider="cxto_alpha"', "hello update"])

    def test_missing_target_environment_key_fails_before_starting(self):
        self.cli("use", "alpha")
        path = self.home / ".codex" / "config.toml"
        config = path.read_text(encoding="utf-8").replace('experimental_bearer_token = "alpha-key"', 'env_key = "UNSET_SYNTHETIC_CHANNEL_KEY"')
        path.write_text(config, encoding="utf-8")
        result = self.cli("run", "--codex", str(self.real), "--")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UNSET_SYNTHETIC_CHANNEL_KEY", result.stderr)

    def test_profile_provider_and_user_model_options_are_preserved(self):
        self.cli("use", "alpha")
        self.cli("use", "beta")
        (self.home / ".codex" / "alpha-profile.config.toml").write_text(
            'model_provider="cxto_alpha"\nmodel="profile-model"\nmodel_reasoning_effort="low"\n', encoding="utf-8")
        result = self.cli("run", "--codex", str(self.real), "--", "-p", "alpha-profile", "-m", "user-model")
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(result.stdout)
        self.assertEqual(captured["token"], "alpha-key")
        self.assertIn('model="profile-model"', captured["argv"])
        self.assertEqual(captured["argv"][-4:], ["-p", "alpha-profile", "-m", "user-model"])

    def test_explicit_resume_overrides_can_replace_an_unset_old_env_key(self):
        self.cli("use", "alpha")
        path = self.home / ".codex" / "config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace('experimental_bearer_token = "alpha-key"', 'env_key = "UNSET_OLD_CHANNEL_KEY"'), encoding="utf-8")
        self.fixture.make_rollout("cxto_alpha")
        self.entry.unlink()
        self.entry.symlink_to(self.real)
        self.cli("install-launcher", "--path", str(self.entry))
        result = self.cli("resume", "beta", test_cli.SESSION_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        # cxto's status lines precede the fake Codex's one-line JSON.
        captured = json.loads(result.stdout.splitlines()[-1])
        self.assertIn('model_provider="cxto_alpha"', captured["argv"])
        self.assertIn("https://beta.invalid/v1", " ".join(captured["argv"]))

    def test_full_provider_override_does_not_require_old_global_auth(self):
        self.cli("use", "alpha")
        path = self.home / ".codex" / "config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace('experimental_bearer_token = "alpha-key"', 'env_key = "UNSET_OLD_CHANNEL_KEY"'), encoding="utf-8")
        (self.home / ".codex" / "auth.json").unlink()
        result = self.cli("run", "--codex", str(self.real), "--", "-c",
                          'model_providers.cxto_alpha={name="override", base_url="https://override.invalid/v1", wire_api="responses", requires_openai_auth=false, experimental_bearer_token="synthetic-target"}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("synthetic-target", json.loads(result.stdout)["argv"][-1])

    def test_failed_install_restores_original_entry(self):
        sys.path.insert(0, str(test_cli.ROOT / "src"))
        from cxto import launcher
        self.entry.unlink()
        self.entry.symlink_to(self.real)
        original_write = launcher.atomic_write

        def failing_write(path, text, mode=None):
            if path == self.entry:
                raise OSError("synthetic installation failure")
            return original_write(path, text, mode)

        with mock.patch.object(launcher, "atomic_write", side_effect=failing_write):
            with self.assertRaisesRegex(OSError, "synthetic installation failure"):
                launcher.command_install(argparse.Namespace(path=str(self.entry), dry_run=False))
        self.assertTrue(self.entry.is_symlink())
        self.assertEqual(os.readlink(self.entry), str(self.real))
        self.assertFalse(self.entry.with_name("codex.cxto-original").exists())
        self.assertFalse(self.entry.with_name("codex.cxto-launcher.json").exists())

    def test_channel_switch_keeps_symlinked_config_and_writes_valid_snapshot(self):
        config = self.home / ".codex" / "config.toml"
        target = self.home / "shared-config.toml"
        config.replace(target)
        config.symlink_to(target)
        result = self.cli("use", "alpha")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(config.is_symlink())
        self.assertIn('model_provider = "cxto_alpha"', target.read_text(encoding="utf-8"))
        launched = self.cli("run", "--codex", str(self.real), "--")
        self.assertEqual(launched.returncode, 0, launched.stderr)
        self.assertEqual(json.loads(launched.stdout)["token"], "alpha-key")

    def test_explicit_codex_home_does_not_read_default_home_credentials(self):
        self.cli("use", "alpha")
        isolated = self.home / "isolated-codex"
        isolated.mkdir()
        (isolated / "config.toml").write_text(
            'model_provider="cxto_isolated"\nmodel="isolated-model"\nmodel_reasoning_effort="low"\n'
            '[model_providers.cxto_isolated]\nname="isolated"\nbase_url="https://isolated.invalid/v1"\n'
            'wire_api="responses"\nrequires_openai_auth=true\n', encoding="utf-8")
        (isolated / "auth.json").write_text('{"OPENAI_API_KEY":"isolated-synthetic-key"}', encoding="utf-8")
        result = self.cli("run", "--codex", str(self.real), "--", env=self.env | {"CODEX_HOME": str(isolated)})
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(result.stdout)
        self.assertIn('model_provider="cxto_isolated"', captured["argv"])
        self.assertEqual(captured["token"], "isolated-synthetic-key")

    def test_explicit_token_override_is_the_process_token(self):
        self.cli("use", "alpha")
        result = self.cli("run", "--codex", str(self.real), "--", "-c",
                          'model_providers.cxto_alpha.experimental_bearer_token="synthetic-user-override"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["token"], "synthetic-user-override")


if __name__ == "__main__":
    unittest.main()
