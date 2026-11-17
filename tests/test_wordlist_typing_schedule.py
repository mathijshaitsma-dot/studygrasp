from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_wrong_answers_repeat_after_two_questions_and_return_in_final_round():
    scheduler = (ROOT / "frontend" / "js" / "wordlist-typing.js").read_text(encoding="utf-8")
    view = (ROOT / "frontend" / "js" / "views" / "wordlist.js").read_text(encoding="utf-8")

    assert "currentIndex + 3" in scheduler
    assert 'queue.splice(retryAt, 0, { card, phase })' in scheduler
    assert 'queue.push({ card, phase: "final" })' in view
    assert "else if (!state.finalQueued)" in view
    assert 'entry.phase === "final" ? "final" : "retry"' in view
