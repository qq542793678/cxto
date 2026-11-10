"""Real Codex processes, shared home, synthetic HTTP responses; no external provider."""
from __future__ import annotations

import http.server
import json
import os
import pty
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
import unittest
import struct
import fcntl
import termios

import test_cli

sys.path.insert(0, str(test_cli.ROOT / "src"))
from cxto import launcher


class CodexClient:
    def __init__(self, entry: Path, env: dict[str, str], root: Path):
        self.log = (root / f"client-{time.monotonic_ns()}.log").open("w+", encoding="utf-8")
        self.process = subprocess.Popen([str(entry), "app-server", "--stdio"], env=env, cwd=root,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True,
                                        start_new_session=True)
        self.messages = queue.Queue()
        self.pending = []
        self.ident = 0

        def read():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
            self.messages.put({"_eof": True})

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            response = self.rpc("initialize", {"clientInfo": {"name": "cxto_live_test", "version": "1.0"}})
            if "error" in response:
                raise AssertionError(response)
            self.process.stdin.write('{"method":"initialized"}\n')
            self.process.stdin.flush()
        except BaseException:
            self.close()
            raise

    def next_message(self, timeout=20):
        message = self.messages.get(timeout=timeout)
        if message.get("_eof"):
            self.log.seek(0)
            raise AssertionError("Codex exited: " + self.log.read()[-4000:])
        return message

    def rpc(self, method, params):
        self.ident += 1
        self.process.stdin.write(json.dumps({"id": self.ident, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 20
        while True:
            response = self.next_message(max(0.01, deadline - time.monotonic()))
            if response.get("id") == self.ident:
                return response
            self.pending.append(response)

    def completed(self, turn_id):
        deadline = time.monotonic() + 25
        while True:
            for index, message in enumerate(self.pending):
                if message.get("method") == "turn/completed" and message.get("params", {}).get("turn", {}).get("id") == turn_id:
                    return self.pending.pop(index)["params"]["turn"]
            self.pending.append(self.next_message(max(0.01, deadline - time.monotonic())))

    def close(self):
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.reader.join(timeout=5)
        self.process.stdout.close()
        self.log.close()


@unittest.skipUnless(os.environ.get("CXTO_TEST_CODEX"), "set CXTO_TEST_CODEX for real-process channel isolation")
class LiveIsolationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_cli.CxtoCliTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.home = self.fixture.home
        self.env = self.fixture.env | {"CODEX_HOME": str(self.home / ".codex")}
        with (self.home / ".codex" / "config.toml").open("a", encoding="utf-8") as stream:
            stream.write('\n[features]\nplugin_discovery_prefetch=false\nplugins=false\nremote_plugin=false\n')
        self.env.pop("CXTO_SESSION_API_KEY", None)
        self.arrived = threading.Event()
        self.release = threading.Event()
        self.requests = []
        self.retry_first = False
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                record = {"path": self.path, "auth": self.headers.get("Authorization"),
                          "model": body.get("model"), "effort": body.get("reasoning", {}).get("effort")}
                owner.requests.append(record)
                first_aa = "/aa/" in self.path and sum("/aa/" in entry["path"] for entry in owner.requests) == 1
                if first_aa:
                    owner.arrived.set()
                    owner.release.wait(35)
                    if owner.retry_first:
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(b'{"error":{"message":"synthetic retry","type":"server_error"}}')
                        return
                message = {"id": "msg_synthetic", "type": "message", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": "Synthetic task completed.", "annotations": []}]}
                response = {"id": "resp_synthetic", "object": "response", "status": "completed", "output": [message],
                            "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}}
                events = [
                    {"type": "response.created", "response": {"id": "resp_synthetic", "object": "response", "output": []}},
                    {"type": "response.output_item.added", "output_index": 0, "item": {**message, "content": []}},
                    {"type": "response.content_part.added", "item_id": message["id"], "output_index": 0, "content_index": 0,
                     "part": {"type": "output_text", "text": "", "annotations": []}},
                    {"type": "response.output_text.delta", "item_id": message["id"], "output_index": 0, "content_index": 0,
                     "delta": "Synthetic task completed."},
                    {"type": "response.output_item.done", "output_index": 0, "item": message},
                    {"type": "response.completed", "response": response},
                ]
                data = "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        self.endpoint = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=self.endpoint.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(self.endpoint.server_close)
        self.addCleanup(self.endpoint.shutdown)
        self.addCleanup(self.release.set)
        port = self.endpoint.server_port
        providers = ["providers:"]
        for name, route, model, effort in [("alpha", "aa", "gpt-5.5", "high"), ("beta", "bb", "gpt-6.1-sol", "xhigh"), ("cc", "cc", "gpt-5.5", "low")]:
            key = self.home / ".cxto" / "keys" / (name + ".key")
            key.write_text(name + "-key\n", encoding="utf-8")
            providers.extend([f"  {name}:", f'    model: "{model}"', f'    reasoning_effort: "{effort}"',
                              f'    base_url: "http://127.0.0.1:{port}/{route}/v1"', '    wire_api: "responses"',
                              '    requires_openai_auth: true', f'    auth_token_path: "{key}"'])
        (self.home / ".cxto.local.yaml").write_text("\n".join(providers) + "\n", encoding="utf-8")
        (self.home / ".cxto.team.yaml").write_text("", encoding="utf-8")
        self.entry = self.home / "bin" / "codex"
        self.real = Path(launcher.real_command(os.environ["CXTO_TEST_CODEX"])).resolve()
        # The local Node wrapper derives its musl loader from HOME. Use its native
        # Codex binary when running under a synthetic HOME instead.
        if self.real.suffix == ".js":
            native = list(self.real.parent.parent.glob("node_modules/@openai/codex-*/vendor/*/bin/codex"))
            self.assertEqual(len(native), 1, "expected one installed native Codex binary")
            self.real = native[0]

    def cli(self, *arguments):
        result = self.fixture.run_cli(*arguments, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def client(self):
        value = CodexClient(self.entry, self.env, self.home)
        self.addCleanup(value.close)
        return value

    def start_thread(self, client, provider):
        result = client.rpc("thread/start", {"cwd": str(self.home), "approvalPolicy": "never", "sandbox": "danger-full-access"})
        self.assertNotIn("error", result)
        self.assertEqual(result["result"]["modelProvider"], provider)
        return result["result"]["thread"]["id"]

    def start_turn(self, client, thread):
        result = client.rpc("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "Complete this synthetic local task."}]})
        self.assertNotIn("error", result)
        return result["result"]["turn"]["id"]

    def exercise(self, retry):
        self.retry_first = retry
        self.cli("use", "alpha")
        self.entry.unlink()
        self.entry.symlink_to(self.real)
        self.cli("install-launcher", "--path", str(self.entry))
        aa = self.client()
        thread_a = self.start_thread(aa, "cxto_alpha")
        first_turn = self.start_turn(aa, thread_a)
        self.assertTrue(self.arrived.wait(10), "AA did not reach the local endpoint")

        self.cli("use", "beta")
        bb = self.client()
        thread_b = self.start_thread(bb, "cxto_beta")
        turn_b = self.start_turn(bb, thread_b)
        self.assertEqual(bb.completed(turn_b)["status"], "completed")
        self.assertIsNone(aa.process.poll(), "AA exited while BB was running")
        self.assertFalse(self.release.is_set(), "AA request must remain active during the switches")

        # Even reselecting AA with a changed key/model cannot alter its existing process.
        (self.home / ".cxto" / "keys" / "alpha.key").write_text("alpha-new-key\n", encoding="utf-8")
        self.cli("use", "alpha", "gpt-6.1-sol", "--effort", "low")
        processes = [subprocess.Popen([sys.executable, str(test_cli.CLI), "use", name], env=self.env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for name in ["beta", "cc", "beta", "cc"]]
        for process in processes:
            _, error = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, error)
        self.cli("use", "cc")
        cfg_a = aa.rpc("config/read", {})["result"]["config"]
        cfg_b = bb.rpc("config/read", {})["result"]["config"]
        self.assertEqual(cfg_a["model_provider"], "cxto_alpha")
        self.assertEqual(cfg_a["model"], "gpt-5.5")
        self.assertEqual(cfg_a["model_reasoning_effort"], "high")
        self.assertEqual(cfg_b["model_provider"], "cxto_beta")
        cc = self.client()
        thread_c = self.start_thread(cc, "cxto_cc")
        turn_c = self.start_turn(cc, thread_c)
        self.assertEqual(cc.completed(turn_c)["status"], "completed")

        self.release.set()
        self.assertEqual(aa.completed(first_turn)["status"], "completed")
        # Rebinding the same thread within the live process must still pass identity checks.
        resumed = aa.rpc("thread/resume", {"threadId": thread_a})
        self.assertNotIn("error", resumed)
        self.assertEqual(resumed["result"]["modelProvider"], "cxto_alpha")
        next_turn = self.start_turn(aa, thread_a)
        self.assertEqual(aa.completed(next_turn)["status"], "completed")
        bb_next = self.start_turn(bb, thread_b)
        self.assertEqual(bb.completed(bb_next)["status"], "completed")
        aa_requests = [entry for entry in self.requests if "/aa/" in entry["path"]]
        bb_requests = [entry for entry in self.requests if "/bb/" in entry["path"]]
        self.assertGreaterEqual(len(aa_requests), 3 if retry else 2)
        self.assertGreaterEqual(len(bb_requests), 2)
        for request in aa_requests:
            self.assertEqual(request["auth"], "Bearer alpha-key")
            self.assertEqual(request["model"], "gpt-5.5")
            self.assertEqual(request["effort"], "high")
        for request in bb_requests:
            self.assertEqual(request["auth"], "Bearer beta-key")
            self.assertEqual(request["model"], "gpt-6.1-sol")

    def test_active_processes_survive_repeated_and_concurrent_channel_switches(self):
        self.exercise(retry=False)

    def test_request_retry_after_other_channel_switches_keeps_original_credentials(self):
        self.exercise(retry=True)

    def test_normal_interactive_codex_entry_starts_with_bound_channel(self):
        self.release.set()
        self.cli("use", "alpha", "gpt-6.1-sol")
        self.entry.unlink()
        self.entry.symlink_to(self.real)
        self.cli("install-launcher", "--path", str(self.entry))
        with (self.home / ".codex" / "config.toml").open("a", encoding="utf-8") as stream:
            stream.write(f'\n[projects.{json.dumps(str(self.home))}]\ntrust_level="trusted"\n')
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 140, 0, 0))

        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        proc = subprocess.Popen([str(self.entry), "--no-alt-screen", "-a", "never", "Complete this synthetic local task."],
                                env=self.env | {"TERM": "xterm-256color", "CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT": "1"},
                                cwd=self.home, stdin=slave, stdout=slave, stderr=slave, preexec_fn=controlling_terminal)
        os.close(slave)
        output = bytearray()

        def read_terminal():
            answered_model_prompt = False
            try:
                while True:
                    data = os.read(master, 16384)
                    if not data:
                        break
                    output.extend(data)
                    if b"\x1b[6n" in data:
                        os.write(master, b"\x1b[1;1R")
                    if not answered_model_prompt and b"Use existing model" in output:
                        os.write(master, b"\x1b[B\r")
                        answered_model_prompt = True
            except OSError:
                pass

        reader = threading.Thread(target=read_terminal, daemon=True)
        reader.start()
        try:
            self.assertTrue(self.arrived.wait(30), "TUI did not start a request: " + output.decode(errors="replace")[-2000:])
            self.assertEqual(self.requests[0]["auth"], "Bearer alpha-key")
            self.assertEqual(self.requests[0]["model"], "gpt-6.1-sol")
            self.assertIsNone(proc.poll())
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(master)
            reader.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
