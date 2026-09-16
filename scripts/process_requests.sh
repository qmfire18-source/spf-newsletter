#!/bin/bash
# Ramasse les demandes de réécriture déposées depuis l'interface hébergée.
# Appelé toutes les cinq minutes par le LaunchAgent ; ne fait rien s'il n'y a
# aucune demande, et ne traite qu'une demande par passage.
set -uo pipefail
PROJET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOURNAL="$PROJET/logs/demandes.log"
mkdir -p "$(dirname "$JOURNAL")"

# Sans .env lisible, DATABASE_URL serait absente et le code retomberait
# silencieusement sur une base SQLite locale et vide : la tâche ne verrait
# jamais les demandes, sans le dire.
if ! grep -q '^DATABASE_URL=.' "$PROJET/.env" 2>/dev/null; then
  echo "DATABASE_URL absente de $PROJET/.env — tâche interrompue." >&2
  exit 1
fi

{
  "$PROJET/venv/bin/python" "$PROJET/scripts/process_requests.py"
} >> "$JOURNAL" 2>&1
