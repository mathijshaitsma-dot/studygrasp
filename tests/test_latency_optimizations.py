"""Regressies voor snelheidswinst zonder minder bronmateriaal of beeldkwaliteit."""

import json
import time

import core
import routers.media as media
from core import ChatTurn, ExplainRequest
from routers.media import SmartAnswerResult


def test_same_slide_vision_render_is_reused_from_memory(tmp_path, monkeypatch):
    image = tmp_path / "hash" / "ai" / "page_0.jpg"
    calls = 0

    def fake_ensure(*_args):
        nonlocal calls
        calls += 1
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"exact-same-high-resolution-jpeg")
        return image

    monkeypatch.setattr(core, "ensure_slide_image", fake_ensure)
    monkeypatch.setattr(core, "AI_IMAGE_MEMORY_CACHE_MB", 1)
    with core._ai_image_memory_lock:
        core._ai_image_memory.clear()
        core._ai_image_memory_bytes = 0

    first = core.cached_slide_image_part("hash", 0)
    second = core.cached_slide_image_part("hash", 0)

    assert calls == 1
    assert first.image_bytes == second.image_bytes == b"exact-same-high-resolution-jpeg"


def test_generation_age_tracks_existing_prefetch(monkeypatch):
    key = "inflight-latency-test"
    event, claimed = core.claim_generation(key)
    try:
        assert claimed is True
        monkeypatch.setitem(core._inflight_started, key, core.time.monotonic() - 5.5)
        same_event, second_claimed = core.claim_generation(key)

        assert same_event is event
        assert second_claimed is False
        assert core.generation_age(key) >= 5.5
    finally:
        core.release_generation(key)


def test_follow_up_cache_key_preserves_account_and_full_context():
    req = ExplainRequest(
        file_hash="abc", page_index=4, question="Waarom gebeurt dit?",
        history=[ChatTurn(role="assistant", content="Omdat receptor X wordt geactiveerd.")],
    )

    key = core.follow_up_cache_key_for("student-a", req)

    assert key == core.follow_up_cache_key_for("student-a", req)
    assert key != core.follow_up_cache_key_for("student-b", req)
    assert key != core.follow_up_cache_key_for("student-a", req.model_copy(update={"question": "Hoe gebeurt dit?"}))


def test_prepare_explain_inputs_still_builds_full_quality_context(monkeypatch):
    monkeypatch.setattr(core, "ensure_document_exists", lambda *_: {"file_name": "College.pdf"})
    monkeypatch.setattr(core, "get_document_texts", lambda *_: ("pdf", ["Receptor activeert signaalroute"]))
    monkeypatch.setattr(core, "cached_slide_image_part", lambda *_: core.ai_engine.image_part_bytes(b"jpeg", "image/jpeg"))

    prepared = core.prepare_explain_inputs("student", ExplainRequest(
        file_hash="abc", page_index=0, question="Waarom?",
    ))

    assert prepared["used_vision"] is True
    assert prepared["contents"][0].parts[0].image_bytes == b"jpeg"
    assert "FOLLOW-UP QUESTION OVERRIDE" in prepared["system_instruction"]


def test_slide_stream_rejects_internal_safety_stub_and_uses_fallback(monkeypatch):
    class Candidate:
        def __init__(self, label, output):
            self.label = label
            self.output = output

        def stream(self, *_args):
            yield self.output

    bad = Candidate("fast-but-invalid", "User Safety: safe")
    good_text = "Linkage disequilibrium betekent dat varianten vaker samen worden overgeërfd dan toeval voorspelt."
    good = Candidate("valid-fallback", good_text)
    monkeypatch.setattr(core.ai_engine, "candidates", lambda interactive=False: [bad, good])
    monkeypatch.setattr(core.ai_engine, "candidate_available", lambda _candidate: True)
    monkeypatch.setattr(core.ai_engine, "report_failure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(core.ai_engine, "report_success", lambda *_args, **_kwargs: None)

    first_delta_calls = []
    events = [json.loads(line.removeprefix("data: ")) for line in core.stream_markdown(
        [], "prompt", None, interactive=True,
        on_first_delta=lambda: first_delta_calls.append("start-prefetch"),
    )]
    rendered = "".join(event.get("text", "") for event in events)

    assert "User Safety" not in rendered
    assert rendered == good_text
    assert events[-1]["type"] == "done"
    assert events[-1]["model"] == "valid-fallback"
    assert first_delta_calls == ["start-prefetch"]


def test_slide_stream_rejects_internal_reasoning_and_uses_fallback(monkeypatch):
    class Candidate:
        def __init__(self, label, output):
            self.label = label
            self.output = output

        def stream(self, *_args):
            yield self.output

    leaked = Candidate(
        "reasoning-leak",
        "We need to explain the slide in one paragraph. Let's craft: Slide title: Toekomstig onderzoek. "
        "Content: sequencing and risk profiles.",
    )
    answer = "Toekomstig onderzoek gebruikt sequencing en risicoprofielen voor vroegere preventie en gerichtere behandeling."
    good = Candidate("clean-fallback", answer)
    monkeypatch.setattr(core.ai_engine, "candidates", lambda interactive=False: [leaked, good])
    monkeypatch.setattr(core.ai_engine, "candidate_available", lambda _candidate: True)
    monkeypatch.setattr(core.ai_engine, "report_failure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(core.ai_engine, "report_success", lambda *_args, **_kwargs: None)

    events = [json.loads(line.removeprefix("data: ")) for line in core.stream_markdown(
        [], "prompt", None, interactive=True,
    )]
    rendered = "".join(event.get("text", "") for event in events)

    assert "We need" not in rendered
    assert "Let's craft" not in rendered
    assert rendered == answer
    assert events[-1]["type"] == "done"


def test_slide_stream_falls_back_when_first_token_stalls(monkeypatch):
    class SlowCandidate:
        label = "stalled-model"

        def stream(self, *_args):
            time.sleep(0.2)
            yield "Dit antwoord kwam te laat en mag niet zichtbaar worden."

    class FastCandidate:
        label = "fast-fallback"

        def stream(self, *_args):
            yield "Deze snelle fallback geeft meteen een volledige en inhoudelijk bruikbare uitleg."

    monkeypatch.setattr(core, "AI_FIRST_TOKEN_TIMEOUT_S", 0.02)
    monkeypatch.setattr(core.ai_engine, "candidates", lambda interactive=False: [SlowCandidate(), FastCandidate()])
    monkeypatch.setattr(core.ai_engine, "candidate_available", lambda _candidate: True)
    monkeypatch.setattr(core.ai_engine, "report_failure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(core.ai_engine, "report_success", lambda *_args, **_kwargs: None)

    events = [json.loads(line.removeprefix("data: ")) for line in core.stream_markdown(
        [], "prompt", None, interactive=True,
    )]
    rendered = "".join(event.get("text", "") for event in events)

    assert rendered.startswith("Deze snelle fallback")
    assert all("te laat" not in event.get("text", "") for event in events)
    assert any(event.get("type") == "waiting" for event in events)


def test_slide_stream_keeps_text_after_initial_validation_buffer(monkeypatch):
    pieces = ["Een heldere uitleg " * 12, "met een belangrijk slot dat niet mag verdwijnen."]

    class Candidate:
        label = "valid-model"

        def stream(self, *_args):
            yield from pieces

    monkeypatch.setattr(core.ai_engine, "candidates", lambda interactive=False: [Candidate()])
    monkeypatch.setattr(core.ai_engine, "candidate_available", lambda _candidate: True)
    monkeypatch.setattr(core.ai_engine, "report_success", lambda *_args, **_kwargs: None)

    events = [json.loads(line.removeprefix("data: ")) for line in core.stream_markdown(
        [], "prompt", None, interactive=True,
    )]

    assert "".join(event.get("text", "") for event in events) == "".join(pieces)


def test_small_document_keeps_full_ai_planner_quality(client, uploaded_doc, monkeypatch):
    file_hash, headers = uploaded_doc
    planned = media.SmartSearchPlan(
        intent="answer", search_terms=["centrale", "mechanisme"], focus="AI-planner behouden",
    )
    monkeypatch.setattr(media, "_smart_plan", lambda *_: planned)

    def fake_answer(_query, _scope, plan, material, _language, _history, source_catalog):
        assert plan is planned
        assert material.strip()
        assert "exact filename:" in source_catalog
        return SmartAnswerResult(
            title="Direct antwoord", markdown="Alle pagina's en de AI-planner zijn gebruikt.",
        )

    monkeypatch.setattr(media, "_smart_answer", fake_answer)

    response = client.post("/smart-search", headers=headers, json={
        "query": "Leg het centrale mechanisme uit", "file_hash": file_hash,
    })

    assert response.status_code == 200, response.text
    assert response.json()["markdown"] == "Alle pagina's en de AI-planner zijn gebruikt."


def test_smart_follow_up_passes_conversation_to_planner_and_answer(client, uploaded_doc, monkeypatch):
    file_hash, headers = uploaded_doc
    seen = {}
    plan = media.SmartSearchPlan(intent="answer", search_terms=["receptor"], focus="vervolgvraag")

    def fake_plan(query, language, history, source_catalog):
        seen["plan_history"] = history
        seen["catalog"] = source_catalog
        return plan

    def fake_answer(query, scope, received_plan, material, language, history, source_catalog):
        seen["answer_history"] = history
        assert received_plan is plan
        assert source_catalog == seen["catalog"]
        return SmartAnswerResult(title="Vervolguitleg", markdown="Omdat de receptor actief blijft.")

    monkeypatch.setattr(media, "_smart_plan", fake_plan)
    monkeypatch.setattr(media, "_smart_answer", fake_answer)
    response = client.post("/smart-search", headers=headers, json={
        "query": "Waarom gebeurt dat vervolgens?", "file_hash": file_hash,
        "history": [
            {"role": "user", "content": "Wat doet deze receptor?"},
            {"role": "assistant", "content": "De receptor activeert de signaalroute."},
        ],
    })

    assert response.status_code == 200, response.text
    assert seen["plan_history"][-1].content == "De receptor activeert de signaalroute."
    assert seen["answer_history"][-1].content == "De receptor activeert de signaalroute."
    assert response.json()["markdown"] == "Omdat de receptor actief blijft."


def test_document_codes_select_exact_filenames_before_content_ranking():
    catalog = [
        {"file_hash": "mono", "file_name": "HC-PD-06 Monogenetische diabetes 2026.pdf"},
        {"file_hash": "cancer", "file_name": "HC-PD-04 Erfelijke aanleg voor kanker 2026.pdf"},
        {"file_hash": "repeat", "file_name": "HC-06 G1CM Triplet Repeatexpansie Ziekte 2026.pdf"},
        {"file_hash": "cf", "file_name": "PD-HC- 05 College CF GNK 18-09-2026 (3).pdf"},
    ]

    hashes, recognized = media._smart_requested_hashes(catalog, "Gebruik alleen HC-PD-06")
    family_hashes, family_recognized = media._smart_requested_hashes(catalog, "Vergelijk alle HC-PD colleges")

    assert recognized is True
    assert hashes == ["mono"]
    assert family_recognized is True
    assert family_hashes == ["mono", "cancer"]
    assert media._smart_filename_match_score(catalog[0]["file_name"], "HC-PD-06") >= 100
    assert media._smart_filename_match_score(catalog[2]["file_name"], "HC-PD-06") == 0


def test_large_focused_document_is_chunked_before_final_answer(client, uploaded_doc, monkeypatch):
    file_hash, headers = uploaded_doc
    plan = media.SmartSearchPlan(intent="answer", search_terms=["mechanisme"], focus="gericht antwoord")
    extracted = []

    monkeypatch.setattr(media, "_smart_plan", lambda *_: plan)
    monkeypatch.setattr(media, "_smart_chunks", lambda records: [records, records])

    def fake_extract(chunk, query, language, history):
        extracted.append((chunk, query))
        return f"gecontroleerde notities {len(extracted)}"

    def fake_answer(_query, _scope, _plan, material, _language, _history, _catalog):
        assert "Coverage chunk 1/2" in material
        assert "gecontroleerde notities 2" in material
        return SmartAnswerResult(title="Robuust antwoord", markdown="Alle delen zijn verwerkt.")

    monkeypatch.setattr(media, "_smart_extract_chunk", fake_extract)
    monkeypatch.setattr(media, "_smart_answer", fake_answer)

    response = client.post("/smart-search", headers=headers, json={
        "query": "Leg het mechanisme uit", "file_hash": file_hash,
    })

    assert response.status_code == 200, response.text
    assert len(extracted) == 2
    assert response.json()["markdown"] == "Alle delen zijn verwerkt."


def test_smart_question_never_starts_competing_ocr(monkeypatch):
    monkeypatch.setattr(media, "load_meta", lambda *_: {"file_name": "College.pdf"})
    monkeypatch.setattr(
        media, "get_document_search_texts",
        lambda *_: ("pdf", ["Receptor activeert de volledige signaalroute."], False),
    )
    monkeypatch.setattr(
        media, "queue_search_index",
        lambda *_: (_ for _ in ()).throw(AssertionError("OCR mag niet concurreren met een slimme vraag")),
    )

    records = media._smart_page_records("student", ["hash"], [{
        "file_hash": "hash", "file_name": "College.pdf", "folder_path": "",
    }])

    assert records[0]["text"] == "Receptor activeert de volledige signaalroute."


def test_medium_document_goes_directly_to_answer_without_lossy_prepass():
    records = [{
        "file_hash": "hash", "file_name": "College.pdf", "folder_path": "",
        "doc_index": 1, "page_index": index, "page": index + 1,
        "label": "Dia", "text": "x" * 1800,
    } for index in range(24)]

    chunks = media._smart_chunks(records)

    assert len(chunks) == 1
    assert chunks[0] == records


def test_exhaustive_answer_removes_null_and_appends_missing_exact_sources():
    catalog = [
        {"file_name": "HC-PD-06 Monogenetische diabetes 2026.pdf", "folder_path": "Cel tot molecuul > thema 4"},
        {"file_name": "HC-17 Glucose Homeostase 2026.pptx", "folder_path": "Cel tot molecuul > thema 4"},
    ]

    markdown = media._smart_finalize_markdown(
        "### Antwoord\nBron: HC-PD-06 Monogenetische diabetes 2026.pdf\n\nnull", catalog, True,
    )

    assert "\nnull" not in markdown
    assert "`HC-17 Glucose Homeostase 2026.pptx`" in markdown
    assert "Cel tot molecuul > thema 4" in markdown
    assert markdown.count("HC-PD-06 Monogenetische diabetes 2026.pdf") == 1


def test_fake_numeric_source_markers_are_removed_from_markdown():
    markdown = media._smart_finalize_markdown(
        "Normoglykemie ligt rond 5 mM [1, 28, 29].", [], False,
    )

    assert markdown == "Normoglykemie ligt rond 5 mM."


def test_make_clear_is_a_focused_answer_not_a_broad_overview():
    plan = media._smart_basic_plan(
        "Leg het verband uit tussen receptoren en glucose. Maak duidelijk wat elk college bijdraagt."
    )

    assert plan.intent == "answer"


def test_nested_structured_content_can_be_rendered_as_markdown():
    markdown = core._nested_content_to_markdown({
        "intro": "Kernzin",
        "sections": [{"title": "Receptor", "text": "Binding is verzadigbaar."}],
    })

    assert "Kernzin" in markdown
    assert "Receptor" in markdown
    assert "Binding is verzadigbaar." in markdown


def test_exhaustive_audit_demands_formula_itself_not_only_a_citation(monkeypatch):
    captured = {}
    repaired = SmartAnswerResult(title="Compleet", markdown="$$v = V_{max}[S]/(K_m+[S])$$")

    def fake_generate(contents, system, schema):
        captured["prompt"] = contents[0].parts[0].text
        captured["system"] = system
        assert schema is SmartAnswerResult
        return repaired

    monkeypatch.setattr(media, "generate_structured", fake_generate)
    result = media._smart_audit_exhaustive_answer(
        "Welke formules moet ik kennen?",
        "HC-16 — Michaelis-Menten: v = Vmax[S]/(Km+[S])",
        "Document 1 | exact filename: HC-16 college ligand, receptor en enzym.pdf",
        SmartAnswerResult(title="Concept", markdown="Alleen een verwijzing naar Michaelis-Menten."),
        "nl",
    )

    assert result is repaired
    assert "DRAFT ANSWER" in captured["prompt"]
    assert "include each relevant formula itself" in captured["system"]
