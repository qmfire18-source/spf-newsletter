"""Génération du brouillon de newsletter via l'API Claude — voir PLAN.md §3."""
import json
import logging

import anthropic

from src.config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5"
MAX_TOKENS = 16000
MAX_ATTEMPTS = 2

SYSTEM_PROMPT = """Tu es le rédacteur de la newsletter hebdomadaire de
l'association Sciences Po Finance.

Ton : professionnel, concis, accessible à des étudiants qui découvrent la
finance. Pas de jargon non expliqué, pas d'emphase commerciale.

Section actualités (news_html) :
- Sélectionne les 3 à 5 actualités les plus pertinentes pour des étudiants en
  finance parmi celles fournies. Ignore le reste, y compris le hors-sujet.
- 1 à 2 phrases par actualité, et un lien vers l'article source.
- Privilégie marchés, banques centrales, M&A, régulation et grandes
  manœuvres d'entreprises.

Section stages (stages_html) :
- Titre "Stages de la semaine", puis une entrée par offre avec intitulé,
  entreprise, lieu, deadline si connue, et lien de candidature.
- Si aucune offre n'est fournie, produis un court paragraphe indiquant qu'il
  n'y a pas de nouvelle offre cette semaine.

Contraintes de fond :
- N'invente jamais un fait, un chiffre, une date ou un lien : utilise
  uniquement ce qui figure dans les données fournies.
- Si une information manque (deadline par exemple), ne la mentionne pas
  plutôt que de la supposer.

Contraintes de forme : HTML simple compatible email, limité aux balises h3,
p, ul, li, a, strong et em. Pas de script, style, html, head ni body, pas de
CSS externe, pas de commentaire HTML."""

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "news_html": {
            "type": "string",
            "description": "Fragment HTML de la section actualités.",
        },
        "stages_html": {
            "type": "string",
            "description": "Fragment HTML de la section stages.",
        },
    },
    "required": ["news_html", "stages_html"],
    "additionalProperties": False,
}

_client = None


class DraftGenerationError(Exception):
    pass


def generate_draft(news_items: list[dict], stage_items: list[dict]) -> dict:
    """
    Construit le prompt, appelle l'API Claude, parse la réponse JSON.
    Retourne {"news_html": str, "stages_html": str}.
    Lève DraftGenerationError si le parsing échoue après un retry.
    """
    user_content = _build_user_prompt(news_items, stage_items)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = _get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            output_config={
                "format": {"type": "json_schema", "schema": DRAFT_SCHEMA}
            },
            messages=[{"role": "user", "content": user_content}],
        )

        # Un refus ou une troncature ne se règlent pas en réessayant à
        # l'identique : on remonte l'erreur tout de suite.
        if response.stop_reason == "refusal":
            raise DraftGenerationError(
                f"Génération refusée par le modèle : {response.stop_details}"
            )
        if response.stop_reason == "max_tokens":
            raise DraftGenerationError(
                f"Réponse tronquée à {MAX_TOKENS} tokens : réduisez le nombre "
                "d'actus en entrée ou augmentez MAX_TOKENS."
            )

        try:
            return _parse_response(response)
        except (json.JSONDecodeError, KeyError, StopIteration) as error:
            logger.warning(
                "Réponse IA non exploitable (tentative %d/%d) : %s",
                attempt, MAX_ATTEMPTS, error,
            )
            last_error = error

    raise DraftGenerationError(
        f"Réponse IA non parsable après {MAX_ATTEMPTS} tentatives : {last_error}"
    )


def _parse_response(response) -> dict:
    text = next(block.text for block in response.content if block.type == "text")
    draft = json.loads(text)
    return {
        "news_html": draft["news_html"],
        "stages_html": draft["stages_html"],
    }


def _build_user_prompt(news_items: list[dict], stage_items: list[dict]) -> str:
    return (
        "Actualités brutes :\n"
        f"{json.dumps(news_items, ensure_ascii=False, indent=2)}\n\n"
        "Offres de stage brutes :\n"
        f"{json.dumps(stage_items, ensure_ascii=False, indent=2)}"
    )


def _get_client() -> anthropic.Anthropic:
    # Client construit à la demande : sans ça, importer ce module sans clé API
    # configurée ferait échouer les tests et l'interface web.
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client
