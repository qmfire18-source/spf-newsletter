"""Envoi de la campagne via l'API Brevo — voir PLAN.md §5.

L'envoi n'est jamais déclenché par le cron : il part de l'interface de
validation, sur action humaine explicite.
"""
import html
import logging
import re
from datetime import datetime

import requests

from src.config import (
    BREVO_API_KEY,
    BREVO_LIST_ID,
    BREVO_SENDER_EMAIL,
    BREVO_SENDER_NAME,
)

logger = logging.getLogger(__name__)

# Charte de l'association, reprise de l'interface de validation.
MARINE = "#183050"
# Bandeau de semaine : un bleu très pâle, qui laisse le marine lisible dessus.
BLEU_PALE = "#DCE6F2"
ENCRE = "#141B2B"
GRIS = "#5A6478"
FOND = "#F4F5F7"
FILET = "#E3E7ED"
# Étiquettes de rubrique. Le marine pur se confondrait avec l'encre des titres ;
# ce bleu-ci garde l'écart qui faisait le rôle d'accent de l'ancien doré, tout
# en passant le contraste sur fond blanc.
BLEU_RUBRIQUE = "#2E6BB0"

# La sans-serif du site Canva ne peut pas être embarquée : Gmail supprime les
# @font-face. La pile système en est la plus proche qui s'affiche partout —
# San Francisco sur iPhone et Mac, Segoe UI sur Windows, Roboto sur Android.
SANS = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
        "Helvetica,Arial,sans-serif")
LIEN = "#183050"
# Un mail ne peut pas pointer vers un fichier local : le blason est servi par
# GitHub Pages, à une adresse publique et stable.
#
# C'est le blason qui figure DANS le message. Le médaillon rond, lui, est
# destiné à l'icône d'expéditeur affichée par les messageries — un autre
# usage, sur lequel notre code n'a pas la main.
LOGO_URL = "https://newsletter.sciencespo-finance.fr/logo.jpeg"

BASE_URL = "https://api.brevo.com/v3"
REQUEST_TIMEOUT_SECONDS = 30


class BrevoError(Exception):
    pass


def send_campaign(subject: str, html_content: str, list_id: int | None = None) -> str:
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
    except (TypeError, ValueError) as erreur:
        raise BrevoError(
            f"BREVO_LIST_ID invalide : {list_id!r} n'est pas un entier."
        ) from erreur

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


# Styles appliqués directement sur chaque balise du contenu généré.
#
# Le bloc <style> ne suffit pas : Gmail sur mobile et plusieurs autres clients
# le suppriment, et les rubriques redevenaient alors des titres noirs
# ordinaires — l'étiquette dorée disparaissait précisément là où on lit le
# plus. Ces styles-là voyagent avec la balise et survivent partout.
#
# Le nettoyage retire tout attribut `style` du contenu reçu ; ceux-ci sont
# ajoutés ensuite, et leurs valeurs viennent d'ici, pas du modèle.
STYLES_CONTENU = {
    "h3": (
        "font-size:13px;letter-spacing:.12em;text-transform:uppercase;"
        f"color:{BLEU_RUBRIQUE};font-weight:700;margin:32px 0 0;padding-top:16px;"
        f"border-top:1px solid {FILET};font-family:{SANS};"
    ),
    "h4": (
        f"font-family:{SANS};font-size:19px;"
        f"line-height:1.35;color:{ENCRE};font-weight:600;margin:10px 0 10px;"
        "letter-spacing:-.01em;"
    ),
    "p": "margin:0 0 14px;",
    "ul": "margin:0 0 16px;padding-left:20px;",
    "ol": "margin:0 0 16px;padding-left:20px;",
    "li": "margin:0 0 10px;",
    "a": f"color:{LIEN};",
}

_BALISE_OUVRANTE = re.compile(r"<(h3|h4|p|ul|ol|li|a)(\s[^>]*)?>")
_TITRE_ANCRE = re.compile(r'<h4([^>]*\bid="(a\d+)"[^>]*)>')


def _ancres_nommees(fragment: str) -> str:
    """Double chaque ancre de titre d'une balise <a name>.

    Gmail retire les attributs `id` du HTML qu'il affiche : le sommaire
    pointait alors vers des cibles qui n'existaient plus, et cliquer ne
    faisait rien. L'attribut `name` sur une balise <a>, lui, survit. On garde
    les deux, les autres clients se servant de l'un ou de l'autre.
    """
    return _TITRE_ANCRE.sub(
        lambda m: f'<h4{m.group(1)}><a name="{m.group(2)}"></a>', fragment or ""
    )


def inline_styles(fragment: str) -> str:
    """Pose les styles sur chaque balise, pour les clients sans <style>."""

    def remplacer(trouve):
        balise = trouve.group(1)
        attributs = trouve.group(2) or ""
        return f'<{balise}{attributs} style="{STYLES_CONTENU[balise]}">'

    return _ancres_nommees(_BALISE_OUVRANTE.sub(remplacer, fragment or ""))


def render_newsletter(news_html: str, stages_html: str, week_of) -> str:
    """Enveloppe les fragments générés dans un email aux couleurs de l'asso.

    Contraintes propres au mail, qui expliquent la mise en page en tableaux :
    les clients ignorent les feuilles de style externes et la plupart des
    sélecteurs modernes, donc tout est en styles inline sur une largeur fixe
    centrée. Les rares règles groupées (bloc <style>) ne servent qu'aux
    ajustements mobiles et à l'affichage sombre : un client qui les ignore
    obtient quand même une mise en page correcte.

    Le logo est chargé depuis GitHub Pages : un email ne peut pas référencer
    un fichier local, il lui faut une URL publique et stable.
    """
    titre = f"Newsletter Sciences Po Finance : semaine du {week_of}"
    semaine = html.escape(_semaine_en_lettres(week_of))
    news_html = _sans_filet_initial(inline_styles(news_html))
    stages_html = inline_styles(stages_html)

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light">
<title>{html.escape(titre)}</title>
<style>
  /* Le pré-en-tête : première ligne affichée dans la liste des messages,
     jamais dans le corps du mail. */
  .preheader {{ display:none !important; visibility:hidden; opacity:0;
                height:0; width:0; overflow:hidden; mso-hide:all; }}
  a {{ color:{LIEN}; }}
  /* h3 = nom de rubrique : une étiquette, précédée d'un filet qui sépare
     les blocs. h4 = titre de l'article. Cette hiérarchie remplace le mur de
     titres de même niveau, où l'on ne savait plus ce qui commençait où. */
  .contenu h3 {{ font-size:12px; letter-spacing:.14em; text-transform:uppercase;
                 color:{BLEU_RUBRIQUE}; margin:34px 0 0; padding-top:18px;
                 border-top:1px solid {FILET}; font-weight:700; }}
  .contenu h3:first-child {{ margin-top:8px; border-top:0; padding-top:0; }}
  .contenu h4 {{ font-family:{SANS}; font-size:19px; letter-spacing:-.01em;
                 line-height:1.3; color:{ENCRE}; margin:10px 0 10px;
                 font-weight:600; }}
  .contenu p  {{ margin:0 0 14px; }}
  .contenu ul {{ margin:0 0 16px; padding-left:20px; }}
  .contenu li {{ margin-bottom:10px; }}
  @media only screen and (max-width:620px) {{
    .enveloppe {{ width:100% !important; }}
    .marges    {{ padding-left:20px !important; padding-right:20px !important; }}
    /* Le bandeau de semaine fait 14px sur grand écran. Une règle mobile le
       portait à 20px, ce qui le coupait en deux lignes sur un téléphone :
       « Semaine du 14 septembre » / « 2026 ». Il rétrécit au contraire. */
    .titre     {{ font-size:13px !important; letter-spacing:0 !important; }}
    .contenu h3 {{ font-size:13px !important; }}
    .contenu h4 {{ font-size:17px !important; }}
    .contenu, .contenu p, .contenu li {{ font-size:16px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{FOND};
             -webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<div class="preheader" style="display:none;max-height:0;overflow:hidden;
     mso-hide:all;font-size:1px;line-height:1px;color:#F4F5F7;opacity:0;">{semaine}. L'actu finance décryptée et les stages de la semaine.</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{FOND};">
<tr><td align="center" style="padding:24px 12px;">

<table role="presentation" class="enveloppe" width="600" cellpadding="0" cellspacing="0"
       border="0" style="width:600px;max-width:600px;background:#ffffff;
       border-radius:12px;overflow:hidden;
       font-family:{SANS};
       color:{ENCRE};line-height:1.6;">

  <!-- en-tête -->
  <tr><td class="marges" style="background:{MARINE};padding:26px 32px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td width="48" style="width:48px;padding-right:14px;vertical-align:middle;">
        <img src="{LOGO_URL}" width="48" height="48" alt="Sciences Po Finance"
             style="display:block;width:48px;height:48px;border:0;border-radius:8px;
                    background:#ffffff;">
      </td>
      <td style="vertical-align:middle;">
        <div style="font-family:{SANS};font-size:20px;
                    font-weight:600;color:#ffffff;line-height:1.2;
                    letter-spacing:-.01em;">
          Sciences Po Finance
        </div>
        <div style="font-size:12px;color:#BED0E8;letter-spacing:.06em;
                    text-transform:uppercase;padding-top:3px;">
          Newsletter hebdomadaire
        </div>
      </td>
    </tr>
    </table>
  </td></tr>

  <!-- bandeau de semaine -->
  <tr><td class="marges" style="background:{BLEU_PALE};padding:10px 32px;">
    <div class="titre" style="font-family:{SANS};font-weight:600;
                              font-size:14px;color:{MARINE};letter-spacing:.01em;">
      {semaine}
    </div>
  </td></tr>

  <!-- actualités -->
  <tr><td class="marges contenu" style="padding:8px 32px 4px;font-size:15px;color:{ENCRE};">
{news_html}
  </td></tr>

  <!-- séparateur -->
  <tr><td class="marges" style="padding:4px 32px;">
    <div style="height:1px;background:{FILET};font-size:0;line-height:0;">&nbsp;</div>
  </td></tr>

  <!-- stages -->
  <tr><td class="marges contenu" style="padding:4px 32px 28px;font-size:15px;color:{ENCRE};">
{stages_html}
  </td></tr>

  <!-- pied -->
  <tr><td class="marges" style="background:{FOND};padding:20px 32px;
             font-size:12px;color:{GRIS};text-align:center;">
    Newsletter hebdomadaire de l'association <strong>Sciences Po Finance</strong>.<br>
    Tu la reçois parce que tu t'y es abonné. Le lien de désabonnement figure
    ci-dessous.
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _sans_filet_initial(fragment: str) -> str:
    """Retire le filet de la toute première rubrique : rien ne la précède."""
    premiere = fragment.find("<h3")
    if premiere == -1:
        return fragment
    fin = fragment.find(">", premiere)
    debut = fragment[premiere:fin]
    allege = debut.replace(f"border-top:1px solid {FILET};", "").replace(
        "padding-top:16px;", ""
    ).replace("margin:32px 0 0;", "margin:8px 0 0;")
    return fragment[:premiere] + allege + fragment[fin:]


def _semaine_en_lettres(week_of) -> str:
    """« Semaine du 14 septembre 2026 » — une date ISO dans un mail fait brut."""
    mois = (
        "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre",
    )
    try:
        return f"Semaine du {week_of.day} {mois[week_of.month - 1]} {week_of.year}"
    except AttributeError:
        return f"Semaine du {week_of}"


def count_recipients(list_id=None) -> int | None:
    """Nombre d'abonnés de la liste, relevé au moment de l'envoi.

    Sert l'historique : « envoyée à 42 abonnés » n'a de sens que si le
    chiffre est figé le jour de l'envoi, la liste continuant d'évoluer.
    Une défaillance ici ne doit jamais empêcher un envoi : on renvoie None.
    """
    list_id = list_id if list_id is not None else BREVO_LIST_ID
    if not BREVO_API_KEY or not list_id:
        return None
    try:
        response = requests.get(
            f"{BASE_URL}/contacts/lists/{int(list_id)}",
            headers={"api-key": BREVO_API_KEY, "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json().get("totalSubscribers")
    except (requests.RequestException, ValueError, TypeError):
        logger.warning("Nombre d'abonnés indisponible pour la liste %s", list_id)
        return None
