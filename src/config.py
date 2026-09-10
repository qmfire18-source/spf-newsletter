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
    e.strip() for e in os.getenv("ALLOWED_REVIEWER_EMAILS", "").split(",") if e
]

# Sources par défaut — à ajuster
NEWS_SOURCES = [
    {"type": "rss", "url": "https://www.lesechos.fr/rss/rss_finance_marches.xml"},
    {"type": "rss", "url": "https://www.boursorama.com/rss/actualites/"},
    # {"type": "newsapi", "query": "finance marchés M&A"},
]

STAGE_SOURCES = [
    {"name": "jobteaser", "url": "TODO: url de recherche filtrée"},
    {"name": "wttj", "url": "TODO: url de recherche filtrée"},
]
