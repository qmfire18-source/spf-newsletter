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
            duration=offer.get("duration"),
            start_label=offer.get("start_label"),
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


def recent_offers(
    db, days: int = 7, limit: int = 40, exclude_urls: set[str] | None = None
) -> list[dict]:
    """Offres collectées récemment, les plus fraîches d'abord.

    Deux écartées d'office : celle dont la date limite est passée — la publier
    enverrait les lecteurs vers une candidature close — et celle déjà parue
    dans une édition précédente, via `exclude_urls`.
    """
    cutoff = utcnow() - timedelta(days=days)
    today = utcnow().date()
    exclude_urls = exclude_urls or set()

    rows = (
        db.query(CollectedOffer)
        .filter(CollectedOffer.collected_at >= cutoff)
        .order_by(CollectedOffer.collected_at.desc())
        .all()
    )
    retenues = [
        {
            "title": row.title,
            "company": row.company,
            "location": row.location,
            "deadline": row.deadline.isoformat() if row.deadline else None,
            "duration": row.duration,
            "start_label": row.start_label,
            "url": row.url,
        }
        for row in rows
        if (row.deadline is None or row.deadline >= today)
        and row.url not in exclude_urls
    ]
    return _spread_across_employers(retenues)[:limit]


def _spread_across_employers(offers: list[dict]) -> list[dict]:
    """Alterne les employeurs, du plus récemment collecté au plus ancien.

    Un employeur prolixe fausse l'édition : Lazard publiait à lui seul 53 des
    74 offres du stock, et aurait pris presque toutes les places. On tourne
    entre employeurs pour que la newsletter montre le marché, pas un seul
    recruteur.
    """
    par_employeur: dict[str, list[dict]] = {}
    for offer in offers:
        par_employeur.setdefault(offer.get("company") or "?", []).append(offer)

    files = list(par_employeur.values())
    ordonnees = []
    while files:
        files = [f for f in files if f]
        for file in files:
            ordonnees.append(file.pop(0))
    return ordonnees


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


def already_published_urls(db, before_week=None) -> set[str]:
    """URL parues dans les éditions ANTÉRIEURES — actualités et offres.

    Une newsletter hebdomadaire qui reproduit la précédente n'a aucun intérêt :
    ce qui est déjà sorti ne ressort pas.

    `before_week` exclut du calcul l'édition en cours de rédaction. Sans ce
    garde-fou, régénérer le brouillon de la semaine ferait considérer son
    propre contenu comme déjà paru, et produirait une édition vide.
    """
    from src.db.models import Draft, NewsItem, StageOffer

    def urls(model):
        query = db.query(model.url).join(Draft, model.draft_id == Draft.id)
        if before_week is not None:
            query = query.filter(Draft.week_of < before_week)
        return [url for (url,) in query.all() if url]

    return set(urls(StageOffer)) | set(urls(NewsItem))
