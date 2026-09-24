import pytest

from crew import cli, config, plugin_settings, worktree
from tests.conftest import git


def write_providers(path, model):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'[[provider]]\nname = "p"\nbase_url = "http://127.0.0.1:9/v1"\napi_keys = []\n'
                    f'[[model]]\nid = "{model}"\nprovider = "p"\nroles = ["writer", "reviewer"]\n', encoding="utf-8")


def test_the_projects_file_wins_over_the_shared_one(repo, crew_home, monkeypatch):
    write_providers(crew_home / "providers.toml", "shared")
    monkeypatch.chdir(repo)
    assert config.providers_path() == crew_home / "providers.toml"
    write_providers(repo / ".agent-crew" / "providers.toml", "local")
    assert config.providers_path() == repo / ".agent-crew" / "providers.toml"
    assert config.load_providers().models[0].id == "local"


def test_a_task_worktree_finds_its_projects_file(repo, crew_home, monkeypatch):
    write_providers(repo / ".agent-crew" / "providers.toml", "local")
    path = worktree.create(config.load_project(repo), "scope-task")
    try:
        assert not (path / ".agent-crew" / "providers.toml").exists()  # git-ignored, so not in the worktree
        monkeypatch.chdir(path)
        assert config.providers_path().resolve() == (repo / ".agent-crew" / "providers.toml").resolve()
    finally:
        worktree.remove(config.load_project(repo), "scope-task")


def test_the_crew_home_is_never_taken_for_a_project(tmp_path, crew_home, monkeypatch):
    # ~/.agent-crew is the crew home; a folder under the home directory must not
    # read the shared file as if it were a project's.
    home = tmp_path / "user"
    (home / ".agent-crew").mkdir(parents=True)
    write_providers(home / ".agent-crew" / "providers.toml", "shared")
    work = home / "somewhere"
    work.mkdir()
    monkeypatch.chdir(work)
    assert config.project_providers_path() is None


def test_init_in_a_project_writes_there_and_keeps_it_out_of_git(repo, crew_home, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["config", "init"]) == 0
    assert (repo / ".agent-crew" / "providers.toml").exists()
    assert not (crew_home / "providers.toml").exists()
    assert "providers.toml" in (repo / ".agent-crew" / ".gitignore").read_text(encoding="utf-8").split()
    assert git(repo, "status", "--porcelain", "--", ".agent-crew/providers.toml").strip() == ""
    capsys.readouterr()
    assert cli.main(["config", "path"]) == 0
    assert "(project)" in capsys.readouterr().out


def test_init_can_choose_the_shared_file(repo, crew_home, monkeypatch):
    monkeypatch.chdir(repo)
    assert cli.main(["config", "init", "--scope", "global"]) == 0
    assert (crew_home / "providers.toml").exists()
    assert not (repo / ".agent-crew" / "providers.toml").exists()


def test_init_refuses_a_providers_file_that_git_tracks(repo, crew_home, monkeypatch):
    write_providers(repo / ".agent-crew" / "providers.toml", "tracked")
    git(repo, "add", "-f", ".agent-crew/providers.toml")
    git(repo, "commit", "-qm", "oops")
    monkeypatch.chdir(repo)
    assert cli.main(["config", "init", "--scope", "project"]) == 2


def test_plugin_settings_leave_a_projects_file_alone(repo, crew_home, server, monkeypatch):
    write_providers(repo / ".agent-crew" / "providers.toml", "local")
    monkeypatch.chdir(repo)
    note = plugin_settings.sync({"CLAUDE_PLUGIN_OPTION_ENDPOINT": server.url,
                                 "CLAUDE_PLUGIN_OPTION_WRITER_MODELS": "fast",
                                 "CLAUDE_PLUGIN_OPTION_REVIEWER_MODELS": "campaign"})
    assert "this project's" in note
    assert not (crew_home / "providers.toml").exists()


@pytest.mark.parametrize("scope", ["project"])
def test_init_outside_a_project_with_the_project_scope_says_why(tmp_path, crew_home, monkeypatch, scope, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["config", "init", "--scope", scope]) == 2
    assert "crew init" in capsys.readouterr().err
