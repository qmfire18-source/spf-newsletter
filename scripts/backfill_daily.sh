#!/bin/bash
# Reprise étalée des offres anciennes, une dizaine par jour.
#
# Welcome to the Jungle ralentit les visites soutenues : tout reprendre d'un
# coup a valu une demi-journée de refus. Une dizaine de pages par jour, avec
# dix secondes entre chacune, passe sans encombre et vide le stock en quelques
# jours. Le script s'arrête de lui-même quand il n'y a plus rien à reprendre.
set -uo pipefail
PROJET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOURNAL="$PROJET/logs/reprise.log"
mkdir -p "$(dirname "$JOURNAL")"

if ! grep -q '^DATABASE_URL=.' "$PROJET/.env" 2>/dev/null; then
  echo "DATABASE_URL absente de $PROJET/.env — tâche interrompue." >&2
  exit 1
fi

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
  "$PROJET/venv/bin/python" "$PROJET/scripts/backfill_offer_details.py" \
    --limite 10 --pause 10
} >> "$JOURNAL" 2>&1
