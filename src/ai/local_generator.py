"""Génération du brouillon via le CLI Claude Code déjà installé.

Alternative sans clé API : le binaire `claude` répond en mode non interactif
(`-p`) avec les identifiants de l'abonnement Claude Code. C'est le même prompt
et le même nettoyage que `draft_generator`, seul le transport change.

Limite à connaître : ce moteur exige une session Claude Code authentifiée sur
la machine. Il ne peut donc pas tourner dans le cron GitHub Actions, seulement
en local (à la main, ou via un déclencheur local type launchd).
"""
import glob
import json
import logging
import os
import re
import shutil
import subprocess

from src.ai.draft_generator import (
    SYSTEM_PROMPT,
    DraftGenerationError,
    _build_user_prompt,
)
from src.sanitize import remove_dashes, sanitize_html

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 900

# `claude -p` est un agent, pas un moteur de complétion : laissé à lui-même il
# se sert de ses outils. Observé en production — au lieu de répondre, il a
# écrit le brouillon dans un fichier et renvoyé le chemin. On le ramène donc à
# une génération de texte : notre charte remplace son prompt système, et les
# outils d'écriture et d'exécution lui sont retirés.
BLOCKED_TOOLS = (
    "Bash", "Write", "Edit", "Read", "Glob", "Grep", "NotebookEdit",
    "WebFetch", "WebSearch", "Task", "TodoWrite",
)

_OUTPUT_RULE = (
    "\n\nRéponds UNIQUEMENT par l'objet JSON demandé, directement dans ta "
    "réponse. N'écris aucun fichier, n'utilise aucun outil, n'ajoute aucun "
    "commentaire avant ou après le JSON."
)

# Le CLI est parfois absent du PATH : l'extension VS Code embarque son propre
# binaire, dans un dossier versionné qui change à chaque mise à jour.
_BUNDLED_GLOB = os.path.expanduser(
    "~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def find_cli() -> str | None:
    """Chemin du binaire `claude`, ou None s'il est introuvable."""
    on_path = shutil.which("claude")
    if on_path:
        return on_path

    bundled = sorted(glob.glob(_BUNDLED_GLOB), key=_version_key)
    return bundled[-1] if bundled else None


def generate_draft_locally(news_items: list[dict], stage_items: list[dict]) -> dict:
    """Même contrat que `generate_draft`, sans clé API.

    Retourne {"news_html": str, "stages_html": str}.
    Lève DraftGenerationError si le CLI est absent, échoue, ou répond autre
    chose qu'un JSON exploitable.
    """
    cli = find_cli()
    if not cli:
        raise DraftGenerationError(
            "CLI Claude Code introuvable : ni dans le PATH, ni dans les "
            "extensions VS Code. Installe-le, ou utilise --generator api."
        )

    prompt = _build_user_prompt(news_items, stage_items)
    logger.info("Génération via le CLI local (%s), %d caractères.", cli, len(prompt))

    command = [
        cli, "-p",
        "--system-prompt", SYSTEM_PROMPT + _OUTPUT_RULE,
        "--disallowed-tools", ",".join(BLOCKED_TOOLS),
    ]

    try:
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise DraftGenerationError(
            f"Le CLI n'a pas répondu en {TIMEOUT_SECONDS} s."
        ) from error
    except OSError as error:
        raise DraftGenerationError(f"CLI inexécutable : {error}") from error

    if completed.returncode != 0:
        raise DraftGenerationError(
            f"Le CLI a échoué (code {completed.returncode}) : "
            f"{(completed.stderr or '').strip()[:400]}"
        )

    return parse_cli_output(completed.stdout)


def parse_cli_output(raw: str) -> dict:
    """Isole et nettoie le JSON, que la réponse soit nue ou bavarde."""
    fenced = _FENCE_RE.search(raw or "")
    candidate = fenced.group(1) if fenced else (raw or "")

    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise DraftGenerationError(
            f"Aucun objet JSON dans la réponse du CLI : {(raw or '')[:300]!r}"
        )

    try:
        draft = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as error:
        raise DraftGenerationError(f"JSON invalide renvoyé par le CLI : {error}") from error

    missing = {"news_html", "stages_html"} - draft.keys()
    if missing:
        raise DraftGenerationError(
            f"Clés absentes de la réponse : {', '.join(sorted(missing))}"
        )

    return {
        "news_html": sanitize_html(remove_dashes(draft["news_html"])),
        "stages_html": sanitize_html(remove_dashes(draft["stages_html"])),
    }


def _version_key(path: str) -> tuple:
    """Trie les chemins d'extension par version, pour prendre la plus récente."""
    match = re.search(r"claude-code-(\d+(?:\.\d+)*)", path)
    return tuple(int(n) for n in match.group(1).split(".")) if match else (0,)
