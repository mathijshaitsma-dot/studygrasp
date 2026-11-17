import io
import uuid

from PIL import Image, ImageDraw

import auth
from core import (
    VisualVocabPair, VisualVocabSet, VisualWordlistGenerateRequest,
    WordlistUpdateRequest, load_wordlist,
)
from routers import wordlists as wordlists_router


def _visual_pair():
    return VisualVocabPair.model_validate({
        "term": "Aorta",
        "definition": "Grote lichaamsslagader",
        "box": {"x": 0.35, "y": 0.25, "width": 0.2, "height": 0.3},
        "label_box": {"x": 0.62, "y": 0.25, "width": 0.15, "height": 0.06},
    })


def test_visual_generation_stores_regions_and_private_source(monkeypatch, tmp_path):
    uid = f"visual-{uuid.uuid4().hex}"
    source = tmp_path / "diagram.jpg"
    Image.new("RGB", (800, 600), "white").save(source)
    checked_users = []

    monkeypatch.setattr(auth, "require_user_id", lambda _request: uid)
    monkeypatch.setattr(wordlists_router, "request_user_id", lambda _request: uid)
    monkeypatch.setattr(wordlists_router, "ensure_document_exists", lambda user, _hash: (
        checked_users.append(user) or {"file_name": "hart.jpg", "total_pages": 1}
    ))
    monkeypatch.setattr(wordlists_router, "ensure_slide_image", lambda *_args: source)
    monkeypatch.setattr(wordlists_router, "generate_structured", lambda *_args, **_kwargs: VisualVocabSet(pairs=[_visual_pair()]))

    response = wordlists_router.wordlists_generate_visual(
        VisualWordlistGenerateRequest(file_hash="a" * 64), None,
    )
    card = response["wordlist"]["cards"][0]
    assert card["term"] == "Aorta"
    assert card["visual"]["box"]["width"] == 0.2
    assert checked_users == [uid]
    assert load_wordlist(uid, response["wordlist"]["id"])["cards"][0]["visual"]["file_hash"] == "a" * 64

    updated = wordlists_router.wordlists_update(
        response["wordlist"]["id"],
        WordlistUpdateRequest.model_validate({"cards": [{
            "term": "Aorta ascendens", "definition": "Grote lichaamsslagader",
            "visual": card["visual"],
        }]}),
        None,
    )
    assert updated["wordlist"]["cards"][0]["visual"]["box"]["height"] == 0.3


def test_visual_crop_is_zoomed_and_label_is_concealed(monkeypatch, tmp_path):
    uid = f"visual-crop-{uuid.uuid4().hex}"
    source = tmp_path / "diagram.jpg"
    image = Image.new("RGB", (1000, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((350, 200, 550, 500), fill="red")
    draw.rectangle((620, 200, 770, 248), fill="black")
    image.save(source)
    monkeypatch.setattr(auth, "require_user_id", lambda _request: uid)
    monkeypatch.setattr(wordlists_router, "ensure_document_exists", lambda user, _hash: {"owner": user})
    monkeypatch.setattr(wordlists_router, "ensure_slide_image", lambda *_args: source)
    monkeypatch.setattr(wordlists_router, "load_wordlist", lambda user, _list_id: {
        "owner_id": user,
        "cards": [{"id": 7, "visual": {
            "file_hash": "b" * 64, "page_index": 0,
            "box": {"x": 0.35, "y": 0.25, "width": 0.2, "height": 0.375},
            "label_box": {"x": 0.62, "y": 0.25, "width": 0.15, "height": 0.06},
        }}],
    })

    response = wordlists_router.wordlist_card_visual("list", 7, None)

    cropped = Image.open(io.BytesIO(response.body))
    assert cropped.width < 1000 and cropped.height < 800
    assert response.media_type == "image/jpeg"


def test_visual_session_mode_is_valid():
    from core import WordlistSessionRequest

    session = WordlistSessionRequest.model_validate({
        "mode": "visual", "direction": "term_first", "index": 0,
        "queue": [{"card_id": 1}], "selected_ids": [1],
    })
    assert session.mode == "visual"

