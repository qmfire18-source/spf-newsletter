"""Script exécuté par le cron hebdomadaire — voir PLAN.md §6.

Génère UNIQUEMENT le brouillon et le met en attente de validation.
Ne déclenche jamais l'envoi : celui-ci part de l'interface web, sur action
humaine. Ce module n'importe volontairement rien de src.email.
"""
import logging
import sys
from datetime import date, datetime, timedelta

from sqlalchemy.exc import IntegrityError

from src.ai.draft_generator import generate_draft
from src.config import NEWS_SOURCES, STAGE_SOURCES
from src.db.models import Draft, NewsItem, SessionLocal, StageOffer, init_db
from src.scraper.news_scraper import fetch_news
from src.scraper.stage_scraper import run_fetch_stage_offers

logger = logging.getLogger("run_weekly")


def current_week_of(today: date | None = None) -> date:
    """Lundi de la semaine en cours."""
    today = today or date.today()
    return today - timedelta(days=today.weekday())


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    init_db()
    week_of = current_week_of()
    db = SessionLocal()

    try:
        if db.query(Draft).filter(Draft.week_of == week_of).first():
            logger.info(
                "Un brouillon existe déjà pour la semaine du %s : rien à faire.",
                week_of,
            )
            return 0

        news = fetch_news(NEWS_SOURCES)
        stages = run_fetch_stage_offers(STAGE_SOURCES)
        logger.info("%d actus et %d offres récoltées.", len(news), len(stages))

        if not news and not stages:
            logger.error("Aucune source n'a répondu : pas de brouillon généré.")
            return 1

        generated = generate_draft(news, stages)

        draft = Draft(
            week_of=week_of,
            news_content=generated["news_html"],
            stages_content=generated["stages_html"],
            status="pending_review",
        )
        draft.news_items = [_to_news_item(item) for item in news]
        draft.stage_offers = [_to_stage_offer(offer) for offer in stages]
        db.add(draft)

        try:
            db.commit()
        except IntegrityError:
            # Une exécution concurrente a inséré le brouillon entre-temps :
            # c'est le résultat voulu, pas une erreur.
            db.rollback()
            logger.info(
                "Brouillon de la semaine du %s déjà créé par une autre exécution.",
                week_of,
            )
            return 0

        logger.info(
            "Brouillon %s créé pour la semaine du %s, en attente de validation.",
            draft.id, week_of,
        )
        return 0
    finally:
        db.close()


def _to_news_item(item: dict) -> NewsItem:
    return NewsItem(
        title=item.get("title"),
        source=item.get("source"),
        url=item.get("url"),
        raw_summary=item.get("raw_summary"),
    )


def _to_stage_offer(offer: dict) -> StageOffer:
    return StageOffer(
        title=offer.get("title"),
        company=offer.get("company"),
        location=offer.get("location"),
        deadline=_parse_date(offer.get("deadline")),
        url=offer.get("url"),
    )


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit(main())
