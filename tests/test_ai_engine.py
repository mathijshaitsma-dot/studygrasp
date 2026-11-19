"""
Tests voor de multi-provider AI-laag (ai_engine.py) — het hart van de
kostenbeheersing en betrouwbaarheid. Geen echte providers: puur de logica die
bepaalt welke kandidaat wordt gekozen, hoe fouten worden geclassificeerd en hoe
cooldowns in/uit gaan.
"""
import types

import ai_engine


def test_short_lookup_passes_timeout_and_disables_sdk_retries(monkeypatch):
    seen = {}
    def generate_content(**kwargs):
        seen.update(kwargs)
        return types.SimpleNamespace(text='{"slides": []}')
    provider = ai_engine.GeminiProvider(["fake"], ["gemini-2.5-flash-lite"])
    monkeypatch.setattr(provider, "_client", lambda key: types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate_content)))
    provider.generate("fake", "gemini-2.5-flash-lite", [], "locate", .3, timeout_s=8)
    assert seen["config"].http_options.timeout == 10000
    assert seen["config"].http_options.retry_options.attempts == 1


class _FakeResponse:
    def __init__(self, *, content_type, text="", json_data=None, chunks=None):
        self.headers = {"content-type": content_type}
        self.text = text
        self._json_data = json_data
        self._chunks = chunks or []
        self.closed = False

    def json(self):
        if self._json_data is None:
            raise ValueError("not json")
        return self._json_data

    def iter_content(self, **_kwargs):
        yield from self._chunks

    def iter_lines(self, **_kwargs):
        return iter(())

    def close(self):
        self.closed = True


def test_classify_error_picks_right_cooldown():
    # Daglimiet => lange cooldown (uren), minuutlimiet => korte.
    day_s, day_reason = ai_engine._classify_error(Exception("RESOURCE_EXHAUSTED: quota PerDay exceeded"))
    min_s, _ = ai_engine._classify_error(Exception("429 rate limit"))
    auth_s, auth_reason = ai_engine._classify_error(Exception("401 invalid api key"))
    gone_s, _ = ai_engine._classify_error(Exception("404 model does not exist"))

    assert day_s >= 3600 and "dag" in day_reason
    assert min_s < 600            # minuutlimiet komt snel terug
    assert auth_s >= 3600 and "key" in auth_reason
    assert gone_s >= 24 * 3600    # dood model: pas morgen weer proberen


def test_extract_json_from_fences_and_noise():
    assert ai_engine.extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert ai_engine.extract_json('Zeker! Hier is het:\n{"a": 1}\nKlaar.') == '{"a": 1}'
    assert ai_engine.extract_json('{"a": 1}') == '{"a": 1}'
    # Geen JSON => veilige lege-object-fallback, geen crash.
    assert ai_engine.extract_json("helemaal geen json") == "helemaal geen json"


def test_quality_rank_prefers_stronger_models():
    r = ai_engine._quality_rank
    # Lagere rank = beter. Gemini 3 boven 2.5-flash boven flash-lite.
    assert r("gemini-3-flash-preview") < r("gemini-2.5-flash")
    assert r("gemini-2.5-flash") < r("gemini-2.5-flash-lite")
    assert r("een-onbekend-model") == 65  # onbekend => middenveld


def test_interactive_rank_prefers_low_latency_vision_models():
    r = ai_engine._interactive_rank

    assert r("gemini-2.5-flash-lite") < r("gemini-2.5-flash")
    assert r("gemini-2.5-flash") < r("gemini-3-flash-preview")
    assert r("gemini-3-flash-preview") < r("mistral-small-latest")


def test_responsive_rank_keeps_strong_gemini_first_but_avoids_slow_external_fallbacks():
    r = ai_engine._responsive_rank

    assert r("gemini-3-flash-preview") < r("gemini-2.5-flash")
    assert r("gemini-2.5-flash") < r("gemini-2.5-flash-lite")
    assert r("gemini-2.5-flash-lite") < r("openai/gpt-4.1")


def test_cooldown_set_and_cleared():
    cand = types.SimpleNamespace(
        label="unit-test:model#key1", provider=types.SimpleNamespace(name="unit-test"), key_index=0,
    )
    assert ai_engine._cooldown_remaining(cand.label) == 0

    ai_engine.report_failure(cand, Exception("429 rate limit"))
    assert ai_engine._cooldown_remaining(cand.label) > 0  # nu afgekoeld => wordt overgeslagen

    ai_engine.report_success(cand)
    assert ai_engine._cooldown_remaining(cand.label) == 0  # weer beschikbaar


def test_daily_quota_only_cools_the_exhausted_model():
    provider = types.SimpleNamespace(name="gemini")
    first_model = types.SimpleNamespace(
        label="gemini:model-a#key1", provider=provider, key_index=0,
    )
    same_key_other_model = types.SimpleNamespace(
        label="gemini:model-b#key1", provider=provider, key_index=0,
    )

    ai_engine.report_failure(first_model, Exception("RESOURCE_EXHAUSTED: quota PerDay exceeded"))

    assert ai_engine._cooldown_remaining(first_model.label) > 0
    assert ai_engine._cooldown_remaining(ai_engine._key_cooldown_label(same_key_other_model)) == 0
    assert ai_engine.candidate_available(same_key_other_model)
    ai_engine.report_success(first_model)


def test_invalid_key_cools_same_key_across_models():
    provider = types.SimpleNamespace(name="gemini")
    first_model = types.SimpleNamespace(
        label="gemini:model-a#key1", provider=provider, key_index=0,
    )
    same_key_other_model = types.SimpleNamespace(
        label="gemini:model-b#key1", provider=provider, key_index=0,
    )

    ai_engine.report_failure(first_model, Exception("401 invalid api key"))

    assert not ai_engine.candidate_available(same_key_other_model)
    ai_engine.report_success(first_model)


def test_daily_quota_on_one_key_keeps_other_key_available():
    provider = types.SimpleNamespace(name="gemini")
    first_key = types.SimpleNamespace(
        label="gemini:model-a#key1", provider=provider, model="model-a", key_index=0,
    )
    second_key = types.SimpleNamespace(
        label="gemini:model-a#key2", provider=provider, model="model-a", key_index=1,
    )

    ai_engine.report_failure(
        first_key,
        Exception("RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel exceeded"),
    )

    assert ai_engine.candidate_available(second_key)
    ai_engine.report_success(first_key)


def test_configured_gemini_models_always_adds_fast_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_MODELS", "gemini-3-flash-preview")
    monkeypatch.delenv("GEMINI_FALLBACK_MODELS", raising=False)

    assert ai_engine._configured_gemini_models() == [
        "gemini-3-flash-preview", "gemini-2.5-flash-lite",
    ]


def test_equivalent_gemini_keys_rotate_between_requests():
    provider = types.SimpleNamespace(name="gemini")
    candidates = [
        ai_engine.Candidate(provider, "gemini-2.5-flash-lite", f"key-{index}", index, 3)
        for index in range(3)
    ]
    ai_engine._rotation_cursors.clear()

    first = ai_engine._rotate_equivalent_keys(candidates)
    second = ai_engine._rotate_equivalent_keys(candidates)
    third = ai_engine._rotate_equivalent_keys(candidates)

    assert [first[0].key, second[0].key, third[0].key] == ["key-0", "key-1", "key-2"]


def test_default_provider_order_no_longer_uses_retired_github_models(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)

    assert ai_engine._provider_order() == ["gemini", "groq", "openrouter", "mistral"]


def test_gemini_default_thinking_is_latency_optimized(monkeypatch):
    monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
    monkeypatch.delenv("GEMINI_25_THINKING_BUDGET", raising=False)

    gemini3 = ai_engine.GeminiProvider._thinking_config("gemini-3-flash-preview")
    gemini25 = ai_engine.GeminiProvider._thinking_config("gemini-2.5-flash")

    assert str(gemini3.thinking_level).lower().endswith("minimal")
    assert gemini25.thinking_budget == 0


def test_representative_error_prefers_capacity_over_last_invalid_fallback():
    quota = Exception("429 RESOURCE_EXHAUSTED daily quota")
    invalid = Exception("401 invalid api key")

    selected = ai_engine.representative_error([quota, invalid])

    assert selected is quota
    assert "capaciteit" in ai_engine.humanize_error(selected)


def test_openai_compat_accepts_plain_text_completion(monkeypatch):
    provider = ai_engine.OpenAICompatProvider(
        name="plain-provider", base_url="https://example.invalid", keys=["secret"], models=["model"],
    )
    response = _FakeResponse(content_type="text/plain", text="Een geldige uitleg.\r\n")
    monkeypatch.setattr(provider, "_post", lambda *_args, **_kwargs: response)

    result = provider.generate(
        "secret", "model", [ai_engine.Message(role="user", parts=[ai_engine.text_part("Vraag")])],
        "Systeem", 0.2,
    )

    assert result == "Een geldige uitleg."


def test_openai_compat_streams_plain_text_chunks(monkeypatch):
    provider = ai_engine.OpenAICompatProvider(
        name="plain-provider", base_url="https://example.invalid", keys=["secret"], models=["model"],
    )
    response = _FakeResponse(
        content_type="text/plain", chunks=["Eerste regel\n", "Tweede regel"],
    )
    monkeypatch.setattr(provider, "_post", lambda *_args, **_kwargs: response)

    result = "".join(provider.stream(
        "secret", "model", [ai_engine.Message(role="user", parts=[ai_engine.text_part("Vraag")])],
        "Systeem", 0.2,
    ))

    assert result == "Eerste regel\nTweede regel"
    assert response.closed is True


def test_openai_compat_still_reads_json_completion(monkeypatch):
    provider = ai_engine.OpenAICompatProvider(
        name="mistral", base_url="https://example.invalid", keys=["secret"], models=["model"],
    )
    response = _FakeResponse(
        content_type="application/json",
        text='{"choices":[{"message":{"content":"JSON-uitleg"}}]}',
        json_data={"choices": [{"message": {"content": "JSON-uitleg"}}]},
    )
    monkeypatch.setattr(provider, "_post", lambda *_args, **_kwargs: response)

    result = provider.generate(
        "secret", "model", [ai_engine.Message(role="user", parts=[ai_engine.text_part("Vraag")])],
        "Systeem", 0.2,
    )

    assert result == "JSON-uitleg"
