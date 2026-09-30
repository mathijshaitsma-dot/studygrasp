"""Stripe Checkout, klantenportaal en ondertekende webhooks."""
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

import auth
import billing_service
from core import raise_api_error


router = APIRouter()
logger = logging.getLogger(__name__)


class CheckoutRequest(BaseModel):
    plan: str


@router.get("/billing/status")
def billing_status(request: Request):
    user = auth.require_user(request)
    return {"ok": True, **billing_service.public_status(user)}


@router.post("/billing/checkout")
def create_checkout(req: CheckoutRequest, request: Request):
    user = auth.require_user(request)
    try:
        url = billing_service.checkout_url(user, req.plan)
    except ValueError as exc:
        raise_api_error(400, "BILLING_INVALID_PLAN", str(exc))
    except PermissionError as exc:
        raise_api_error(409, "BILLING_PORTAL_REQUIRED", str(exc))
    except Exception:
        logger.exception("Stripe Checkout kon niet worden aangemaakt")
        raise_api_error(503, "BILLING_UNAVAILABLE", "Betalen is tijdelijk niet beschikbaar. Probeer het later opnieuw.")
    return {"ok": True, "url": url}


@router.post("/billing/portal")
def create_portal(request: Request):
    user = auth.require_user(request)
    try:
        url = billing_service.portal_url(user)
    except ValueError as exc:
        raise_api_error(404, "BILLING_CUSTOMER_NOT_FOUND", str(exc))
    except Exception:
        logger.exception("Stripe-klantenportaal kon niet worden aangemaakt")
        raise_api_error(503, "BILLING_UNAVAILABLE", "Facturering is tijdelijk niet beschikbaar.")
    return {"ok": True, "url": url}


@router.post("/billing/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = billing_service.construct_event(payload, signature)
    except Exception:
        logger.warning("Ongeldige Stripe-webhook geweigerd", exc_info=True)
        raise_api_error(400, "BILLING_WEBHOOK_INVALID", "Ongeldige Stripe-webhook.")
    billing_service.process_event(event)
    return {"ok": True}
