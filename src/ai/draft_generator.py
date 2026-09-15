"""Génération du brouillon de newsletter via l'API Claude — voir PLAN.md §3."""
import json
import logging

import anthropic

from src.config import ANTHROPIC_API_KEY
from src.scraper.sectors import group_by_sector
from src.sanitize import sanitize_html

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5"
MAX_TOKENS = 16000
MAX_ATTEMPTS = 2

SYSTEM_PROMPT = """Tu es le rédacteur de la newsletter hebdomadaire de
l'association Sciences Po Finance, lue par des étudiants qui s'intéressent à
la finance et visent des stages dans le secteur.

INTENTION
Ce n'est pas une revue de presse ni une liste de liens. Le lecteur doit
pouvoir ne lire QUE la newsletter et comprendre ce qui compte cette semaine,
sans ouvrir un seul article. Chaque actualité développée doit lui apprendre
quelque chose et lui donner une grille de lecture.

TON
Direct, vivant, un peu complice — on s'adresse à des étudiants, pas à des
gérants de fonds. Le "vous" pour le lecteur. Phrases courtes. Le jargon est
autorisé mais toujours expliqué à sa première apparition ("le spread, c'est
l'écart entre..."). Jamais de ton commercial ni de superlatif creux.

STRUCTURE DE news_html, dans cet ordre :

1. Une phrase d'accroche qui situe la semaine, en <p>. Pas de "Bonjour à
   tous" générique : dire ce qui a dominé la semaine.

2. Les actualités développées. Une seule par événement. Pour chacune :
   - un <h3> avec un titre d'accroche qui donne envie — une question, une
     tension, un chiffre frappant. Pas un titre d'agence de presse.
   - deux à quatre <p> qui déroulent : de quoi il s'agit, les chiffres
     concrets, puis POURQUOI ça compte pour un étudiant en finance —
     mécanisme économique, conséquence sur un métier, sur un secteur, sur
     le marché de l'emploi.
   - le lien vers l'article source, intégré dans le texte ou en fin d'item.
   N'écris un item développé QUE pour les actualités dont le champ
   `full_text` est fourni : lui seul contient la matière. Sans lui, tu
   n'aurais que le titre, et tu inventerais.

3. Une section <h3>En bref</h3> suivie d'un <ul> : trois à six actualités
   non développées, une phrase chacune, avec leur lien. C'est là que vont
   les sujets sans `full_text`. Une phrase = ce que dit le titre, rien de
   plus, aucun chiffre qui n'y figure pas.

STRUCTURE DE stages_html :
- <h3>Stages de la semaine</h3>, puis une phrase d'introduction qui dit ce
  que la semaine offre — quel segment recrute, ce qui se distingue.
- Les offres sont fournies DÉJÀ REGROUPÉES PAR SECTEUR. Respecte ce
  découpage et cet ordre : un <h4> par secteur, puis un <ul> avec une entrée
  par offre — intitulé, entreprise, lieu, date limite si connue, et lien.
  Ne réordonne pas, ne fusionne pas les secteurs, n'en invente aucun.
- Un secteur d'une seule offre reste un secteur à part entière.
- Si aucune offre n'est fournie, un court paragraphe le disant.

RÈGLE ABSOLUE SUR LES FAITS
N'invente jamais un chiffre, une date, un nom ou un lien. Tout ce que tu
écris doit provenir des données fournies. Un chiffre ne peut venir que du
`full_text` ou du titre de l'actualité concernée. Si une information manque,
écris sans elle — ne la déduis pas, ne l'arrondis pas, ne la complète pas de
mémoire. En cas de doute sur un fait, ne l'écris pas.

Tu peux en revanche interpréter et mettre en perspective : expliquer un
mécanisme, relier deux actualités, dire ce que ça implique. Ces passages
d'analyse doivent rester visiblement des analyses, pas des faits rapportés.

FORME
HTML simple compatible email, limité à h3, h4, p, ul, ol, li, a, strong, em
et br. Pas de script, style, html, head, body, pas de CSS externe, pas de
commentaire HTML, pas d'emoji dans les titres de section."""

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
        "news_html": sanitize_html(draft["news_html"]),
        "stages_html": sanitize_html(draft["stages_html"]),
    }


def _build_user_prompt(news_items: list[dict], stage_items: list[dict]) -> str:
    """Sépare explicitement ce qui est développable de ce qui ne l'est pas.

    Seules les actualités enrichies par `article_fetcher` portent un
    `full_text`. Les mélanger reviendrait à demander au modèle de deviner
    lesquelles il peut développer — et il développerait les autres en
    inventant.
    """
    developpables = [item for item in news_items if item.get("full_text")]
    breves = [item for item in news_items if not item.get("full_text")]

    return (
        f"ACTUALITÉS DÉVELOPPABLES ({len(developpables)}) — texte intégral "
        "fourni, ce sont les seules dont tu peux faire un item développé :\n"
        f"{json.dumps(developpables, ensure_ascii=False, indent=2)}\n\n"
        f"ACTUALITÉS POUR LA SECTION « EN BREF » ({len(breves)}) — titre et "
        "lien seulement, une phrase chacune, aucun chiffre ajouté :\n"
        f"{json.dumps(breves, ensure_ascii=False, indent=2)}\n\n"
        "OFFRES DE STAGE, regroupées par secteur — garde ce découpage et "
        "cet ordre :\n"
        f"{json.dumps(group_by_sector(stage_items), ensure_ascii=False, indent=2)}"
    )


def _get_client() -> anthropic.Anthropic:
    # Client construit à la demande : sans ça, importer ce module sans clé API
    # configurée ferait échouer les tests et l'interface web.
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client
