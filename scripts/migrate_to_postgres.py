"""Recopie la base SQLite locale vers Postgres, table par table.

L'hébergement gratuit n'a pas de disque durable : SQLite y perdrait tout à
chaque redémarrage. La bascule se fait donc une fois, et ce script existe pour
qu'elle soit vérifiable — il compte les lignes des deux côtés et refuse de se
déclarer réussi si les totaux diffèrent.

Il ne détruit rien : la base SQLite reste en place, et la cible doit être vide,
sauf à passer --ecraser.
"""

import argparse
import logging
import sys
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.models import Base, _url_normalisee

logger = logging.getLogger("migration")


def _compte(moteur, table) -> int:
    with moteur.connect() as c:
        return c.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()


def migrer(url_source: str, url_cible: str, ecraser: bool) -> int:
    source = sa.create_engine(_url_normalisee(url_source))
    cible = sa.create_engine(_url_normalisee(url_cible), pool_pre_ping=True)

    Base.metadata.create_all(cible)

    tables = Base.metadata.sorted_tables
    if not ecraser:
        deja = {t.name: _compte(cible, t) for t in tables if _compte(cible, t)}
        if deja:
            logger.error("La cible n'est pas vide : %s", deja)
            logger.error("Relance avec --ecraser si c'est voulu.")
            return 1

    if ecraser:
        # Ordre inverse : les tables filles avant les tables mères.
        with cible.begin() as c:
            for table in reversed(tables):
                c.execute(sa.delete(table))

    ecarts = []
    for table in tables:
        with source.connect() as c:
            lignes = [dict(r) for r in c.execute(sa.select(table)).mappings()]
        if lignes:
            with cible.begin() as c:
                c.execute(sa.insert(table), lignes)
        # Postgres ignore les identifiants insérés à la main : sans ça, la
        # prochaine insertion réutiliserait l'identifiant 1, déjà pris.
        if cible.dialect.name == "postgresql" and lignes:
            colonne = next((c for c in table.columns if c.primary_key), None)
            if colonne is not None and colonne.autoincrement:
                with cible.begin() as c:
                    c.execute(sa.text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', "
                        f"'{colonne.name}'), COALESCE((SELECT MAX({colonne.name}) "
                        f"FROM {table.name}), 1))"
                    ))
        avant, apres = len(lignes), _compte(cible, table)
        marque = "OK " if avant == apres else "ECART"
        logger.info("%-18s %5d -> %5d  %s", table.name, avant, apres, marque)
        if avant != apres:
            ecarts.append(table.name)

    if ecarts:
        logger.error("Tables incomplètes : %s", ", ".join(ecarts))
        return 1
    logger.info("Migration vérifiée : tous les totaux concordent.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="sqlite:///./spf.db")
    parser.add_argument("--cible", required=True, help="chaîne de connexion Postgres")
    parser.add_argument("--ecraser", action="store_true",
                        help="vide la cible avant de copier")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return migrer(args.source, args.cible, args.ecraser)


if __name__ == "__main__":
    sys.exit(main())
