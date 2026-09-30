from types import SimpleNamespace
import time

import auth
import billing_service
import cache_store


def _free_account(client, make_account):
    # Het allereerste testaccount kan lokaal eigenaar worden. Een tweede uniek
    # account is altijd gratis en kan dus veilig door Checkout.
    make_account()
    headers = make_account()
    user = client.get("/auth/me", headers=headers).json()["user"]
    return headers, user


def _stripe_env(monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_fake")
    monkeypatch.setenv("STRIPE_PREMIUM_PRICE_ID", "price_premium")
    monkeypatch.setenv("STRIPE_ULTRA_PRICE_ID", "price_ultra")
    monkeypatch.setenv("STRIPE_MANAGED_PAYMENTS", "true")
    monkeypatch.setenv("APP_BASE_URL", "https://studygrasp.example")


def test_checkout_is_server_priced_and_account_bound(client, make_account, monkeypatch):
    headers, user = _free_account(client, make_account)
    _stripe_env(monkeypatch)
    captured = {}

    class Customers:
        @staticmethod
        def create(params):
            captured["customer"] = params
            return SimpleNamespace(id="cus_test")

    class CheckoutSessions:
        @staticmethod
        def create(params):
            captured["checkout"] = params
            return SimpleNamespace(url="https://checkout.stripe.test/session")

    fake = SimpleNamespace(v1=SimpleNamespace(
        customers=Customers(),
        checkout=SimpleNamespace(sessions=CheckoutSessions()),
    ))
    monkeypatch.setattr(billing_service, "_client", lambda: fake)

    response = client.post("/billing/checkout", json={"plan": "premium"}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["url"].startswith("https://checkout.stripe.test/")
    assert captured["checkout"]["line_items"] == [{"price": "price_premium", "quantity": 1}]
    assert captured["checkout"]["client_reference_id"] == user["id"]
    assert captured["checkout"]["managed_payments"] == {"enabled": True}
    assert captured["customer"]["metadata"]["studygrasp_user_id"] == user["id"]


def test_browser_cannot_choose_an_unknown_plan(client, make_account, monkeypatch):
    headers, _ = _free_account(client, make_account)
    _stripe_env(monkeypatch)

    response = client.post("/billing/checkout", json={"plan": "owner"}, headers=headers)

    assert response.status_code == 400
    assert response.json()["error_code"] == "BILLING_INVALID_PLAN"


def test_checkout_is_closed_without_explicit_enable(client, make_account, monkeypatch):
    headers, _ = _free_account(client, make_account)
    _stripe_env(monkeypatch)
    monkeypatch.setenv("BILLING_ENABLED", "false")

    status = client.get("/billing/status", headers=headers)
    checkout = client.post("/billing/checkout", json={"plan": "premium"}, headers=headers)

    assert status.status_code == 200
    assert status.json()["configured"] is False
    assert checkout.status_code == 503
    assert checkout.json()["error_code"] == "BILLING_UNAVAILABLE"


def test_signed_subscription_events_control_plan(client, make_account, monkeypatch):
    _, user = _free_account(client, make_account)
    _stripe_env(monkeypatch)
    cache_store.put_json("billing_customers", "cus_webhook", {"user_id": user["id"]})
    now = int(time.time())

    active = {
        "id": "evt_active",
        "type": "customer.subscription.updated",
        "created": now,
        "data": {"object": {
            "id": "sub_test",
            "customer": "cus_webhook",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": now + 2592000,
            "items": {"data": [{"price": {"id": "price_premium"}}]},
        }},
    }
    assert billing_service.process_event(active) is True
    assert auth.get_user(user["id"])["plan"] == "premium"
    assert billing_service.process_event(active) is False  # webhookretry is idempotent

    cancelled = {
        "id": "evt_cancelled",
        "type": "customer.subscription.deleted",
        "created": now + 1,
        "data": {"object": {
            "id": "sub_test",
            "customer": "cus_webhook",
            "status": "canceled",
            "items": {"data": [{"price": {"id": "price_premium"}}]},
        }},
    }
    assert billing_service.process_event(cancelled) is True
    assert auth.get_user(user["id"])["plan"] == "free"


def test_old_webhook_cannot_undo_newer_subscription_state(client, make_account, monkeypatch):
    _, user = _free_account(client, make_account)
    _stripe_env(monkeypatch)
    cache_store.put_json("billing_customers", "cus_order", {"user_id": user["id"]})
    base = {
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_order",
            "customer": "cus_order",
            "items": {"data": [{"price": {"id": "price_ultra"}}]},
        }},
    }
    newer = {**base, "id": "evt_new", "created": 200, "data": {"object": {**base["data"]["object"], "status": "active"}}}
    older = {**base, "id": "evt_old", "created": 100, "data": {"object": {**base["data"]["object"], "status": "canceled"}}}

    billing_service.process_event(newer)
    billing_service.process_event(older)

    assert auth.get_user(user["id"])["plan"] == "ultra"


def test_cancelled_subscription_allows_new_checkout(client, make_account, monkeypatch):
    headers, user = _free_account(client, make_account)
    _stripe_env(monkeypatch)
    cache_store.put_json("billing_accounts", user["id"], {
        "user_id": user["id"],
        "customer_id": "cus_returning",
        "subscription_id": "sub_cancelled",
        "subscription_status": "canceled",
    })

    status = client.get("/billing/status", headers=headers)

    assert status.status_code == 200
    assert status.json()["customer"] is True
    assert status.json()["subscription"] is False
