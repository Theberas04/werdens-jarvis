#!/usr/bin/env bash
#
# werdens-jarvis — zet Jarvis neer in een eigen Proxmox-LXC.
#
# Draai dit op je Proxmox-host als root:
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/Theberas04/werdens-jarvis/main/jarvis.sh)
#
# Wat je krijgt: Asterisk met een SIP-trunk van CheapConnect, whisper.cpp en
# piper voor spraak, en de dienst jarvisd die gesprekken voert en zelf belt als
# er iets mis is.
#
# Opties (als omgevingsvariabele mee te geven):
#   TRUNK_NUMMER=31612345678     je CheapConnect-nummer (internationaal, zonder +)
#   TRUNK_WACHTWOORD=...          wachtwoord uit het CheapConnect-portaal
#   MIJN_MOBIEL=31612345678       het nummer dat Jarvis belt
#   BREIN_URL=http://192.168.1.100:8642/v1     OpenAI-compatible chat-endpoint
#   BREIN_SLEUTEL=...             sleutel daarvoor (Hermes: API_SERVER_KEY)
#   BREIN_MODEL=hermes-agent      modelnaam
#   STT_MODEL=base                base (snel) of small (nauwkeuriger)
#   CTID=140 CTNAME=jarvis
#   CORES=4 RAM=2048 DISK=10      whisper op 4 cores is ongeveer 0,3x realtime
#   WEB_POORT=8099
#   UBUNTU=24.04 BRIDGE=vmbr0 TEMPLATE_STORAGE=local ROOTFS_STORAGE=local-lvm
#   FETCH_ONLY=1                  alleen controleren of alle bestanden bestaan
#
set -uo pipefail

REPO="${REPO:-Theberas04/werdens-jarvis}"
BRANCH="${BRANCH:-main}"
RAW="${REPO_RAW:-https://raw.githubusercontent.com/$REPO/$BRANCH}"

CTID="${CTID:-}"
CTNAME="${CTNAME:-jarvis}"
CORES="${CORES:-4}"
RAM="${RAM:-2048}"
DISK="${DISK:-10}"
WEB_POORT="${WEB_POORT:-8099}"
BRIDGE="${BRIDGE:-vmbr0}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
ROOTFS_STORAGE="${ROOTFS_STORAGE:-local-lvm}"
PVE_ETC="${PVE_ETC:-/etc/pve}"
FETCH_ONLY="${FETCH_ONLY:-0}"

TRUNK_NUMMER="${TRUNK_NUMMER:-}"
TRUNK_WACHTWOORD="${TRUNK_WACHTWOORD:-}"
MIJN_MOBIEL="${MIJN_MOBIEL:-}"
BREIN_URL="${BREIN_URL:-}"
BREIN_SLEUTEL="${BREIN_SLEUTEL:-}"
BREIN_MODEL="${BREIN_MODEL:-hermes-agent}"
STT_MODEL="${STT_MODEL:-base}"
CHEAPCONNECT_HOST="${CHEAPCONNECT_HOST:-sip.cheapconnect.net}"

BESTANDEN="guest-setup.sh jarvis.sh jarvis/__init__.py jarvis/config.py jarvis/stem.py jarvis/telefoon.py jarvis/brein.py jarvis/pagina.py jarvis/jarvisd.py jarvis/instellingen.py alarm/__init__.py alarm/wacht.py alarm/waken.json asterisk/pjsip.conf asterisk/extensions.conf asterisk/manager.conf asterisk/rtp.conf asterisk/modules.conf asterisk/logger.conf systemd/jarvisd.service systemd/jarvis-wacht.service systemd/jarvis-wacht.timer tools/__init__.py tools/proefgesprek.py deploy/update-from-github.sh"

FOUTEN=0
zeg()  { printf '%s\n' "$*"; }
stap() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
let_op(){ printf '  \033[33mlet op\033[0m %s\n' "$*"; }
fout() { printf '  \033[31m[fout]\033[0m %s\n' "$*"; FOUTEN=$((FOUTEN + 1)); }

HIER="$(cd "$(dirname "${BASH_SOURCE[0]:-/dev/null}")" 2>/dev/null && pwd || echo "")"

# ---------------------------------------------------------------- vooraf

stap "Controles"

if [ "$(id -u)" -ne 0 ]; then
    fout "dit script moet als root op de Proxmox-host draaien (nu uid $(id -u))."
    zeg  "     Draai:  sudo bash <(curl -fsSL $RAW/jarvis.sh)"
    exit 1
fi
ok "root"

if [ ! -d "$PVE_ETC" ]; then
    fout "geen $PVE_ETC — dit lijkt geen Proxmox-host."
    zeg  "     Op een gewone Ubuntu-machine kun je guest-setup.sh direct draaien; zie de README."
    exit 1
fi
ok "Proxmox ($PVE_ETC aanwezig)"

for cmd in pct pveam curl; do
    if command -v "$cmd" >/dev/null 2>&1; then ok "$cmd"; else fout "$cmd ontbreekt"; fi
done
[ "$FOUTEN" -eq 0 ] || exit 1
zeg "  $(pveversion | head -1)"

# ------------------------------------------------------ bestanden ophalen

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

stap "Installatiebestanden ophalen"

if [ -n "$HIER" ] && [ -f "$HIER/guest-setup.sh" ]; then
    cp "$HIER/guest-setup.sh" "$TMP/guest-setup.sh"
    ok "guest-setup.sh (uit deze map)"
elif curl -fsSL --max-time 60 -o "$TMP/guest-setup.sh" "$RAW/guest-setup.sh"; then
    ok "guest-setup.sh opgehaald"
else
    fout "kon guest-setup.sh niet ophalen van $RAW"
    exit 1
fi

if [ ! -s "$TMP/guest-setup.sh" ] || ! grep -q "JARVIS-GUEST-SETUP" "$TMP/guest-setup.sh"; then
    fout "guest-setup.sh is leeg of onvolledig (marker ontbreekt)"
    exit 1
fi
ok "$(wc -c <"$TMP/guest-setup.sh") bytes, marker gevonden"

ontbreekt=0
for bestand in $BESTANDEN; do
    if [ -n "$HIER" ] && [ -f "$HIER/$bestand" ]; then
        ok "$bestand (uit deze map)"
    elif curl -fsSL --max-time 30 -o /dev/null "$RAW/$bestand"; then
        ok "$bestand staat op GitHub"
    else
        fout "$bestand staat niet op $RAW"
        ontbreekt=$((ontbreekt + 1))
    fi
done
[ "$ontbreekt" -eq 0 ] || { zeg "  Zet eerst alle bestanden in de repo en push ze."; exit 1; }

if [ "$FETCH_ONLY" = "1" ]; then
    stap "FETCH_ONLY: niets aangemaakt"
    ok "alle bestanden compleet"
    exit 0
fi

# ---------------------------------------------------------------- template
#
# Ubuntu 24.04 en niet Debian. Debian heeft het pakket asterisk uit de
# distributie gehaald — het staat alleen nog in sid en experimental, niet in
# bookworm en niet in trixie — terwijl Asterisk juist de kern van Jarvis is
# (SIP-trunk, AudioSocket, AMI). Ubuntu 24.04 levert asterisk 20.6 met
# app_audiosocket.so en chan_pjsip.so erin; daar is alles wat we gebruiken
# aanwezig. Met een Debian-container stopt de installatie daarom meteen met
# "Package 'asterisk' has no installation candidate".

stap "Ubuntu-template"

UBUNTU="${UBUNTU:-24.04}"

TEMPLATE="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{print $1}' \
    | grep -E "^${TEMPLATE_STORAGE}:vztmpl/ubuntu-${UBUNTU}-standard_.*_amd64\.tar\.(zst|gz)$" \
    | sort | tail -1)"

if [ -z "$TEMPLATE" ]; then
    zeg "  geen ubuntu-${UBUNTU}-template in $TEMPLATE_STORAGE; nu ophalen"
    pveam update >/dev/null 2>&1 || let_op "pveam update gaf een fout, toch verder"
    NAAM="$(pveam available --section system 2>/dev/null | awk '{print $2}' \
        | grep -E "^ubuntu-${UBUNTU}-standard_.*_amd64\.tar\.(zst|gz)$" | sort | tail -1)"
    if [ -z "$NAAM" ]; then
        fout "geen ubuntu-${UBUNTU}-template beschikbaar via pveam"
        zeg  "     Bekijk wat je host aanbiedt:"
        pveam available --section system 2>/dev/null | grep -E "ubuntu-" | sed 's/^/       /'
        zeg  "     Of gebruik een andere opslag voor templates: TEMPLATE_STORAGE=local"
        exit 1
    fi
    if pveam download "$TEMPLATE_STORAGE" "$NAAM" >/dev/null 2>&1; then
        TEMPLATE="$TEMPLATE_STORAGE:vztmpl/$NAAM"
        ok "gedownload: $NAAM"
    else
        fout "downloaden van $NAAM mislukt"
        exit 1
    fi
else
    ok "al aanwezig: $TEMPLATE"
fi

# ------------------------------------------------------------- container

stap "Container"

if [ -z "$CTID" ]; then
    for i in $(seq 140 199); do
        if ! pct status "$i" >/dev/null 2>&1; then CTID="$i"; break; fi
    done
fi
if [ -z "$CTID" ]; then
    fout "geen vrij container-ID gevonden tussen 140 en 199 — geef er zelf een mee: CTID=200 bash ..."
    exit 1
fi
if pct status "$CTID" >/dev/null 2>&1; then
    fout "container $CTID bestaat al. Twee mogelijkheden:"
    zeg  "       pct destroy $CTID          (opnieuw beginnen, de container is nog leeg)"
    zeg  "       CTID=141 bash <(curl -fsSL $RAW/jarvis.sh)    (een ander nummer gebruiken)"
    exit 1
fi
ok "container-ID $CTID"

zeg "  aanmaken ($CTNAME: ${CORES} core, ${RAM} MB, ${DISK} GB, DHCP op $BRIDGE)"
if ! pct create "$CTID" "$TEMPLATE" \
        --hostname "$CTNAME" \
        --cores "$CORES" --memory "$RAM" --swap 512 \
        --rootfs "$ROOTFS_STORAGE:$DISK" \
        --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
        --unprivileged 1 \
        --features nesting=1 \
        --onboot 1 \
        --description "Jarvis: telefonische assistent (Asterisk + whisper + piper). Beheer: http://<ip>:$WEB_POORT/" \
        >/dev/null 2>"$TMP/create.log"; then
    fout "pct create mislukt"
    sed 's/^/       /' "$TMP/create.log"
    exit 1
fi
ok "container aangemaakt"

stap "Starten"
start_lukt=0
if pct start "$CTID" >/dev/null 2>&1; then
    start_lukt=1; ok "gestart"
else
    let_op "eerste startpoging mislukt; opnieuw met apparmor unconfined"
    pct set "$CTID" --lxc.apparmor.profile unconfined >/dev/null 2>&1
    if pct start "$CTID" >/dev/null 2>&1; then
        start_lukt=1; ok "gestart met lxc.apparmor.profile=unconfined"
    fi
fi
if [ "$start_lukt" -ne 1 ]; then
    fout "container $CTID wil niet starten"
    zeg "  --- pct config ---"; pct config "$CTID" 2>&1 | sed 's/^/     /'
    zeg "  --- dmesg ---";      dmesg 2>/dev/null | tail -n 15 | sed 's/^/     /'
    zeg ""; zeg "  Opruimen: pct destroy $CTID"
    exit 1
fi

stap "Netwerk van de container"
IP=""
for _ in $(seq 1 40); do
    IP="$(pct exec "$CTID" -- ip -4 -o addr show eth0 2>/dev/null \
        | awk '{print $4}' | cut -d/ -f1 | head -1)"
    [ -n "$IP" ] && break
    sleep 1
done
if [ -z "$IP" ]; then
    fout "geen IP-adres op eth0 (DHCP). Draai: pct exec $CTID -- ip -4 a"
    exit 1
fi
ok "IP-adres $IP"

# Vooraf controleren of de container internet heeft. Zonder dat mislukt de
# hele installatie (apt, GitHub, Hugging Face) en zoekt de gebruiker in de
# verkeerde hoek.
if pct exec "$CTID" -- bash -c 'exec 3<>/dev/tcp/archive.ubuntu.com/80' >/dev/null 2>&1; then
    ok "internet vanuit de container werkt"
else
    fout "de container kan archive.ubuntu.com niet bereiken"
    zeg "     DNS in de container:"
    pct exec "$CTID" -- sh -c 'cat /etc/resolv.conf 2>/dev/null | sed "s/^/       /"'
    zeg "     Controleer op de host het uitgaande verkeer van $CTID, of probeer:"
    zeg "       pct exec $CTID -- sh -c 'echo nameserver 1.1.1.1 > /etc/resolv.conf'"
    zeg "     en daarna opnieuw:  bash <(curl -fsSL $RAW/jarvis.sh)"
    exit 1
fi

# --------------------------------------------------------- installeren

stap "Jarvis installeren in de container"
zeg "  (Asterisk, whisper.cpp, piper en de modellen: dit duurt een paar minuten)"

if ! pct push "$CTID" "$TMP/guest-setup.sh" /root/guest-setup.sh; then
    fout "kon guest-setup.sh niet in de container zetten"
    exit 1
fi

LOG="$TMP/guest.log"
if ! pct exec "$CTID" -- env \
        REPO="$REPO" BRANCH="$BRANCH" REPO_RAW="$RAW" \
        TRUNK_NUMMER="$TRUNK_NUMMER" TRUNK_WACHTWOORD="$TRUNK_WACHTWOORD" \
        MIJN_MOBIEL="$MIJN_MOBIEL" CHEAPCONNECT_HOST="$CHEAPCONNECT_HOST" \
        BREIN_URL="$BREIN_URL" BREIN_SLEUTEL="$BREIN_SLEUTEL" BREIN_MODEL="$BREIN_MODEL" \
        STT_MODEL="$STT_MODEL" WEB_POORT="$WEB_POORT" \
        bash /root/guest-setup.sh >"$LOG" 2>&1; then
    fout "installatie in de container mislukte (laatste regels):"
    tail -n 30 "$LOG" | sed 's/^/     /'
    zeg "     Volledige log: pct exec $CTID -- journalctl -u jarvisd -n 50"
    exit 1
fi
sed 's/^/  /' "$LOG" | grep -E "ok |\[fout\]|ZELFTEST|TRUNK|KLAAR|letop" || true

if ! grep -q "ZELFTEST OK" "$LOG"; then
    fout "de zelftest in de container is niet geslaagd (spraak of brein)"
    zeg "     Kijk met: pct exec $CTID -- python3 -m jarvis.jarvisd --zelftest"
fi

# ---------------------------------------------------- controle buitenaf

stap "Controle vanaf de host"

if pct exec "$CTID" -- systemctl is-active --quiet jarvisd; then
    ok "dienst jarvisd is actief"
else
    fout "dienst jarvisd is niet actief"
fi

if pct exec "$CTID" -- bash -c 'ss -ltn | grep -q ":9092 "'; then
    ok "AudioSocket-poort 9092 luistert"
else
    fout "AudioSocket-poort 9092 luistert niet"
fi

WEB="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://$IP:$WEB_POORT/health")"
if [ "$WEB" = "200" ]; then
    ok "statuspagina antwoordt op http://$IP:$WEB_POORT"
else
    fout "statuspagina antwoordt niet (HTTP $WEB)"
fi

if pct exec "$CTID" -- bash -c "asterisk -rx 'pjsip show registrations' | grep -qi registered"; then
    ok "SIP-trunk is geregistreerd bij CheapConnect"
elif [ -z "$TRUNK_NUMMER" ]; then
    let_op "nog geen trunk ingesteld — vul TRUNK_NUMMER en TRUNK_WACHTWOORD in /etc/jarvis/jarvis.env"
else
    fout "de trunk registreert niet; controleer nummer en wachtwoord"
fi

# ------------------------------------------------------------ samenvatting

stap "Klaar"

zeg "  Statuspagina:  http://$IP:$WEB_POORT/"
zeg "  Instellingen:  http://$IP:$WEB_POORT/instellingen   <- hier vul je je nummer en gegevens in"
zeg "                 (hoeft dus niet tijdens de installatie)"
zeg "  Container:     $CTID ($CTNAME, ${CORES} core / ${RAM} MB)"
zeg ""
zeg "  Eerste keer: vul op de instellingenpagina je trunk-nummer, het wachtwoord"
zeg "  van CheapConnect en je mobiele nummer in; Asterisk laadt de trunk meteen in."
zeg ""
zeg "  Zo bel je Jarvis:      jouw CheapConnect-nummer  ->  container $CTID"
zeg "  Zo belt Jarvis jou:    knop op de statuspagina, of:"
zeg "     pct exec $CTID -- python3 -m jarvis.jarvisd --bel \"De back-up is klaar.\""
zeg ""
zeg "  Instellingen:  /etc/jarvis/jarvis.env   (daarna: pct exec $CTID -- systemctl restart jarvisd)"
zeg "  Controles:     /etc/jarvis/waken.json   (wat de waakhond in de gaten houdt)"
zeg "  Logboek:       pct exec $CTID -- journalctl -u jarvisd -f"
zeg ""
zeg "  Inkomend bellen achter een router: stuur UDP 5060 en UDP 10000-10200"
zeg "  door naar $IP. Uitgaand bellen werkt zonder die regels."
zeg ""
zeg "  Opruimen:  pct destroy $CTID"

if [ "$FOUTEN" -gt 0 ]; then
    zeg ""
    zeg "  $FOUTEN controle(s) met een fout — zie [fout] hierboven."
    exit 1
fi
exit 0