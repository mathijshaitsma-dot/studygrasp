"""
Basisdekking voor de v3-backend. Bewust géén tests die echte AI-providers
aanroepen (explain/summary/quiz/flashcards/exam) — dat vraagt om mocking van
ai_engine die in deze ronde nog niet is gebouwd. Focus: de dingen die deze
professionaliseringsronde net heeft toegevoegd/gewijzigd (data-persistentie,
eigendom, rate limiting) plus een paar bestaande pure-logica-helpers.
"""
import time
import uuid
import hashlib
import io
import json
import zipfile

import ai_stats
import auth
import backend
import cache_store
import mailer
import rate_limit
import usage
from core import build_review_plan, apply_sm2, assess_image_quality


def test_public_root_serves_frontend_with_security_headers(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>StudyGrasp" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_production_readiness_requires_safe_public_config(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_BASE_URL", "https://studygrasp.example")
    monkeypatch.setenv("CORS_ORIGINS", "https://studygrasp.example")
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("EMAIL_REGISTRATION_ENABLED", "false")
    # Google-only registratie heeft geen wachtwoordherstelmail nodig. De
    # SMTP-check blijft zichtbaar, maar blokkeert readiness dan niet.
    monkeypatch.setattr(mailer, "SMTP_HOST", "")
    monkeypatch.setattr(mailer, "SMTP_FROM", "")

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["checks"]["cors_restricted"] is True
    assert response.json()["checks"]["password_email"] is False


def _migration_zip(document: bytes, *, original_name: str = "Oud college.pdf") -> bytes:
    file_hash = hashlib.sha256(document).hexdigest()
    manifest = {
        "format": "studygrasp-legacy-migration",
        "version": 1,
        "documents": [{
            "file_hash": file_hash,
            "suffix": ".pdf",
            "upload_member": f"uploads/{file_hash}.pdf",
            "meta": {
                "file_hash": file_hash,
                "file_name": original_name,
                "file_type": "pdf",
                "total_pages": 1,
                "status": "ready",
                "last_page_index": 0,
            },
            "notes": {"pages": {"0": {"note": "Oude notitie"}}},
            "study": {"flashcards": [], "srs": {}},
        }],
        "folders": {"folders": [{"id": "vak-1", "name": "Oud vak", "created_at": 1}]},
        "wordlists": [],
        "ai_cache_members": [],
    }
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"uploads/{file_hash}.pdf", document)
        archive.writestr("manifest.json", json.dumps(manifest))
    return result.getvalue()


def test_owner_can_import_legacy_documents(client, make_pdf_bytes, make_account):
    headers = make_account()
    me = client.get("/auth/me", headers=headers).json()["user"]
    auth.set_plan(me["id"], "owner")
    document = make_pdf_bytes("Oud college voor migratietest")

    response = client.post(
        "/owner/migration/import",
        files={"backup": ("StudyGrasp-migratie.zip", _migration_zip(document), "application/zip")},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["documents"] == 1
    docs = client.get("/documents", headers=headers).json()["documents"]
    imported = next(doc for doc in docs if doc["file_name"] == "Oud college.pdf")
    notes = client.get(f"/document/{imported['file_hash']}/notes", headers=headers).json()
    assert notes["pages"]["0"]["note"] == "Oude notitie"


def test_non_owner_cannot_import_legacy_documents(client, make_pdf_bytes, make_account):
    headers = make_account()
    me = client.get("/auth/me", headers=headers).json()["user"]
    auth.set_plan(me["id"], "free")
    response = client.post(
        "/owner/migration/import",
        files={"backup": ("backup.zip", _migration_zip(make_pdf_bytes("Niet van owner")), "application/zip")},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "OWNER_REQUIRED"


def test_auth_config_reads_google_client_id_after_import(client, monkeypatch):
    """De Google-knop mag niet afhangen van de importvolgorde van auth/core."""
    client_id = "123456789-example.apps.googleusercontent.com"
    monkeypatch.setenv("GOOGLE_CLIENT_ID", client_id)

    response = client.get("/auth/config")

    assert response.status_code == 200
    assert response.json()["google_client_id"] == client_id


def test_google_login_validates_audience_and_creates_session(client, monkeypatch):
    client_id = "123456789-example.apps.googleusercontent.com"
    monkeypatch.setenv("GOOGLE_CLIENT_ID", client_id)

    class GoogleResponse:
        ok = True

        @staticmethod
        def json():
            return {
                "aud": client_id,
                "sub": "google-user-123",
                "email": f"google-{uuid.uuid4().hex[:10]}@test.nl",
                "email_verified": "true",
            }

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: GoogleResponse())
    response = client.post("/auth/google", json={"id_token": "test-google-id-token"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {payload['token']}"})
    assert me.status_code == 200
    assert me.json()["user"]["email"] == payload["user"]["email"]


def test_google_login_rejects_token_for_another_app(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "our-app.apps.googleusercontent.com")

    class GoogleResponse:
        ok = True

        @staticmethod
        def json():
            return {
                "aud": "another-app.apps.googleusercontent.com",
                "sub": "google-user-123",
                "email": "someone@test.nl",
                "email_verified": "true",
            }

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: GoogleResponse())
    response = client.post("/auth/google", json={"id_token": "test-google-id-token"})

    assert response.status_code == 401
    assert response.json()["error_code"] == "GOOGLE_TOKEN_INVALID"


def test_first_account_claims_owner_once(monkeypatch):
    """Ook na een deploy kan er via de permanente marker maar één owner zijn."""
    records = {}

    def fake_get(namespace, key):
        return records.get((namespace, key))

    def fake_put(namespace, key, value):
        records[(namespace, key)] = value.copy()

    monkeypatch.setattr(cache_store, "get_json", fake_get)
    monkeypatch.setattr(cache_store, "put_json", fake_put)

    first = {"id": "first", "email": "first@test.nl", "plan": "free"}
    second = {"id": "second", "email": "second@test.nl", "plan": "free"}
    monkeypatch.delenv("OWNER_EMAIL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    auth._save_new_user(first)
    auth._save_new_user(second)

    assert first["plan"] == "owner"
    assert second["plan"] == "free"
    assert records[("app_config", "owner")]["user_id"] == "first"


def test_production_owner_is_only_claimed_by_configured_email(monkeypatch):
    records = {}
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_EMAIL", "owner@test.nl")
    monkeypatch.setattr(cache_store, "get_json", lambda namespace, key: records.get((namespace, key)))
    monkeypatch.setattr(cache_store, "put_json", lambda namespace, key, value: records.__setitem__((namespace, key), value.copy()))

    visitor = {"id": "visitor", "email": "owner@test.nl", "plan": "free"}
    owner = {"id": "owner", "email": "owner@test.nl", "plan": "free"}
    # Alleen het adres kennen is niet genoeg: een onbevestigde wachtwoordsignup
    # mag de eigenaarstitel niet kapen.
    auth._save_new_user(visitor)
    auth._save_new_user(owner, verified_email=True)

    assert visitor["plan"] == "free"
    assert owner["plan"] == "owner"
    assert records[("app_config", "owner")]["user_id"] == "owner"


def test_owner_plan_is_unlimited():
    assert usage.plan_limit("owner") is None


def test_monthly_credit_limits_have_no_daily_cap(monkeypatch):
    for name in ("FREE_MONTHLY_CREDITS", "PREMIUM_MONTHLY_CREDITS", "ULTRA_MONTHLY_CREDITS"):
        monkeypatch.delenv(name, raising=False)

    assert usage.plan_limit("free") == 200
    assert usage.plan_limit("plus") == 1000  # legacy alias
    assert usage.plan_limit("premium") == 1000
    assert usage.plan_limit("pro") == 2000  # legacy alias
    assert usage.plan_limit("ultra") == 2000
    assert not hasattr(usage, "daily_limit")


def test_exact_content_is_charged_once_per_account(monkeypatch):
    records = {}

    monkeypatch.setenv("ENABLE_QUOTA", "true")
    monkeypatch.setattr(cache_store, "get_json", lambda ns, key: records.get((ns, key)))
    monkeypatch.setattr(cache_store, "put_json", lambda ns, key, value: records.__setitem__((ns, key), value.copy()))

    assert usage.consume("student-a", "free", cost=1, unlock_key="explain:dia-1")
    assert usage.status("student-a", "free")[0] == 1
    assert usage.consume("student-a", "free", cost=1, unlock_key="explain:dia-1")
    assert usage.status("student-a", "free")[0] == 1

    assert usage.consume("student-b", "free", cost=1, unlock_key="explain:dia-1")
    assert usage.status("student-b", "free")[0] == 1


def test_weighted_monthly_credits_are_enforced(monkeypatch):
    records = {}

    monkeypatch.setenv("ENABLE_QUOTA", "true")
    monkeypatch.setenv("FREE_MONTHLY_CREDITS", "3")
    monkeypatch.setattr(cache_store, "get_json", lambda ns, key: records.get((ns, key)))
    monkeypatch.setattr(cache_store, "put_json", lambda ns, key, value: records.__setitem__((ns, key), value.copy()))

    assert usage.consume("student", "free", cost=2, unlock_key="summary:a")
    assert not usage.consume("student", "free", cost=2, unlock_key="quiz:b")
    assert usage.consume("student", "free", cost=1, unlock_key="explain:c")
    assert usage.status("student", "free")[0] == 3


def test_device_session_is_long_lived_and_renews(client, monkeypatch):
    """Een terugkerend apparaat blijft ingelogd zonder opnieuw aan te melden."""
    monkeypatch.setenv("SESSION_DAYS", "365")
    email = f"remember-{uuid.uuid4().hex[:10]}@test.nl"
    response = client.post("/auth/register", json={"email": email, "password": "geheim1234"})
    assert response.status_code == 200
    token = response.json()["token"]
    key = auth._token_key(token)
    session = cache_store.get_json("sessions", key)
    assert session["expires_at"] > time.time() + 364 * 86400

    session["expires_at"] = time.time() + 60
    cache_store.put_json("sessions", key, session)
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 200
    renewed = cache_store.get_json("sessions", key)
    assert renewed["expires_at"] > time.time() + 364 * 86400
    assert renewed["last_seen_at"] <= time.time()


def test_upload_creates_document_with_owner(uploaded_doc, client):
    file_hash, headers = uploaded_doc
    resp = client.get(f"/document/{file_hash}", headers=headers)
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["total_pages"] == 1
    assert doc["file_name"] == "test.pdf"


def test_public_speculative_prefetch_is_disabled_by_default(uploaded_doc, client):
    file_hash, headers = uploaded_doc

    response = client.post(f"/prefetch/{file_hash}/0", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "prefetched": False, "reason": "disabled"}


def test_slide_image_is_authenticated_and_privately_cached(uploaded_doc, client):
    file_hash, headers = uploaded_doc
    response = client.get(f"/slide-image/{file_hash}/0", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["cache-control"].startswith("private,")
    assert response.headers["vary"] == "Authorization"

    anonymous = client.get(f"/slide-image/{file_hash}/0")
    assert anonymous.status_code == 401


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


def test_andere_gebruiker_ziet_jouw_document_niet(uploaded_doc, client, make_account):
    """De kern van de isolatie: voor iemand anders bestaat jouw document niet."""
    file_hash, owner_headers = uploaded_doc
    ander = make_account()

    # Onzichtbaar in de lijst
    assert file_hash not in [d["file_hash"] for d in client.get("/documents", headers=ander).json()["documents"]]
    # En niet op te vragen: 404 (niet 403 — anders kun je raden wat er bestaat)
    for path in (f"/document/{file_hash}", f"/document/{file_hash}/notes", f"/slide-image/{file_hash}/0"):
        assert client.get(path, headers=ander).status_code == 404, path
    assert client.delete(f"/document/{file_hash}", headers=ander).status_code == 404
    # De eigenaar kan alles nog wel
    assert client.get(f"/document/{file_hash}", headers=owner_headers).status_code == 200
    assert client.delete(f"/document/{file_hash}", headers=owner_headers).status_code == 200


def test_zonder_inloggen_geen_toegang(client, uploaded_doc):
    file_hash, _ = uploaded_doc
    for path in ("/documents", "/folders", "/wordlists", f"/document/{file_hash}"):
        r = client.get(path)
        assert r.status_code == 401, f"{path} gaf {r.status_code}"
        assert r.json()["error_code"] == "NOT_AUTHENTICATED"


def test_notities_lekken_niet_naar_andere_gebruiker(client, make_account, make_pdf_bytes):
    """Twee accounts die hetzelfde bestand uploaden delen de (dure) tekst- en
    beeldverwerking, maar nooit elkaars aantekeningen."""
    pdf = make_pdf_bytes(f"Gedeeld bestand {uuid.uuid4()}")
    a_headers, b_headers = make_account(), make_account()

    ra = client.post("/upload", files={"file": ("test.pdf", pdf, "application/pdf")}, headers=a_headers)
    file_hash = ra.json()["file_hash"]
    client.post(f"/document/{file_hash}/notes",
                json={"page_index": 0, "note": "aantekening van A", "star": True}, headers=a_headers)

    # B uploadt exact dezelfde bytes: zelfde hash, eigen bibliotheek.
    rb = client.post("/upload", files={"file": ("test.pdf", pdf, "application/pdf")}, headers=b_headers)
    assert rb.json()["file_hash"] == file_hash
    notes_b = client.get(f"/document/{file_hash}/notes", headers=b_headers).json()
    assert notes_b["pages"] == {}, "B ziet de aantekening van A"
    notes_a = client.get(f"/document/{file_hash}/notes", headers=a_headers).json()
    assert notes_a["pages"]["0"]["note"] == "aantekening van A"


def test_plan_uit_header_wordt_genegeerd(client, make_account):
    """Je plan komt uit je account, niet uit een header die je zelf stuurt."""
    headers = {**make_account(), "X-User-Plan": "premium"}
    me = client.get("/auth/me", headers=headers).json()
    assert me["user"]["plan"] == "free"


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


def test_image_quality_flags_dark_but_passes_clean():
    from PIL import Image, ImageDraw
    import io as _io
    # Scherpe, lichte "foto": witte achtergrond met veel zwarte randen => OK.
    good = Image.new("L", (600, 400), 255)
    d = ImageDraw.Draw(good)
    for i in range(0, 600, 24):
        d.line([(i, 0), (i, 400)], fill=0, width=2)
    for j in range(0, 400, 40):
        d.line([(0, j), (600, j)], fill=0, width=2)
    buf = _io.BytesIO(); good.save(buf, "PNG")
    assert assess_image_quality(buf.getvalue()) is None

    # Sterk verduisterde versie => moet 'dark' melden (niet blokkerend, alleen tip).
    dark = good.point(lambda p: p // 6)
    buf2 = _io.BytesIO(); dark.save(buf2, "PNG")
    res = assess_image_quality(buf2.getvalue())
    assert res and "dark" in res["issues"]


def test_rate_limit_override_is_independent_of_default(client):
    key = f"test-upload-ip-{uuid.uuid4()}"
    assert rate_limit.check(key, max_per_window=2)
    assert rate_limit.check(key, max_per_window=2)
    assert not rate_limit.check(key, max_per_window=2)
    # Een andere key met de standaardlimiet is hierdoor niet geraakt.
    assert rate_limit.check(f"other-{uuid.uuid4()}")


def test_folder_ownership(client, make_account):
    owner_headers = make_account()
    resp = client.post("/folders", json={"name": f"Testvak {uuid.uuid4()}"}, headers=owner_headers)
    assert resp.status_code == 200
    folder_id = resp.json()["folder"]["id"]

    ander = make_account()
    assert client.get("/folders", headers=ander).json()["folders"] == []
    blocked = client.patch(f"/folders/{folder_id}", json={"name": "Andere naam"}, headers=ander)
    assert blocked.status_code == 404

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


def test_upload_kind_quick_echoed_in_list(client, make_pdf_bytes, auth_headers):
    pdf = make_pdf_bytes(f"Quick {uuid.uuid4()}")
    resp = client.post("/upload", files={"file": ("q.pdf", pdf, "application/pdf")},
                       data={"kind": "quick"}, headers=auth_headers)
    file_hash = resp.json()["file_hash"]
    docs = client.get("/documents", headers=auth_headers).json()["documents"]
    match = next(d for d in docs if d["file_hash"] == file_hash)
    assert match["kind"] == "quick"


def test_wordlist_crud_ownership_and_stable_ids(client, make_account):
    owner = make_account()
    created = client.post("/wordlists", json={
        "name": f"Frans {uuid.uuid4()}",
        "cards": [{"term": "chien", "definition": "hond"}, {"term": "chat", "definition": "kat"}],
    }, headers=owner).json()["wordlist"]
    list_id = created["id"]
    assert created["total"] == 2
    id_chien = next(c["id"] for c in created["cards"] if c["term"] == "chien")

    # eigendom: een ander mag niet verwijderen
    assert client.request("DELETE", f"/wordlists/{list_id}", headers=make_account()).status_code == 404

    # review werkt en verzet de due-datum
    rev = client.post(f"/wordlists/{list_id}/review", json={"card_id": id_chien, "rating": "good"}, headers=owner)
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
    assert client.get(f"/wordlists/{list_id}", headers=owner).status_code == 404
