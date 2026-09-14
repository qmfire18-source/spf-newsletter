"""Stock d'offres de stage accumulé au fil des collectes.

WTTJ coupe le scraper après quelques pages : une visite hebdomadaire ne
ramènerait qu'une poignée d'offres sur les ~190 du vivier. On collecte donc
plusieurs fois par semaine, chaque exécution ignorant ce qui est déjà stocké,
et la newsletter puise dans l'accumulation des sept derniers jours.
"""
import logging
from datetime import timedelta

from sqlalchemy.exc import IntegrityError

from src.db.models import CollectedOffer, utcnow

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30


def known_urls(db) -> set[str]:
    """URL déjà collectées : le scraper les écarte avant de dépenser un appel."""
    return {url for (url,) in db.query(CollectedOffer.url).all()}


def store_offers(db, offers: list[dict]) -> int:
    """Ajoute les offres inédites. Retourne le nombre réellement ajouté."""
    added = 0
    for offer in offers:
        url = offer.get("url")
        if not url:
            continue
        record = CollectedOffer(
            url=url,
            title=offer.get("title"),
            company=offer.get("company"),
            location=offer.get("location"),
            deadline=_as_date(offer.get("deadline")),
        )
        db.add(record)
        try:
            # Un commit par offre : l'unicité de l'URL est la seule protection
            # contre les doublons, et un lot entier ne doit pas tomber pour une
            # collision sur une seule.
            db.commit()
            added += 1
        except IntegrityError:
            db.rollback()
    return added


def recent_offers(db, days: int = 7, limit: int = 40) -> list[dict]:
    """Offres collectées récemment, les plus fraîches d'abord.

    Une offre dont la date limite est passée est écartée : la publier
    enverrait les lecteurs vers une candidature close.
    """
    cutoff = utcnow() - timedelta(days=days)
    today = utcnow().date()

    rows = (
        db.query(CollectedOffer)
        .filter(CollectedOffer.collected_at >= cutoff)
        .order_by(CollectedOffer.collected_at.desc())
        .all()
    )
    return [
        {
            "title": row.title,
            "company": row.company,
            "location": row.location,
            "deadline": row.deadline.isoformat() if row.deadline else None,
            "url": row.url,
        }
        for row in rows
        if row.deadline is None or row.deadline >= today
    ][:limit]


def purge_old(db, days: int = RETENTION_DAYS) -> int:
    """Supprime les offres trop anciennes pour resservir."""
    cutoff = utcnow() - timedelta(days=days)
    removed = (
        db.query(CollectedOffer)
        .filter(CollectedOffer.collected_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return removed


def _as_date(value):
    from datetime import date, datetime

    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None
