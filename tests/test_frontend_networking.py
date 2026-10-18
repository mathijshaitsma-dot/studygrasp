from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_frontend_discards_persisted_api_override():
    config = (ROOT / "frontend" / "js" / "config.js").read_text(encoding="utf-8")

    assert 'if (localDev) storedOverride = localStorage.getItem("sc.apiBase")' in config
    assert 'else localStorage.removeItem("sc.apiBase")' in config
    assert "storedOverride ||" in config


def test_service_worker_never_caches_same_origin_api_responses():
    worker = (ROOT / "frontend" / "sw.js").read_text(encoding="utf-8")

    static_branch = "url.origin === self.location.origin && isStaticAppAsset(url)"
    api_passthrough = "if (url.origin === self.location.origin) return;"
    assert static_branch in worker
    assert api_passthrough in worker
    assert worker.index(static_branch) < worker.index(api_passthrough)


def test_service_worker_updates_bypass_the_http_cache():
    app = (ROOT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")

    assert 'register("sw.js", { updateViaCache: "none" })' in app
