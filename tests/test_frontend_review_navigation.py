from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_flashcard_arrows_navigate_without_rating():
    review = (ROOT / "frontend" / "js" / "review.js").read_text(encoding="utf-8")

    assert 'navigateCard(e.key === "ArrowLeft" ? -1 : 1)' in review
    assert 'rate(e.key === "ArrowLeft" ? "again" : "good")' not in review
    assert "const next = Math.max(0, Math.min(queue.length - 1, i + delta));" in review


def test_rated_flashcard_cannot_be_scored_twice_when_browsing_back():
    review = (ROOT / "frontend" / "js" / "review.js").read_text(encoding="utf-8")

    assert "if (reviewing() || !entry || entry.rated) return;" in review
    assert "entry.rated = true;" in review
    assert 'queue.push({ card, rated: false })' in review
