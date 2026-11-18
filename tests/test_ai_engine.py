"""
Tests voor de multi-provider AI-laag (ai_engine.py) — het hart van de
kostenbeheersing en betrouwbaarheid. Geen echte providers: puur de logica die
bepaalt welke kandidaat wordt gekozen, hoe fouten worden geclassificeerd en hoe
cooldowns in/uit gaan.
"""
import types

import ai_engine


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


def test_project_model_daily_quota_cools_same_model_across_keys():
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

    assert not ai_engine.candidate_available(second_key)
    ai_engine.report_success(first_key)


def test_representative_error_prefers_capacity_over_last_invalid_fallback():
    quota = Exception("429 RESOURCE_EXHAUSTED daily quota")
    invalid = Exception("401 invalid api key")

    selected = ai_engine.representative_error([quota, invalid])

    assert selected is quota
    assert "capaciteit" in ai_engine.humanize_error(selected)


def test_openai_compat_accepts_plain_text_completion(monkeypatch):
    provider = ai_engine.OpenAICompatProvider(
        name="github", base_url="https://example.invalid", keys=["secret"], models=["model"],
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
        name="github", base_url="https://example.invalid", keys=["secret"], models=["model"],
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
