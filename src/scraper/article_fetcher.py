"""Récupération du texte des articles retenus — support des items développés.

Les flux RSS ne livrent qu'un résumé de 86 caractères en médiane, le plus
souvent le titre répété. Impossible d'écrire autre chose qu'une liste de liens
à partir de ça : pour développer une actualité avec ses chiffres, il faut le
texte de l'article.

Trois garde-fous, dans l'esprit du reste du projet :

- `robots.txt` fait foi, par domaine, et le résultat est mémorisé le temps de
  l'exécution. Un domaine qui nous interdit n'est pas visité.
- On ne visite que les articles réellement candidats à un item développé, pas
  les soixante collectés, et avec un délai entre requêtes.
- Un site qui rend ses pages en JavaScript (Le Monde, BFMTV) renvoie une
  coquille vide : on le détecte et on renonce, sans chercher à exécuter du JS.
  L'actualité reste utilisable en brève, avec son titre et son lien.
"""
import logging
import re
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "SPFNewsletterBot/1.0 (newsletter Sciences Po Finance)"
REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 20
# En deçà, la page est une coquille (mur JavaScript, page de consentement) :
# il n'y a pas de quoi écrire un paragraphe argumenté.
MIN_USABLE_CHARS = 600
# Au-delà, on tronque : le début d'un article porte l'essentiel, et le prompt
# ne doit pas enfler inutilement.
MAX_TEXT_CHARS = 4000

# Redirections de consentement et agrégateurs : l'URL ne mène pas à un article.
UNREADABLE_HOSTS = ("news.google.com", "consent.google.com", "consent.youtube.com")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_BLOCK_RE = re.compile(
    r"<(script|style|nav|header|footer|aside|form|figure)\b.*?</\1>", re.S | re.I
)
_PARA_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
# Rognures d'habillage que les pages mêlent aux paragraphes d'article.
_BOILERPLATE_RE = re.compile(
    r"(?i)(accepter (?:et continuer|tous)|nous utilisons des cookies|"
    r"passer la publicité|activez les notifications|réservé aux abonnés|"
    r"enable javascript|veuillez activer|abonnez-vous|newsletter|"
    r"flash info|suivez l.actu|changer d.avis à tout moment|"
    r"réglages de votre navigateur|connectez-vous ou créez un compte|"
    r"temps de lecture|tous droits réservés|©\s*\d{4}|"
    r"partager l.article|cet article est réservé)"
)

_robots_cache: dict[str, RobotFileParser | None] = {}


class ArticleUnavailable(Exception):
    """L'article n'est pas lisible : interdit, injoignable ou rendu en JS."""


def fetch_article_text(url: str, session: requests.Session | None = None) -> str | None:
    """Texte de l'article, ou None s'il n'est pas lisible sans exécuter de JS."""
    if _is_aggregator(url):
        logger.info("Agrégateur, pas d'article à lire : %s", url)
        return None

    if not _robots_allow(url, session):
        logger.info("robots.txt interdit la lecture, article ignoré : %s", url)
        return None

    try:
        response = (session or requests).get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        logger.info("Article injoignable (%s) : %s", error, url)
        return None

    if any(urlsplit(response.url).netloc.lower().endswith(bad) for bad in UNREADABLE_HOSTS):
        logger.info("Redirigé vers une page de consentement, ignoré : %s", url)
        return None

    text = extract_text(response.text)
    if len(text) < MIN_USABLE_CHARS:
        logger.info(
            "Page trop pauvre (%d car.), probablement rendue en JS : %s", len(text), url
        )
        return None
    return text[:MAX_TEXT_CHARS]


def _is_aggregator(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(host.endswith(bad) for bad in UNREADABLE_HOSTS)


def extract_text(html: str) -> str:
    """Concatène les paragraphes de la page, hors habillage."""
    body = _BLOCK_RE.sub(" ", html or "")
    paragraphs = []
    for raw in _PARA_RE.findall(body):
        para = _WS_RE.sub(" ", _TAG_RE.sub(" ", raw)).strip()
        # Les paragraphes courts sont presque toujours de l'habillage.
        if len(para) < 60 or _BOILERPLATE_RE.search(para):
            continue
        paragraphs.append(para)
    return _WS_RE.sub(" ", " ".join(paragraphs)).strip()


def enrich_with_article_text(
    items: list[dict], limit: int, max_attempts: int = 30
) -> list[dict]:
    """Ajoute `full_text` aux `limit` premières actus dont l'article est lisible.

    Les items sont parcourus dans l'ordre reçu — celui du classement du
    scraper — et la boucle s'arrête dès que `limit` articles ont été lus.
    Les autres gardent leur titre et leur lien : ils feront des brèves.
    """
    read = attempts = 0
    with requests.Session() as session:
        for item in items:
            if read >= limit or attempts >= max_attempts:
                break
            url = item.get("url", "")
            # Un agrégateur est écarté sans requête : il ne coûte pas un essai.
            if _is_aggregator(url):
                continue
            if attempts:
                time.sleep(REQUEST_DELAY_SECONDS)
            attempts += 1
            text = fetch_article_text(url, session)
            if text:
                item["full_text"] = text
                read += 1

    logger.info(
        "Articles lus intégralement : %d, sur %d pages visitées", read, attempts
    )
    return items


def _robots_allow(url: str, session: requests.Session | None) -> bool:
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"

    if base not in _robots_cache:
        _robots_cache[base] = _load_robots(base, session)

    parser = _robots_cache[base]
    if parser is None:
        # robots.txt illisible : on s'abstient plutôt que de supposer l'accord.
        return False
    return parser.can_fetch(USER_AGENT, url)


def _load_robots(base: str, session: requests.Session | None) -> RobotFileParser | None:
    try:
        response = (session or requests).get(
            f"{base}/robots.txt",
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        logger.info("robots.txt injoignable sur %s (%s)", base, error)
        return None

    if response.status_code >= 400:
        # Pas de robots.txt : rien n'est interdit (RFC 9309).
        parser = RobotFileParser()
        parser.parse([])
        return parser

    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser
