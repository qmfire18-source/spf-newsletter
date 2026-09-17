"""Complète durée et date de début sur les offres déjà collectées.

Ces deux champs ont été ajoutés après coup : les offres entrées en stock
avant leur existence ne les portent pas. Les retrouver demande de revisiter
la page de chaque offre, ce que Welcome to the Jungle throttle — le script
avance donc lentement, encaisse les refus sans s'arrêter, et peut être relancé
autant de fois que nécessaire : il ne traite que ce qui reste à faire.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.models import (
    CollectedOffer,
    SessionLocal,
    init_db,
    utcnow,
)
from src.scraper.stage_scraper import (
    _decode,
    _find_job_posting,
    completer_depuis_intitule,
    details_depuis_page,
    extraire_duree_et_debut,
)

logger = logging.getLogger("backfill")

NAVIGATEUR = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)
# 202 est la réponse de throttling de WTTJ : elle n'annonce pas une erreur,
# seulement qu'il faut lever le pied.
THROTTLE = {202, 429, 503}


async def traiter(client, offre) -> str:
    try:
        reponse = await client.get(offre.url)
    except httpx.HTTPError as erreur:
        logger.warning("Injoignable (%s) : %s", erreur, offre.url[:70])
        return "erreur"

    if reponse.status_code in THROTTLE:
        return "throttle"
    if reponse.status_code != 200:
        return "erreur"

    # Au-delà d'ici, la page a été lue : l'offre ne sera pas revisitée, même
    # si elle ne dit rien de sa durée.
    offre.details_checked_at = utcnow()

    html = _decode(reponse.content)

    # Mêmes sources et même ordre que la collecte : l'état JSON de la page
    # d'abord, qui déclare durée et début en clair, puis le texte de l'annonce,
    # puis l'intitulé. Ce script ne lisait que le texte, et passait donc à côté
    # de la seule source vraiment fiable.
    duree, debut = details_depuis_page(html)
    if not (duree and debut):
        posting = _find_job_posting(html) or {}
        texte_duree, texte_debut = extraire_duree_et_debut(posting.get("description") or "")
        duree = duree or texte_duree
        debut = debut or texte_debut

    depuis_titre = completer_depuis_intitule(
        {"title": offre.title, "duration": duree, "start_label": debut}
    )
    duree = depuis_titre.get("duration")
    debut = depuis_titre.get("start_label")

    if not (duree or debut):
        return "rien_trouve"

    offre.duration = duree
    offre.start_label = debut
    logger.info("%s : durée=%s début=%s", (offre.company or "?")[:24], duree, debut)
    return "complete"


async def executer(limite: int, pause: float) -> dict:
    init_db()
    db = SessionLocal()
    comptes: dict[str, int] = {}
    try:
        restantes = (
            db.query(CollectedOffer)
            .filter(CollectedOffer.details_checked_at.is_(None))
            .limit(limite)
            .all()
        )
        logger.info("%d offres à revisiter", len(restantes))

        attente = pause
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=30, headers={"User-Agent": NAVIGATEUR}
        ) as client:
            for offre in restantes:
                issue = await traiter(client, offre)
                comptes[issue] = comptes.get(issue, 0) + 1
                if issue == "throttle":
                    # On ralentit au lieu d'abandonner : le stock se complète
                    # sur plusieurs passages, pas sur un seul.
                    attente = min(attente * 2, 60)
                    logger.warning("Throttlé, pause portée à %.0f s", attente)
                else:
                    attente = max(attente * 0.9, pause)
                db.commit()
                await asyncio.sleep(attente)
    finally:
        db.commit()
        db.close()
    return comptes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limite", type=int, default=40,
                        help="offres traitées au maximum sur ce passage")
    parser.add_argument("--pause", type=float, default=3.0,
                        help="secondes entre deux pages, avant ralentissement")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    comptes = asyncio.run(executer(args.limite, args.pause))
    for issue, nombre in sorted(comptes.items()):
        print(f"  {issue:14} {nombre}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
