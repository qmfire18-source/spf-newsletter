#!/bin/bash
# Programme (ou retire) la génération hebdomadaire via launchd.
#
#   ./scripts/install_schedule.sh              # tous les lundis à 10h30
#   ./scripts/install_schedule.sh --a 9:15     # à une autre heure
#   ./scripts/install_schedule.sh --retirer
#
# launchd rattrape un rendez-vous manqué au réveil de la machine : si le Mac
# dort le lundi à 8h, la génération part au réveil plutôt que d'être sautée.
set -euo pipefail

ETIQUETTE="com.sciencespofinance.newsletter"
COLLECTE="com.sciencespofinance.collecte"
PLIST="$HOME/Library/LaunchAgents/$ETIQUETTE.plist"
PLIST_COLLECTE="$HOME/Library/LaunchAgents/$COLLECTE.plist"
PROJET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HEURE=10
MINUTE=30

while [ $# -gt 0 ]; do
  case "$1" in
    --retirer)
      launchctl bootout "gui/$(id -u)/$ETIQUETTE" 2>/dev/null || true
      launchctl bootout "gui/$(id -u)/$COLLECTE" 2>/dev/null || true
      rm -f "$PLIST" "$PLIST_COLLECTE"
      echo "Programmation retirée (newsletter et collecte)."
      exit 0
      ;;
    --a)
      if [[ ! "$2" =~ ^([0-9]{1,2}):([0-9]{2})$ ]]; then
        echo "Heure attendue au format H:MM, par exemple 10:30 — reçu : $2" >&2
        exit 1
      fi
      HEURE="${BASH_REMATCH[1]}"; MINUTE="${BASH_REMATCH[2]}"
      shift 2
      ;;
    *) echo "Option inconnue : $1" >&2; exit 1 ;;
  esac
done

mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$ETIQUETTE</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJET/scripts/weekly_local.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJET</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>1</integer>
    <key>Hour</key><integer>$HEURE</integer>
    <key>Minute</key><integer>$((10#$MINUTE))</integer>
  </dict>
  <key>StandardOutPath</key><string>$PROJET/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$PROJET/logs/launchd.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLISTEOF

# Collecte quotidienne des offres : WTTJ coupe le scraper après quelques
# pages, une seule visite par semaine ne ramènerait qu'une poignée d'offres.
# Sept petites collectes valent bien mieux qu'une grosse, et ménagent le site.
cat > "$PLIST_COLLECTE" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$COLLECTE</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJET/scripts/collect_stages.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJET</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>$PROJET/logs/collecte.out.log</string>
  <key>StandardErrorPath</key><string>$PROJET/logs/collecte.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLISTEOF

mkdir -p "$PROJET/logs"
launchctl bootout "gui/$(id -u)/$ETIQUETTE" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl bootout "gui/$(id -u)/$COLLECTE" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_COLLECTE"

printf 'Newsletter : tous les lundis à %dh%02d.\n' "$((10#$HEURE))" "$((10#$MINUTE))"
echo "Collecte d'offres : tous les jours à 7h15."
echo "  vérifier  : launchctl print gui/$(id -u)/$ETIQUETTE | head -20"
echo "  essayer   : launchctl kickstart -p gui/$(id -u)/$ETIQUETTE"
echo "  retirer   : ./scripts/install_schedule.sh --retirer"
