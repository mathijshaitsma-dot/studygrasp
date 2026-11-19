import pytest
from pydantic import ValidationError

import core


def test_lookup_switches_model_after_short_deadline(monkeypatch):
    import time
    from types import SimpleNamespace
    calls = []
    def candidate(model, delay):
        def generate(*args, **kwargs):
            calls.append((model, kwargs["timeout_s"]))
            time.sleep(delay)
            return '{"slides": []}'
        return SimpleNamespace(provider=SimpleNamespace(name="test"), model=model, label=model, generate=generate)
    monkeypatch.setattr(core.ai_engine, "candidates", lambda **kwargs: [candidate("slow", .12), candidate("slow", 0), candidate("fast", 0)])
    monkeypatch.setattr(core.ai_engine, "candidate_available", lambda c: True)
    monkeypatch.setattr(core.ai_engine, "report_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr(core.ai_engine, "report_success", lambda *args, **kwargs: None)
    started = time.monotonic()
    result = core.generate_structured([], "locate", core.ExerciseLocateResult,
        candidate_mode="fast", attempt_timeout_s=.02, total_timeout_s=.08, max_attempts=3)
    assert result.slides == []
    assert [model for model, _ in calls] == ["slow", "fast"]
    assert time.monotonic() - started < .1


def test_lookup_stops_at_total_budget(monkeypatch):
    import time
    from types import SimpleNamespace
    def slow(*args, **kwargs):
        time.sleep(.15)
        return '{"slides": []}'
    candidates = [SimpleNamespace(provider=SimpleNamespace(name="test"), model=str(i), label=str(i), generate=slow) for i in range(10)]
    monkeypatch.setattr(core.ai_engine, "candidates", lambda **kwargs: candidates)
    monkeypatch.setattr(core.ai_engine, "candidate_available", lambda c: True)
    monkeypatch.setattr(core.ai_engine, "report_failure", lambda *args, **kwargs: None)
    started = time.monotonic()
    with pytest.raises(core.HTTPException):
        core.generate_structured([], "locate", core.ExerciseLocateResult,
            attempt_timeout_s=.02, total_timeout_s=.045, max_attempts=3)
    assert time.monotonic() - started < .12


def test_text_lookup_skips_unnecessary_source_images(monkeypatch):
    rendered = []
    monkeypatch.setattr(core, "ensure_slide_image", lambda *args: rendered.append(args))
    long_text = "Inhoudelijke uitleg " * 20
    core.build_exercise_source_visual_parts("u", ["source"], "theorie",
        picked_pages=[(1, 0, long_text), (1, 1, "Anatomische afbeelding")], sparse_only=True)
    assert rendered == [("source", 1, "ai")]


def test_persistent_text_cache_avoids_redownloading_original_pdf(monkeypatch):
    monkeypatch.setattr(core, "load_json", lambda path: None)
    monkeypatch.setattr(core.cache_store, "get_json", lambda ns, key:
        {"file_type": "pdf", "texts": ["Bewaarde college-inhoud"]} if ns == "document_text" else None)
    def no_download(*args):
        raise AssertionError("persistent text hit must not download the original PDF")
    monkeypatch.setattr(core, "get_document_info", no_download)
    monkeypatch.setattr(core, "save_json", lambda *args: None)
    assert core._document_texts_cached.__wrapped__("persistent-text-test") == ("pdf", ("Bewaarde college-inhoud",))


def test_hint_has_separate_no_answer_prompt():
    hint = core.build_exercise_help_system("Nederlands", "hint")
    answer = core.build_exercise_help_system("Nederlands", "answer")
    steps = core.build_exercise_help_system("Nederlands", "steps")
    assert "Do not give the final answer" in hint
    assert "Give ONLY the final answer" in answer
    assert "No introduction, numbered reasoning steps" in answer
    assert "Solve it COMPLETELY" in steps
    assert core.ExerciseHelpRequest(exercise_hash="h", mode="steps").mode == "steps"
    with pytest.raises(ValidationError):
        core.ExerciseHelpRequest(exercise_hash="h", mode="unsupported")


def test_cached_source_lookup_does_not_render_any_images(monkeypatch):
    from routers import exercise
    monkeypatch.setattr(exercise.auth, "require_user_id", lambda _: "u")
    monkeypatch.setattr(exercise, "ensure_document_exists", lambda *args: {})
    monkeypatch.setattr(exercise, "exercise_material_hashes", lambda *args: ["source"])
    monkeypatch.setattr(exercise, "quota_gate", lambda *args, **kwargs: None)
    monkeypatch.setattr(exercise.cache_store, "get_json", lambda *args: {"slides": [{"page_index": 23}]})
    def no_render(*args):
        raise AssertionError("cached lookups must not render exercise pages")
    monkeypatch.setattr(exercise, "_exercise_page_context", no_render)
    result = exercise.exercise_locate(core.ExerciseLocateRequest(exercise_hash="h"), None)
    assert result["cached"] is True
    assert result["slides"][0]["page_index"] == 23


def test_specific_question_search_excludes_unrelated_questions():
    query = core.exercise_search_query(
        "Hoe ontstaat myeline?", "Hoe ontstaat myeline? Beschrijf de lever.",
        "Voorbereiding HC 11,12; literatuur over lever en longen."
    )
    assert "lever" not in query
    assert "HC11" in query and "HC12" in query


def test_learning_objectives_and_external_tasks_are_not_questions():
    assert core.is_external_exercise_instruction("U kunt een geschikt radiologisch onderzoek kiezen.")
    assert core.is_external_exercise_instruction("Ga naar Brightspace en speel de Simulatie Game.")
    assert not core.is_external_exercise_instruction("Welke radiologische onderzoeken zijn hier geschikt?")


def test_corrupt_pdf_encoding_is_not_copied_into_question_prompt():
    assert "page image instead" in core.exercise_readable_text_layer("Een pati\x01\x10nt")
    assert "page image instead" in core.exercise_readable_text_layer("Een pati\ufffdnt")
    assert core.exercise_readable_text_layer("Een patiënt") == "Een patiënt"


def test_source_selection_preserves_cross_lecture_matches():
    records = [dict(doc_index=1, page_index=i, title="HC03 Anatomie", text="Anatomische positie")
               for i in range(20)]
    records.append(dict(doc_index=2, page_index=0, title="HC20 Spieren", text="Motorische eenheid axon spiervezels"))
    picked = core.pick_exercise_source_records(records, "Wat is een motorische eenheid? HC3")
    assert any(r["doc_index"] == 2 for r in picked)


def test_exercise_questions_are_page_aware():
    question = core.ExerciseQuestion(number="3b", text="Leg uit waarom.", page_index=2)
    assert question.page_index == 2
    prompt = core.build_exercise_parse_system("Nederlands")
    assert "page_index" in prompt
    assert "where it starts" in prompt


def test_exercise_requests_reject_negative_page_indexes():
    with pytest.raises(ValidationError):
        core.ExerciseLocateRequest(exercise_hash="abc", page_index=-1)
    with pytest.raises(ValidationError):
        core.ExerciseHelpRequest(exercise_hash="abc", page_index=-1)


def test_ranker_ignores_instruction_words_and_prefers_medical_phrase():
    records = [
        {"doc_index": 1, "page_index": 0, "title": "Algemene instructies",
         "text": "Beschrijf wat je ziet en verklaar waarom. Beantwoord alle vragen."},
        {"doc_index": 2, "page_index": 7, "title": "HC30 Hals",
         "text": "De parafaryngeale ruimte ligt lateraal van de farynx en bevat belangrijke structuren."},
    ]
    ranked = core.rank_exercise_source_records(
        records, "Beschrijf de begrenzingen van de parafaryngeale ruimte en verklaar de ligging."
    )
    assert ranked[0]["doc_index"] == 2
    assert ranked[0]["page_index"] == 7


def test_ranker_uses_title_and_coverage_not_raw_repetition():
    records = [
        {"doc_index": 1, "page_index": 0, "title": "Los materiaal",
         "text": "spier spier spier spier spier"},
        {"doc_index": 2, "page_index": 3, "title": "Bouw spierweefsel",
         "text": "Glad spierweefsel gebruikt actine en myosine; de latch state houdt spanning vast."},
    ]
    ranked = core.rank_exercise_source_records(
        records, "Hoe werkt de latch state bij contractie van glad spierweefsel?"
    )
    assert ranked[0]["doc_index"] == 2


def test_ranker_canonicalizes_explicit_lecture_code():
    records = [
        {"doc_index": 1, "page_index": 0, "title": "HC05 Bouwplan Circulatiestelsel.pdf",
         "text": "Introductie tot de vaatwand."},
        {"doc_index": 2, "page_index": 0, "title": "HC28 Buik en bekken.pdf",
         "text": "Arterien en venen in de buik."},
    ]
    ranked = core.rank_exercise_source_records(records, "Voorbereiding: HC 5. Vergelijk arterien en venen.")
    assert ranked[0]["doc_index"] == 1


def test_validate_located_slides_requires_real_source_quote(monkeypatch):
    monkeypatch.setattr(core, "load_meta", lambda _uid, _hash: {
        "total_pages": 2, "file_name": "HC20 Bouw spierweefsel.pdf"
    })
    monkeypatch.setattr(core, "get_document_texts", lambda _hash: ("pdf", [
        "De latch state houdt langdurig spanning vast met weinig ATP-verbruik.",
        "Algemene afsluiting van het college.",
    ]))
    monkeypatch.setattr(core, "slide_image_url", lambda h, p: f"/slides/{h}/{p}")
    result = core.ExerciseLocateResult(slides=[
        core.LocatedSlide(doc_index=1, page=1, why="Directe uitleg.",
                          source_quote="latch state houdt langdurig spanning vast", confidence="high"),
        core.LocatedSlide(doc_index=1, page=2, why="Verzonnen.",
                          source_quote="dit citaat bestaat helemaal niet", confidence="high"),
        core.LocatedSlide(doc_index=1, page=1, why="Dubbel.",
                          source_quote="latch state houdt langdurig spanning vast", confidence="medium"),
    ])
    slides = core.validate_located_slides("u", ["h"], result)
    assert len(slides) == 1
    assert slides[0]["page_index"] == 0
    assert slides[0]["file_name"] == "HC20 Bouw spierweefsel.pdf"


def test_validate_image_only_source_requires_attached_candidate(monkeypatch):
    monkeypatch.setattr(core, "load_meta", lambda _uid, _hash: {"total_pages": 1})
    monkeypatch.setattr(core, "get_document_texts", lambda _hash: ("pdf", [""]))
    monkeypatch.setattr(core, "slide_image_url", lambda h, p: f"/slides/{h}/{p}")
    result = core.ExerciseLocateResult(slides=[
        core.LocatedSlide(doc_index=1, page=1, why="Anatomisch schema.", source_quote="", confidence="high")
    ])
    assert core.validate_located_slides("u", ["h"], result) == []
    assert len(core.validate_located_slides("u", ["h"], result, {(1, 0)})) == 1
