import json

from crew import cli, config, plugin_settings


def env(**values):
    return {f"CLAUDE_PLUGIN_OPTION_{name.upper()}": value for name, value in values.items()}


def test_choose_puts_fast_coders_first_and_a_reviewer_from_another_family():
    models = ["harbor/deepseek-v4.1-flash:free", "harbor/qwen3.8-flash:free", "harbor/mimo-v2.5:free",
              "text-embedding-3", "cc/claude-sonnet-5", "qwen-coder-32b"]
    writers, reviewers = plugin_settings.choose(models)
    assert writers[0] == "qwen-coder-32b"
    assert "text-embedding-3" not in writers + reviewers
    families = {plugin_settings.family(m) for m in writers}
    assert plugin_settings.family(reviewers[0]) not in families
    assert reviewers[0] == "cc/claude-sonnet-5"


def test_family_reads_the_name_after_any_prefix():
    assert plugin_settings.family("harbor/qwen3.8-flash:free") == "qwen"
    assert plugin_settings.family("cc/claude-sonnet-5") == "claude"


def test_no_endpoint_means_nothing_to_do(crew_home):
    assert plugin_settings.sync({}) is None
    assert not config.providers_path().exists()


def test_settings_with_models_write_a_managed_file_and_keep_the_key_out_of_it(crew_home, server):
    note = plugin_settings.sync(env(endpoint=server.url, api_key="sk-secret", writer_models="fast",
                                    reviewer_models="campaign"))
    assert "writers fast" in note and "reviewers campaign" in note and "automatically" not in note
    text = config.providers_path().read_text(encoding="utf-8")
    assert text.startswith(plugin_settings.MANAGED) and "sk-secret" not in text
    loaded = config.load_providers()
    assert [(m.id, m.roles) for m in loaded.models] == [("fast", ["writer"]), ("campaign", ["reviewer"])]
    assert loaded.providers["endpoint"].keys()[0][1] == "sk-secret"


def test_without_models_they_are_chosen_from_the_endpoint(crew_home, server):
    server.models = ["qwen3.8-flash", "deepseek-coder", "claude-sonnet-5", "whisper-1"]
    note = plugin_settings.sync(env(endpoint=server.url))
    assert "chosen automatically" in note
    loaded = config.load_providers()
    writers = [m.id for m in loaded.models if "writer" in m.roles]
    reviewers = [m.id for m in loaded.models if "reviewer" in m.roles]
    assert writers[0] == "deepseek-coder" and "whisper-1" not in writers + reviewers
    assert reviewers == ["claude-sonnet-5"]
    assert loaded.providers["endpoint"].keys() == [("no-key", "")]


def test_unchanged_settings_do_nothing_and_changed_ones_rewrite(crew_home, server):
    settings = env(endpoint=server.url, writer_models="fast", reviewer_models="campaign")
    assert plugin_settings.sync(settings) is not None
    assert plugin_settings.sync(settings) is None
    settings["CLAUDE_PLUGIN_OPTION_WRITER_MODELS"] = "campaign"
    assert "writers campaign" in plugin_settings.sync(settings)


def test_a_hand_written_file_wins_and_says_so(crew_home, providers_file, server):
    before = providers_file.read_text(encoding="utf-8")
    note = plugin_settings.sync(env(endpoint=server.url, writer_models="x", reviewer_models="y"))
    assert "written by hand" in note
    assert providers_file.read_text(encoding="utf-8") == before


def test_an_endpoint_that_does_not_answer_is_reported(crew_home):
    note = plugin_settings.sync(env(endpoint="http://127.0.0.1:9/v1"))
    assert "did not answer" in note
    assert not config.providers_path().exists()


def test_session_start_applies_the_settings(crew_home, server, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_ENDPOINT", server.url)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_WRITER_MODELS", "fast")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_REVIEWER_MODELS", "campaign")
    assert cli.main(["hook", "session-start"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "set up from the plugin settings" in out["systemMessage"]
    assert "hookSpecificOutput" not in out  # nothing left for Claude to set up
