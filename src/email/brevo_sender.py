"""Envoi de la campagne via l'API Brevo — voir PLAN.md §5.

L'envoi n'est jamais déclenché par le cron : il part de l'interface de
validation, sur action humaine explicite.
"""
import html
import logging
from datetime import datetime

import requests

from src.config import (
    BREVO_API_KEY,
    BREVO_LIST_ID,
    BREVO_SENDER_EMAIL,
    BREVO_SENDER_NAME,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.brevo.com/v3"
REQUEST_TIMEOUT_SECONDS = 30


class BrevoError(Exception):
    pass


def send_campaign(subject: str, html_content: str, list_id: int = None) -> str:
    """
    Crée puis envoie une campagne Brevo. Retourne l'ID de la campagne créée.
    Le lien de désabonnement est ajouté automatiquement par Brevo.
    """
    if not BREVO_API_KEY:
        raise BrevoError("BREVO_API_KEY manquante.")
    if not BREVO_SENDER_EMAIL:
        raise BrevoError(
            "BREVO_SENDER_EMAIL manquante : l'adresse doit être un expéditeur "
            "validé dans Brevo."
        )

    list_id = list_id if list_id is not None else BREVO_LIST_ID
    if list_id is None or str(list_id).strip() == "":
        raise BrevoError("BREVO_LIST_ID manquante : aucune liste destinataire.")
    try:
        list_id = int(list_id)
    except (TypeError, ValueError):
        raise BrevoError(f"BREVO_LIST_ID invalide : {list_id!r} n'est pas un entier.")

    headers = {"api-key": BREVO_API_KEY, "Content-Type": "application/json"}
    payload = {
        # Brevo impose un nom de campagne unique : on horodate pour qu'un
        # renvoi après échec ne se heurte pas au nom déjà pris.
        "name": f"{subject} ({datetime.now():%Y-%m-%d %H:%M})",
        "subject": subject,
        "sender": {"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
        "htmlContent": html_content,
        "recipients": {"listIds": [list_id]},
    }

    campaign = _post("/emailCampaigns", headers, json=payload)
    campaign_id = campaign.get("id")
    if campaign_id is None:
        raise BrevoError(f"Brevo n'a pas renvoyé d'identifiant de campagne : {campaign}")

    _post(f"/emailCampaigns/{campaign_id}/sendNow", headers)
    logger.info("Campagne Brevo %s envoyée à la liste %s", campaign_id, list_id)
    return str(campaign_id)


def _post(path: str, headers: dict, json: dict | None = None) -> dict:
    try:
        response = requests.post(
            f"{BASE_URL}{path}",
            json=json,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise BrevoError(f"Brevo injoignable sur {path} : {error}") from error

    if response.status_code >= 400:
        # Brevo détaille la cause en JSON ; raise_for_status la perdrait.
        raise BrevoError(
            f"Brevo a refusé {path} (HTTP {response.status_code}) : {response.text[:300]}"
        )

    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


def render_newsletter(news_html: str, stages_html: str, week_of) -> str:
    """Enveloppe les fragments générés dans un document HTML lisible en email.

    Les clients mail ignorent les feuilles de style externes : tout est en
    styles inline, sur une largeur fixe centrée.
    """
    titre = f"Newsletter Sciences Po Finance — semaine du {week_of}"
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titre)}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#f4f4f6;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%;background:#ffffff;border-radius:8px;
              font-family:Helvetica,Arial,sans-serif;color:#1a1a1a;
              line-height:1.5;">
<tr><td style="padding:24px 28px;border-bottom:3px solid #1a4f8b;">
  <h1 style="margin:0;font-size:19px;color:#1a4f8b;">
    Sciences Po Finance
  </h1>
  <p style="margin:4px 0 0;font-size:13px;color:#666;">
    Semaine du {html.escape(str(week_of))}
  </p>
</td></tr>
<tr><td style="padding:20px 28px;font-size:15px;">
{news_html}
</td></tr>
<tr><td style="padding:0 28px 24px;font-size:15px;">
{stages_html}
</td></tr>
<tr><td style="padding:16px 28px;background:#f4f4f6;font-size:12px;color:#666;
               border-radius:0 0 8px 8px;">
  Newsletter hebdomadaire de l'association Sciences Po Finance.
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
