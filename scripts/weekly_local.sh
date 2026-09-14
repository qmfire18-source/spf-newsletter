#!/bin/bash
# Exécution hebdomadaire locale — appelé par le LaunchAgent, ou à la main.
#
# Génère le brouillon avec le CLI Claude Code (pas de clé API), journalise,
# et prévient par une notification macOS. N'envoie JAMAIS la newsletter :
# l'envoi reste un geste humain, depuis l'interface de validation.
set -uo pipefail

PROJET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOURNAL="$PROJET/logs/weekly.log"
mkdir -p "$(dirname "$JOURNAL")"

notifier() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"Newsletter SPF\"" 2>/dev/null || true
}

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
  "$PROJET/venv/bin/python" "$PROJET/scripts/run_weekly.py" --generator local
} >> "$JOURNAL" 2>&1

CODE=$?
if [ $CODE -eq 0 ]; then
  notifier "Brouillon prêt à relire sur http://127.0.0.1:8000"
else
  notifier "Échec de la génération (code $CODE) — voir logs/weekly.log"
fi
exit $CODE
