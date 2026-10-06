"""Regressietests voor intentie, bereik en bronselectie van slim zoeken."""

import usage
import routers.media as media
from routers.media import (
    SmartAnswerCitation,
    SmartAnswerResult,
    SmartSearchPlan,
    _smart_chapter_range,
    _smart_explicit_range,
    _smart_fallback_intent,
    _smart_rank,
)


def test_smart_search_recognizes_the_three_jobs():
    assert _smart_fallback_intent("Op welke dia staat de citroenzuurcyclus?") == "locate"
    assert _smart_fallback_intent("Maak een overzicht van alle casussen") == "overview"
    assert _smart_fallback_intent("Waarom remt taxol de celdeling?") == "answer"


def test_smart_search_understands_slide_ranges():
    assert _smart_explicit_range("Vat dia 1 t/m dia 25 samen") == (1, 25)
    assert _smart_explicit_range("samenvatting pagina 40 tot en met 12") == (12, 40)
    assert _smart_explicit_range("welke formules moet ik kennen?") is None


def test_smart_search_detects_a_numbered_chapter_boundary():
    records = [
        {"page": 1, "text": "Inhoudsopgave"},
        {"page": 2, "text": "Hoofdstuk 1\nCelbiologie"},
        {"page": 3, "text": "Celmembranen"},
        {"page": 4, "text": "Hoofdstuk 2\nMetabolisme"},
    ]

    assert _smart_chapter_range(records, "Vat hoofdstuk 1 samen") == (2, 3)
    assert _smart_chapter_range(records, "Vat hoofdstuk 9 samen") is None


def test_smart_search_uses_ai_expanded_terms_for_semantic_shortlisting():
    records = [
        {"doc_index": 1, "page_index": 0, "text": "Mitose en de vorming van de spoelfiguur"},
        {"doc_index": 1, "page_index": 1, "text": "Citroenzuurcyclus en oxidatieve fosforylering"},
    ]

    ranked = _smart_rank(records, "energieproductie in mitochondriën", ["citroenzuurcyclus", "oxidatieve fosforylering"])

    assert ranked[0]["page_index"] == 1


def test_smart_search_requires_a_real_account(client):
    guest = client.post("/auth/guest", json={}).json()
    response = client.post(
        "/smart-search",
        headers={"Authorization": f"Bearer {guest['token']}"},
        json={"query": "Welke formules moet ik kennen?"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "LOGIN_REQUIRED"


def test_smart_search_returns_clickable_sources_without_consuming_credits(
        client, uploaded_doc, monkeypatch):
    file_hash, headers = uploaded_doc
    monkeypatch.setattr(media, "_smart_plan", lambda *_: SmartSearchPlan(
        intent="answer", search_terms=["testdocument"], focus="test", exhaustive=False,
    ))
    monkeypatch.setattr(media, "_smart_answer", lambda *args: SmartAnswerResult(
        title="Testantwoord", markdown="Dit komt uit het document.",
        citations=[SmartAnswerCitation(doc_index=1, page=1, why="De relevante uitleg")],
    ))
    monkeypatch.setattr(usage, "consume", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("slim zoeken mag geen credits gebruiken")
    ))

    response = client.post("/smart-search", headers=headers, json={
        "query": "Wat staat er in het testdocument?", "file_hash": file_hash, "language": "Nederlands",
    })

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["markdown"] == "Dit komt uit het document."
    assert data["documents_scanned"] == 1
    assert data["pages_scanned"] == 1
    assert data["citations"][0]["file_hash"] == file_hash
    assert data["citations"][0]["page_index"] == 0
