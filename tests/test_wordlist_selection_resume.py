from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_wordlist_supports_selection_results_and_resuming_both_modes():
    view = (ROOT / "frontend" / "js" / "views" / "wordlist.js").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "js" / "api.js").read_text(encoding="utf-8")
    review = (ROOT / "frontend" / "js" / "review.js").read_text(encoding="utf-8")

    assert 'type: "checkbox"' in view
    assert 't("wordlist_select_mostly_wrong"' in view
    assert "correct_count" in view and "wrong_count" in view
    assert "wordlistSessionSave" in view and "wordlistSessionDelete" in view
    assert 'activeSession?.mode === "typing"' in view
    assert 'activeSession?.mode === "flashcards"' in view
    assert 'wordlistSessionSave: (id, body)' in api
    assert "opts.initialEntries?.length" in review
    assert "opts.onProgress" in review
