import pytest
from pydantic import ValidationError

import core


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
