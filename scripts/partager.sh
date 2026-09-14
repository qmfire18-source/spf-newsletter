#!/bin/bash
# Ouvre l'interface de validation à l'extérieur, le temps d'une relecture.
#
#   ./scripts/partager.sh
#
# Démarre le serveur web s'il ne tourne pas, puis un tunnel Cloudflare qui lui
# donne une adresse HTTPS publique. Le lien est à donner au bureau ; sans les
# identifiants, personne ne peut ni lire ni envoyer le brouillon.
#
# L'adresse change à chaque lancement (tunnel gratuit sans compte). Ctrl+C
# ferme le tunnel et coupe l'accès extérieur.
set -uo pipefail

PROJET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8000

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared est absent. Installe-le : brew install cloudflared" >&2
  exit 1
fi

# COOKIE_SECURE=false laisse le cookie de session circuler aussi en clair.
# Le tunnel étant en HTTPS, l'accès fonctionne dans les deux cas — mais dès que
# l'interface est exposée, mieux vaut true. Contrepartie : http://localhost ne
# permet alors plus de se connecter, seul le lien HTTPS fonctionne.
if grep -qs '^COOKIE_SECURE=false' "$PROJET/.env"; then
  echo "Note : COOKIE_SECURE=false dans .env — le tunnel marchera quand même,"
  echo "mais passe-le à true pour un partage régulier (localhost cessera alors"
  echo "de fonctionner, ce qui est normal)."
  echo
fi

if ! curl -s -o /dev/null -m 2 "http://127.0.0.1:$PORT/login"; then
  echo "Démarrage du serveur sur le port $PORT…"
  "$PROJET/venv/bin/uvicorn" src.app.main:app --port "$PORT" --host 127.0.0.1 \
    > "$PROJET/logs/uvicorn.log" 2>&1 &
  until curl -s -o /dev/null -m 2 "http://127.0.0.1:$PORT/login"; do sleep 0.5; done
fi

echo "Ouverture du tunnel — l'adresse publique s'affiche dans quelques secondes."
echo "Ctrl+C pour refermer l'accès extérieur."
echo
exec cloudflared tunnel --url "http://127.0.0.1:$PORT"
