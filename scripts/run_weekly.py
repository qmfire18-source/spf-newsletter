"""Script exécuté par le cron hebdomadaire — voir PLAN.md §6.

Génère UNIQUEMENT le brouillon et le met en attente de validation.
Ne déclenche jamais l'envoi : celui-ci part de l'interface web, sur action
humaine. Ce module n'importe volontairement rien de src.email.
"""
import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Exécuté directement (`python scripts/run_weekly.py`), le dossier du script
# est sur sys.path mais pas la racine du projet : sans ça, `src` est introuvable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.exc import IntegrityError

from src.ai.draft_generator import generate_draft
from src.ai.local_generator import find_cli, generate_draft_locally
from src.config import ANTHROPIC_API_KEY, NEWS_SOURCES, STAGE_SOURCES
from src.db import offer_store
from src.db.models import Draft, NewsItem, SessionLocal, StageOffer, init_db
from src.scraper.article_fetcher import enrich_with_article_text
from src.scraper.news_scraper import fetch_news
from src.scraper.stage_scraper import run_fetch_stage_offers

logger = logging.getLogger("run_weekly")

# On lit plus d'articles qu'on n'en publiera : le modèle choisit ensuite les
# cinq meilleurs. Lui en donner exactement cinq ne lui laissait aucun
# arbitrage — il développait ce qu'on lui tendait, important ou non.
READ_FOR_SELECTION = 9

# Offres puisées dans le stock accumulé par scripts/collect_stages.py.
STAGE_POOL_DAYS = 7
MAX_STAGES_PER_EDITION = 40


def choose_generator(mode: str):
    """Retourne la fonction de génération, et le nom du moteur retenu.

    `auto` privilégie l'API si une clé est configurée — c'est le seul moteur
    utilisable sans intervention humaine, donc le seul qui convienne au cron.
    À défaut, le CLI Claude Code local prend le relais, sans clé ni frais.
    """
    if mode == "api":
        return generate_draft, "API Anthropic"
    if mode == "local":
        return generate_draft_locally, "CLI Claude Code local"

    if ANTHROPIC_API_KEY:
        return generate_draft, "API Anthropic"
    if find_cli():
        logger.info("Pas de clé API : génération via le CLI Claude Code local.")
        return generate_draft_locally, "CLI Claude Code local"
    return generate_draft, "API Anthropic"


def current_week_of(today: date | None = None) -> date:
    """Lundi de la semaine en cours."""
    today = today or date.today()
    return today - timedelta(days=today.weekday())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remplacer",
        action="store_true",
        help="régénère l'édition de la semaine même si un brouillon existe ; "
             "une édition déjà envoyée n'est jamais touchée",
    )
    parser.add_argument(
        "--generator",
        choices=("auto", "api", "local"),
        default="auto",
        help="moteur de rédaction : API Anthropic, CLI Claude Code local, "
             "ou choix automatique selon les identifiants disponibles",
    )
    # Appelé sans arguments (tests, import), on ne lit PAS sys.argv : il
    # contient ceux de l'appelant. Le point d'entrée les passe explicitement.
    args = parser.parse_args(argv if argv is not None else [])

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    generate, engine = choose_generator(args.generator)
    logger.info("Moteur de rédaction : %s", engine)
    init_db()
    week_of = current_week_of()
    db = SessionLocal()

    try:
        existant = db.query(Draft).filter(Draft.week_of == week_of).first()
        if existant and args.remplacer:
            # Une édition partie ne se réécrit pas : les abonnés l'ont reçue,
            # et l'historique doit continuer de dire ce qui leur a été envoyé.
            if existant.status == "sent":
                logger.error(
                    "L'édition de la semaine du %s est déjà envoyée : "
                    "elle ne peut pas être régénérée.",
                    week_of,
                )
                return 1
            logger.info("Remplacement du brouillon de la semaine du %s.", week_of)
            db.delete(existant)
            db.commit()
        elif existant:
            logger.info(
                "Un brouillon existe déjà pour la semaine du %s : rien à faire.",
                week_of,
            )
            return 0

        # Rien de ce qui est déjà paru ne ressort : une newsletter qui répète
        # l'édition précédente ne vaut pas la peine d'être ouverte.
        deja_parues = offer_store.already_published_urls(db, before_week=week_of)

        news = [
            item for item in fetch_news(NEWS_SOURCES)
            if item.get("url") not in deja_parues
        ]
        # Sans le texte des articles, l'IA ne peut produire qu'une liste de
        # liens : les flux ne livrent qu'un résumé de 86 caractères en médiane.
        enrich_with_article_text(news, limit=READ_FOR_SELECTION)
        # Le stock accumulé jour après jour contient bien plus que ce qu'une
        # visite unique peut ramener : WTTJ nous coupe après quelques pages.
        stages = offer_store.recent_offers(
            db, days=STAGE_POOL_DAYS, limit=MAX_STAGES_PER_EDITION,
            exclude_urls=deja_parues,
        )
        if not stages:
            logger.info("Stock d'offres vide : collecte immédiate.")
            fresh = run_fetch_stage_offers(
                STAGE_SOURCES, known_urls=offer_store.known_urls(db)
            )
            offer_store.store_offers(db, fresh)
            stages = offer_store.recent_offers(
                db, days=STAGE_POOL_DAYS, limit=MAX_STAGES_PER_EDITION,
                exclude_urls=deja_parues,
            )
        logger.info(
            "%d actus inédites (%d développables) et %d offres inédites.",
            len(news),
            sum(1 for item in news if item.get("full_text")),
            len(stages),
        )

        if not news and not stages:
            logger.error("Aucune source n'a répondu : pas de brouillon généré.")
            return 1

        generated = generate(news, stages)

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
        duration=offer.get("duration"),
        start_label=offer.get("start_label"),
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
    sys.exit(main(sys.argv[1:]))
