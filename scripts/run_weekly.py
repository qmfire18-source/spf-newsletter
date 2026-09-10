"""Script exécuté par le cron hebdomadaire — voir PLAN.md §6.

Génère UNIQUEMENT le brouillon et le met en attente de validation.
Ne déclenche jamais l'envoi automatiquement.
"""
from datetime import date, timedelta

from src.config import NEWS_SOURCES, STAGE_SOURCES
from src.scraper.news_scraper import fetch_news
from src.scraper.stage_scraper import run_fetch_stage_offers
from src.ai.draft_generator import generate_draft
from src.db.models import SessionLocal, Draft, init_db


def main():
    init_db()
    db = SessionLocal()

    today = date.today()
    week_of = today - timedelta(days=today.weekday())

    existing = db.query(Draft).filter(Draft.week_of == week_of).first()
    if existing:
        print(f"Un brouillon existe déjà pour la semaine du {week_of}, on arrête.")
        return

    news = fetch_news(NEWS_SOURCES)
    stages = run_fetch_stage_offers(STAGE_SOURCES)
    generated = generate_draft(news, stages)

    draft = Draft(
        week_of=week_of,
        news_content=generated["news_html"],
        stages_content=generated["stages_html"],
        status="pending_review",
    )
    db.add(draft)
    db.commit()
    print(f"Brouillon créé pour la semaine du {week_of}, en attente de validation.")


if __name__ == "__main__":
    main()
