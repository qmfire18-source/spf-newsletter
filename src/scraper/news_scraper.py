"""Récupération des actualités financières brutes — voir PLAN.md §1.

L'IA (draft_generator.py) se charge du résumé/de la mise en forme ensuite :
ce module renvoie du contenu brut, dédupliqué.
"""
import feedparser
from datetime import datetime, timedelta


def fetch_news(sources: list[dict]) -> list[dict]:
    """
    sources: [{"type": "rss", "url": "..."}, {"type": "newsapi", "query": "..."}]
    Retourne une liste de dicts bruts, dédupliqués, limités aux 7 derniers jours :
    [{"title": ..., "source": ..., "url": ..., "raw_summary": ..., "published": ...}]
    """
    items = []
    cutoff = datetime.utcnow() - timedelta(days=7)

    for src in sources:
        if src["type"] == "rss":
            items.extend(_fetch_rss(src["url"], cutoff))
        elif src["type"] == "newsapi":
            items.extend(_fetch_newsapi(src["query"], cutoff))

    return _deduplicate(items)


def _fetch_rss(url: str, cutoff: datetime) -> list[dict]:
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries:
        # TODO: parser entry.published_parsed et filtrer par cutoff
        results.append({
            "title": entry.get("title"),
            "source": feed.feed.get("title", url),
            "url": entry.get("link"),
            "raw_summary": entry.get("summary", ""),
        })
    return results


def _fetch_newsapi(query: str, cutoff: datetime) -> list[dict]:
    # TODO: appel requests.get à https://newsapi.org/v2/everything avec NEWSAPI_KEY
    return []


def _deduplicate(items: list[dict]) -> list[dict]:
    seen_urls = set()
    unique = []
    for item in items:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            unique.append(item)
    # TODO: dédup additionnelle par similarité de titre (difflib.SequenceMatcher)
    return unique
