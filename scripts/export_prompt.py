"""Écrit le prompt de la semaine dans un fichier, à coller dans Claude.ai.

Alternative gratuite à l'appel API : la génération est hebdomadaire et de
toute façon relue par un humain. On produit ici exactement ce que
`generate_draft` enverrait, à coller dans n'importe quelle conversation
Claude ; la réponse JSON se réinjecte avec `scripts/import_draft.py`.

    python scripts/export_prompt.py
    # coller le contenu de brouillon_prompt.txt dans Claude.ai
    # enregistrer sa réponse dans reponse.json
    python scripts/import_draft.py reponse.json
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.draft_generator import SYSTEM_PROMPT, _build_user_prompt
from src.config import NEWS_SOURCES, STAGE_SOURCES
from src.scraper.article_fetcher import enrich_with_article_text
from src.scraper.news_scraper import fetch_news
from src.scraper.stage_scraper import run_fetch_stage_offers
from scripts.run_weekly import DEVELOPED_ITEMS

OUTPUT = Path("brouillon_prompt.txt")

CONSIGNE = """\
=============================================================================
À COLLER TEL QUEL DANS UNE CONVERSATION CLAUDE.AI
La réponse attendue est un JSON unique, sans texte autour :
  {"news_html": "...", "stages_html": "..."}
Enregistre cette réponse dans un fichier, puis lance :
  python scripts/import_draft.py <ce fichier>
=============================================================================

"""


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    news = fetch_news(NEWS_SOURCES)
    enrich_with_article_text(news, limit=DEVELOPED_ITEMS)
    stages = run_fetch_stage_offers(STAGE_SOURCES)

    developpables = sum(1 for item in news if item.get("full_text"))
    OUTPUT.write_text(
        CONSIGNE + SYSTEM_PROMPT + "\n\n" + _build_user_prompt(news, stages),
        encoding="utf-8",
    )

    print(
        f"\n{OUTPUT} écrit — {len(news)} actus dont {developpables} développables, "
        f"{len(stages)} offres.\n"
        f"Colle son contenu dans Claude.ai, enregistre la réponse, puis :\n"
        f"  python scripts/import_draft.py <fichier de réponse>\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
