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

# Sujets grand public que les flux économie charrient en masse et qui n'ont pas
# leur place dans une newsletter de finance : ils passaient le filtre par des
# mots comme "financer le plan d'aide" ou "taux de chômage". Mesuré sur une
# semaine réelle : 16 actus sur 60 (27 %) relevaient de cette catégorie.
# Le veto s'applique même si un marqueur finance a été trouvé.
CONSUMER_NOISE_MARKERS = (
    "carburant", "essence", "diesel", "gazole", "prix a la pompe",
    "pouvoir d achat", "tomate", "panier", "supermarche", "cantine",
    "ticket restaurant", "chomage", "greve", "retraite", "sncf", "peage",
    "changement climatique", "ecologie", "canicule",
)

# Google News agrège sans filtre éditorial : courtiers en crédit, republieurs
# de communiqués, sites crypto spéculatifs et agrégateurs sans rédaction y
# côtoient la presse établie. Une newsletter d'association ne peut pas relayer
# ça — une seule reprise douteuse coûte plus cher que dix actus manquées.
#
# Liste blanche plutôt que liste noire : le bruit se renouvelle, la presse de
# référence beaucoup moins. Une source inconnue est écartée, pas publiée.
# Les flux RSS de NEWS_SOURCES ne passent pas par ici : nous les avons
# choisis un par un, ils sont fiables par construction.
TRUSTED_GNEWS_SOURCES = (
    # Presse économique et financière
    "les echos", "investir", "l'agefi", "agefi", "option finance",
    "la tribune", "challenges", "capital", "le revenu", "mieux vivre",
    "l'usine nouvelle", "décideurs", "decideurs", "business immo",
    "daf-mag", "revue banque", "l'argus de l'assurance",
    # Quotidiens et hebdomadaires généralistes
    "le monde", "le figaro", "libération", "liberation", "le point",
    "l'express", "l'obs", "mediapart", "l'opinion", "la croix",
    "ouest-france", "sud ouest", "les jours", "alternatives économiques",
    "le parisien", "les inrocks", "marianne", "touteleurope",
    # Audiovisuel public et grandes chaînes
    "franceinfo", "france info", "france 24", "radio france", "france inter",
    "tf1", "bfm", "europe 1", "rfi", "arte", "lci", "rts.ch", "rtbf",
    "tv5monde", "france télévisions", "france televisions",
    # Marchés
    "boursorama", "boursier", "zonebourse", "morningstar", "investing.com",
    # Presse internationale de référence
    "reuters", "bloomberg", "financial times", "wall street journal",
    "the economist", "le temps", "l'echo", "de tijd", "handelsblatt",
    "el país", "el pais", "il sole 24 ore", "euractiv", "politico",
    "associated press", "afp", "the guardian", "cnbc",
    # Institutions
    "banque de france", "banque centrale européenne", "european central bank",
    "autorité des marchés financiers", "insee", "eurostat", "ocde", "oecd",
    "fmi", "imf", "commission européenne",
)

# Poids éditorial : un sujet traité par la presse financière compte davantage
# pour des étudiants en finance que le même sujet dans un quotidien
# généraliste. C'est un jugement sur la hiérarchie des titres, pas une vérité.
POIDS_SOURCES = {
    3: ("les echos", "investir", "l'agefi", "agefi", "option finance",
        "reuters", "bloomberg", "financial times", "wall street journal",
        "revue banque", "l'argus de l'assurance"),
    2: ("la tribune", "challenges", "capital", "le monde", "le figaro",
        "le point", "l'opinion", "le temps", "mediapart", "euractiv",
        "boursorama", "zonebourse", "morningstar", "business immo",
        "banque de france", "european central bank", "insee"),
}
POIDS_PAR_DEFAUT = 1

# Deux titres qui partagent au moins ce quart de leurs mots significatifs
# parlent du même événement. Mesuré sur une semaine réelle : en dessous, des
# sujets voisins fusionnaient ; au-dessus, la BCE se scindait en six.
SEUIL_MEME_SUJET = 1 / 3
MOTS_COMMUNS_MINIMUM = 2

# Le filtre par longueur jetait « BCE », « FMI », « OPA » — les sigles qui
# désignent justement l'acteur du sujet — tout en gardant « face » ou « selon ».
# On écarte donc une liste de mots outils, et on garde les sigles.
MOTS_VIDES = frozenset(
    """
    alors apres aussi autre autres avant avec avoir bien cela ces cet cette ceux
    chez comme contre dans depuis deux dont elle elles encore entre etre face
    fait faire fois font hier ils leur leurs mais meme moins nous pour plus pres
    quand que quel quelle qui quoi sans selon ses son sont sous sur tous tout
    toute toutes trois tres vers vont vous ans annee annees jour jours semaine
    mois deja voici voila etait ont une des les aux par est car donc lundi mardi
    mercredi jeudi vendredi samedi dimanche direct video live
    """.split()
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
                items.extend(_trusted_only(_fetch_gnews(src["query"], cutoff)))
            elif src["type"] == "newsapi":
                items.extend(_fetch_newsapi(src["query"], cutoff))
            else:
                logger.warning("Type de source inconnu, ignoré : %r", src["type"])
        except Exception:
            # Un flux injoignable ne doit pas faire échouer la génération hebdomadaire.
            logger.exception("Source injoignable, ignorée : %r", src)

    relevant = [item for item in _deduplicate(items) if _is_finance_related(item)]
    classees = _classer_par_importance(relevant)
    logger.info(
        "Actus : %d collectées, %d pertinentes, %d sujets distincts, %d retenus",
        len(items), len(relevant), len(classees), min(len(classees), MAX_NEWS_ITEMS),
    )
    return classees[:MAX_NEWS_ITEMS]


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


def _trusted_only(items: list[dict]) -> list[dict]:
    """Ne garde que les actus issues d'une source de la liste blanche."""
    gardees = []
    for item in items:
        if is_trusted_source(item.get("source")):
            gardees.append(item)
        else:
            logger.info("Source non reconnue, actu écartée : %r", item.get("source"))
    if len(gardees) != len(items):
        logger.info(
            "Google News : %d actus sur %d viennent d'une source de confiance.",
            len(gardees), len(items),
        )
    return gardees


def is_trusted_source(source: str | None) -> bool:
    """Le nom vient parfois sous forme de domaine : « lepoint.fr » pour Le Point.

    On compare donc aussi sans les espaces — sinon « le point » ne
    reconnaîtrait pas « lepoint fr » une fois la ponctuation retirée.

    Mais pas pour les sigles courts : « rfi » se cache dans
    « f-rfi-nanceyahoocom », ce qui faisait passer Yahoo Finance pour Radio
    France Internationale. En dessous de six caractères, le nom doit
    apparaître comme un mot entier.
    """
    nom = _normalize_title(source or "")
    if not nom:
        return False

    mots = set(nom.split())
    compact = nom.replace(" ", "")

    for connue in TRUSTED_GNEWS_SOURCES:
        attendu = _normalize_title(connue)
        serre = attendu.replace(" ", "")
        if len(serre) < 6:
            # Sigle : mot entier exigé, dans un sens comme dans l'autre.
            if attendu in mots or serre in mots:
                return True
        elif attendu in nom or serre in compact:
            return True
    return False


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
    padded = f" {normalized} "

    # Le macro (BCE, dette, déficit, croissance) reste dans le périmètre : c'est
    # l'angle consommateur qui ne l'est pas.
    if any(marker in padded for marker in CONSUMER_NOISE_MARKERS):
        return False

    tokens = normalized.split()
    if any(token.startswith(FINANCE_KEYWORD_STEMS) for token in tokens):
        return True
    return any(f" {phrase} " in padded for phrase in FINANCE_KEYWORD_PHRASES)


def poids_source(source: str | None) -> int:
    nom = _normalize_title(source or "")
    if not nom:
        return POIDS_PAR_DEFAUT
    compact = nom.replace(" ", "")
    for poids, connues in POIDS_SOURCES.items():
        for connue in connues:
            attendu = _normalize_title(connue)
            if attendu in nom or attendu.replace(" ", "") in compact:
                return poids
    return POIDS_PAR_DEFAUT


def _mots_significatifs(titre: str) -> set:
    """Les mots qui portent le sujet : ni mots outils, ni nombres isolés."""
    mots = _normalize_title(titre or "").split()
    return {
        mot
        for mot in mots
        if mot not in MOTS_VIDES and (len(mot) >= 3 or (mot.isdigit() and len(mot) >= 2))
    }


def grouper_par_sujet(items: list[dict]) -> list[list[dict]]:
    """Rassemble les actus qui racontent le même événement.

    La déduplication exacte ne voit pas que « La BCE relève ses taux » et
    « La Banque centrale européenne relève de 0,25 point » sont le même
    sujet : elles comparent des titres, pas des événements. On regroupe donc
    sur le recouvrement des mots significatifs.
    """
    groupes: list[dict] = []
    for item in items:
        mots = _mots_significatifs(item.get("title"))
        for groupe in groupes:
            if _meme_sujet(mots, groupe["mots"]):
                groupe["items"].append(item)
                break
        else:
            groupes.append({"mots": mots, "items": [item]})
    return [groupe["items"] for groupe in groupes]


def _meme_sujet(mots: set, reference: set) -> bool:
    """Deux titres racontent le même événement.

    On rapporte les mots communs au plus court des deux titres, et non à leur
    réunion : « Direct - La BCE relève ses taux » et une dépêche de vingt mots
    sur la même décision ont peu de mots en commun rapportés à l'ensemble,
    beaucoup rapportés au plus court.

    Le minimum de deux mots communs est ce qui empêche la mesure de déborder :
    sans lui, « Le taux français à 10 ans dépasse 4,5 % » rejoindrait la BCE
    sur le seul mot « taux ».
    """
    commun = mots & reference
    plus_court = min(len(mots), len(reference))
    if not plus_court or len(commun) < MOTS_COMMUNS_MINIMUM:
        return False
    return len(commun) / plus_court >= SEUIL_MEME_SUJET


def _classer_par_importance(items: list[dict]) -> list[dict]:
    """Un sujet par événement, les plus importants d'abord.

    L'importance se mesure à deux choses : combien de rédactions couvrent le
    sujet, et lesquelles. Sans ça, l'ordre ne tenait qu'à la fraîcheur et à
    la rotation des sources — on développait des sujets couverts par une
    seule rédaction pendant que l'événement de la semaine partait en brève.

    Seul le meilleur article de chaque sujet est conservé : garder les autres
    reviendrait à proposer six fois la même actualité.
    """
    classes = []
    for groupe in grouper_par_sujet(items):
        # Représentant : la source la plus qualifiée, la plus récente à égalité.
        meilleur = max(
            groupe,
            key=lambda i: (poids_source(i.get("source")), _published_sort_key(i)),
        )
        meilleur["reprises"] = len(groupe)
        meilleur["poids_source"] = poids_source(meilleur.get("source"))
        meilleur["importance"] = meilleur["reprises"] * meilleur["poids_source"]
        classes.append(meilleur)

    classes.sort(key=lambda i: (i["importance"], _published_sort_key(i)), reverse=True)
    return classes


def _spread_across_sources(items: list[dict]) -> list[dict]:
    """Alterne les sources, du plus récent au plus ancien dans chacune.

    Un tri par date seule laissait une source unique occuper 27 % de la liste
    (mesuré : 16 actus sur 60 pour un même flux généraliste), et donc le haut
    de ce que voit l'IA. On tourne entre flux pour que les têtes de liste
    viennent d'autant de rédactions différentes que possible.
    """
    by_source: dict[str, list[dict]] = {}
    for item in sorted(items, key=_published_sort_key, reverse=True):
        by_source.setdefault(item.get("source") or "?", []).append(item)

    queues = list(by_source.values())
    ordered = []
    while queues:
        queues = [q for q in queues if q]
        for queue in queues:
            ordered.append(queue.pop(0))
    return ordered


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
