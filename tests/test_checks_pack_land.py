import pathlib

import pytest

from crew import checks, config, land, pack, worktree
from tests.conftest import git


def test_text_damage_counts_only_what_the_change_added(repo):
    (repo / "README.md").write_text("# repo\n\nSection sign: Â§\n", encoding="utf-8")
    problems = checks.text_damage(repo, "main", [".md"])
    assert problems and "mojibake" in problems[0]
    (repo / "README.md").write_text("# repo\n\nSection sign: §\n\nmore\n", encoding="utf-8")
    assert checks.text_damage(repo, "main", [".md"]) == []


def test_control_characters_and_bom(repo):
    (repo / "src" / "a.rs").write_text("\ufeffpub fn a() {} // \x07ctor\n", encoding="utf-8")
    problem = checks.text_damage(repo, "main", [".rs"])[0]
    assert "control" in problem and "bom" in problem


def test_strays(repo):
    (repo / "debug.txt").write_text("x", encoding="utf-8")
    (repo / "src" / "owned.rs").write_text("x", encoding="utf-8")
    assert checks.strays(repo, lambda p: p.startswith("src/")) == ["debug.txt"]


def test_build_artefacts_and_configured_globs_are_not_strays(repo):
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "calc.cpython-313.pyc").write_bytes(b"x")
    (repo / "out.log").write_text("x", encoding="utf-8")
    assert checks.strays(repo, lambda p: False) == ["out.log"]
    assert checks.strays(repo, lambda p: False, ["*.log"]) == []


def test_an_undeclared_rust_module_is_reported(repo):
    (repo / "src" / "b.rs").write_text("pub fn b() {}\n", encoding="utf-8")
    assert "never compiled" in checks.unreferenced_rust_modules(repo, "main")[0]
    (repo / "src" / "lib.rs").write_text("pub mod a;\npub mod b;\n", encoding="utf-8")
    assert checks.unreferenced_rust_modules(repo, "main") == []


def test_pack_inlines_ranges_greps_and_notes_missing_files(repo):
    prompt = "Task\n@@include src/lib.rs\n@@include README.md#L3-L3\n@@grep src/a.rs fn\n@@include src/missing.rs\n"
    packed = pack.pack(prompt, repo)
    assert "pub mod a;" in packed
    assert "Section sign" in packed and "# repo" not in packed.split("README.md lines 3-3")[1].split("```")[1]
    assert "pub fn a()" in packed
    assert "does not exist yet" in packed


def test_pack_diff(repo):
    (repo / "src" / "a.rs").write_text("pub fn a() { changed() }\n", encoding="utf-8")
    assert "changed()" in pack.pack("@@diff main src/a.rs", repo)


def test_task_worktree_then_land_as_one_commit(repo):
    project = config.load_project(repo)
    path = worktree.create(project, "t1")
    assert (path / "README.md").exists()
    (path / "src" / "b.rs").write_text("pub fn b() {}\n", encoding="utf-8")
    (path / "src" / "lib.rs").write_text("pub mod a;\npub mod b;\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "wip one")
    (path / "src" / "a.rs").write_text("pub fn a() { 1; }\n", encoding="utf-8")  # uncommitted, still landed
    head = land.land(project, "t1", "feat: add b\n")
    assert "feat: add b" in head
    assert git(repo, "rev-list", "--count", "HEAD").strip() == "2"
    assert (repo / "src" / "b.rs").exists() and "1;" in (repo / "src" / "a.rs").read_text()
    assert not path.exists()
    assert "crew/t1" not in git(repo, "branch")


def test_land_refuses_a_dirty_checkout_and_an_empty_task(repo):
    project = config.load_project(repo)
    worktree.create(project, "t2")
    with pytest.raises(land.LandError, match="changed nothing"):
        land.land(project, "t2", "empty")
    (repo / "notes.txt").write_text("untracked is fine", encoding="utf-8")
    (pathlib.Path(worktree.info("t2")["path"]) / "new.txt").write_text("x", encoding="utf-8")
    (repo / "README.md").write_text("dirty", encoding="utf-8")
    with pytest.raises(land.LandError, match="uncommitted"):
        land.land(project, "t2", "x")
    git(repo, "checkout", "README.md")
    assert "feat" in land.land(project, "t2", "feat: new\n")


def test_shared_directories_are_linked_and_survive_removal(repo, tmp_path):
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "keep.txt").write_text("keep", encoding="utf-8")
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore")
    text = (repo / ".agent-crew" / "project.toml").read_text(encoding="utf-8").replace("shared = []", 'shared = ["node_modules"]')
    (repo / ".agent-crew" / "project.toml").write_text(text, encoding="utf-8")
    git(repo, "commit", "-qam", "share")
    project = config.load_project(repo)
    path = worktree.create(project, "t3")
    assert (path / "node_modules" / "keep.txt").read_text() == "keep"
    worktree.remove(project, "t3")
    assert (repo / "node_modules" / "keep.txt").read_text() == "keep"


def test_pack_inlines_templates():
    packed = pack.pack("@@template rules\nTask body", pathlib.Path("."))
    assert "Everything you need is in this prompt" in packed and "Task body" in packed
    with pytest.raises(config.ConfigError, match="known"):
        pack.template("nope")
