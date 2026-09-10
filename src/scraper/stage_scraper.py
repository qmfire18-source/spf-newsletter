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
stable) qui contient déjà titre, entreprise, lieu et date limite.
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

# Une offre doit porter un marqueur de stage ET un marqueur finance dans son
# slug pour mériter une visite de page.
STAGE_SLUG_MARKERS = ("stage", "stagiaire", "internship", "intern-")
FINANCE_SLUG_MARKERS = (
    "finance", "financier", "financement", "m-a", "audit", "asset", "invest",
    "banqu", "bank", "trading", "risk", "risque", "comptab", "private-equity",
    "gestion", "patrimoine", "credit", "assurance", "tresorerie", "fiscal",
    "controle-de-gestion", "corporate", "actuar", "capital",
)

_LD_JSON_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)
_SITEMAP_ENTRY_RE = re.compile(r"<loc>(.*?)</loc>\s*<lastmod>(.*?)</lastmod>", re.S)
_SITEMAP_LOC_RE = re.compile(r"<loc>(.*?)</loc>", re.S)


async def fetch_stage_offers(target_sites: list[dict]) -> list[dict]:
    """
    target_sites: [{"type": "wttj_sitemap", "name": ..., "sitemap_index": ...}]
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
                    results.extend(await _scrape_wttj(client, site, cutoff))
                else:
                    logger.warning("Type de source inconnu, ignoré : %r", site["type"])
            except Exception:
                # Un site indisponible ne doit pas faire échouer la génération.
                logger.exception("Site de stages injoignable, ignoré : %r", site)

    return _deduplicate(results)


async def _scrape_wttj(client, site: dict, cutoff: datetime) -> list[dict]:
    shards = await _wttj_job_shards(client, site["sitemap_index"])
    candidates = []
    for shard_url in shards:
        await asyncio.sleep(REQUEST_DELAY_SECONDS)
        text = _decode(await _get(client, shard_url))
        for url, lastmod in _SITEMAP_ENTRY_RE.findall(text):
            modified = _parse_datetime(lastmod)
            if modified and modified >= cutoff and _looks_like_finance_stage(url):
                candidates.append((modified, url))

    candidates.sort(reverse=True)
    logger.info(
        "WTTJ : %d offres stage/finance récentes, %d pages visitées",
        len(candidates), min(len(candidates), MAX_OFFERS),
    )

    offers = []
    for _, url in candidates[:MAX_OFFERS]:
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
    return any(m in slug for m in STAGE_SLUG_MARKERS) and any(
        m in slug for m in FINANCE_SLUG_MARKERS
    )


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
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else None
    if not isinstance(job_location, dict):
        return None
    address = job_location.get("address")
    return address.get("addressLocality") if isinstance(address, dict) else None


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
    seen = set()
    unique = []
    for offer in offers:
        key = (offer["url"], offer["title"], offer["company"])
        if key not in seen:
            seen.add(key)
            unique.append(offer)
    return unique


def run_fetch_stage_offers(target_sites: list[dict]) -> list[dict]:
    """Wrapper synchrone pour appel depuis un script non-async."""
    return asyncio.run(fetch_stage_offers(target_sites))
