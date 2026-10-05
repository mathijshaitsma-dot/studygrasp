"""Transactionele e-mail versturen (wachtwoord vergeten).

Brevo's HTTPS-API heeft in productie de voorkeur, omdat Railway uitgaande SMTP
op Free-, Trial- en Hobby-plannen blokkeert. Gewone SMTP blijft beschikbaar als
provider-onafhankelijke fallback, bijvoorbeeld voor lokale installaties of een
Railway Pro-service.

Is geen provider ingesteld, dan verstuurt de app niets en schrijft hij een
waarschuwing zonder herstellink of andere gevoelige inhoud naar de log.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

import requests

from core import logger

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "").strip() or SMTP_USER
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
BREVO_FROM_EMAIL = os.getenv("BREVO_FROM_EMAIL", "").strip() or SMTP_FROM
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
APP_NAME = "StudyGrasp"


def configured() -> bool:
    return bool((BREVO_API_KEY and BREVO_FROM_EMAIL) or (SMTP_HOST and SMTP_FROM))


def _send_brevo(to: str, subject: str, body_text: str) -> bool:
    """Verstuur via Brevo's HTTPS-API; geschikt voor alle Railway-plannen."""
    try:
        response = requests.post(
            BREVO_API_URL,
            headers={
                "accept": "application/json",
                "api-key": BREVO_API_KEY,
                "content-type": "application/json",
            },
            json={
                "sender": {"name": APP_NAME, "email": BREVO_FROM_EMAIL},
                "to": [{"email": to}],
                "subject": subject,
                "textContent": body_text,
            },
            timeout=20,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        # Nooit headers of requestdata loggen: daarin staat de geheime API-key.
        status = getattr(getattr(e, "response", None), "status_code", None)
        detail = f"HTTP {status}" if status else type(e).__name__
        logger.warning("Brevo API-mail aan %s mislukt: %s", to, detail)
        return False


def send(to: str, subject: str, body_text: str) -> bool:
    """Verstuurt een platte-tekstmail. Geeft terug of het gelukt is; een fout
    wordt gelogd maar nooit doorgegeven aan de aanroeper — of een mailserver het
    even niet doet, mag niet zichtbaar zijn voor degene die een reset aanvraagt
    (zie routers/account.py: dat antwoord is altijd hetzelfde)."""
    if not configured():
        logger.warning("E-mailprovider niet ingesteld — mail aan %s niet verstuurd", to)
        return False

    if BREVO_API_KEY and BREVO_FROM_EMAIL:
        return _send_brevo(to, subject, body_text)

    msg = EmailMessage()
    msg["From"] = f"{APP_NAME} <{SMTP_FROM}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_text)

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=20) as s:
                if SMTP_USER:
                    s.login(SMTP_USER, SMTP_PASSWORD)
                s.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                if SMTP_USER:
                    s.login(SMTP_USER, SMTP_PASSWORD)
                s.send_message(msg)
        return True
    except Exception as e:
        logger.warning("Versturen van mail aan %s mislukt: %s", to, str(e)[:200])
        return False


def send_password_reset(to: str, reset_url: str) -> bool:
    return send(
        to,
        f"{APP_NAME}: je wachtwoord opnieuw instellen",
        f"""Hoi,

Je hebt gevraagd om je wachtwoord voor {APP_NAME} opnieuw in te stellen.
Open deze link om een nieuw wachtwoord te kiezen:

{reset_url}

De link is 1 uur geldig en werkt één keer.

Heb je dit niet aangevraagd? Dan hoef je niets te doen: je wachtwoord blijft
gewoon zoals het was.
""",
    )


def send_email_verification(to: str, verify_url: str) -> bool:
    return send(
        to,
        f"{APP_NAME}: bevestig je e-mailadres",
        f"""Hoi,

Je bent bijna klaar met het aanmaken van je {APP_NAME}-account.
Bevestig je e-mailadres via deze link:

{verify_url}

De link is 1 uur geldig en werkt één keer.

Heb je zelf geen account aangevraagd? Dan kun je deze e-mail negeren.
""",
    )
