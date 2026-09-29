"""
Testconfiguratie: zet env-variabelen VOORDAT backend.py/cache_store.py worden
geïmporteerd (die lezen BASE_DIR e.a. bij module-import), zodat tests op een
tijdelijke, lege cache-map draaien en nooit de echte backend_cache_v3
aanraken. Schakelt ook prefetch uit — anders zou een upload in de tests
achtergrond-taken starten die (op een aparte thread) proberen een echte AI-
provider aan te roepen.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["BACKEND_CACHE_DIR"] = tempfile.mkdtemp(prefix="sc_test_cache_")
os.environ["PREFETCH_ON_UPLOAD"] = "0"
os.environ["PREFETCH_STUDY_ON_UPLOAD"] = "false"
os.environ["APP_ENV"] = "development"
os.environ["EMAIL_REGISTRATION_ENABLED"] = "true"
os.environ["RATE_LIMIT_REGISTER_MAX_PER_HOUR"] = "1000"
# Tests zijn altijd volledig lokaal. Lege proceswaarden voorkomen dat
# load_dotenv later echte Supabase-secrets aanvult en fixtures naar L2 schrijft.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""
os.environ["GEMINI_API_KEYS"] = "test-key-never-sent"
os.environ["GROQ_API_KEYS"] = ""
os.environ["OPENROUTER_API_KEYS"] = ""
os.environ["MISTRAL_API_KEYS"] = ""
os.environ["GITHUB_MODELS_TOKEN"] = ""
os.environ["GOOGLE_CLIENT_ID"] = "test-client.apps.googleusercontent.com"
os.environ.setdefault("ENABLE_QUOTA", "false")
os.environ["RATE_LIMIT_MAX_PER_MIN"] = "1000"  # andere tests mogen de noodrem niet per ongeluk raken
os.environ["RATE_LIMIT_UPLOAD_MAX_PER_MIN"] = "1000"  # idem voor de losse upload-noodrem

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import backend  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(backend.app)


@pytest.fixture()
def make_pdf_bytes():
    """Fabriceert een minimaal geldig 1-pagina-PDF met unieke tekst, zodat
    elke test een eigen file_hash krijgt (content-addressed opslag)."""
    def _make(text: str) -> bytes:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text)
        data = doc.tobytes()
        doc.close()
        return data
    return _make


@pytest.fixture()
def make_account(client):
    """Maakt een account aan en geeft de Authorization-headers terug. Elke test
    krijgt een eigen e-mailadres, zodat tests elkaar niet in de weg zitten."""
    import uuid

    def _make():
        email = f"t{uuid.uuid4().hex[:12]}@test.nl"
        resp = client.post("/auth/register", json={"email": email, "password": "geheim1234"})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['token']}"}
    return _make


@pytest.fixture()
def auth_headers(make_account):
    return make_account()


@pytest.fixture()
def uploaded_doc(client, make_pdf_bytes, auth_headers):
    """Upload een uniek testdocument en geeft (file_hash, headers) terug."""
    import uuid
    pdf = make_pdf_bytes(f"Testdocument {uuid.uuid4()}")
    resp = client.post("/upload", files={"file": ("test.pdf", pdf, "application/pdf")}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["file_hash"], auth_headers
