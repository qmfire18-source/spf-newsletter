"""Récupération des offres de stage — voir PLAN.md §2.

IMPORTANT : ne jamais scraper LinkedIn directement (violation des CGU, risque
de bannissement).

JobTeaser est écarté pour la même raison : le site répond 403 à toute requête
automatisée. Passer ce mur supposerait de se faire passer pour un navigateur
afin de franchir un blocage explicite — on s'y refuse.

Welcome to the Jungle est interrogé via son sitemap, que son robots.txt
annonce lui-même. Ce robots.txt interdit toute URL à query string
(`Disallow: /*?`), donc les pages de recherche filtrée sont hors limites :
on part du sitemap, on filtre sur le slug, puis on ne visite que les pages
d'offres retenues. Chaque page expose un JSON-LD JobPosting (contrat SEO
stable) qui contient déjà titre, entreprise, lieu, pays et date limite.

Le site nous coupe (202 au corps vide) après une poignée de pages, pour un
vivier d'environ 400 offres hebdomadaires : le budget de requêtes est la
ressource rare, et l'ordre de visite compte donc plus que le volume. D'où le
resserrement du vivier avant visite, puis une rotation entre employeurs.
"""
import asyncio
import gzip
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# Format conventionnel "Mozilla/5.0 (compatible; <bot>; +<contact>)", celui-là
# même qu'emploient Googlebot et Bingbot : le robot reste identifié et
# joignable. WTTJ refuse (403) un User-Agent sans le préfixe Mozilla/5.0, y
# compris celui de Googlebot — c'est une règle WAF grossière, pas une politique
# anti-robot : leur robots.txt autorise ces chemins et publie lui-même le
# sitemap d'offres. On ne se fait pas passer pour un navigateur humain.
# Les en-têtes HTTP sont encodés en ASCII : pas d'accent ni de tiret cadratin.
USER_AGENT = (
    "Mozilla/5.0 (compatible; SPFNewsletterBot/1.0; "
    "+https://www.sciencespo.fr/; newsletter Sciences Po Finance)"
)
# Le job est hebdomadaire : on peut se permettre d'être lent et discret.
REQUEST_DELAY_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 30
MAX_OFFERS = 25
# Garde-fou : le site nous coupe bien avant, mais une série de rejets ne doit
# pas transformer la boucle en parcours intégral du sitemap.
MAX_PAGE_VISITS = 60

# Le sitemap rend ~400 offres stage/finance sur 7 jours, mais WTTJ nous coupe
# après une poignée de pages : l'ordre de visite est donc LA décision de ce
# module. On resserre d'abord le vivier sur le coeur finance (ce que vise
# l'asso), on écarte le bruit, puis on répartit le budget entre employeurs.
STAGE_SLUG_MARKERS = ("stage", "stagiaire", "internship", "intern-")

# Coeur finance : M&A, banque, investissement, audit, marchés, risque. Ni la
# comptabilité, ni le contrôle de gestion, ni la fiscalité n'y figurent : les
# retenir triplait le vivier (306 candidats au lieu de 136 sur une semaine
# réelle) sans servir la ligne éditoriale de l'asso.
# Marqueurs volontairement explicites : "banqu" attraperait "stage banquet" et
# "capital" attraperait "capital humain" — deux faux positifs relevés en
# production sur une semaine réelle.
FINANCE_SLUG_MARKERS = (
    "finance", "financier", "financiere", "financement", "m-a", "fusion-acq",
    "fusions-acq", "audit", "asset-manage", "invest", "banque", "banques",
    "banquier", "banquiere", "bancaire", "banking", "trading", "risk-manage",
    "risques-financiers", "private-equity", "venture-capital",
    "capital-risque", "capital-invest", "capital-market",
    "marches-de-capitaux", "equity", "transaction-services", "patrimoine",
    "actuar", "controle-financier",
)

# Beaucoup d'offres IT et RH portent "services financiers" ou "SI finance"
# dans leur slug (Sopra Steria, Vinci) : le métier n'a rien de financier.
EXCLUDED_SLUG_MARKERS = (
    "developpeur", "developpeuse", "ingenieur", "full-stack", "fullstack",
    "java", "cobol", "-net-", "angular", "cybersecurite", "recrutement",
    "sap", "informatique", "devops", "data-engineer",
    # Maîtrise d'ouvrage SI : le domaine est la finance, le métier non.
    "analyste-fonctionnel", "si-finance", "si-gestion",
    "outils-collaboratifs", "support-outils",
    # Le vocabulaire RH emprunte celui de la finance.
    "capital-humain", "banquet",
)

# Périmètre géographique : Paris, l'Île-de-France, et l'international. Une
# offre en province est écartée — l'asso vise les places financières, pas un
# poste isolé à Rodez. À l'inverse, Londres, Luxembourg ou New York sont
# exactement la cible : le filtre précédent, qui ne gardait que la France, les
# jetait (14 offres par semaine, dont un Global Investment Banking ECM/M&A).
#
# Ces marqueurs évitent de dépenser une requête sur une offre qu'on écartera :
# le code postal du JSON-LD tranche ensuite pour de bon.
PROVINCE_SLUG_MARKERS = (
    "bordeaux", "talence", "merignac", "lyon", "limonest", "villeurbanne",
    "marseille", "aix-en-provence", "toulouse", "colomiers", "blagnac",
    "nantes", "saint-herblain", "lille", "villeneuve-d-ascq", "strasbourg",
    "rennes", "montpellier", "nice", "sophia-antipolis", "grenoble",
    "angouleme", "rodez", "tours", "le-mans", "niort", "bessines", "laval",
    "brest", "dijon", "reims", "metz", "nancy", "orleans", "amiens",
    "rouen", "le-havre", "caen", "clermont-ferrand", "saint-etienne",
    "toulon", "perpignan", "besancon", "poitiers", "limoges", "pau",
    "bayonne", "biarritz", "la-rochelle", "angers", "arras", "valence",
    "chambery", "annecy", "mulhouse", "troyes", "belfort", "vannes",
    "quimper", "lorient", "saint-nazaire", "cholet", "saint-brieuc",
    "coutances", "saint-lo", "mayenne", "ernee", "craon", "segre",
    "domfront", "flers", "argentan", "pontorson", "soissons", "mousson",
    "gonfreville", "talant", "bourges", "chartres", "evreux",
    "la-roche-sur-yon", "mouilleron", "vannes", "cherbourg", "lens",
)

# Départements franciliens : 75 Paris, 77 Seine-et-Marne, 78 Yvelines,
# 91 Essonne, 92 Hauts-de-Seine, 93 Seine-Saint-Denis, 94 Val-de-Marne,
# 95 Val-d'Oise.
ILE_DE_FRANCE_PREFIXES = ("75", "77", "78", "91", "92", "93", "94", "95")

FRENCH_COUNTRIES = {"FR", "FRA", "FRANCE"}

_LD_JSON_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)
_SITEMAP_ENTRY_RE = re.compile(r"<loc>(.*?)</loc>\s*<lastmod>(.*?)</lastmod>", re.S)
_SITEMAP_LOC_RE = re.compile(r"<loc>(.*?)</loc>", re.S)


async def fetch_stage_offers(
    target_sites: list[dict], known_urls: set[str] | None = None
) -> list[dict]:
    """
    target_sites: [{"type": "wttj_sitemap", "name": ..., "sitemap_index": ...}]
    known_urls: offres déjà en stock, écartées avant toute visite — c'est ce
        qui permet à des exécutions successives de rapporter du nouveau plutôt
        que de redépenser le budget de requêtes sur les mêmes pages.
    Retourne : [{"title", "company", "location", "deadline", "url"}]
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    results = []

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        for site in target_sites:
            try:
                if site["type"] == "wttj_sitemap":
                    results.extend(
                        await _scrape_wttj(client, site, cutoff, known_urls or set())
                    )
                else:
                    logger.warning("Type de source inconnu, ignoré : %r", site["type"])
            except Exception:
                # Un site indisponible ne doit pas faire échouer la génération.
                logger.exception("Site de stages injoignable, ignoré : %r", site)

    return _deduplicate(results)


async def _scrape_wttj(
    client, site: dict, cutoff: datetime, known_urls: set[str]
) -> list[dict]:
    shards = await _wttj_job_shards(client, site["sitemap_index"])
    candidates = []
    for shard_url in shards:
        await asyncio.sleep(REQUEST_DELAY_SECONDS)
        text = _decode(await _get(client, shard_url))
        for url, lastmod in _SITEMAP_ENTRY_RE.findall(text):
            modified = _parse_datetime(lastmod)
            if modified and modified >= cutoff and _looks_like_finance_stage(url):
                candidates.append((modified, url))

    ranked = [url for url in _prioritise(candidates) if url not in known_urls]
    logger.info(
        "WTTJ : %d offres stage/finance récentes, %d déjà en stock, "
        "%d pages à visiter au plus",
        len(candidates), len(candidates) - len(ranked), min(len(ranked), MAX_OFFERS),
    )

    offers = []
    for url in ranked[:MAX_PAGE_VISITS]:
        if len(offers) >= MAX_OFFERS:
            break
        await asyncio.sleep(REQUEST_DELAY_SECONDS)
        try:
            html = _decode(await _get(client, url))
        except SiteThrottledError as error:
            logger.warning(
                "WTTJ nous limite (%s) : arrêt après %d offre(s) récoltée(s).",
                error, len(offers),
            )
            break
        except httpx.HTTPError as error:
            logger.warning("Offre injoignable, ignorée (%s) : %s", error, url)
            continue
        offer = _offer_from_jsonld(html, url)
        if offer:
            offers.append(offer)
    return offers


async def _wttj_job_shards(client, sitemap_index: str) -> list[str]:
    text = _decode(await _get(client, sitemap_index))
    return [
        url for url in _SITEMAP_LOC_RE.findall(text) if "job-listings" in url
    ]


class SiteThrottledError(Exception):
    """Le site nous freine : il répond sans erreur mais sans contenu."""


async def _get(client, url: str) -> bytes:
    response = await client.get(url)
    response.raise_for_status()
    # WTTJ signale la limitation par un 202 au corps vide plutôt que par un
    # 429. Insister ne ferait qu'aggraver : on remonte pour arrêter la boucle.
    if not response.content:
        raise SiteThrottledError(f"corps vide (HTTP {response.status_code})")
    return response.content


def _decode(payload: bytes) -> str:
    # Les shards sont servis en .gz, mais httpx décompresse déjà quand le
    # serveur annonce Content-Encoding : on teste le magic number.
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    return payload.decode("utf-8", "replace")


def _looks_like_finance_stage(url: str) -> bool:
    slug = url.lower()
    if any(m in slug for m in EXCLUDED_SLUG_MARKERS):
        return False
    if any(m in slug for m in PROVINCE_SLUG_MARKERS):
        return False
    return any(m in slug for m in STAGE_SLUG_MARKERS) and any(
        m in slug for m in FINANCE_SLUG_MARKERS
    )


def _prioritise(candidates: list[tuple]) -> list[str]:
    """Ordonne les pages à visiter : un employeur à la fois, plus récent d'abord.

    Trois employeurs pèsent à eux seuls un tiers du sitemap ; un tri par date
    seule leur ferait manger tout le budget de requêtes. On tourne donc entre
    employeurs, ce qui garantit autant d'entreprises différentes que d'offres
    récoltées.
    """
    by_company: dict[str, list] = {}
    for modified, url in sorted(candidates, reverse=True):
        by_company.setdefault(_company_slug(url), []).append(url)

    # Les employeurs les plus fraîchement actifs passent en premier.
    queues = list(by_company.values())
    ordered = []
    while queues:
        queues = [q for q in queues if q]
        for queue in queues:
            ordered.append(queue.pop(0))
    return ordered


def _company_slug(url: str) -> str:
    _, _, rest = url.partition("/companies/")
    return rest.split("/")[0] or url


def _offer_from_jsonld(html: str, url: str) -> dict | None:
    posting = _find_job_posting(html)
    if not posting:
        logger.warning("Pas de JSON-LD JobPosting sur %s", url)
        return None

    # employmentType confirme le stage là où le slug ne faisait que le suggérer.
    employment = posting.get("employmentType")
    employment = employment if isinstance(employment, list) else [employment]
    if not any(str(e).upper() in ("INTERN", "INTERNSHIP") for e in employment):
        return None

    # Le slug ne porte pas toujours la ville ; le JSON-LD donne le pays et le
    # code postal. Une offre française hors Île-de-France est écartée ici —
    # l'étranger, lui, reste le bienvenu. Une donnée absente ne fait jamais
    # rejeter l'offre : mieux vaut une offre de trop qu'une perdue.
    address = _first_address(posting.get("jobLocation"))
    country = _first_country(posting.get("jobLocation"))
    if country and country.upper() in FRENCH_COUNTRIES:
        code = str((address or {}).get("postalCode") or "").strip()
        if code and not code.startswith(ILE_DE_FRANCE_PREFIXES):
            logger.info("Offre en province (%s), ignorée : %s", code, url)
            return None

    duree, debut = extraire_duree_et_debut(posting.get("description") or "")
    return {
        "title": (posting.get("title") or "").strip(),
        "company": (posting.get("hiringOrganization") or {}).get("name"),
        "location": _first_locality(posting.get("jobLocation")),
        "deadline": _to_date(posting.get("validThrough")),
        "duration": duree,
        "start_label": debut,
        "url": url,
    }


# Les descriptions mélangent des champs étiquetés (« Duration: 3 months »), du
# texte libre (« a 12-week long summer internship ») et des leurres qui
# ressemblent à une durée sans en être une (« month-end close », « six months
# on an isolated intern project »). On privilégie donc la précision : mieux
# vaut ne rien annoncer qu'annoncer faux, comme partout ailleurs ici.

_PIEGES = re.compile(r"month[- ]end|monthly|month's|fin de mois", re.I)

_MOIS = (r"(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
         r"septembre|octobre|novembre|décembre|decembre|january|february|march|"
         r"april|may|june|july|august|september|october|november|december)")

_DUREE = (
    # Champ étiqueté : la forme la plus sûre.
    # On capture une valeur structurée, pas du texte libre : la description
    # est aplatie sur une seule ligne, donc rien n'arrêterait la capture avant
    # la phrase suivante.
    re.compile(r"(?:dur[ée]e|duration)[^:\n]{0,24}[:\-–]\s*"
               r"((?:[≥><~]\s*|environ\s+|about\s+)?\d+\s*(?:[-–/]|to|à|ou|and)?\s*\d*\s*"
               r"(?:mois|months?|semaines?|weeks?))", re.I),
    # Durée rattachée explicitement au stage.
    re.compile(r"stage\s+(?:de|d['’]une dur[ée]e de)\s+(\d+\s*(?:à|-)?\s*\d*\s*mois)", re.I),
    re.compile(r"(\d+[\s-]*(?:to|à|-)[\s-]*\d+[\s-]*months?)\s+internship", re.I),
    re.compile(r"internship\s+of\s+(\d+[\s-]*(?:to|-)?[\s-]*\d*\s*months?)", re.I),
    re.compile(r"(\d+[\s-]*(?:week|month)s?)[\s-]*(?:long\s+)?(?:summer\s+)?internship", re.I),
)

_DEBUT = (
    re.compile(r"(?:start(?:ing)? date|date de d[ée]but|d[ée]but du stage)[^:\n]{0,16}[:\-–]\s*"
               r"(" + _MOIS + r"(?:\s*/\s*" + _MOIS + r")?(?:\s+\d{4})?)", re.I),
    re.compile(r"(?:à partir (?:de|du)|starting (?:in|from))\s+"
               r"(" + _MOIS + r"\s*\d{0,4})", re.I),
)


def _nettoyer(valeur: str) -> str | None:
    valeur = re.sub(r"\s+", " ", valeur).strip(" .,;:-–—")
    if not valeur or len(valeur) > 32 or _PIEGES.search(valeur):
        return None
    return valeur


def extraire_duree_et_debut(description: str) -> tuple[str | None, str | None]:
    texte = re.sub(r"<[^>]+>", " ", description or "")
    texte = re.sub(r"\s+", " ", texte)

    def premier(motifs):
        for motif in motifs:
            trouve = motif.search(texte)
            if trouve:
                propre = _nettoyer(trouve.group(1))
                if propre:
                    return propre
        return None

    return premier(_DUREE), premier(_DEBUT)


def _find_job_posting(html: str) -> dict | None:
    for block in _LD_JSON_RE.findall(html):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        for entry in parsed if isinstance(parsed, list) else [parsed]:
            if isinstance(entry, dict) and entry.get("@type") == "JobPosting":
                return entry
    return None


def _first_locality(job_location) -> str | None:
    address = _first_address(job_location)
    return address.get("addressLocality") if address else None


def _first_country(job_location) -> str | None:
    address = _first_address(job_location)
    country = address.get("addressCountry") if address else None
    # schema.org autorise soit le code pays, soit un objet Country.
    if isinstance(country, dict):
        country = country.get("name")
    return country.strip() if isinstance(country, str) and country.strip() else None


def _first_address(job_location) -> dict | None:
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else None
    if not isinstance(job_location, dict):
        return None
    address = job_location.get("address")
    return address if isinstance(address, dict) else None


def _to_date(value) -> str | None:
    parsed = _parse_datetime(value)
    return parsed.date().isoformat() if parsed else None


def _parse_datetime(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _deduplicate(offers: list[dict]) -> list[dict]:
    # WTTJ republie parfois une même annonce sous deux URL (identifiant de
    # suivi différent) : c'est le couple titre/entreprise qui fait foi, pas
    # l'URL, sinon le doublon occupe deux places dans la newsletter.
    seen = set()
    unique = []
    for offer in offers:
        key = (
            (offer["title"] or "").strip().casefold(),
            (offer["company"] or "").strip().casefold(),
        )
        if key not in seen:
            seen.add(key)
            unique.append(offer)
    return unique


def run_fetch_stage_offers(
    target_sites: list[dict], known_urls: set[str] | None = None
) -> list[dict]:
    """Wrapper synchrone pour appel depuis un script non-async."""
    return asyncio.run(fetch_stage_offers(target_sites, known_urls))


# L'intitulé est la source la plus rentable : 35 % des offres y annoncent leur
# date de début, contre 4 % dans le texte des annonces — et il est déjà là,
# quelle que soit la source, sans une requête de plus. Les API employeurs, qui
# fournissent les trois quarts du stock, ne renvoient d'ailleurs aucune
# description exploitable.
_INTITULE_DUREE = re.compile(
    r"\b(\d{1,2}\s*(?:à|-|/)\s*\d{1,2}\s*mois|\d{1,2}\s*mois"
    r"|\d{1,2}[\s-]*months?|\d{1,2}[\s-]*weeks?)\b", re.I)
_INTITULE_DEBUT = re.compile(
    rf"\b({_MOIS}\s*(?:à|-|to|–)\s*{_MOIS}\s*\d{{4}}"
    rf"|{_MOIS}\s*\d{{4}}"
    r"|Q[1-4]\s*\d{4}"
    r"|(?:summer|été|fall|spring|automne|printemps)\s*\d{4}"
    r"|d[ée]but imm[ée]diat|asap)\b", re.I)


def completer_depuis_intitule(offre: dict) -> dict:
    """Complète durée et début à partir de l'intitulé, sans écraser l'existant.

    Le texte de l'annonce reste prioritaire quand il dit quelque chose : il est
    plus explicite. L'intitulé prend le relais, et il le prend souvent.
    """
    titre = offre.get("title") or ""
    if not offre.get("duration"):
        trouve = _INTITULE_DUREE.search(titre)
        if trouve:
            offre["duration"] = _nettoyer(trouve.group(1))
    if not offre.get("start_label"):
        trouve = _INTITULE_DEBUT.search(titre)
        if trouve:
            offre["start_label"] = _nettoyer(trouve.group(1))
    return offre
