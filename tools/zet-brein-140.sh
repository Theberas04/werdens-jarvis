#!/bin/bash
# Zet het brein van Jarvis op een Hermes-installatie in een andere container.
# Draai dit als root op de Proxmox-host.
#
#   bash zet-brein-140.sh [container-van-jarvis] [brein-url]
#   bash zet-brein-140.sh 140 http://192.168.1.248:8642/v1
#
# Er staat GEEN sleutel in dit script: het leest API_SERVER_KEY uit de .env van
# de Hermes-container en schrijft hem in /etc/jarvis/jarvis.env van Jarvis.
set -euo pipefail

JARVIS="${1:-140}"
BREIN_URL="${2:-http://192.168.1.248:8642/v1}"
BREIN_MODEL="${3:-hermes-agent}"
ENV_PAD=/etc/jarvis/jarvis.env

[ "$(id -u)" = 0 ] || { echo "FOUT: draai dit als root op de Proxmox-host."; exit 1; }
command -v pct >/dev/null || { echo "FOUT: pct niet gevonden — dit is geen Proxmox-host."; exit 1; }

echo "== 1. is container $JARVIS de Jarvis-container? =="
pct status "$JARVIS"
pct exec "$JARVIS" -- sh -c 'hostname; ip -4 -o addr show scope global | awk "{print \$2, \$4}"'
if ! pct exec "$JARVIS" -- test -f "$ENV_PAD"; then
  echo "FOUT: $ENV_PAD bestaat niet in container $JARVIS — is dit wel de Jarvis-container?"
  exit 1
fi
echo "   $ENV_PAD gevonden"

echo "== 2. sleutel van de Hermes-container lezen =="
BRON=""
for id in $(pct list | awk 'NR>1 && $2=="running" {print $1}'); do
  if pct exec "$id" -- sh -c 'test -f /home/hermes/.hermes/.env && grep -q "^API_SERVER_KEY=" /home/hermes/.hermes/.env' 2>/dev/null; then
    naam=$(pct exec "$id" -- hostname 2>/dev/null | tr -d '\r')
    echo "   container $id ($naam) heeft een Hermes-sleutel"
    if [ "$naam" = "hermesagent" ]; then BRON="$id"; fi
    if [ -z "$BRON" ]; then BRON="$id"; fi
  fi
done
if [ -z "$BRON" ]; then
  echo "FOUT: geen draaiende container gevonden met /home/hermes/.hermes/.env en een API_SERVER_KEY."
  echo "      Staat de Hermes-container aan? (pct list)"
  exit 1
fi
K=$(pct exec "$BRON" -- sh -c 'grep -o "API_SERVER_KEY=.*" /home/hermes/.hermes/.env | cut -d= -f2- | tr -d "\"" | tr -d "\r"')
if [ "${#K}" -lt 20 ]; then echo "FOUT: sleutel uit container $BRON ziet er niet goed uit (${#K} tekens)."; exit 1; fi
echo "   sleutel uit container $BRON: ${#K} tekens, sha256 $(printf '%s' "$K" | sha256sum | cut -c1-16)"

echo "== 3. klaarzetten in container $JARVIS =="
# Het binnenwerk als bestand meegeven scheelt geëmmer met aanhalingstekens.
cat > /tmp/jarvis-brein.sh <<BINNENWERK
set -e
ENV=$ENV_PAD
cp -a \$ENV \$ENV.bak-\$(date +%Y%m%d-%H%M%S)
sed -i '/^JARVIS_BREIN_/d' \$ENV
cat >> \$ENV <<'EINDE'
JARVIS_BREIN_URL=$BREIN_URL
JARVIS_BREIN_SLEUTEL=$K
JARVIS_BREIN_MODEL=$BREIN_MODEL
EINDE
chmod 600 \$ENV
echo "   regels in \$ENV nu:"
grep -c '^JARVIS_BREIN_' \$ENV
echo "== 4. dienst herstarten =="
systemctl restart jarvisd jarvis-wacht
sleep 3
systemctl is-active jarvisd jarvis-wacht || true
echo "== 5. proef: antwoordt de gateway van het brein op deze sleutel? =="
K=\$(grep '^JARVIS_BREIN_SLEUTEL=' \$ENV | cut -d= -f2-)
curl -sS -m 25 -o /dev/null -w 'HTTP %{http_code}\n' -X POST $BREIN_URL/chat/completions \\
  -H "Authorization: Bearer \$K" -H 'Content-Type: application/json' \\
  -d '{"model":"$BREIN_MODEL","messages":[{"role":"user","content":"Antwoord met precies één woord: ok"}]}'
BINNENWERK
chmod 600 /tmp/jarvis-brein.sh
pct push "$JARVIS" /tmp/jarvis-brein.sh /tmp/jarvis-brein.sh --perms 600
rm -f /tmp/jarvis-brein.sh
pct exec "$JARVIS" -- bash /tmp/jarvis-brein.sh
pct exec "$JARVIS" -- rm -f /tmp/jarvis-brein.sh

echo
echo "Klaar. HTTP 200 hierboven = de sleutel klopt en de dienst draait ermee."
echo "401 = de sleutel in .env van de Hermes-container is niet de sleutel die de gateway gebruikt."
echo "Terugdraaien: zet het .bak-bestand in $ENV_PAD terug en herstart jarvisd + jarvis-wacht."
