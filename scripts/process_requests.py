"""Exécute les demandes de réécriture déposées par l'interface hébergée.

La rédaction s'appuie sur le CLI Claude Code, qui vit sur le poste du
responsable : l'hébergement ne peut pas l'exécuter. Le bureau dépose donc une
demande depuis son navigateur, et ce script la ramasse ici.

Il est conçu pour être appelé souvent, par le planificateur, et se termine
aussitôt s'il n'y a rien à faire. Une seule demande est traitée par passage :
une réécriture prend plusieurs minutes, et rien ne presse.
"""

import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from src.db.models import (
    RegenerationRequest,
    SessionLocal,
    init_db,
    utcnow,
)

logger = logging.getLogger("demandes")
PROJET = Path(__file__).resolve().parent.parent
DELAI_MAXIMUM_SECONDES = 15 * 60

# Le planificateur peut se déclencher pile au réveil de la machine, avant que
# le Wi-Fi n'ait fini de se reconnecter : sans nouvelle tentative, ce seul
# instant suffit à faire échouer la connexion et bloque la demande jusqu'au
# passage suivant, une heure plus tard.
TENTATIVES_CONNEXION = 4
DELAI_ENTRE_TENTATIVES_SECONDES = 15


def _connecter_avec_reprise():
    derniere_erreur = None
    for tentative in range(1, TENTATIVES_CONNEXION + 1):
        try:
            init_db()
            db = SessionLocal()
            db.execute(text("SELECT 1"))
            return db
        except OperationalError as erreur:
            derniere_erreur = erreur
            if tentative < TENTATIVES_CONNEXION:
                logger.warning(
                    "Connexion à la base impossible (tentative %d/%d) : %s",
                    tentative, TENTATIVES_CONNEXION, erreur,
                )
                time.sleep(DELAI_ENTRE_TENTATIVES_SECONDES)
    raise derniere_erreur


def traiter_une_demande() -> int:
    try:
        db = _connecter_avec_reprise()
    except OperationalError as erreur:
        logger.error(
            "Base injoignable après %d tentatives, abandon pour ce passage : %s",
            TENTATIVES_CONNEXION, erreur,
        )
        return 1
    try:
        demande = (
            db.query(RegenerationRequest)
            .filter(RegenerationRequest.status == "en_attente")
            .order_by(RegenerationRequest.requested_at)
            .first()
        )
        if demande is None:
            return 0

        logger.info("Demande %s, déposée par %s", demande.id, demande.requested_by)
        # Marquée avant de commencer : si la machine s'éteint en cours de
        # route, la demande ne sera pas reprise en boucle au réveil.
        demande.status = "en_cours"
        db.commit()

        resultat = subprocess.run(
            [sys.executable, "scripts/run_weekly.py",
             "--generator", "local", "--remplacer"],
            cwd=PROJET, capture_output=True, text=True,
            timeout=DELAI_MAXIMUM_SECONDES,
        )
        reussi = resultat.returncode == 0
        demande.status = "faite" if reussi else "echouee"
        demande.detail = None if reussi else (resultat.stderr or "")[-400:]
        demande.handled_at = utcnow()
        db.commit()

        logger.info("Demande %s : %s", demande.id, demande.status)
        return 0 if reussi else 1
    except subprocess.TimeoutExpired:
        demande.status = "echouee"
        demande.detail = f"Aucune réponse après {DELAI_MAXIMUM_SECONDES // 60} minutes."
        demande.handled_at = utcnow()
        db.commit()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    sys.exit(traiter_une_demande())
