#!/bin/bash
# Ouvre l'interface de validation à l'extérieur, le temps d'une relecture.
#
#   ./scripts/partager.sh
#
# Démarre le serveur si besoin, puis un tunnel qui lui donne une adresse HTTPS
# publique à transmettre au bureau. Sans les identifiants, personne ne peut
# lire ni envoyer le brouillon.
#
# Deux transports, essayés dans cet ordre :
#   1. SSH via localhost.run (port 22) — aucun logiciel à installer.
#   2. Cloudflare (port 7844) — plus robuste, mais ce port est bloqué sur
#      beaucoup de réseaux d'école et d'entreprise.
#
# L'adresse change à chaque lancement. Ctrl+C referme l'accès extérieur.
set -uo pipefail

PROJET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8000
mkdir -p "$PROJET/logs"

# COOKIE_SECURE=false laisse le cookie de session circuler aussi en clair. Le
# tunnel étant en HTTPS, l'accès fonctionne dans les deux cas — mais pour un
# partage régulier, mieux vaut true. Contrepartie : http://localhost ne permet
# alors plus de se connecter, seul le lien HTTPS fonctionne.
if grep -qs '^COOKIE_SECURE=false' "$PROJET/.env"; then
  echo "Note : COOKIE_SECURE=false dans .env — le tunnel marchera quand même,"
  echo "mais passe-le à true pour un partage régulier (localhost cessera alors"
  echo "de fonctionner, c'est normal)."
  echo
fi

if ! curl -s -o /dev/null -m 2 "http://127.0.0.1:$PORT/login"; then
  echo "Démarrage du serveur sur le port $PORT…"
  "$PROJET/venv/bin/uvicorn" src.app.main:app --port "$PORT" --host 127.0.0.1 \
    > "$PROJET/logs/uvicorn.log" 2>&1 &
  until curl -s -o /dev/null -m 2 "http://127.0.0.1:$PORT/login"; do sleep 0.5; done
fi

# Le port 7844 est-il joignable ? Sinon inutile de tenter Cloudflare.
if command -v cloudflared >/dev/null 2>&1 \
   && nc -z -G 4 region1.v2.argotunnel.com 7844 >/dev/null 2>&1; then
  echo "Tunnel Cloudflare — l'adresse s'affiche dans quelques secondes."
  echo "Ctrl+C pour refermer l'accès extérieur."
  echo
  exec cloudflared tunnel --url "http://127.0.0.1:$PORT"
fi

echo "Tunnel SSH (localhost.run) — l'adresse s'affiche dans quelques secondes."
echo "Ctrl+C pour refermer l'accès extérieur."
echo
exec ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 \
         -R "80:127.0.0.1:$PORT" nokey@localhost.run
