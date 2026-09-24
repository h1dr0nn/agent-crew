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


def test_replies_with_a_trailing_done_or_as_events_are_read():
    single = '{"choices":[{"message":{"role":"assistant","content":"ok"}}],"usage":{}}data: [DONE]\n\n'
    assert providers.parse_reply(single)["choices"][0]["message"]["content"] == "ok"
    events = "\n".join([
        'data: {"choices":[{"delta":{"role":"assistant","content":"he"}}]}',
        'data: {"choices":[{"delta":{"content":"llo","tool_calls":[{"index":0,"id":"c1","function":{"name":"fin","arguments":"{\\"a\\""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"ish","arguments":":1}"}}]}}],"usage":{"prompt_tokens":3}}',
        "data: [DONE]"])
    reply = providers.parse_reply(events)
    message = reply["choices"][0]["message"]
    assert message["content"] == "hello" and reply["usage"] == {"prompt_tokens": 3}
    assert message["tool_calls"][0]["function"] == {"name": "finish", "arguments": '{"a":1}'}


def test_a_quota_error_wrapped_in_a_503_is_an_exhausted_allowance(providers_file, server):
    body = ('{"error":{"message":"[429]: You\'ve used this period\'s free allowance. Your next rolling 7-day '
            'period starts at 2999-09-30T11:31:07.931174+00:00.","code":"free_tier_limit_reached"}}')
    server.replies += [(503, body)]
    route = providers.routes(config.load_providers(), "writer")[0]
    with pytest.raises(providers.QuotaExhausted):
        providers.complete(route, [{"role": "user", "content": "x"}], retries=3)
    assert len(server.requests) == 1  # not retried as if it were transient


def test_a_plain_404_is_retried_and_a_json_404_is_not(providers_file, server, monkeypatch):
    monkeypatch.setattr(providers.time, "sleep", lambda seconds: None)
    route = providers.routes(config.load_providers(), "writer")[0]
    server.replies += [(404, "404 page not found\n"), "fine"]
    assert providers.complete(route, [{"role": "user", "content": "x"}])["choices"][0]["message"]["content"] == "fine"
    server.replies += [(404, '{"error":{"message":"model not found"}}')]
    with pytest.raises(providers.RouteFailed, match="refused"):
        providers.complete(route, [{"role": "user", "content": "x"}])
