#!/usr/bin/env bash
#
# Werk Jarvis bij in een bestaande container.
#
# Op de Proxmox-host, als root:
#
#   CTID=140 bash <(curl -fsSL https://raw.githubusercontent.com/Theberas04/werdens-jarvis/main/deploy/update-from-github.sh)
#
# Je instellingen (/etc/jarvis/jarvis.env) en de waakhond-controles
# (/etc/jarvis/waken.json) blijven staan; er komt wel een .bak-bestand naast.
set -uo pipefail

REPO="${REPO:-Theberas04/werdens-jarvis}"
BRANCH="${BRANCH:-main}"
RAW="${REPO_RAW:-https://raw.githubusercontent.com/$REPO/$BRANCH}"
CTID="${CTID:-}"

FOUTEN=0
zeg()  { printf '%s\n' "$*"; }
stap() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
fout() { printf '  \033[31m[fout]\033[0m %s\n' "$*"; FOUTEN=$((FOUTEN + 1)); }

if [ "$(id -u)" -ne 0 ]; then
    fout "draai dit als root op de Proxmox-host."
    exit 1
fi
if [ -z "$CTID" ]; then
    fout "geen CTID meegegeven. Voorbeeld: CTID=140 bash <(curl -fsSL $RAW/deploy/update-from-github.sh)"
    exit 1
fi
if ! pct status "$CTID" >/dev/null 2>&1; then
    fout "container $CTID bestaat niet (of is van een andere host)."
    exit 1
fi
ok "container $CTID gevonden"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

stap "Nieuwe installatiebestanden ophalen"
if curl -fsSL --max-time 60 -o "$TMP/guest-setup.sh" "$RAW/guest-setup.sh" \
    && grep -q "JARVIS-GUEST-SETUP" "$TMP/guest-setup.sh"; then
    ok "guest-setup.sh ($(wc -c <"$TMP/guest-setup.sh") bytes)"
else
    fout "kon guest-setup.sh niet ophalen of hij is onvolledig"
    exit 1
fi

stap "Bijwerken in de container"
if ! pct push "$CTID" "$TMP/guest-setup.sh" /root/guest-setup.sh; then
    fout "kon guest-setup.sh niet in de container zetten"
    exit 1
fi

LOG="$TMP/guest.log"
# De instellingen van de container gaan mee, zodat een update niets overschrijft
# met lege waarden.
if ! pct exec "$CTID" -- env REPO="$REPO" BRANCH="$BRANCH" REPO_RAW="$RAW" \
        bash /root/guest-setup.sh >"$LOG" 2>&1; then
    fout "bijwerken mislukte (laatste regels):"
    tail -n 25 "$LOG" | sed 's/^/     /'
    exit 1
fi
sed 's/^/  /' "$LOG" | grep -E "ok |\[fout\]|ZELFTEST|TRUNK|KLAAR" || true

stap "Controle"
if pct exec "$CTID" -- systemctl is-active --quiet jarvisd; then
    ok "jarvisd draait"
else
    fout "jarvisd draait niet: pct exec $CTID -- journalctl -u jarvisd -n 50"
fi
if grep -q "ZELFTEST OK" "$LOG"; then
    ok "zelftest geslaagd"
else
    fout "zelftest niet geslaagd: pct exec $CTID -- bash -c 'cd /opt/jarvis && python3 -m jarvis.jarvisd --zelftest'"
fi

if [ "$FOUTEN" -gt 0 ]; then
    zeg ""; zeg "  $FOUTEN probleem(en) — zie [fout] hierboven."
    exit 1
fi
zeg ""
ok "klaar"
exit 0
