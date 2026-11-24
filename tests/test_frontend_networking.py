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


def test_home_upload_supports_multi_select_drop_and_bounded_parallelism():
    home = (ROOT / "frontend" / "js" / "views" / "home.js").read_text(encoding="utf-8")

    assert 'multiple: ""' in home
    assert "Array.from(fileInput.files || [])" in home
    assert "Array.from(e.dataTransfer.files || [])" in home
    assert "startUploads(files)" in home
    assert "api.upload(row.file" in home
    assert "Math.min(10, files.length)" in home
    assert "await Promise.all" in home


def test_multi_upload_copy_exists_in_every_supported_language():
    translations = (ROOT / "frontend" / "js" / "i18n.js").read_text(encoding="utf-8")

    for key in (
        "upload_batch_title", "upload_batch_progress", "upload_batch_done",
        "upload_batch_partial", "upload_item_done", "upload_item_failed",
        "upload_item_ready",
    ):
        assert translations.count(f"{key}:") == 5


def test_owner_account_management_can_sort_by_plan_and_monthly_usage():
    admin = (ROOT / "frontend" / "js" / "views" / "admin.js").read_text(encoding="utf-8")
    translations = (ROOT / "frontend" / "js" / "i18n.js").read_text(encoding="utf-8")

    assert 'mode.startsWith("plan_")' in admin
    assert 'mode.startsWith("usage_")' in admin
    assert "a.usage?.used" in admin and "b.usage?.used" in admin
    assert 'value: "plan_asc"' in admin and 'value: "plan_desc"' in admin
    assert 'value: "usage_desc"' in admin and 'value: "usage_asc"' in admin
    for key in (
        "admin_sort", "admin_sort_newest", "admin_sort_plan_asc",
        "admin_sort_plan_desc", "admin_sort_usage_desc", "admin_sort_usage_asc",
    ):
        assert translations.count(f"{key}:") == 5


def test_owner_unlimited_usage_reveals_actual_monthly_credits_accessibly():
    admin = (ROOT / "frontend" / "js" / "views" / "admin.js").read_text(encoding="utf-8")
    styles = (ROOT / "frontend" / "css" / "app.css").read_text(encoding="utf-8")
    translations = (ROOT / "frontend" / "js" / "i18n.js").read_text(encoding="utf-8")

    assert 'class: "admin-usage-unlimited", tabindex: "0"' in admin
    assert 'class: "admin-usage-tooltip", role: "tooltip"' in admin
    assert 't("admin_usage_actual", { n: usage.used || 0 })' in admin
    assert ".admin-usage-tooltip-wrap:hover .admin-usage-tooltip" in styles
    assert ".admin-usage-tooltip-wrap:focus-within .admin-usage-tooltip" in styles
    assert translations.count("admin_usage_actual:") == 5
