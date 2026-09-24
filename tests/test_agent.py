import json
import pathlib

from crew import agent, config, providers


def _run(repo, server, owns, verify_kind="pass", max_steps=10):
    project = config.load_project(repo)
    settings = agent.Settings(max_steps=max_steps, stall_steps=6)
    boundary = agent.Boundary(repo, owns, [])
    verify = agent.make_verify(project.verify[verify_kind], boundary, "main", project.checks, project.source_suffixes, 60)
    chain = providers.routes(config.load_providers(), "writer")
    log = agent.Log(repo.parent / "events.jsonl")
    try:
        return agent.run("do the task", boundary, chain, verify, log, settings)
    finally:
        log.close()


def test_write_verify_finish(repo, server, providers_file):
    server.replies += [
        [("write_file", {"path": "src/new.txt", "content": "hello\n"})],
        [("verify", {})],
        [("finish", {"summary": "wrote it"})],
    ]
    outcome = _run(repo, server, ["src/new.txt"])
    assert outcome.status == "finished"
    assert (repo / "src" / "new.txt").read_text() == "hello\n"


def test_writes_outside_the_boundary_are_refused(repo, server, providers_file):
    server.replies += [
        [("write_file", {"path": "README.md", "content": "gone\n"})],
        [("write_file", {"path": "../escape.txt", "content": "x"})],
    ]
    _run(repo, server, ["src/new.txt"], max_steps=3)
    assert "Section sign" in (repo / "README.md").read_text(encoding="utf-8")
    assert not (repo.parent / "escape.txt").exists()
    tool_results = [m for r in server.requests for m in r["body"]["messages"] if m.get("role") == "tool"]
    assert any("not a file this task owns" in m["content"] for m in tool_results)
    assert any("outside the worktree" in m["content"] for m in tool_results)


def test_finish_is_refused_before_verify_passes(repo, server, providers_file):
    server.replies += [
        [("write_file", {"path": "src/new.txt", "content": "x\n"})],
        [("finish", {"summary": "premature"})],
        [("verify", {})],
        [("finish", {"summary": "now"})],
    ]
    outcome = _run(repo, server, ["src/new.txt"])
    assert outcome.status == "finished"
    assert outcome.summary == "now"


def test_a_failing_verify_never_finishes(repo, server, providers_file):
    server.replies += [[("write_file", {"path": "src/new.txt", "content": "x\n"})]] + [[("verify", {}), ("finish", {"summary": "?"})]] * 8
    outcome = _run(repo, server, ["src/new.txt"], verify_kind="fail", max_steps=8)
    assert outcome.status in ("no_progress", "max_steps")
    assert "Final verify: FAIL" in outcome.summary


def test_a_stray_file_blocks_finish(repo, server, providers_file):
    (repo / "debug.txt").write_text("left behind", encoding="utf-8")
    server.replies += [[("verify", {})], [("finish", {"summary": "x"})]]
    outcome = _run(repo, server, ["src/new.txt"], max_steps=3)
    assert outcome.status != "finished"


def test_quota_mid_run_fails_over_and_keeps_the_conversation(repo, server, providers_file):
    server.replies += [
        [("write_file", {"path": "src/new.txt", "content": "x\n"})],
        (429, '{"error":{"message":"You have used this period\'s free allowance. Resets at 2999-01-01T00:00:00"}}'),
        [("verify", {})],
        [("finish", {"summary": "done after failover"})],
    ]
    outcome = _run(repo, server, ["src/new.txt"])
    assert outcome.status == "finished"
    assert providers.exhausted("CREW_TEST_KEY_A", "free")
    # The request after the failover still carries the earlier tool result.
    later = server.requests[-2]
    assert later["auth"] == "Bearer key-b"
    assert any(m.get("role") == "tool" and "wrote src/new.txt" in m["content"] for m in later["body"]["messages"])


def test_compaction_elides_old_file_contents():
    settings = agent.Settings(keep_calls=1, keep_tool_results=1)
    big = "line\n" * 100
    messages = [{"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "write_file", "arguments": json.dumps({"path": "a", "content": big})}}]},
                {"role": "tool", "tool_call_id": "1", "content": "r" * 1000},
                {"role": "assistant", "tool_calls": [{"id": "2", "function": {"name": "verify", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "2", "content": "ok"}]
    agent.compact(messages, settings)
    first = json.loads(messages[0]["tool_calls"][0]["function"]["arguments"])
    assert "elided" in first["content"]
    assert "elided" in messages[1]["content"]


def test_search_stays_inside_the_worktree(repo):
    boundary = agent.Boundary(repo, [], [])
    output, failed = agent.run_tool("search", {"pattern": "x", "path": str(pathlib.Path(repo).parent)}, boundary, None, agent.Settings())
    assert failed and "outside the worktree" in output
