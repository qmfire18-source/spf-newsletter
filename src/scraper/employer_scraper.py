"""Offres publiées par les employeurs eux-mêmes, hors Welcome to the Jungle.

WTTJ ne référence qu'une partie des employeurs que vise l'association : ni
Lazard, ni Eight Advisory, ni Euronext n'y publient. Chacun a son propre
système de recrutement, et il n'en existe pas deux pareils — d'où un
connecteur par famille de système plutôt qu'un par employeur.

Chaque connecteur renvoie la même forme que le scraper WTTJ
(`title`, `company`, `location`, `deadline`, `url`), pour que le reste du
pipeline — stock, classement par secteur, déduplication — ne fasse aucune
différence entre les deux origines.

Employeurs écartés faute d'accès, et pourquoi :
- **Bpifrance** répond 403 à toute requête automatisée. Blocage délibéré,
  même famille que JobTeaser : on renonce plutôt que de le franchir.
- **Natixis** interdit `/emploi/` et `/recherche-d'offres` dans son
  robots.txt, et rend ses listes en JavaScript.
"""
import logging
import re
import time
from urllib.parse import urljoin

import requests

from src.scraper.stage_scraper import PROVINCE_SLUG_MARKERS

logger = logging.getLogger(__name__)

USER_AGENT = "SPFNewsletterBot/1.0 (newsletter Sciences Po Finance)"
REQUEST_TIMEOUT_SECONDS = 25
REQUEST_DELAY_SECONDS = 1.0

# Une offre doit parler de stage : ces portails publient tous types de postes.
INTERNSHIP_MARKERS = (
    "stage", "stagiaire", "intern", "internship", "apprenti", "alternance",
    "vie ", "v.i.e", "summer analyst", "off-cycle", "offcycle",
)


def fetch_recruitee(company_slug: str, display_name: str) -> list[dict]:
    """Offres d'un employeur hébergé par Recruitee — API JSON publique.

    Exemple : Eight Advisory, `8advisory`.
    """
    url = f"https://{company_slug}.recruitee.com/api/offers/"
    payload = _get_json(url)
    if not payload:
        return []

    offres = []
    for offre in payload.get("offers", []):
        titre = (offre.get("title") or "").strip()
        if not _is_internship(titre):
            continue
        if not _is_in_scope(offre.get("city"), offre.get("country_code")):
            logger.info("Hors périmètre (%s), ignorée : %s", offre.get("city"), titre)
            continue
        offres.append(
            {
                "title": titre,
                "company": display_name,
                "location": _recruitee_location(offre),
                # Recruitee ne publie pas de date limite de candidature.
                "deadline": None,
                "url": offre.get("careers_url") or offre.get("url") or url,
            }
        )
    logger.info("%s : %d stage(s) sur %d annonces.", display_name,
                len(offres), len(payload.get("offers", [])))
    return offres


def _recruitee_location(offre: dict) -> str | None:
    ville = (offre.get("city") or "").strip()
    pays = (offre.get("country") or offre.get("country_code") or "").strip()
    if ville and pays:
        return f"{ville}, {pays}"
    return ville or pays or None


def _is_in_scope(city: str | None, country_code: str | None) -> bool:
    """Paris, l'Île-de-France et l'étranger ; pas la province française.

    Même règle que pour Welcome to the Jungle. Une ville absente ne fait pas
    rejeter l'offre : mieux vaut une offre de trop qu'une offre perdue.
    """
    if (country_code or "").strip().upper() not in ("FR", "FRA", ""):
        return True  # étranger : dans la cible

    ville = _normalise(city or "")
    if not ville:
        return True
    return not any(m in ville for m in PROVINCE_SLUG_MARKERS)


def _normalise(texte: str) -> str:
    import unicodedata

    decompose = unicodedata.normalize("NFKD", texte.lower())
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return "".join(c if c.isalnum() else "-" for c in sans_accent)


def _is_internship(titre: str) -> bool:
    minuscule = f" {titre.lower()} "
    return any(m in minuscule for m in INTERNSHIP_MARKERS)


def _get_json(url: str, session: requests.Session | None = None) -> dict | None:
    try:
        response = (session or requests).get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as error:
        # Un employeur injoignable ne doit pas faire échouer la collecte.
        logger.warning("Employeur injoignable (%s) : %s", error, url)
        return None


CONNECTORS = {
    "recruitee": lambda source: fetch_recruitee(source["slug"], source["name"]),
}


def fetch_employer_offers(sources: list[dict]) -> list[dict]:
    """Parcourt les employeurs configurés, en isolant leurs défaillances.

    Un portail en panne ne doit pas priver la newsletter des autres : chaque
    employeur est traité séparément et ses erreurs restent locales.
    """
    offres = []
    for index, source in enumerate(sources):
        connecteur = CONNECTORS.get(source.get("type"))
        if not connecteur:
            logger.warning("Type d'employeur inconnu, ignoré : %r", source.get("type"))
            continue
        if index:
            time.sleep(REQUEST_DELAY_SECONDS)
        try:
            offres.extend(connecteur(source))
        except Exception:
            logger.exception("Employeur en échec, ignoré : %r", source.get("name"))

    logger.info("Employeurs directs : %d stage(s) au total.", len(offres))
    return offres
