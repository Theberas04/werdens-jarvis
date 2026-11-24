#!/bin/bash
# Zet het brein van Jarvis op een Hermes-installatie in een andere container.
# Draai dit als root op een Proxmox-host. Werkt ook als Jarvis en Hermes op
# verschillende nodes van hetzelfde cluster staan: de host gaat via ssh naar de
# andere node (dat kan in een cluster zonder wachtwoord).
#
#   bash zet-brein-140.sh [container-van-jarvis] [brein-url] [model]
#   bash zet-brein-140.sh 140 http://192.168.1.248:8642/v1 hermes-agent
#
# Er staat GEEN sleutel in dit script: het leest API_SERVER_KEY uit de .env van
# de Hermes-container en schrijft hem in /etc/jarvis/jarvis.env van Jarvis.
set -euo pipefail

JARVIS="${1:-140}"
BREIN_URL="${2:-http://192.168.1.248:8642/v1}"
BREIN_MODEL="${3:-hermes-agent}"
ENV_PAD=/etc/jarvis/jarvis.env
HERMES_ENV=/home/hermes/.hermes/.env

[ "$(id -u)" = 0 ] || { echo "FOUT: draai dit als root op de Proxmox-host."; exit 1; }
command -v pct >/dev/null || { echo "FOUT: pct niet gevonden — dit is geen Proxmox-host."; exit 1; }

HIER=$(hostname -s)
# Alle nodes van het cluster, de eigen node eerst.
NODES="$HIER"
if [ -d /etc/pve/nodes ]; then
  for n in $(ls /etc/pve/nodes 2>/dev/null); do
    [ "$n" = "$HIER" ] || NODES="$NODES $n"
  done
fi
echo "== 0. cluster =="
echo "   deze host: $HIER | nodes: $NODES"

# Commando in een container uitvoeren, waar die container ook staat.
in_ct() {
  local node="$1" vmid="$2"; shift 2
  if [ "$node" = "$HIER" ]; then
    pct exec "$vmid" -- "$@"
  else
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$node" pct exec "$vmid" -- "$@"
  fi
}

# Bestand in een container zetten, waar die container ook staat (via stdin).
naar_ct() {
  local node="$1" vmid="$2" bron="$3" doel="$4"
  if [ "$node" = "$HIER" ]; then
    pct exec "$vmid" -- sh -c "cat > $doel" < "$bron"
  else
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$node" pct exec "$vmid" -- sh -c "cat > $doel" < "$bron"
  fi
}

# Draaiende containers van een node opvragen.
lijst_ct() {
  local node="$1"
  if [ "$node" = "$HIER" ]; then pct list; else ssh -o BatchMode=yes -o ConnectTimeout=10 "$node" pct list; fi
}

echo "== 1. waar staat de Hermes-container? =="
HERMES_NODE=""; HERMES_ID=""
for node in $NODES; do
  for id in $(lijst_ct "$node" 2>/dev/null | awk 'NR>1 && $2=="running" {print $1}'); do
    if in_ct "$node" "$id" sh -c "test -f $HERMES_ENV && grep -q '^API_SERVER_KEY=' $HERMES_ENV" 2>/dev/null; then
      naam=$(in_ct "$node" "$id" hostname 2>/dev/null | tr -d '\r')
      echo "   node $node, container $id ($naam)"
      if [ "$naam" = "hermesagent" ] || [ -z "$HERMES_ID" ]; then HERMES_NODE="$node"; HERMES_ID="$id"; fi
    fi
  done
done
if [ -z "$HERMES_ID" ]; then
  echo "FOUT: geen draaiende container gevonden met $HERMES_ENV en een API_SERVER_KEY."
  echo "      Staat de Hermes-container aan? (pct list, ook op de andere node)"
  exit 1
fi
K=$(in_ct "$HERMES_NODE" "$HERMES_ID" sh -c "grep -o 'API_SERVER_KEY=.*' $HERMES_ENV | cut -d= -f2- | tr -d '\"' | tr -d '\r'")
if [ "${#K}" -lt 20 ]; then echo "FOUT: sleutel uit container $HERMES_ID ziet er niet goed uit (${#K} tekens)."; exit 1; fi
echo "   sleutel: ${#K} tekens, sha256 $(printf '%s' "$K" | sha256sum | cut -c1-16)"

echo "== 2. waar staat de Jarvis-container? =="
JARVIS_NODE=""
for node in $NODES; do
  if lijst_ct "$node" 2>/dev/null | awk -v id="$JARVIS" 'NR>1 && $1==id {print $2}' | grep -q running; then
    JARVIS_NODE="$node"
  fi
done
if [ -z "$JARVIS_NODE" ]; then echo "FOUT: container $JARVIS draait niet in dit cluster."; exit 1; fi
in_ct "$JARVIS_NODE" "$JARVIS" sh -c 'hostname; ip -4 -o addr show scope global | awk "{print \$2, \$4}"'
if ! in_ct "$JARVIS_NODE" "$JARVIS" test -f "$ENV_PAD"; then
  echo "FOUT: $ENV_PAD bestaat niet in container $JARVIS — is dit wel de Jarvis-container?"
  exit 1
fi
echo "   node $JARVIS_NODE, container $JARVIS — $ENV_PAD gevonden"

echo "== 3. klaarzetten =="
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
echo "   brein-regels in \$ENV:"
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
naar_ct "$JARVIS_NODE" "$JARVIS" /tmp/jarvis-brein.sh /tmp/jarvis-brein.sh
rm -f /tmp/jarvis-brein.sh
in_ct "$JARVIS_NODE" "$JARVIS" bash /tmp/jarvis-brein.sh
in_ct "$JARVIS_NODE" "$JARVIS" rm -f /tmp/jarvis-brein.sh

echo
echo "Klaar. HTTP 200 hierboven = de sleutel klopt en de dienst draait ermee."
echo "401 = de sleutel in $HERMES_ENV van container $HERMES_ID is niet de sleutel die de gateway gebruikt."
echo "Terugdraaien: zet het .bak-bestand in $ENV_PAD terug en herstart jarvisd + jarvis-wacht."
