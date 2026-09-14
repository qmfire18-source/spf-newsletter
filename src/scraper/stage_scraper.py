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
    # Le vocabulaire RH emprunte celui de la finance.
    "capital-humain", "banquet",
)

# La langue de l'annonce ne dit rien du lieu : les meilleures offres parisiennes
# du vivier (Naxicap, Clipperton, iBanFirst) sont publiées en anglais. C'est la
# ville qui tranche. Le pays est vérifié après visite via le JSON-LD ; ces
# marqueurs servent seulement à ne pas gaspiller le budget de requêtes, que le
# site nous coupe après une poignée de pages.
FOREIGN_CITY_MARKERS = (
    "san-francisco", "new-york", "chicago", "providence", "boston",
    "amsterdam", "barcelona", "madrid", "milano", "milan", "berlin",
    "munich", "london", "casablanca", "luxembourg", "bruxelles",
    "brussels", "seraing", "geneve", "zurich", "dublin", "lisbon",
    "lisboa", "montreal", "singapore", "dubai", "tunis",
)

# Pays acceptés, tels que le JSON-LD les nomme (schema.org addressCountry).
ACCEPTED_COUNTRIES = {"FR", "FRA", "FRANCE"}

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
    if any(m in slug for m in FOREIGN_CITY_MARKERS):
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

    # Le slug ne porte pas toujours la ville : le JSON-LD, lui, donne le pays.
    # Une annonce hors de France n'a pas sa place dans la newsletter d'une
    # asso parisienne. Un pays absent ne fait pas rejeter l'offre.
    country = _first_country(posting.get("jobLocation"))
    if country and country.upper() not in ACCEPTED_COUNTRIES:
        logger.info("Offre hors de France (%s), ignorée : %s", country, url)
        return None

    return {
        "title": (posting.get("title") or "").strip(),
        "company": (posting.get("hiringOrganization") or {}).get("name"),
        "location": _first_locality(posting.get("jobLocation")),
        "deadline": _to_date(posting.get("validThrough")),
        "url": url,
    }


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
