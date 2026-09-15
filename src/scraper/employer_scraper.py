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
import html
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

# Chez une banque d'affaires, tout poste est financier. Chez une fintech ou
# un groupe industriel, non : un stage commercial ou marketing n'intéresse pas
# l'association. Ces employeurs-là portent `finance_only` dans la
# configuration, et leur intitulé doit alors porter un marqueur métier.
FINANCE_TITLE_MARKERS = (
    "financ", "comptab", "audit", "risk", "risque", "credit", "crédit",
    "trésor", "tresor", "treasury", "compliance", "conformité", "conformite",
    "regulatory", "réglementaire", "reglementaire", "reporting", "controlling",
    "contrôle de gestion", "controle de gestion", "m&a", "invest", "fund",
    "banking", "banque", "actuari", "fiscal", "tax", "valuation", "fp&a",
    "legal", "juridique", "strategy", "stratégie", "strategie", "data analyst",
)

# Une offre doit parler de stage : ces portails publient tous types de postes.
# Cherchés en MOT ENTIER — « intern » se cache dans « Internal Advisor », et
# un poste de consultant interne n'est pas un stage.
INTERNSHIP_MARKERS = (
    "stage", "stages", "stagiaire", "stagiaires", "intern", "interns",
    "internship", "internships", "apprenti", "apprentie", "alternance",
    "alternant", "vie", "summer analyst", "off-cycle", "offcycle",
)
_INTERNSHIP_RE = re.compile(
    r"(?<![a-z])(" + "|".join(re.escape(m) for m in INTERNSHIP_MARKERS) + r")(?![a-z])"
)


def fetch_recruitee(
    company_slug: str, display_name: str, finance_only: bool = False
) -> list[dict]:
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
        if finance_only and not _is_finance_role(titre):
            logger.info("Poste non financier, ignoré : %s", titre)
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


def _is_finance_role(titre: str) -> bool:
    minuscule = _normalise(titre or "").replace("-", " ")
    return any(_normalise(m).replace("-", " ") in minuscule for m in FINANCE_TITLE_MARKERS)


def _clean_location(lieu: str | None) -> str | None:
    """Retire les drapeaux et symboles que certains portails collent aux villes."""
    if not lieu:
        return None
    propre = "".join(c for c in lieu if c.isalnum() or c in " ,-'()./").strip(" ,-")
    return " ".join(propre.split()) or None


def _is_internship(titre: str) -> bool:
    return bool(_INTERNSHIP_RE.search(_normalise(titre or "").replace("-", " ")))


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


def fetch_lever(
    company_slug: str, display_name: str, finance_only: bool = False
) -> list[dict]:
    """Offres d'un employeur hébergé par Lever. Exemple : Qonto, Agicap.

    Lever expose le type de contrat dans un champ dédié, `commitment`, ce qui
    vaut mieux qu'un mot-clé dans l'intitulé : un « Internal Auditor » ne
    passe pas, une « Alternance comptabilité » passe.
    """
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
    annonces = _get_json(url)
    if not isinstance(annonces, list):
        return []

    offres = []
    for annonce in annonces:
        titre = (annonce.get("text") or "").strip()
        categories = annonce.get("categories") or {}
        contrat = (categories.get("commitment") or "").strip()

        # Le champ tranche ; l'intitulé rattrape les annonces sans contrat
        # renseigné, fréquentes sur les offres françaises.
        if not (_is_internship(contrat) or _is_internship(titre)):
            continue
        if finance_only and not _is_finance_role(titre):
            logger.info("Poste non financier, ignoré : %s", titre)
            continue

        ville = (categories.get("location") or "").strip()
        pays = (annonce.get("country") or "").strip()
        if not _is_in_scope(ville, pays or _country_code(ville, "")):
            logger.info("Hors périmètre (%s), ignorée : %s", ville, titre)
            continue

        offres.append(
            {
                "title": titre,
                "company": display_name,
                "location": _clean_location(ville),
                "deadline": None,
                "url": annonce.get("hostedUrl") or annonce.get("applyUrl") or url,
            }
        )

    logger.info("%s : %d stage(s) sur %d annonces.", display_name,
                len(offres), len(annonces))
    return offres


def fetch_oracle(host: str, sites: list[str], display_name: str) -> list[dict]:
    """Offres d'un employeur hébergé par Oracle Recruiting Cloud.

    Exemple : Lazard, dont les portails professionnels et étudiants sont deux
    « sites » distincts du même hôte — d'où la liste.

    Sans le paramètre `expand`, l'API renvoie le nombre d'annonces mais pas
    les annonces elles-mêmes : le compteur est correct et la liste vide, ce
    qui donne l'illusion d'un portail en panne.
    """
    offres = []
    for index, site in enumerate(sites):
        if index:
            time.sleep(REQUEST_DELAY_SECONDS)
        url = (
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            "?onlyData=true&expand=requisitionList.secondaryLocations"
            f"&finder=findReqs;siteNumber={site},limit=200,sortBy=POSTING_DATES_DESC"
        )
        payload = _get_json(url)
        if not payload:
            continue

        for item in payload.get("items", []):
            for annonce in item.get("requisitionList", []):
                titre = (annonce.get("Title") or "").strip()
                if not _is_internship(titre):
                    continue
                lieu = (annonce.get("PrimaryLocation") or "").strip()
                pays = (annonce.get("PrimaryLocationCountry") or "").strip()
                if not _is_in_scope(lieu, _country_code(lieu, pays)):
                    logger.info("Hors périmètre (%s), ignorée : %s", lieu, titre)
                    continue
                offres.append(
                    {
                        "title": titre,
                        "company": display_name,
                        "location": lieu or None,
                        "deadline": _iso_date(annonce.get("PostingEndDate")),
                        "url": (
                            f"https://{host}/hcmUI/CandidateExperience/fr/sites/"
                            f"{site}/job/{annonce.get('Id')}"
                        ),
                    }
                )

    logger.info("%s : %d stage(s) retenus.", display_name, len(offres))
    return offres


def _country_code(lieu: str, pays: str) -> str:
    """Les portails nomment le pays en toutes lettres, parfois dans le lieu."""
    texte = _normalise(f"{pays} {lieu}")
    return "FR" if "france" in texte else "XX"


def _iso_date(valeur) -> str | None:
    if not isinstance(valeur, str) or not valeur:
        return None
    return valeur[:10]


def fetch_euronext() -> list[dict]:
    """Offres d'Euronext, publiées dans un tableau sur leur propre site.

    Le type de contrat est une colonne à part — plus sûr qu'un mot-clé dans
    l'intitulé : « Intern (Fixed Term) (Trainee) » et « International
    Graduate Programme VIE » sont explicites.
    """
    url = "https://www.euronext.com/en/about/careers/open-positions"
    page = _get_html(url)
    if not page:
        return []

    offres = []
    lignes = re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S)
    for ligne in lignes:
        titre = _table_cell(ligne, "field-job-title")
        if not titre:
            continue
        contrat = _table_cell(ligne, "field-job-sub-type") or ""
        if not _is_internship(contrat):
            continue

        ville = _table_cell(ligne, "name") or ""
        pays = _table_cell(ligne, "field-country") or ""
        if not _is_in_scope(ville, _country_code(ville, pays)):
            logger.info("Hors périmètre (%s), ignorée : %s", ville, titre)
            continue

        lien = re.search(r'href="([^"]*job-offers[^"]*)"', ligne)
        offres.append(
            {
                "title": titre,
                "company": "Euronext",
                "location": ", ".join(x for x in (ville, pays) if x) or None,
                "deadline": None,
                "url": urljoin(url, lien.group(1)) if lien else url,
            }
        )

    logger.info("Euronext : %d stage(s) sur %d annonces.", len(offres), len(lignes))
    return offres


def fetch_talentsoft(host: str, display_name: str) -> list[dict]:
    """Offres d'un employeur hébergé par TalentSoft. Exemple : Amundi.

    Chaque annonce porte une courte description en trois points — type de
    contrat, entité, pays. La ville n'y figure pas : elle demanderait de
    visiter chaque fiche, pour un gain faible. Une annonce française sans
    ville est donc conservée, comme partout ailleurs dans le projet.
    """
    url = f"https://{host}/offre-de-emploi/liste-toutes-offres.aspx"
    page = _get_html(url)
    if not page:
        return []

    blocs = re.split(r'<li class="ts-offer-list-item', page)[1:]
    offres = []
    for bloc in blocs:
        lien = re.search(r'title-link[^>]*href="([^"]+)"[^>]*>(.*?)</a>', bloc, re.S)
        if not lien:
            continue
        titre = _text(lien.group(2))
        details = _talentsoft_details(bloc)
        contrat = details[0] if details else ""
        if not _is_internship(contrat):
            continue

        pays = details[-1] if len(details) > 1 else ""
        if not _is_in_scope("", _country_code("", pays)):
            continue

        entite = details[1] if len(details) > 2 else ""
        offres.append(
            {
                "title": titre,
                "company": display_name,
                "location": ", ".join(x for x in (entite, pays) if x) or None,
                "deadline": None,
                "url": urljoin(url, lien.group(1)),
            }
        )

    logger.info("%s : %d stage(s) sur %d annonces.", display_name, len(offres), len(blocs))
    return offres


def _talentsoft_details(bloc: str) -> list[str]:
    liste = re.search(r'ts-offer-list-item__description\s*">(.*?)</ul>', bloc, re.S)
    if not liste:
        return []
    return [_text(x) for x in re.findall(r"<li>(.*?)</li>", liste.group(1), re.S)]


def _table_cell(ligne: str, classe: str) -> str | None:
    cellule = re.search(
        r'class="views-field views-field-' + re.escape(classe) + r'"[^>]*>(.*?)</td>',
        ligne, re.S,
    )
    return _text(cellule.group(1)) if cellule else None


def _text(brut: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", brut)).strip()


def _get_html(url: str) -> str | None:
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as error:
        logger.warning("Employeur injoignable (%s) : %s", error, url)
        return None


CONNECTORS = {
    "recruitee": lambda source: fetch_recruitee(
        source["slug"], source["name"], source.get("finance_only", False)
    ),
    "oracle": lambda source: fetch_oracle(
        source["host"], source["sites"], source["name"]
    ),
    "lever": lambda source: fetch_lever(
        source["slug"], source["name"], source.get("finance_only", False)
    ),
    "euronext": lambda source: fetch_euronext(),
    "talentsoft": lambda source: fetch_talentsoft(source["host"], source["name"]),
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
