"""Chargement centralisé des variables d'environnement."""
import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
BREVO_LIST_ID = os.getenv("BREVO_LIST_ID")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./spf.db")
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY")
ALLOWED_REVIEWER_EMAILS = [
    e.strip().lower() for e in os.getenv("ALLOWED_REVIEWER_EMAILS", "").split(",") if e
]

# Mot de passe partagé du bureau, stocké haché (voir scripts/hash_password.py).
REVIEWER_PASSWORD_HASH = os.getenv("REVIEWER_PASSWORD_HASH", "")
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", str(12 * 3600)))
# Le cookie n'est envoyé qu'en HTTPS ; à passer à false pour un dev en local.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").strip().lower() != "false"

# Sources par défaut — flux vérifiés le 2026-09-10.
# Les Echos, Boursorama, Zonebourse, Boursier, AbcBourse et l'AGEFI ont été
# écartés : leurs flux RSS renvoient 403 (anti-bot) ou 404.
NEWS_SOURCES = [
    # Généralistes économie (FR)
    {"type": "rss", "url": "https://www.lemonde.fr/entreprises/rss_full.xml"},
    {"type": "rss", "url": "https://www.lefigaro.fr/rss/figaro_economie.xml"},
    {"type": "rss", "url": "https://www.bfmtv.com/rss/economie/"},
    {"type": "rss", "url": "https://www.challenges.fr/rss.xml"},
    {"type": "rss", "url": "https://www.francetvinfo.fr/economie.rss"},
    # Banques centrales
    {"type": "rss", "url": "https://www.ecb.europa.eu/rss/press.html"},
    # Marchés (anglais)
    {"type": "rss", "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"},
    # Couche mots-clés — Google News remplace NewsAPI (gratuit, sans clé,
    # meilleure couverture francophone). La branche "newsapi" reste disponible
    # si NEWSAPI_KEY est renseignée.
    {"type": "gnews", "query": "finance marchés"},
    {"type": "gnews", "query": '"M&A" OR "fusion-acquisition"'},
    {"type": "gnews", "query": 'BCE OR "banque centrale" taux'},
]

# JobTeaser est écarté : 403 sur toute requête automatisée (voir stage_scraper).
# LinkedIn est exclu par ses CGU.
STAGE_SOURCES = [
    {
        "type": "wttj_sitemap",
        "name": "Welcome to the Jungle",
        "sitemap_index": "https://www.welcometothejungle.com/sitemaps/index.xml.gz",
    },
]
