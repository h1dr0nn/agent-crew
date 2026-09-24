import io
import json
import os
import subprocess
import sys
import time

import pytest

from crew import agent, cli, config, procs, review
from tests.conftest import git


def test_target_prefers_uncommitted_work_and_includes_untracked_files(repo):
    (repo / "src" / "a.rs").write_text("pub fn a() { 2 }\n", encoding="utf-8")
    (repo / "src" / "new.rs").write_text("pub fn fresh() {}\n", encoding="utf-8")
    what = review.target(repo, None, "auto", "main")
    assert what.files == ["src/a.rs", "src/new.rs"]
    assert "pub fn a() { 2 }" in what.diff and "+pub fn fresh() {}" in what.diff


def test_target_falls_back_to_the_branch_and_can_be_empty(repo):
    assert review.target(repo, None, "auto", "main").empty
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "src" / "a.rs").write_text("pub fn a() { 3 }\n", encoding="utf-8")
    git(repo, "commit", "-qam", "three")
    what = review.target(repo, None, "auto", "main")
    assert what.files == ["src/a.rs"] and "since main" in what.label
    with pytest.raises(review.ReviewError, match="name a base"):
        review.target(repo, None, "branch")


def test_review_fails_over_to_the_next_reviewer_route(repo, server, providers_file):
    (repo / "src" / "a.rs").write_text("pub fn a() { panic!() }\n", encoding="utf-8")
    server.replies += [(401, "bad key"), "VERDICT: needs-attention\nFINDING 1 [high]\nfile: src/a.rs:1"]
    found = review.run(repo, "adversarial", None, "auto", "panics", fallback="main")
    assert found["review"].startswith("VERDICT: needs-attention")
    assert found["route"].endswith("(CREW_TEST_KEY_B)")
    sent = server.requests[-1]["body"]["messages"][0]["content"]
    assert "Adversarial review" in sent and "panics" in sent and "panic!()" in sent and "{{" not in sent


def _stop(repo, monkeypatch, capsys, event):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(event).encode())))
    assert cli.main(["hook", "stop"]) == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_stop_gate_is_off_by_default_then_blocks_and_never_loops(repo, server, providers_file, monkeypatch, capsys):
    (repo / "src" / "a.rs").write_text("pub fn a() { todo!() }\n", encoding="utf-8")
    event = {"cwd": str(repo), "stop_hook_active": False, "last_assistant_message": "All done."}
    assert _stop(repo, monkeypatch, capsys, event) is None
    assert not server.requests
    review.set_gate(repo, True)
    server.replies += ["BLOCK: todo!() left in a()\nsrc/a.rs:1 - placeholder - implement it"]
    decision = _stop(repo, monkeypatch, capsys, event)
    assert decision["decision"] == "block" and "todo!()" in decision["reason"]
    assert "All done." in server.requests[-1]["body"]["messages"][0]["content"]
    assert _stop(repo, monkeypatch, capsys, dict(event, stop_hook_active=True)) is None
    server.replies += ["ALLOW: fine"]
    assert _stop(repo, monkeypatch, capsys, event) is None
    review.set_gate(repo, False)
    assert not review.gate_enabled(repo)


def test_stop_gate_lets_the_stop_through_when_no_reviewer_answers(repo, server, providers_file, monkeypatch, capsys):
    review.set_gate(repo, True)
    (repo / "src" / "a.rs").write_text("pub fn a() { 4 }\n", encoding="utf-8")
    server.replies += [(401, "no"), (401, "no")]
    decision = _stop(repo, monkeypatch, capsys, {"cwd": str(repo)})
    assert "decision" not in decision and "skipped" in decision["systemMessage"]


def test_run_from_stdin_with_a_new_task_then_result(repo, server, providers_file, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    server.replies += [[("write_file", {"path": "src/b.txt", "content": "b\n"})], [("verify", {})],
                       [("finish", {"summary": "wrote b"})]]
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"write src/b.txt\n")))
    code = cli.main(["run", "-", "--task", "tb", "--new-task", "--owns", "src/b.txt", "--verify", "pass"])
    assert code == 0
    capsys.readouterr()
    assert cli.main(["result", "tb"]) == 0
    out = capsys.readouterr().out
    assert "finished" in out and "wrote b" in out and "crew land tb" in out
    assert cli.main(["result"]) == 0 and "tb" in capsys.readouterr().out


def test_cancel_clears_a_worker_whose_process_is_gone(repo, capsys):
    running = config.state_dir() / "running"
    running.mkdir(parents=True, exist_ok=True)
    (running / "gone.json").write_text(json.dumps({"task": "gone", "started": 0, "pid": 2 ** 22 + 12345,
                                                   "route": "r", "workdir": str(repo)}), encoding="utf-8")
    assert cli.main(["status"]) == 0
    assert "process gone" in capsys.readouterr().out
    assert cli.main(["cancel", "gone"]) == 0
    assert not (running / "gone.json").exists()
    assert cli.main(["result", "gone"]) == 0 and "cancelled" in capsys.readouterr().out
    assert cli.main(["cancel", "gone"]) == 2


def test_template_lists_and_prints(capsys):
    assert cli.main(["template"]) == 0
    names = capsys.readouterr().out.split()
    assert {"rules", "task", "task-review", "code-review", "adversarial-review", "stop-gate"} <= set(names)
    assert cli.main(["template", "nope"]) == 2


def test_process_identity_tells_a_reused_pid_apart():
    me = procs.identity(os.getpid())
    assert me is not None and procs.alive(os.getpid(), me)
    assert not procs.alive(os.getpid(), "some-other-start-time")
    assert procs.identity(2 ** 22 + 12345) is None


def test_a_verify_that_times_out_is_stopped_with_what_it_started(tmp_path):
    started = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        procs.run(f'"{sys.executable}" -c "import time; time.sleep(30)"', tmp_path, 1)
    assert time.time() - started < 20


def test_a_cancelled_run_stops_before_its_next_step(repo, server, providers_file):
    project = config.load_project(repo)
    boundary = agent.Boundary(repo, ["src/x.txt"], [])
    verify = agent.make_verify(project.verify["pass"], boundary, "main", project.checks, project.source_suffixes, 60)
    chain = review.providers.routes(config.load_providers(), "writer")
    server.replies += [[("read_file", {"path": "README.md"})]] * 5
    asked = []
    log = agent.Log(repo.parent / "c.jsonl")
    try:
        outcome = agent.run("task", boundary, chain, verify, log, agent.Settings(max_steps=5),
                            cancelled=lambda: asked.append(1) or len(asked) > 2)
    finally:
        log.close()
    assert outcome.status == "cancelled" and len(server.requests) == 2


def test_stdin_prompt_needs_a_name(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"x")))
    assert cli.main(["run", "-"]) == 2
    assert "needs --task or --name" in capsys.readouterr().err


@pytest.mark.parametrize("answer", ["**BLOCK:** broken", "```\nBLOCK: broken\n```", "Verdict: BLOCK - broken"])
def test_decorated_block_verdicts_still_block(repo, server, providers_file, answer):
    review.set_gate(repo, True)
    (repo / "src" / "a.rs").write_text("pub fn a() { 5 }\n", encoding="utf-8")
    server.replies += [answer]
    assert review.stop_hook({"cwd": str(repo)})["decision"] == "block"


def test_a_repository_with_no_commits_and_a_non_ascii_name(tmp_path):
    root = tmp_path / "fresh"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "café.txt").write_text("crème\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02")
    what = review.target(root, None, "working-tree")
    assert sorted(what.files) == ["blob.bin", "café.txt"]
    assert "+crème" in what.diff and "blob.bin (binary, not shown)" in what.diff


def test_untracked_files_stop_being_inlined_at_the_size_limit(repo, monkeypatch):
    monkeypatch.setattr(review, "MAX_DIFF", 2000)
    for n in range(10):
        (repo / f"big{n}.txt").write_text("x" * 900 + "\n", encoding="utf-8")
    what = review.target(repo, None, "working-tree")
    assert len(what.files) == 10 and "already at its size limit" in what.diff
    assert len(what.diff) < 5000


def test_versions_agree():
    import json as _json
    import pathlib
    import tomllib

    import crew
    here = pathlib.Path(__file__).resolve().parent.parent
    plugin = _json.loads((here / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = _json.loads((here / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    project = tomllib.loads((here / "pyproject.toml").read_text(encoding="utf-8"))
    versions = {crew.__version__, plugin["version"], market["plugins"][0]["version"], project["project"]["version"]}
    assert versions == {crew.__version__}
    assert f"## {crew.__version__}" in (here / "CHANGELOG.md").read_text(encoding="utf-8")


def test_cancel_kills_a_live_worker_that_does_not_stop_by_itself(repo, capsys):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        running = config.state_dir() / "running"
        running.mkdir(parents=True, exist_ok=True)
        (running / "live.json").write_text(json.dumps({"task": "live", "started": time.time(), "pid": child.pid,
                                                       "identity": procs.identity(child.pid), "route": "r"}),
                                           encoding="utf-8")
        assert cli.main(["cancel", "live", "--wait", "1"]) == 0
        assert child.wait(timeout=15) is not None
        assert not (running / "live.json").exists() and not (running / "live.cancel").exists()
    finally:
        if child.poll() is None:
            child.kill()


def test_session_start_says_what_setup_is_missing(crew_home, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{}")))
    assert cli.main(["hook", "session-start"]) == 0
    assert "/agent-crew:setup" in json.loads(capsys.readouterr().out)["systemMessage"]
    assert (crew_home / "bin" / "crew").exists()
    assert cli.main(["hook", "session-start"]) == 0  # the template exists now; models still unchosen
    assert "models chosen" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_session_start_is_quiet_when_ready(providers_file, capsys):
    assert cli.main(["hook", "session-start"]) == 0
    assert capsys.readouterr().out == ""


def test_config_init_writes_a_filled_file_for_a_detected_endpoint(crew_home, server, capsys):
    assert cli.main(["config", "init"]) == 0  # the template first
    assert cli.main(["config", "init", "--name", "local", "--base-url", server.url,
                     "--writer", "fast,mid", "--reviewer", "careful"]) == 0
    loaded = config.load_providers()
    assert [(m.id, m.roles) for m in loaded.models] == [("fast", ["writer"]), ("mid", ["writer"]),
                                                        ("careful", ["reviewer"])]
    assert loaded.providers["local"].keys() == [("no-key", "")]
    # A configured file is not replaced without --force.
    assert cli.main(["config", "init", "--base-url", server.url, "--writer", "x", "--reviewer", "y"]) == 2
    assert cli.main(["config", "init", "--base-url", server.url, "--writer", "x", "--reviewer", "y", "--force"]) == 0


def test_a_provider_without_keys_sends_no_authorization(crew_home, server):
    from crew import providers as providers_module
    crew_home.mkdir(parents=True, exist_ok=True)
    (crew_home / "providers.toml").write_text(
        f'[[provider]]\nname = "l"\nbase_url = "{server.url}"\napi_key_envs = []\n'
        '[[model]]\nid = "m"\nprovider = "l"\nroles = ["writer"]\n', encoding="utf-8")
    route = providers_module.routes(config.load_providers(), "writer")[0]
    server.replies += ["hello"]
    providers_module.complete(route, [{"role": "user", "content": "hi"}])
    assert server.requests[-1]["auth"] is None


def test_detect_reports_endpoints_and_their_models(crew_home, monkeypatch):
    from crew import setup
    monkeypatch.setattr(setup, "KNOWN_ENDPOINTS", [("down", "http://127.0.0.1:9/v1")])
    found = setup.detect()
    assert found == [{"name": "down", "base_url": "http://127.0.0.1:9/v1", "configured": False,
                      "status": "down", "models": []}]


def test_init_reads_the_repository(tmp_path, monkeypatch, capsys):
    root = tmp_path / "app"
    (root / "core" / "node_modules").mkdir(parents=True)
    (root / "core" / "node_modules" / "dep").mkdir()
    (root / "core" / "node_modules" / "dep" / "Cargo.toml").write_text("", encoding="utf-8")  # must be ignored
    (root / "crate").mkdir()
    (root / "crate" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (root / "package.json").write_text('{"scripts": {"lint": "eslint .", "test": "vitest"}}', encoding="utf-8")
    (root / ".venv").mkdir()
    git(root, "init", "-q", "-b", "trunk")
    assert cli.main(["init", "--root", str(root)]) == 0
    project = config.load_project(root)
    assert project.branch == "trunk"
    assert project.verify["rust"].startswith("cargo fmt --manifest-path crate/Cargo.toml")
    assert project.verify["js"] == "npm run -s lint && npm run -s test -- --run"
    assert project.shared == [".venv", "core/node_modules"]
    assert "rust-modules" in project.checks
    ignored = (root / ".agent-crew" / ".gitignore").read_text(encoding="utf-8").split()
    assert "prompts/" in ignored and "profile.json" in ignored


def test_session_start_creates_the_providers_file_and_tells_claude(crew_home, capsys):
    assert cli.main(["hook", "session-start"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (crew_home / "providers.toml").exists()
    assert "/agent-crew:setup" in out["systemMessage"]
    assert "agent-crew:setup" in out["hookSpecificOutput"]["additionalContext"]


def test_keys_can_live_in_the_providers_file(crew_home, server):
    from crew import providers as providers_module
    crew_home.mkdir(parents=True, exist_ok=True)
    (crew_home / "providers.toml").write_text(
        f'[[provider]]\nname = "r"\nbase_url = "{server.url}"\napi_keys = ["k-one", "", "k-two"]\n'
        '[[model]]\nid = "m"\nprovider = "r"\nroles = ["writer"]\n', encoding="utf-8")
    chain = providers_module.routes(config.load_providers(), "writer")
    assert [r.key_label for r in chain] == ["r.key1", "r.key3"]
    server.replies += ["hi"]
    providers_module.complete(chain[0], [{"role": "user", "content": "x"}])
    assert server.requests[-1]["auth"] == "Bearer k-one"


def test_an_empty_key_slot_is_reported(crew_home, capsys):
    crew_home.mkdir(parents=True, exist_ok=True)
    (crew_home / "providers.toml").write_text(
        '[[provider]]\nname = "r"\nbase_url = "http://127.0.0.1:9/v1"\napi_keys = [""]\n'
        '[[model]]\nid = "m"\nprovider = "r"\nroles = ["writer", "reviewer"]\n', encoding="utf-8")
    assert "paste the API key for r" in cli.setup_hint()
