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
set -uo pipefail

JARVIS="${1:-140}"
BREIN_URL="${2:-http://192.168.1.248:8642/v1}"
BREIN_MODEL="${3:-hermes-agent}"
ENV_PAD=/etc/jarvis/jarvis.env
HERMES_ENV=/home/hermes/.hermes/.env

[ "$(id -u)" = 0 ] || { echo "FOUT: draai dit als root op de Proxmox-host."; exit 1; }
command -v pct >/dev/null || { echo "FOUT: pct niet gevonden — dit is geen Proxmox-host."; exit 1; }

HIER=$(hostname -s)
# accept-new: de host key van de andere node wordt de eerste keer aangenomen.
# Zonder dit stopt ssh met "Host key verification failed" als die node daar nog
# niet in known_hosts staat (bijvoorbeeld omdat de clusternaam met een
# hoofdletter anders is dan de naam in known_hosts).
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"

# Alle nodes van het cluster, de eigen node eerst. Let op: nodes mogen van
# elkaar verschillen in alleen hoofdletters (Proxmox naast proxmox), dus
# vergelijk exact — anders sla je een echte node over.
NODES="$HIER"
if [ -d /etc/pve/nodes ]; then
  for n in $(ls /etc/pve/nodes 2>/dev/null); do
    [ "$n" = "$HIER" ] || NODES="$NODES $n"
  done
fi
# De IP's van de nodes staan in corosync.conf. Nodennamen die alleen in
# hoofdletters verschillen (Proxmox naast proxmox) lossen via DNS op naar
# dezelfde machine, dus ssh moet op het adres gebeuren, niet op de naam.
node_ip() {
  [ -f /etc/pve/corosync.conf ] || return 0
  awk -v doel="$1" '
    /^[[:space:]]*node[[:space:]]*\{/ { naam=""; ip="" }
    /^[[:space:]]*name:/ { naam=$2 }
    /^[[:space:]]*ring0_addr:/ { ip=$2 }
    /^[[:space:]]*\}/ { if (naam==doel && ip!="") { print ip; exit } }
  ' /etc/pve/corosync.conf
}

# Waar ssh heen moet voor een node: het adres als we dat kennen, anders de naam.
ssh_doel() {
  local ip
  ip=$(node_ip "$1")
  if [ -n "$ip" ]; then echo "$ip"; else echo "$1"; fi
}

echo "== 0. cluster =="
echo "   deze host: $HIER | nodes: $NODES"
for n in $NODES; do
  [ "$n" = "$HIER" ] && continue
  echo "   node $n → ssh naar $(ssh_doel "$n")"
done

is_lokaal() { [ "$1" = "$HIER" ]; }

# ssh plakt zijn argumenten aan elkaar tot één regel die de remote shell
# opnieuw parseert. Alles wat je doorgeeft moet dus gequoteerd worden, anders
# valt een `sh -c "..."` of een `>` uit elkaar en belandt de omleiding op de
# host in plaats van in de container.
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\''/g")"; }

# Commando in een container uitvoeren, waar die container ook staat.
in_ct() {
  local node="$1" vmid="$2"; shift 2
  if is_lokaal "$node"; then
    pct exec "$vmid" -- "$@" </dev/null
  else
    local cmd="" a
    for a in "$@"; do cmd="$cmd $(q "$a")"; done
    ssh $SSH_OPTS "$(ssh_doel "$node")" "pct exec $vmid --$cmd" </dev/null
  fi
}

# Bestand in een container zetten, waar die container ook staat (via stdin).
naar_ct() {
  local node="$1" vmid="$2" bron="$3" doel="$4"
  if is_lokaal "$node"; then
    pct exec "$vmid" -- sh -c "cat > $doel" < "$bron"
  else
    ssh $SSH_OPTS "$(ssh_doel "$node")" "pct exec $vmid -- sh -c $(q "cat > $doel")" < "$bron"
  fi
}

# Draaiende containers van een node opvragen; fouten laten we zien.
lijst_ct() {
  local node="$1"
  if is_lokaal "$node"; then pct list </dev/null; else ssh $SSH_OPTS "$(ssh_doel "$node")" pct list </dev/null; fi
}

echo "== 1. waar staat de Hermes-container? =="
HERMES_NODE=""; HERMES_ID=""; GEVONDEN_MAAR_UIT=""
for node in $NODES; do
  echo "   -- node $node --"
  RUW=$(lijst_ct "$node" 2>&1)
  if [ $? -ne 0 ]; then
    echo "      kon de containerlijst van $node niet ophalen:"
    echo "$RUW" | sed 's/^/      /'
    echo "      (ssh naar de andere node hoort in een cluster zonder wachtwoord te werken;"
    echo "       lukt dat niet, draai dit script dan op node $node zelf)"
    continue
  fi
  echo "$RUW" | sed 's/^/      /'
  for id in $(echo "$RUW" | awk 'NR>1 && $1 ~ /^[0-9]+$/ {print $1}'); do
    status=$(echo "$RUW" | awk -v id="$id" '$1==id {print $2}')
    naam=$(in_ct "$node" "$id" hostname 2>/dev/null | tr -d '\r')
    # Naam uit de containerlijst als de container niet draait (dan kan er geen commando in).
    [ -n "$naam" ] || naam=$(echo "$RUW" | awk -v id="$id" '$1==id {print $NF}')
    heeft=$(in_ct "$node" "$id" sh -c "test -f $HERMES_ENV && grep -q '^API_SERVER_KEY=' $HERMES_ENV && echo ja" 2>/dev/null | tr -d '\r')
    echo "      container $id ($naam, $status): Hermes-sleutel ${heeft:-nee}"
    if [ "$heeft" = "ja" ]; then
      if [ "$status" = "running" ]; then
        if [ "$naam" = "hermesagent" ] || [ -z "$HERMES_ID" ]; then HERMES_NODE="$node"; HERMES_ID="$id"; fi
      else
        GEVONDEN_MAAR_UIT="$node:$id"
      fi
    elif [ "$status" != "running" ]; then
      # Een uitstaande container met 'hermes' in de naam is waarschijnlijk de gezochte.
      case "$naam" in *hermes*|*Hermes*) GEVONDEN_MAAR_UIT="$node:$id";; esac
    fi
  done
done
if [ -z "$HERMES_ID" ]; then
  if [ -n "$GEVONDEN_MAAR_UIT" ]; then
    echo "FOUT: de Hermes-container ($GEVONDEN_MAAR_UIT) staat UIT. Start hem en draai dit opnieuw:"
    echo "      pct start ${GEVONDEN_MAAR_UIT#*:}"
  else
    echo "FOUT: geen container gevonden met $HERMES_ENV en een API_SERVER_KEY."
    echo "      Draait de Hermes-container wel? Hieronder wat er wél in het cluster staat:"
    for node in $NODES; do
      echo "      -- $node --"
      lijst_ct "$node" 2>&1 | sed 's/^/         /'
    done
    if command -v qm >/dev/null; then
      echo "      virtuele machines (misschien draait Hermes in een VM, niet in een container):"
      qm list 2>&1 | sed 's/^/         /'
    fi
    echo "      Staat Hermes op een ándere machine die niet in dit cluster zit, gebruik dan:"
    echo "      bash zet-brein-140.sh $JARVIS $BREIN_URL $BREIN_MODEL   (met de sleutel in een bestand, zie de repo)"
  fi
  exit 1
fi
K=$(in_ct "$HERMES_NODE" "$HERMES_ID" sh -c "grep -o 'API_SERVER_KEY=.*' $HERMES_ENV | cut -d= -f2- | tr -d '\"' | tr -d '\r'")
if [ "${#K}" -lt 20 ]; then echo "FOUT: sleutel uit container $HERMES_ID ziet er niet goed uit (${#K} tekens)."; exit 1; fi
echo "   gevonden: node $HERMES_NODE, container $HERMES_ID — sleutel ${#K} tekens, sha256 $(printf '%s' "$K" | sha256sum | cut -c1-16)"

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
