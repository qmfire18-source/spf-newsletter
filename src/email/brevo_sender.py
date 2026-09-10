"""Envoi de la campagne via l'API Brevo — voir PLAN.md §5."""
import requests
from src.config import BREVO_API_KEY, BREVO_LIST_ID

BASE_URL = "https://api.brevo.com/v3"


def send_campaign(subject: str, html_content: str, list_id: int = None) -> str:
    """
    Crée puis envoie une campagne Brevo. Retourne l'ID de la campagne créée.
    Le lien de désabonnement est ajouté automatiquement par Brevo.
    """
    list_id = list_id or BREVO_LIST_ID
    headers = {"api-key": BREVO_API_KEY, "Content-Type": "application/json"}

    payload = {
        "name": f"Newsletter SPF — {subject}",
        "subject": subject,
        "sender": {"name": "Sciences Po Finance", "email": "newsletter@sciencespofinance.fr"},
        "htmlContent": html_content,
        "recipients": {"listIds": [int(list_id)]},
    }

    resp = requests.post(f"{BASE_URL}/emailCampaigns", json=payload, headers=headers)
    resp.raise_for_status()
    campaign_id = resp.json()["id"]

    # Déclenche l'envoi immédiat
    send_resp = requests.post(
        f"{BASE_URL}/emailCampaigns/{campaign_id}/sendNow", headers=headers
    )
    send_resp.raise_for_status()

    return campaign_id
