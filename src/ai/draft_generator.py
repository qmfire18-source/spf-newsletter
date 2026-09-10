"""Génération du brouillon de newsletter via l'API Claude — voir PLAN.md §3."""
import json
import anthropic
from src.config import ANTHROPIC_API_KEY

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Tu es le rédacteur de la newsletter hebdomadaire de
l'association Sciences Po Finance.
Ton : professionnel, concis, accessible à des étudiants.
Structure attendue : 3-5 actus financières max (1-2 phrases chacune),
puis une section "Stages de la semaine" avec titre/entreprise/deadline/lien
pour chaque offre.
Réponds UNIQUEMENT en JSON valide, sans texte autour, au format :
{"news_html": "...", "stages_html": "..."}
"""


class DraftGenerationError(Exception):
    pass


def generate_draft(news_items: list[dict], stage_items: list[dict]) -> dict:
    """
    Construit le prompt, appelle l'API Claude, parse la réponse JSON.
    Retourne {"news_html": str, "stages_html": str}.
    Lève DraftGenerationError si le parsing échoue après un retry.
    """
    user_content = _build_user_prompt(news_items, stage_items)

    for attempt in range(2):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        raw_text = response.content[0].text
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            if attempt == 0:
                continue
            raise DraftGenerationError(f"Réponse IA non parsable : {raw_text[:200]}")


def _build_user_prompt(news_items: list[dict], stage_items: list[dict]) -> str:
    return (
        "Actualités brutes :\n"
        f"{json.dumps(news_items, ensure_ascii=False, indent=2)}\n\n"
        "Offres de stage brutes :\n"
        f"{json.dumps(stage_items, ensure_ascii=False, indent=2)}"
    )
