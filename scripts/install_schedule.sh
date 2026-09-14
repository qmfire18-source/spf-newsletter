#!/bin/bash
# Programme (ou retire) la génération hebdomadaire via launchd.
#
#   ./scripts/install_schedule.sh              # tous les lundis à 8h00
#   ./scripts/install_schedule.sh --heure 9    # à une autre heure
#   ./scripts/install_schedule.sh --retirer
#
# launchd rattrape un rendez-vous manqué au réveil de la machine : si le Mac
# dort le lundi à 8h, la génération part au réveil plutôt que d'être sautée.
set -euo pipefail

ETIQUETTE="com.sciencespofinance.newsletter"
PLIST="$HOME/Library/LaunchAgents/$ETIQUETTE.plist"
PROJET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HEURE=8

while [ $# -gt 0 ]; do
  case "$1" in
    --retirer)
      launchctl bootout "gui/$(id -u)/$ETIQUETTE" 2>/dev/null || true
      rm -f "$PLIST"
      echo "Programmation retirée."
      exit 0
      ;;
    --heure) HEURE="$2"; shift 2 ;;
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
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>$PROJET/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$PROJET/logs/launchd.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLISTEOF

mkdir -p "$PROJET/logs"
launchctl bootout "gui/$(id -u)/$ETIQUETTE" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Programmé : tous les lundis à ${HEURE}h00."
echo "  vérifier  : launchctl print gui/$(id -u)/$ETIQUETTE | head -20"
echo "  essayer   : launchctl kickstart -p gui/$(id -u)/$ETIQUETTE"
echo "  retirer   : ./scripts/install_schedule.sh --retirer"
