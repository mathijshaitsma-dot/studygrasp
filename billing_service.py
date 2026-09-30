"""Veilige Stripe Billing-koppeling voor StudyGrasp.

De browser kiest alleen een plan. Prijzen, klantkoppelingen en planwijzigingen
worden server-side bepaald. Een account krijgt pas Premium of Ultra nadat een
ondertekende Stripe-webhook een actieve subscription bevestigt.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import stripe
from stripe import StripeClient

import auth
import cache_store


ACTIVE_STATUSES = {"active", "trialing"}
PAID_PLANS = {"premium", "ultra"}


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def price_ids() -> dict[str, str]:
    return {
        "premium": _env("STRIPE_PREMIUM_PRICE_ID"),
        "ultra": _env("STRIPE_ULTRA_PRICE_ID"),
    }


def configured() -> bool:
    prices = price_ids()
    return bool(_env("STRIPE_SECRET_KEY") and _env("STRIPE_WEBHOOK_SECRET")
                and all(prices.values()))


def checkout_configured() -> bool:
    prices = price_ids()
    return bool(_env("STRIPE_SECRET_KEY") and all(prices.values()))


def _client() -> StripeClient:
    key = _env("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("Stripe is niet geconfigureerd.")
    return StripeClient(key, max_network_retries=2)


def _account_record(user_id: str) -> dict[str, Any]:
    return cache_store.get_json("billing_accounts", user_id) or {"user_id": user_id}


def _save_account_record(user_id: str, record: dict[str, Any]) -> None:
    record["user_id"] = user_id
    record["updated_at"] = time.time()
    cache_store.put_json("billing_accounts", user_id, record)
    customer_id = record.get("customer_id")
    if customer_id:
        cache_store.put_json("billing_customers", customer_id, {"user_id": user_id})


def _customer_id_for(user: dict[str, Any]) -> str:
    record = _account_record(user["id"])
    if record.get("customer_id"):
        return record["customer_id"]

    customer = _client().v1.customers.create({
        "email": user.get("email"),
        "metadata": {"studygrasp_user_id": user["id"]},
    })
    record["customer_id"] = customer.id
    _save_account_record(user["id"], record)
    return customer.id


def checkout_url(user: dict[str, Any], plan: str) -> str:
    if not configured():
        raise RuntimeError("Stripe Checkout en de webhook zijn nog niet volledig geconfigureerd.")
    plan = (plan or "").strip().lower()
    prices = price_ids()
    if plan not in PAID_PLANS or not prices.get(plan):
        raise ValueError("Onbekend of niet-geconfigureerd plan.")
    if user.get("plan") == "owner":
        raise PermissionError("Het eigenaarsaccount heeft al onbeperkte toegang.")

    record = _account_record(user["id"])
    if record.get("subscription_id") and record.get("subscription_status") in ACTIVE_STATUSES:
        raise PermissionError("Beheer je bestaande abonnement via het klantenportaal.")

    base = _env("APP_BASE_URL").rstrip("/")
    if not base.startswith("https://") and _env("APP_ENV").lower() == "production":
        raise RuntimeError("APP_BASE_URL is niet veilig geconfigureerd.")

    customer_id = _customer_id_for(user)
    params: dict[str, Any] = {
        "mode": "subscription",
        "customer": customer_id,
        "client_reference_id": user["id"],
        "line_items": [{"price": prices[plan], "quantity": 1}],
        "success_url": f"{base}/?checkout=success#/billing",
        "cancel_url": f"{base}/?checkout=cancelled#/billing",
        "metadata": {"studygrasp_user_id": user["id"], "studygrasp_plan": plan},
        "subscription_data": {
            "metadata": {"studygrasp_user_id": user["id"], "studygrasp_plan": plan},
        },
        "allow_promotion_codes": True,
    }
    # Managed Payments is de gekozen Merchant-of-Record-configuratie. Het veld
    # is nieuw en wordt door de API al geaccepteerd, ook als de gegenereerde
    # types van een stabiele SDK-versie nog achterlopen.
    if _env("STRIPE_MANAGED_PAYMENTS").lower() != "false":
        params["managed_payments"] = {"enabled": True}

    session = _client().v1.checkout.sessions.create(params)
    return session.url


def portal_url(user: dict[str, Any]) -> str:
    record = _account_record(user["id"])
    customer_id = record.get("customer_id")
    if not customer_id:
        raise ValueError("Er is nog geen Stripe-klant voor dit account.")
    base = _env("APP_BASE_URL").rstrip("/")
    session = _client().v1.billing_portal.sessions.create({
        "customer": customer_id,
        "return_url": f"{base}/#/billing",
    })
    return session.url


def public_status(user: dict[str, Any]) -> dict[str, Any]:
    record = _account_record(user["id"])
    subscription_status = record.get("subscription_status")
    return {
        # Toon Checkout pas als ook de webhook klaarstaat. Zonder webhook kan
        # Stripe wel innen, maar kan StudyGrasp het betaalde plan niet veilig
        # activeren.
        "configured": configured(),
        "customer": bool(record.get("customer_id")),
        # Een beëindigde subscription-ID blijft voor historie bewaard, maar
        # mag een nieuwe Checkout niet blokkeren.
        "subscription": subscription_status in ACTIVE_STATUSES,
        "subscription_status": subscription_status,
        "cancel_at_period_end": bool(record.get("cancel_at_period_end")),
        "current_period_end": record.get("current_period_end"),
    }


def construct_event(payload: bytes, signature: str) -> dict[str, Any]:
    secret = _env("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET ontbreekt.")
    return stripe.Webhook.construct_event(payload, signature, secret)


def _plain(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict(for_json=True)
    return dict(value)


def _plan_from_subscription(subscription: dict[str, Any]) -> Optional[str]:
    known = {price_id: plan for plan, price_id in price_ids().items() if price_id}
    items = (((subscription.get("items") or {}).get("data")) or [])
    for item in items:
        item = _plain(item)
        price = _plain(item.get("price") or {})
        if price.get("id") in known:
            return known[price["id"]]
    return None


def _user_id_for_customer(customer_id: str) -> Optional[str]:
    relation = cache_store.get_json("billing_customers", customer_id) or {}
    return relation.get("user_id")


def _apply_subscription(subscription: dict[str, Any], event_created: int) -> None:
    customer_id = subscription.get("customer")
    if isinstance(customer_id, dict):
        customer_id = customer_id.get("id")
    metadata = subscription.get("metadata") or {}
    user_id = metadata.get("studygrasp_user_id") or _user_id_for_customer(customer_id or "")
    if not user_id:
        return

    record = _account_record(user_id)
    if event_created < int(record.get("last_event_created") or 0):
        return

    status = subscription.get("status") or "unknown"
    plan = _plan_from_subscription(subscription)
    active_plan = plan if status in ACTIVE_STATUSES and plan else "free"
    record.update({
        "customer_id": customer_id,
        "subscription_id": subscription.get("id"),
        "subscription_status": status,
        "cancel_at_period_end": bool(subscription.get("cancel_at_period_end")),
        "current_period_end": subscription.get("current_period_end"),
        "last_event_created": event_created,
    })
    _save_account_record(user_id, record)
    auth.set_plan(user_id, active_plan)


def process_event(event: dict[str, Any]) -> bool:
    event = _plain(event)
    event_id = event.get("id")
    if not event_id or cache_store.get_json("billing_events", event_id):
        return False

    event_type = event.get("type") or ""
    data_object = _plain(((event.get("data") or {}).get("object")) or {})
    created = int(event.get("created") or time.time())

    if event_type.startswith("customer.subscription."):
        _apply_subscription(data_object, created)
    elif event_type == "checkout.session.completed":
        user_id = data_object.get("client_reference_id") or (data_object.get("metadata") or {}).get("studygrasp_user_id")
        if user_id:
            record = _account_record(user_id)
            record.update({
                "customer_id": data_object.get("customer") or record.get("customer_id"),
                "subscription_id": data_object.get("subscription") or record.get("subscription_id"),
                "checkout_completed_at": created,
            })
            _save_account_record(user_id, record)

    cache_store.put_json("billing_events", event_id, {
        "type": event_type,
        "processed_at": time.time(),
    })
    return True
