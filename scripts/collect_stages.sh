#!/bin/bash
# Collecte quotidienne des offres — appelé par le LaunchAgent.
# Ne génère aucun brouillon, n'envoie rien : remplit seulement le stock.
set -uo pipefail
PROJET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOURNAL="$PROJET/logs/collect.log"
mkdir -p "$(dirname "$JOURNAL")"
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
  "$PROJET/venv/bin/python" "$PROJET/scripts/collect_stages.py"
} >> "$JOURNAL" 2>&1
