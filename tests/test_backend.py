"""
Basisdekking voor de v3-backend. Bewust géén tests die echte AI-providers
aanroepen (explain/summary/quiz/flashcards/exam) — dat vraagt om mocking van
ai_engine die in deze ronde nog niet is gebouwd. Focus: de dingen die deze
professionaliseringsronde net heeft toegevoegd/gewijzigd (data-persistentie,
eigendom, rate limiting) plus een paar bestaande pure-logica-helpers.
"""
import time
import uuid

import ai_stats
import backend
import cache_store
import rate_limit
from core import build_review_plan, apply_sm2


def test_upload_creates_document_with_owner(uploaded_doc, client):
    file_hash, headers = uploaded_doc
    resp = client.get(f"/document/{file_hash}")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["total_pages"] == 1
    assert doc["file_name"] == "test.pdf"


def test_notes_roundtrip(uploaded_doc, client):
    file_hash, headers = uploaded_doc
    resp = client.post(
        f"/document/{file_hash}/notes",
        json={"page_index": 0, "note": "belangrijke notitie", "star": True},
        headers=headers,
    )
    assert resp.status_code == 200

    got = client.get(f"/document/{file_hash}/notes", headers=headers).json()
    assert got["pages"]["0"]["note"] == "belangrijke notitie"
    assert got["pages"]["0"]["star"] is True


def test_ownership_blocks_other_user_but_allows_owner(uploaded_doc, client):
    file_hash, owner_headers = uploaded_doc

    blocked = client.delete(f"/document/{file_hash}", headers={"X-User-Id": "iemand-anders"})
    assert blocked.status_code == 403

    allowed = client.delete(f"/document/{file_hash}", headers=owner_headers)
    assert allowed.status_code == 200


def test_legacy_document_without_owner_is_editable_by_anyone(client, make_pdf_bytes):
    # Simuleert een document van vóór deze wijziging: geüpload zonder X-User-Id.
    pdf = make_pdf_bytes(f"Legacy {uuid.uuid4()}")
    resp = client.post("/upload", files={"file": ("legacy.pdf", pdf, "application/pdf")})
    file_hash = resp.json()["file_hash"]

    delete_resp = client.delete(f"/document/{file_hash}", headers={"X-User-Id": "wie-dan-ook"})
    assert delete_resp.status_code == 200


def test_upload_rejects_oversized_file(client, make_pdf_bytes, monkeypatch):
    # De upload-endpoint leest MAX_UPLOAD_MB in routers.documents (via `from core import *`),
    # dus daar patchen — niet op core/backend, want dat is een aparte naam-binding.
    monkeypatch.setattr("routers.documents.MAX_UPLOAD_MB", 0)  # elk bestand telt nu als "te groot"
    pdf = make_pdf_bytes(f"Te groot {uuid.uuid4()}")
    resp = client.post("/upload", files={"file": ("groot.pdf", pdf, "application/pdf")})
    assert resp.status_code == 413
    assert resp.json()["error_code"] == "FILE_TOO_LARGE"


def test_upload_rejects_content_type_mismatch(client):
    # Een .pdf die geen echte PDF is (verkeerde magic bytes) wordt geweigerd.
    resp = client.post("/upload", files={"file": ("nep.pdf", b"dit is helemaal geen pdf", "application/pdf")})
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "FILE_CONTENT_MISMATCH"


def test_routers_are_wired(client):
    """Vangnet tegen een router die niet is aangekoppeld (bv. na de split):
    de kern-endpoints van elk domein moeten bestaan (geen 404), en een
    onzin-pad moet juist wél 404 geven."""
    for path in ("/", "/health/deep", "/documents", "/wordlists", "/folders"):
        assert client.get(path).status_code != 404, f"{path} ontbreekt — router niet aangekoppeld?"
    assert client.get("/dit-bestaat-niet").status_code == 404


def test_rate_limit_override_is_independent_of_default(client):
    key = f"test-upload-ip-{uuid.uuid4()}"
    assert rate_limit.check(key, max_per_window=2)
    assert rate_limit.check(key, max_per_window=2)
    assert not rate_limit.check(key, max_per_window=2)
    # Een andere key met de standaardlimiet is hierdoor niet geraakt.
    assert rate_limit.check(f"other-{uuid.uuid4()}")


def test_folder_ownership(client):
    owner_headers = {"X-User-Id": "map-eigenaar"}
    resp = client.post("/folders", json={"name": f"Testvak {uuid.uuid4()}"}, headers=owner_headers)
    assert resp.status_code == 200
    folder_id = resp.json()["folder"]["id"]

    blocked = client.patch(f"/folders/{folder_id}", json={"name": "Andere naam"}, headers={"X-User-Id": "indringer"})
    assert blocked.status_code == 403

    allowed = client.patch(f"/folders/{folder_id}", json={"name": "Nieuwe naam"}, headers=owner_headers)
    assert allowed.status_code == 200
    assert allowed.json()["folder"]["name"] == "Nieuwe naam"


def test_build_review_plan_buckets_by_mastery_and_due_date():
    now = time.time()
    data = {
        "concepts": {
            "a": {"label": "Concept A", "right": 1, "wrong": 3, "interval": 1.0, "due_at": now - 10},
            "b": {"label": "Concept B", "right": 9, "wrong": 1, "interval": 20.0, "due_at": now + 30 * 86400},
            "c": {"label": "Concept C", "right": 1, "wrong": 1, "interval": 1.0, "due_at": now + 3 * 86400},
        }
    }
    plan = build_review_plan(data)
    assert any(item["concept"] == "Concept A" for item in plan["due_now"])
    assert any(item["concept"] == "Concept B" for item in plan["mastered"])
    assert any(item["concept"] == "Concept C" for item in plan["this_week"])


def test_cache_store_json_roundtrip():
    cache_store.put_json("test_ns", "key1", {"hello": "world"})
    assert cache_store.get_json("test_ns", "key1") == {"hello": "world"}
    cache_store.delete_json("test_ns", "key1")
    assert cache_store.get_json("test_ns", "key1") is None


def test_ai_stats_record_and_aggregate():
    label = f"test:model-{uuid.uuid4()}"
    ai_stats.record(label, success=True, latency_ms=120.0)
    ai_stats.record(label, success=False, error_class="rate_limit")
    stats = ai_stats.aggregate(days=1)["providers"][label]
    assert stats["success"] == 1
    assert stats["failure"] == 1
    assert stats["errors"]["rate_limit"] == 1


def test_rate_limit_blocks_after_max():
    key = f"test-ip-{uuid.uuid4()}"
    original_max = rate_limit._MAX_PER_WINDOW
    rate_limit._MAX_PER_WINDOW = 3
    try:
        assert rate_limit.check(key)
        assert rate_limit.check(key)
        assert rate_limit.check(key)
        assert not rate_limit.check(key)
    finally:
        rate_limit._MAX_PER_WINDOW = original_max


def test_apply_sm2_again_resets_and_good_grows():
    now = time.time()
    again = apply_sm2({"interval": 10.0, "ease": 2.5, "reps": 3, "due_at": now}, "again", now)
    assert again["interval"] == 0.0 and again["reps"] == 0
    assert again["due_at"] <= now + 11 * 60  # ~10 min

    good1 = apply_sm2(None, "good", now)      # eerste keer goed → 1 dag
    assert round(good1["interval"], 1) == 1.0
    good2 = apply_sm2(good1, "good", now)     # daarna groeit het interval
    assert good2["interval"] > good1["interval"]


def test_upload_kind_quick_echoed_in_list(client, make_pdf_bytes):
    pdf = make_pdf_bytes(f"Quick {uuid.uuid4()}")
    resp = client.post("/upload", files={"file": ("q.pdf", pdf, "application/pdf")}, data={"kind": "quick"})
    file_hash = resp.json()["file_hash"]
    docs = client.get("/documents").json()["documents"]
    match = next(d for d in docs if d["file_hash"] == file_hash)
    assert match["kind"] == "quick"


def test_wordlist_crud_ownership_and_stable_ids(client):
    owner = {"X-User-Id": "wl-owner"}
    created = client.post("/wordlists", json={
        "name": f"Frans {uuid.uuid4()}",
        "cards": [{"term": "chien", "definition": "hond"}, {"term": "chat", "definition": "kat"}],
    }, headers=owner).json()["wordlist"]
    list_id = created["id"]
    assert created["total"] == 2
    id_chien = next(c["id"] for c in created["cards"] if c["term"] == "chien")

    # eigendom: een ander mag niet verwijderen
    assert client.request("DELETE", f"/wordlists/{list_id}", headers={"X-User-Id": "indringer"}).status_code == 403

    # review werkt en verzet de due-datum
    rev = client.post(f"/wordlists/{list_id}/review", json={"card_id": id_chien, "rating": "good"})
    assert rev.status_code == 200 and rev.json()["interval_days"] >= 1.0

    # bewerken: een kaart toevoegen; bestaande 'chien' behoudt zijn stabiele id (en dus SRS)
    updated = client.patch(f"/wordlists/{list_id}", json={"cards": [
        {"term": "chien", "definition": "hond"},
        {"term": "chat", "definition": "kat"},
        {"term": "oiseau", "definition": "vogel"},
    ]}, headers=owner).json()["wordlist"]
    assert updated["total"] == 3
    assert next(c["id"] for c in updated["cards"] if c["term"] == "chien") == id_chien

    # eigenaar mag verwijderen
    assert client.request("DELETE", f"/wordlists/{list_id}", headers=owner).status_code == 200
    assert client.get(f"/wordlists/{list_id}").status_code == 404
