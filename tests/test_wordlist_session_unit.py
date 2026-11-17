import time
import uuid

import auth
from core import WordlistReviewRequest, WordlistSessionRequest, load_wordlist, save_wordlist
from routers import wordlists as wordlists_router


def test_wordlist_stats_and_session_persist_without_http_transport(monkeypatch):
    user_id = f"wordlist-session-{uuid.uuid4().hex}"
    list_id = uuid.uuid4().hex[:12]
    now = time.time()
    save_wordlist(user_id, {
        "id": list_id, "name": "Frans", "owner_id": user_id, "language": "auto",
        "created_at": now, "next_id": 1,
        "cards": [{"id": 0, "term": "chien", "definition": "hond"}],
        "srs": {"0": {"interval": 0.0, "ease": 2.5, "reps": 0, "due_at": now,
                           "correct_count": 0, "wrong_count": 0}},
    })
    monkeypatch.setattr(auth, "require_user_id", lambda _request: user_id)

    wordlists_router.wordlists_review(
        list_id, WordlistReviewRequest(card_id=0, rating="again", update_schedule=True), None,
    )
    wordlists_router.wordlists_review(
        list_id, WordlistReviewRequest(card_id=0, rating="hard", update_schedule=False), None,
    )
    state = load_wordlist(user_id, list_id)["srs"]["0"]
    assert state["wrong_count"] == 1 and state["correct_count"] == 1

    request = WordlistSessionRequest.model_validate({
        "mode": "typing", "direction": "definition_first", "index": 0,
        "queue": [{"card_id": 0, "phase": "retry"}],
        "card_states": {"0": {"everWrong": True, "savedAgain": True, "finalQueued": False}},
        "missed": [0], "selected_ids": [0],
    })
    wordlists_router.wordlists_save_session(list_id, request, None)
    stored = load_wordlist(user_id, list_id)["practice_session"]
    assert stored["index"] == 0 and stored["direction"] == "definition_first"
    assert stored["queue"] == [{"card_id": 0, "phase": "retry", "rated": False}]

    wordlists_router.wordlists_delete_session(list_id, None)
    assert "practice_session" not in load_wordlist(user_id, list_id)
