from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_check_button_disappears_and_empty_answer_can_be_checked():
    wordlist = (ROOT / "frontend" / "js" / "views" / "wordlist.js").read_text(encoding="utf-8")

    assert "const checkRow = el(\"div\", { class: \"wl-type-check\" }, check);" in wordlist
    assert wordlist.count("checkRow.remove();") == 2
    assert "input.disabled || busy || !input.value.trim()" not in wordlist
    assert "if (input.disabled || busy) return;" in wordlist


def test_answer_buttons_wait_before_advancing():
    wordlist = (ROOT / "frontend" / "js" / "views" / "wordlist.js").read_text(encoding="utf-8")

    assert "const ANSWER_CONTINUE_DELAY_MS = 600;" in wordlist
    assert "if (busy || !continueUnlocked) return;" in wordlist
    assert wordlist.count("unlockContinueAfterDelay(") == 2  # goed en fout antwoord
    assert "button.disabled = true" in wordlist
