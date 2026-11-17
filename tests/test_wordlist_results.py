from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_typing_round_shows_score_and_can_repeat_only_missed_cards():
    view = (ROOT / "frontend" / "js" / "views" / "wordlist.js").read_text(encoding="utf-8")
    helpers = (ROOT / "frontend" / "js" / "wordlist-typing.js").read_text(encoding="utf-8")

    assert "export function gradeFromWordlistRound(correct, total)" in helpers
    assert "1 + 9 * percentage" in helpers
    assert "const wrongCards = cards.filter(card => missed.has(card.id));" in view
    assert "gradeFromWordlistRound(good, cards.length)" in view
    assert "wordlist_result_good" in view and "wordlist_result_wrong" in view
    assert "runTypingPractice(wrongCards, direction, null, visualMode);" in view
    assert "wordlist_repeat_wrong" in view
