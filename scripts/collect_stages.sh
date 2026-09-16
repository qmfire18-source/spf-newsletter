#!/bin/bash
# Collecte quotidienne des offres — appelé par le LaunchAgent.
# Ne génère aucun brouillon, n'envoie rien : remplit seulement le stock.
set -uo pipefail
PROJET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOURNAL="$PROJET/logs/collect.log"
# Sans .env lisible, DATABASE_URL serait absente et le code retomberait
# silencieusement sur une base SQLite locale et vide : la tâche paraîtrait
# réussir en n'écrivant nulle part. Mieux vaut s'arrêter bruyamment.
if ! grep -q '^DATABASE_URL=.' "$PROJET/.env" 2>/dev/null; then
  echo "DATABASE_URL absente de $PROJET/.env — tâche interrompue." >&2
  exit 1
fi

mkdir -p "$(dirname "$JOURNAL")"
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
  "$PROJET/venv/bin/python" "$PROJET/scripts/collect_stages.py"
} >> "$JOURNAL" 2>&1
