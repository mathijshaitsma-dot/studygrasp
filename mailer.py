"""E-mail versturen (wachtwoord vergeten).

Bewust gewone SMTP in plaats van een dienst-specifieke API: dat werkt met Gmail,
Outlook, je eigen domein én met diensten als Resend of Postmark, zonder dat de
code aan één leverancier vastzit.

Is SMTP niet ingesteld, dan verstuurt de app niets en zet hij de link in de log.
Zo kun je lokaal het hele herstelproces doorlopen zonder mailserver — en weet je
zeker dat je het in productie vergeet als je het vergeet, want dan komt er geen
mail aan (in plaats van stilletjes te "slagen").
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from core import logger

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "").strip() or SMTP_USER
APP_NAME = "StudyGrasp"


def configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def send(to: str, subject: str, body_text: str) -> bool:
    """Verstuurt een platte-tekstmail. Geeft terug of het gelukt is; een fout
    wordt gelogd maar nooit doorgegeven aan de aanroeper — of een mailserver het
    even niet doet, mag niet zichtbaar zijn voor degene die een reset aanvraagt
    (zie routers/account.py: dat antwoord is altijd hetzelfde)."""
    if not configured():
        logger.warning("SMTP niet ingesteld — mail aan %s niet verstuurd. Inhoud:\n%s", to, body_text)
        return False

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
