import pytest

from crew import config, providers


def test_routes_order_by_priority_and_expand_every_key(providers_file):
    loaded = config.load_providers()
    chain = providers.routes(loaded, "writer")
    assert [(r.model.id, r.key_label) for r in chain] == [
        ("fast", "CREW_TEST_KEY_A"), ("fast", "CREW_TEST_KEY_B"),
        ("campaign", "CREW_TEST_KEY_A"), ("campaign", "CREW_TEST_KEY_B"),
    ]
    assert [r.model.id for r in providers.routes(loaded, "reviewer")] == ["campaign", "campaign"]


def test_exhaustion_is_per_key_and_allowance(providers_file):
    providers.mark_exhausted("CREW_TEST_KEY_A", "free", "resets at 2999-01-01T00:00:00")
    chain = providers.routes(config.load_providers(), "writer")
    labels = [(r.model.id, r.key_label) for r in chain]
    assert ("fast", "CREW_TEST_KEY_A") not in labels
    # The campaign allowance on the same key is untouched.
    assert ("campaign", "CREW_TEST_KEY_A") in labels


def test_an_expired_marker_is_cleared(providers_file):
    providers.mark_exhausted("CREW_TEST_KEY_A", "free", "resets at 2000-01-01T00:00:00")
    assert not providers.exhausted("CREW_TEST_KEY_A", "free")


def test_pinned_model_list_and_unknown_model(providers_file):
    loaded = config.load_providers()
    assert {r.model.id for r in providers.routes(loaded, "writer", "campaign")} == {"campaign"}
    with pytest.raises(config.ConfigError):
        providers.routes(loaded, "writer", "nope")


def test_complete_sends_the_key_and_records_latency(providers_file, server):
    server.replies.append([("finish", {"summary": "x"})])
    route = providers.routes(config.load_providers(), "writer")[0]
    reply = providers.complete(route, [{"role": "user", "content": "hi"}])
    assert reply["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "finish"
    assert server.requests[0]["auth"] == "Bearer key-a"
    assert "fast" in providers._latency()


def test_quota_is_recognised(providers_file, server):
    server.replies.append((429, '{"error":{"code":"free_tier_limit_reached","message":"used this period\'s free allowance"}}'))
    route = providers.routes(config.load_providers(), "writer")[0]
    with pytest.raises(providers.QuotaExhausted):
        providers.complete(route, [{"role": "user", "content": "hi"}])


def test_a_refused_key_fails_the_route(providers_file, server):
    server.replies.append((401, "bad key"))
    route = providers.routes(config.load_providers(), "writer")[0]
    with pytest.raises(providers.RouteFailed):
        providers.complete(route, [{"role": "user", "content": "hi"}], retries=1)


def test_missing_file_and_bad_model_reference(crew_home):
    with pytest.raises(config.ConfigError):
        config.load_providers()
    crew_home.mkdir(parents=True, exist_ok=True)
    (crew_home / "providers.toml").write_text('[[model]]\nid = "m"\nprovider = "ghost"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_providers()
