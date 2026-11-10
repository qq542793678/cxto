"""Opt-in Codex checks using synthetic sessions and credentials.

Run with CXTO_TEST_CODEX pointing to a real Codex CLI. All configuration,
rollouts, logs and databases live inside TemporaryDirectory. The authentication
check sends one synthetic turn to a local HTTP stub; no external provider is used.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import http.server
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cxto_under_test", ROOT / "src" / "cxto" / "cli.py")
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
launcher = cli.launcher


@unittest.skipUnless(os.environ.get("CXTO_TEST_CODEX"), "set CXTO_TEST_CODEX for isolated real-Codex checks")
class ResumeProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="cxto-resume-integration-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.thread_id = str(uuid.uuid4())
        self.old_provider = "cxto_old.channel"
        self.now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        path = self.root / "sessions" / "2026" / "11" / "02" / f"rollout-{self.thread_id}.jsonl"
        path.parent.mkdir(parents=True)
        records = [
            {"timestamp": self.now, "type": "session_meta", "payload": {
                "id": self.thread_id, "timestamp": self.now, "cwd": str(self.root),
                "originator": "codex-tui", "cli_version": "0.166.0", "source": "cli",
                "model_provider": self.old_provider,
            }},
            {"timestamp": self.now, "type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "Synthetic resume test. Never send turn/start."},
                ],
            }},
        ]
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        self.config = self.root / "config.toml"
        self.config.write_text(
            'model_provider = "cxto_beta"\nmodel = "gpt-test"\n'
            '[model_providers.cxto_beta]\nname = "beta"\n'
            'base_url = "http://127.0.0.1:1/beta"\nwire_api = "responses"\n'
            'experimental_bearer_token = "synthetic-beta-key"\n'
            '[model_providers."cxto_old.channel"]\nname = "old"\n'
            'base_url = "http://127.0.0.1:1/old"\nwire_api = "responses"\n'
            'requires_openai_auth = true\nexperimental_bearer_token = "synthetic-old-key"\n'
            'env_key = "UNSET_OLD_CHANNEL_KEY"\n',
            encoding="utf-8",
        )

    @contextlib.contextmanager
    def server(self, overrides: list[str], token: str | None = None):
        server_log = (self.root / "server.log").open("w+", encoding="utf-8")
        proc = subprocess.Popen(
            [launcher.real_command(os.environ["CXTO_TEST_CODEX"]), "app-server", "--stdio", *overrides],
            cwd=self.root, env=os.environ | {"CODEX_HOME": str(self.root)} | ({cli.RESUME_TOKEN_ENV: token} if token else {}),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=server_log,
            text=True,
        )
        assert proc.stdin is not None and proc.stdout is not None
        messages: queue.Queue = queue.Queue()

        def read_messages() -> None:
            for line in proc.stdout:
                try:
                    messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            messages.put({"_eof": True})

        reader = threading.Thread(target=read_messages, daemon=True)
        reader.start()

        def rpc(ident: int, method: str, params: dict) -> dict:
            proc.stdin.write(json.dumps({"id": ident, "method": method, "params": params}) + "\n")
            proc.stdin.flush()
            while True:
                message = messages.get(timeout=20)
                if message.get("_eof"):
                    server_log.seek(0)
                    self.fail("Codex exited before replying: " + server_log.read()[-3000:])
                if message.get("id") == ident:
                    return message

        try:
            initialized = rpc(1, "initialize", {"clientInfo": {"name": "cxto_integration", "version": "1.0"}})
            self.assertNotIn("error", initialized)
            proc.stdin.write('{"method":"initialized"}\n')
            proc.stdin.flush()
            yield rpc
        finally:
            proc.stdin.close()
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            reader.join(timeout=5)
            proc.stdout.close()
            server_log.close()

    def test_saved_provider_override_resolves_real_mismatch(self) -> None:
        before = self.config.read_bytes()
        rollout = next((self.root / "sessions").rglob("*.jsonl"))
        with rollout.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"timestamp": self.now, "type": "turn_context", "payload": {
                "turn_id": str(uuid.uuid4()), "cwd": str(self.root), "approval_policy": "never",
                "sandbox_policy": {"type": "danger-full-access"}, "model": "gpt-saved",
                "effort": "high", "summary": "none",
            }}) + "\n")
        with self.server([]) as rpc:
            result = rpc(2, "thread/resume", {"threadId": self.thread_id})
            self.assertEqual(result["error"]["code"], -32600)
            self.assertEqual(result["error"]["data"]["type"], "modelProviderMismatch")
        command = cli.resume_arguments(self.old_provider, None, self.thread_id)
        with self.server(command[2:-1]) as rpc:
            result = rpc(2, "thread/resume", {"threadId": self.thread_id})
            self.assertNotIn("error", result)
            self.assertEqual(result["result"]["modelProvider"], self.old_provider)
            self.assertEqual(result["result"]["model"], "gpt-saved")
            self.assertEqual(result["result"]["reasoningEffort"], "high")
        self.assertEqual(self.config.read_bytes(), before)

    def test_explicit_target_with_simple_provider_name(self) -> None:
        self.old_provider = "cxto_beta"
        rollout = next((self.root / "sessions").rglob("*.jsonl"))
        records = [json.loads(line) for line in rollout.read_text(encoding="utf-8").splitlines()]
        records[0]["payload"]["model_provider"] = self.old_provider
        rollout.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        target = {"base_url": "http://127.0.0.1:1/new", "wire_api": "responses",
                  "experimental_bearer_token": "synthetic-new-key"}
        command = cli.resume_arguments(self.old_provider, target, self.thread_id)
        with self.server(command[2:-1], target["experimental_bearer_token"]) as rpc:
            config = rpc(2, "config/read", {})["result"]["config"]
            self.assertEqual(config["model_providers"][self.old_provider]["base_url"], target["base_url"])
            result = rpc(3, "thread/resume", {"threadId": self.thread_id})
            self.assertNotIn("error", result)

    def test_explicit_target_replaces_old_provider_auth_and_restores_session(self) -> None:
        before = self.config.read_bytes()
        target = {"base_url": "http://127.0.0.1:1/target", "wire_api": "responses",
                  "experimental_bearer_token": "synthetic-target-key"}
        command = cli.resume_arguments(self.old_provider, target, self.thread_id)
        with self.server(command[2:-1], target["experimental_bearer_token"]) as rpc:
            config = rpc(2, "config/read", {})["result"]["config"]
            provider = config["model_providers"][self.old_provider]
            self.assertEqual(provider["base_url"], target["base_url"])
            self.assertEqual(provider["experimental_bearer_token"], target["experimental_bearer_token"])
            self.assertFalse(provider.get("requires_openai_auth"))
            self.assertEqual(provider.get("env_key"), cli.RESUME_TOKEN_ENV)
            result = rpc(3, "thread/resume", {"threadId": self.thread_id})
            self.assertNotIn("error", result)
            self.assertEqual(result["result"]["modelProvider"], self.old_provider)
        self.assertEqual(self.config.read_bytes(), before)

    def test_target_credentials_reach_local_endpoint_instead_of_old_env_key(self) -> None:
        received = threading.Event()
        authorization: list[str | None] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                authorization.append(self.headers.get("Authorization"))
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Synthetic local test","type":"invalid_request_error"}}')
                received.set()

            def log_message(self, *_args) -> None:
                pass

        endpoint = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=endpoint.serve_forever, daemon=True)
        worker.start()
        try:
            target = {"base_url": f"http://127.0.0.1:{endpoint.server_port}/v1", "wire_api": "responses",
                      "experimental_bearer_token": "synthetic-target-key"}
            command = cli.resume_arguments(self.old_provider, target, self.thread_id)
            with self.server(command[2:-1], target["experimental_bearer_token"]) as rpc:
                self.assertNotIn("error", rpc(2, "thread/resume", {"threadId": self.thread_id}))
                started = rpc(3, "turn/start", {"threadId": self.thread_id, "input": [
                    {"type": "text", "text": "Synthetic authentication check against the local stub."},
                ]})
                self.assertNotIn("error", started)
                self.assertTrue(received.wait(10), "local endpoint was not called; authentication may have failed")
                self.assertEqual(authorization, ["Bearer synthetic-target-key"])
        finally:
            endpoint.shutdown()
            endpoint.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
