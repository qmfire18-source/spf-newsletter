"""Récupération des actualités financières brutes — voir PLAN.md §1.

L'IA (draft_generator.py) se charge du résumé/de la mise en forme ensuite :
ce module renvoie du contenu brut, dédupliqué.
"""
import html
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests

from src.config import NEWSAPI_KEY

logger = logging.getLogger(__name__)

USER_AGENT = "SPFNewsletterBot/1.0 (newsletter Sciences Po Finance)"
REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 15
TITLE_SIMILARITY_THRESHOLD = 0.85
MAX_SUMMARY_CHARS = 600
MAX_NEWS_ITEMS = 60

# Les flux généralistes économie ramènent ~40% de hors-sujet (tech, conso,
# social). On préfiltre avant l'appel à Claude : lui faire trier 300 actus
# pour en retenir 3 à 5 dilue la sélection.
# Comparés à des titres normalisés (minuscules, sans accents), en début de mot
# pour attraper les dérivés : "financ" → finance, financier, financement.
FINANCE_KEYWORD_STEMS = (
    "financ", "march", "bours", "action", "obligat", "taux", "bce", "fed",
    "banqu", "fusion", "acquisit", "invest", "fonds", "trading", "cac",
    "dette", "inflation", "credit", "assur", "capital", "valorisat", "rachat",
    "dividende", "ipo", "cotation", "cote", "benefice", "resultat", "chiffre",
    "introduction", "emprunt", "monnaie", "euro", "dollar", "trader",
)
# Expressions en plusieurs mots, cherchées comme tokens isolés pour éviter les
# faux positifs ("m a" ne doit pas matcher "filM Américain").
FINANCE_KEYWORD_PHRASES = (
    "m a", "private equity", "wall street", "hedge fund", "gestion d actifs",
    "asset management", "capital risque", "levee de fonds",
)

GNEWS_ENDPOINT = "https://news.google.com/rss/search"
NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALPHANUM_RE = re.compile(r"[^a-z0-9 ]")
_TRACKING_PREFIXES = ("utm_", "at_")
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "xtor", "ref", "cmpid"}


def fetch_news(sources: list[dict]) -> list[dict]:
    """
    sources: [{"type": "rss", "url": "..."}, {"type": "gnews", "query": "..."},
              {"type": "newsapi", "query": "..."}]
    Retourne une liste de dicts bruts, dédupliqués, limités aux 7 derniers jours,
    filtrés sur les mots-clés finance et plafonnés aux MAX_NEWS_ITEMS plus
    récents :
    [{"title": ..., "source": ..., "url": ..., "raw_summary": ..., "published": ...}]
    """
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    for index, src in enumerate(sources):
        if index:
            time.sleep(REQUEST_DELAY_SECONDS)
        try:
            if src["type"] == "rss":
                items.extend(_fetch_rss(src["url"], cutoff))
            elif src["type"] == "gnews":
                items.extend(_fetch_gnews(src["query"], cutoff))
            elif src["type"] == "newsapi":
                items.extend(_fetch_newsapi(src["query"], cutoff))
            else:
                logger.warning("Type de source inconnu, ignoré : %r", src["type"])
        except Exception:
            # Un flux injoignable ne doit pas faire échouer la génération hebdomadaire.
            logger.exception("Source injoignable, ignorée : %r", src)

    relevant = [item for item in _deduplicate(items) if _is_finance_related(item)]
    relevant.sort(key=_published_sort_key, reverse=True)
    logger.info(
        "Actus : %d collectées, %d pertinentes, %d retenues",
        len(items), len(relevant), min(len(relevant), MAX_NEWS_ITEMS),
    )
    return relevant[:MAX_NEWS_ITEMS]


def _fetch_rss(url: str, cutoff: datetime) -> list[dict]:
    feed = feedparser.parse(_http_get(url).content)
    source_name = feed.feed.get("title") or urlsplit(url).netloc

    results = []
    for entry in feed.entries:
        item = _entry_to_item(entry, source_name, cutoff)
        if item:
            results.append(item)
    return results


def _fetch_gnews(query: str, cutoff: datetime) -> list[dict]:
    """Couche mots-clés : Google News agrège la presse FR sans clé d'API."""
    params = {"q": f"{query} when:7d", "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
    feed = feedparser.parse(_http_get(GNEWS_ENDPOINT, params=params).content)

    results = []
    for entry in feed.entries:
        source_name = entry.get("source", {}).get("title") or "Google News"
        item = _entry_to_item(entry, source_name, cutoff)
        if item:
            item["title"] = _strip_source_suffix(item["title"], source_name)
            results.append(item)
    return results


def _fetch_newsapi(query: str, cutoff: datetime) -> list[dict]:
    if not NEWSAPI_KEY:
        logger.warning("NEWSAPI_KEY absente : source newsapi %r ignorée", query)
        return []

    params = {
        "q": query,
        "from": cutoff.date().isoformat(),
        "language": "fr",
        "sortBy": "publishedAt",
        "pageSize": 50,
    }
    response = _http_get(
        NEWSAPI_ENDPOINT, params=params, headers={"X-Api-Key": NEWSAPI_KEY}
    )

    results = []
    for article in response.json().get("articles", []):
        title = (article.get("title") or "").strip()
        url = article.get("url")
        if not title or not url:
            continue
        results.append({
            "title": title,
            "source": (article.get("source") or {}).get("name") or "NewsAPI",
            "url": url,
            "raw_summary": _clean_html(article.get("description") or ""),
            "published": article.get("publishedAt"),
        })
    return results


def _http_get(url: str, params: dict | None = None, headers: dict | None = None):
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response


def _entry_to_item(entry, source_name: str, cutoff: datetime) -> dict | None:
    title = (entry.get("title") or "").strip()
    url = entry.get("link")
    if not title or not url:
        return None

    published = _entry_datetime(entry)
    if published and published < cutoff:
        return None

    # Une entrée sans date est conservée : certains flux omettent pubDate et
    # ne publient de toute façon que du récent — l'écarter perdrait la source.
    return {
        "title": title,
        "source": source_name,
        "url": url,
        "raw_summary": _clean_html(entry.get("summary", "")),
        "published": published.isoformat() if published else None,
    }


def _entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def _strip_source_suffix(title: str, source_name: str) -> str:
    """Google News suffixe le titre par le média : "Titre de l'article - Le Monde"."""
    suffix = f" - {source_name}"
    return title[: -len(suffix)] if title.endswith(suffix) else title


def _clean_html(text: str) -> str:
    stripped = html.unescape(_TAG_RE.sub(" ", text or ""))
    collapsed = _WHITESPACE_RE.sub(" ", stripped).strip()
    return collapsed[:MAX_SUMMARY_CHARS]


def _deduplicate(items: list[dict]) -> list[dict]:
    unique = []
    seen_urls = set()
    seen_titles = []

    for item in items:
        url_key = _normalize_url(item["url"])
        if url_key in seen_urls:
            continue

        title_key = _normalize_title(item["title"])
        if any(_titles_match(title_key, seen) for seen in seen_titles):
            continue

        seen_urls.add(url_key)
        seen_titles.append(title_key)
        unique.append(item)

    return unique


def _is_finance_related(item: dict) -> bool:
    normalized = _normalize_title(f"{item['title']} {item.get('raw_summary', '')}")
    tokens = normalized.split()
    if any(token.startswith(FINANCE_KEYWORD_STEMS) for token in tokens):
        return True
    padded = f" {normalized} "
    return any(f" {phrase} " in padded for phrase in FINANCE_KEYWORD_PHRASES)


def _published_sort_key(item: dict) -> datetime:
    published = item.get("published")
    if published:
        try:
            return datetime.fromisoformat(published)
        except ValueError:
            pass
    # Sans date exploitable, l'actu passe en fin de liste plutôt qu'en tête.
    return datetime.min.replace(tzinfo=timezone.utc)


def _titles_match(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= TITLE_SIMILARITY_THRESHOLD


def _normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_PREFIXES)
        and key.lower() not in _TRACKING_PARAMS
    ]
    netloc = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def _normalize_title(title: str) -> str:
    decomposed = unicodedata.normalize("NFKD", title.lower())
    unaccented = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _WHITESPACE_RE.sub(" ", _NON_ALPHANUM_RE.sub(" ", unaccented)).strip()
