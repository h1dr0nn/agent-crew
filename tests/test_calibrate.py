import json

from crew import calibrate, cli, config
from tests.conftest import git


def _add_history(repo):
    (repo / "src" / "b.rs").write_text("pub fn b() -> u32 {\n    let x = 1;\n    let y = 2;\n    x + y\n}\n", encoding="utf-8")
    (repo / "src" / "lib.rs").write_text("pub mod a;\npub mod b;\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feat: add b")
    (repo / "README.md").write_text("# repo\n\nSection sign: §\n\nMore words here.\n", encoding="utf-8")
    git(repo, "commit", "-qam", "docs: more words")


def test_work_mix_and_probes_come_from_history(repo):
    _add_history(repo)
    kinds = ["pass", "rust", "docs"]
    mix = calibrate.work_mix(repo, kinds)
    assert set(mix) == {"rust", "docs"} and abs(sum(mix.values()) - 1) < 0.01
    probe = calibrate.probes(repo, "rust", kinds)[0]
    assert probe.subject == "feat: add b"
    code = [p for p, _, _ in probe.files]
    prompt = calibrate.probe_prompt(repo, probe, code, [], "rust")
    assert "pub fn b() -> u32 {" in prompt and "@@template rules" in prompt


def test_calibrate_measures_saves_and_orders_later_runs(repo, server, providers_file, monkeypatch, capsys):
    _add_history(repo)
    text = (repo / ".agent-crew" / "project.toml").read_text(encoding="utf-8")
    (repo / ".agent-crew" / "project.toml").write_text(text + 'rust = "python -c \\"print(1)\\""\n', encoding="utf-8")
    git(repo, "commit", "-qam", "verify rust")
    monkeypatch.chdir(repo)
    body = "pub fn b() -> u32 {\n    3\n}\n"
    # "fast" answers the question, then fails to finish; "campaign" answers, then does the task.
    server.replies += ['{"rank": ["rust"], "why": "fast"}'] + [[("read_file", {"path": "src/lib.rs"})]] * 30
    assert cli.main(["calibrate", "--kinds", "rust", "--models", "fast"]) == 0
    server.replies.clear()
    server.replies += ['{"rank": ["rust", "docs"], "why": "careful"}',
                       [("write_file", {"path": "src/b.rs", "content": body})],
                       [("replace", {"path": "src/lib.rs", "old": "pub mod a;\n", "new": "pub mod a;\npub mod b;\n"})],
                       [("verify", {})], [("finish", {"summary": "done"})]]
    assert cli.main(["calibrate", "--kinds", "rust", "--models", "campaign"]) == 0
    profile = json.loads((repo / ".agent-crew" / "profile.json").read_text(encoding="utf-8"))
    assert profile["models"]["campaign"]["kinds"]["rust"]["passed"] == 1
    assert profile["models"]["fast"]["kinds"]["rust"]["passed"] == 0  # kept from the first run
    assert profile["models"]["campaign"]["self"]["rank"] == ["rust"]  # docs is not a verify kind here
    assert profile["prefer"]["rust"] == ["campaign"]
    assert "calib" not in git(repo, "branch")
    capsys.readouterr()
    assert cli.main(["profile"]) == 0
    assert "rust: campaign" in capsys.readouterr().out
    # A later run of rust work tries campaign first, though fast has the better priority.
    server.replies.clear()
    server.replies += [[("finish", {"summary": "x"})]]
    (repo / "p.md").write_text("task", encoding="utf-8")
    cli.main(["run", "p.md", "--owns", "x.txt", "--verify", "rust", "--max-steps", "1"])
    assert server.requests[-1]["body"]["model"] == "campaign"


def test_dry_run_changes_nothing(repo, providers_file, monkeypatch, capsys):
    _add_history(repo)
    monkeypatch.chdir(repo)
    assert cli.main(["calibrate", "--dry-run"]) == 0
    assert "mix" in json.loads(capsys.readouterr().out)
    assert not (repo / ".agent-crew" / "profile.json").exists()


def test_a_probe_worktree_uses_todays_crew_settings(repo, providers_file):
    _add_history(repo)
    (repo / ".agent-crew" / "verify.sh").write_text("echo ok\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add a verify script after the probe commit")
    project = config.load_project(repo)
    probe = calibrate.probes(repo, "rust", list(project.verify) + ["rust"])[0]
    path = calibrate._prepare(project, "calib-settings", probe, ["src/b.rs", "src/lib.rs"])
    try:
        assert (path / ".agent-crew" / "verify.sh").exists()
        assert not (path / "src" / "b.rs").exists()
    finally:
        from crew import worktree
        worktree.remove(project, "calib-settings")
