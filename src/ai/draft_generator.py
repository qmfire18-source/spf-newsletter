"""Génération du brouillon de newsletter via l'API Claude — voir PLAN.md §3."""
import json
import logging

import anthropic

from src.config import ANTHROPIC_API_KEY
from src.scraper.sectors import group_by_sector
from src.sanitize import remove_dashes, sanitize_html

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

1. Une phrase d'accroche qui situe la semaine, en <p>. Pas de « Bonjour à
   tous » générique : dire ce qui a dominé la semaine.

2. Un sommaire, court : <p>Au sommaire</p> suivi d'un <ul> dont chaque puce
   est UNIQUEMENT le titre de l'actualité, raccourci à cinq ou six mots, et
   rien d'autre. Pas de résumé, pas de phrase, pas de ponctuation finale.
   Chaque puce est un lien vers l'article : <li><a href="#a1">Titre</a></li>,
   puis #a2, #a3, dans l'ordre où les articles apparaissent.

3. Les actualités développées, GROUPÉES PAR RUBRIQUE. Les rubriques sont
   fixes et sortent TOUJOURS dans cet ordre, en n'écrivant que celles qui ont
   au moins une actualité :

     MARCHÉS              taux, obligations, actions, matières premières
     MACRO                banques centrales, croissance, dette, inflation
     ENTREPRISES ET DEALS M&A, introductions en bourse, levées, résultats
     RÉGULATION           autorités, normes, fiscalité, ESG contraignant
     MÉTIER ET CARRIÈRE   recrutement, rémunérations, évolution des métiers

   Chaque rubrique s'ouvre par un <h3> contenant SON NOM SEUL, en capitales.
   Chaque actualité à l'intérieur s'écrit ensuite :
   - un <h4> portant l'identifiant correspondant à sa puce du sommaire,
     <h4 id="a1">, avec un titre d'accroche qui donne envie : une question,
     une tension, un chiffre frappant. Pas un titre d'agence de presse.
     Les identifiants se suivent dans l'ordre : a1, a2, a3.
   - deux à quatre <p> qui déroulent : de quoi il s'agit, les chiffres
     concrets, puis POURQUOI ça compte pour un étudiant en finance,
     mécanisme économique, conséquence sur un métier ou sur un secteur.
   - le lien vers l'article source, intégré au texte ou en fin d'item.

   COUVRE AU MOINS TROIS RUBRIQUES DIFFÉRENTES. Une édition entièrement
   macro n'apprend rien sur le reste du marché. Si la semaine est pauvre
   dans une rubrique, écris moins d'articles plutôt que d'en entasser cinq
   dans la même.

   N'écris un item développé QUE pour les actualités dont le champ
   `full_text` est fourni : lui seul contient la matière. Sans lui, tu
   n'aurais que le titre, et tu inventerais.

   Tu en reçois PLUS que nécessaire : choisis les cinq qui comptent le plus
   pour un étudiant en finance. Chaque actualité porte un champ `reprises`,
   le nombre de rédactions qui couvrent le sujet, et `poids_source`, la
   qualité financière du titre qui le traite. Un sujet repris par dix
   rédactions est l'événement de la semaine ; un sujet unique peut valoir
   mieux s'il touche directement un métier de la finance. Ces chiffres
   éclairent ton choix, ils ne le dictent pas. Les articles non retenus
   passent en brève.

4. Une section <h3>LES DEALS DE LA SEMAINE</h3> suivie d'un <ul> de cinq
   entrées AU PLUS, classées de la plus importante à la moins importante.
   Un deal est une opération : fusion, acquisition, cession, introduction
   en bourse, levée de fonds, émission obligataire majeure.
   - Une seule ligne par deal, courte : qui achète quoi, pour combien, et
     le lien. Pas de commentaire, pas d'analyse — c'est une liste, pas un
     article.
   - Classe par importance : le montant et la notoriété des parties
     priment. Une opération à dix milliards passe devant une levée de
     fonds à vingt millions.
   - N'y mets QUE des opérations réellement présentes dans les données
     fournies. Aucun montant qui n'y figure pas. Si tu n'en trouves
     aucune, omets entièrement la section plutôt que de la remplir.
   - Une actualité placée ici ne doit pas être reprise en développé ni en
     bref : chaque sujet n'apparaît qu'une fois.

5. Une section <h3>EN BREF</h3> suivie d'un <ul> : trois à six actualités
   non développées, une phrase chacune, avec leur lien. C'est là que vont
   les sujets sans `full_text`. Une phrase = ce que dit le titre, rien de
   plus, aucun chiffre qui n'y figure pas.

STRUCTURE DE stages_html :
- <h3>STAGES DE LA SEMAINE</h3>, puis une phrase d'introduction qui dit ce
  que la semaine offre — quel segment recrute, ce qui se distingue.
- Les offres sont fournies DÉJÀ REGROUPÉES PAR SECTEUR. Respecte ce
  découpage et cet ordre : un <h4> par secteur, puis un <ul> avec une entrée
  par offre — intitulé, entreprise, lieu, puis, DANS CET ORDRE et seulement
  si les champs correspondants sont fournis : la durée (`duration`), la date
  de début (`start_label`), la date limite de candidature (`deadline`), et
  le lien. Ces trois champs sont souvent absents : ne les devine jamais, et
  n'écris pas « durée non précisée » — omets simplement la mention.
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

PONCTUATION
N'utilise jamais le tiret cadratin (—) ni le demi-cadratin (–). C'est la
ponctuation signature des textes générés, et elle se repère immédiatement.
Le français a tout ce qu'il faut : la virgule, les deux-points, la
parenthèse, ou deux phrases séparées par un point. Le trait d'union (-) des
mots composés reste normal.

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
        "news_html": sanitize_html(remove_dashes(draft["news_html"])),
        "stages_html": sanitize_html(remove_dashes(draft["stages_html"])),
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
        f"ACTUALITÉS DÉVELOPPABLES ({len(developpables)}) : texte intégral "
        "fourni. Ce sont les seules dont tu peux faire un item développé, et "
        "il y en a plus que les cinq à publier. Choisis, en t'aidant de "
        "`reprises` (combien de rédactions couvrent le sujet) et de "
        "`poids_source` (qualité financière du titre). Le reste passe en "
        "brève.\n"
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
