"""Enregistre en base un brouillon rédigé hors API — voir export_prompt.py.

Accepte la réponse brute de Claude.ai : le JSON peut être entouré de texte ou
d'un bloc de code Markdown, on isole l'objet. Le HTML passe par le même
nettoyage que la génération automatique.
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.exc import IntegrityError

from src.db.models import Draft, SessionLocal, init_db
from src.sanitize import sanitize_html
from scripts.run_weekly import current_week_of

logger = logging.getLogger("import_draft")

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_draft(raw: str) -> dict:
    """Isole l'objet JSON dans une réponse qui peut être bavarde."""
    fenced = _FENCE_RE.search(raw)
    candidate = fenced.group(1) if fenced else raw

    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("aucun objet JSON trouvé dans la réponse")

    parsed = json.loads(candidate[start : end + 1])
    missing = {"news_html", "stages_html"} - parsed.keys()
    if missing:
        raise ValueError(f"clés absentes de la réponse : {', '.join(sorted(missing))}")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fichier", help="réponse de Claude.ai (JSON, éventuellement bavard)")
    parser.add_argument(
        "--remplacer",
        action="store_true",
        help="écraser le brouillon existant pour cette semaine",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        draft_data = extract_draft(Path(args.fichier).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        logger.error("Réponse inexploitable : %s", error)
        return 1

    init_db()
    db = SessionLocal()
    week_of = current_week_of()
    try:
        existing = db.query(Draft).filter(Draft.week_of == week_of).first()
        if existing and not args.remplacer:
            logger.error(
                "Un brouillon existe déjà pour la semaine du %s. "
                "Relance avec --remplacer pour l'écraser.",
                week_of,
            )
            return 1
        if existing:
            db.delete(existing)
            db.flush()

        draft = Draft(
            week_of=week_of,
            news_content=sanitize_html(draft_data["news_html"]),
            stages_content=sanitize_html(draft_data["stages_html"]),
            status="pending_review",
        )
        db.add(draft)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            logger.error("Brouillon déjà créé entre-temps pour la semaine du %s.", week_of)
            return 1

        print(
            f"\nBrouillon {draft.id} enregistré pour la semaine du {week_of}.\n"
            f"Ouvre l'interface de validation pour le relire.\n"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
