"""Shared fixtures: an isolated crew home, a scratch git repository, and a
scripted OpenAI-compatible server."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def crew_home(tmp_path, monkeypatch):
    home = tmp_path / "crew-home"
    monkeypatch.setenv("AGENT_CREW_HOME", str(home))
    return home


def git(root: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("# repo\n\nSection sign: §\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "lib.rs").write_text("pub mod a;\n", encoding="utf-8")
    (root / "src" / "a.rs").write_text("pub fn a() {}\n", encoding="utf-8")
    (root / ".agent-crew").mkdir()
    (root / ".agent-crew" / "project.toml").write_text(
        'branch = "main"\nworktrees = "../wt"\nshared = []\nchecks = ["text", "strays", "rust-modules"]\n'
        '[verify]\npass = "python -c \\"print(1)\\""\nfail = "python -c \\"print(\'RESULT: FAIL\')\\""\n',
        encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return root


class ScriptedServer:
    """Answers /chat/completions from a list of scripted replies, in order.
    A reply is a dict of tool calls ([(name, args)]), or an HTTP error
    (status, body). Records every request body."""

    def __init__(self):
        self.replies: list = []
        self.requests: list = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append({"body": body, "auth": self.headers.get("Authorization")})
                reply = server.replies.pop(0) if server.replies else ("finish-default",)
                if isinstance(reply, tuple) and isinstance(reply[0], int):
                    status, text = reply
                    self.send_response(status)
                    self.end_headers()
                    self.wfile.write(text.encode())
                    return
                if reply == ("finish-default",):
                    reply = [("finish", {"summary": "done"})]
                calls = [{"id": f"call{i}", "type": "function",
                          "function": {"name": name, "arguments": json.dumps(args)}} for i, (name, args) in enumerate(reply)]
                payload = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": calls}}],
                           "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    scripted = ScriptedServer()
    yield scripted
    scripted.close()


@pytest.fixture
def providers_file(crew_home, server, monkeypatch):
    monkeypatch.setenv("CREW_TEST_KEY_A", "key-a")
    monkeypatch.setenv("CREW_TEST_KEY_B", "key-b")
    crew_home.mkdir(parents=True, exist_ok=True)
    path = crew_home / "providers.toml"
    path.write_text(f'''
[defaults]
writer = "auto"

[[provider]]
name = "local"
base_url = "{server.url}"
api_key_envs = ["CREW_TEST_KEY_A", "CREW_TEST_KEY_B"]

[[model]]
id = "fast"
provider = "local"
roles = ["writer"]
allowance = "free"
priority = 10

[[model]]
id = "campaign"
provider = "local"
roles = ["writer", "reviewer"]
allowance = "campaign"
priority = 20
''', encoding="utf-8")
    return path
