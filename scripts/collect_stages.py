"""Collecte incrémentale des offres de stage — à lancer plusieurs fois par semaine.

WTTJ limite le scraper après quelques pages. Une seule visite hebdomadaire ne
ramènerait que six offres ; en collectant chaque jour, chaque exécution
rapportant du nouveau, la newsletter du lundi puise dans un stock bien plus
large. Ce script n'écrit aucun brouillon et n'envoie rien.

    python scripts/collect_stages.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import EMPLOYER_SOURCES, STAGE_SOURCES
from src.db import offer_store
from src.db.models import SessionLocal, init_db
from src.scraper.employer_scraper import fetch_employer_offers
from src.scraper.stage_scraper import run_fetch_stage_offers

logger = logging.getLogger("collect_stages")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    init_db()
    db = SessionLocal()
    try:
        deja = offer_store.known_urls(db)
        offers = run_fetch_stage_offers(STAGE_SOURCES, known_urls=deja)
        # Les employeurs absents de WTTJ sont interrogés chez eux. Leurs
        # portails ne nous limitent pas : on peut les lire à chaque passage.
        offers += fetch_employer_offers(EMPLOYER_SOURCES)
        added = offer_store.store_offers(db, offers)
        purged = offer_store.purge_old(db)
        total = len(offer_store.recent_offers(db, limit=10_000))

        logger.info(
            "%d offre(s) récoltée(s), %d inédite(s) ajoutée(s), %d purgée(s). "
            "Stock utilisable pour la newsletter : %d.",
            len(offers), added, purged, total,
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
