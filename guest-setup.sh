#!/usr/bin/env bash
# JARVIS-GUEST-SETUP
#
# Zet Jarvis neer in deze container: Asterisk met een SIP-trunk, whisper.cpp,
# piper en de dienst jarvisd. Wordt gestart door jarvis.sh op de Proxmox-host.
#
# Verwacht als omgevingsvariabelen (leeg mag):
#   REPO, BRANCH, REPO_RAW
#   TRUNK_NUMMER, TRUNK_WACHTWOORD, MIJN_MOBIEL
#   BREIN_URL, BREIN_SLEUTEL, BREIN_MODEL
#   STT_MODEL (base of small), WEB_POORT, CHEAPCONNECT_HOST
set -uo pipefail

REPO="${REPO:-Theberas04/werdens-jarvis}"
BRANCH="${BRANCH:-main}"
RAW="${REPO_RAW:-https://raw.githubusercontent.com/$REPO/$BRANCH}"

TRUNK_NUMMER="${TRUNK_NUMMER:-}"
TRUNK_WACHTWOORD="${TRUNK_WACHTWOORD:-}"
MIJN_MOBIEL="${MIJN_MOBIEL:-}"
BREIN_URL="${BREIN_URL:-http://192.168.1.100:8642/v1}"
BREIN_SLEUTEL="${BREIN_SLEUTEL:-}"
BREIN_MODEL="${BREIN_MODEL:-hermes-agent}"
STT_MODEL="${STT_MODEL:-base}"
WEB_POORT="${WEB_POORT:-8099}"
CHEAPCONNECT_HOST="${CHEAPCONNECT_HOST:-sip.cheapconnect.net}"

WHISPER_TAG="${WHISPER_TAG:-v1.8.7}"
PIPER_TAG="${PIPER_TAG:-v1.2.0}"
PIPER_STEM_URL="${PIPER_STEM_URL:-https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-nl-rdh-medium.tar.gz}"

JARVIS=/opt/jarvis
FOUTEN=0
APT_LOG=/tmp/jarvis-apt.log
ok()   { printf '  ok    %s\n' "$*"; }
zeg()  { printf '%s\n' "$*"; }
waarschuwing() { printf '  letop %s\n' "$*"; }
fout() { printf '  [fout] %s\n' "$*"; FOUTEN=$((FOUTEN + 1)); }
stap() { printf '\n== %s\n' "$*"; }

# Bestanden ophalen zonder van curl afhankelijk te zijn: eerst curl, dan wget,
# dan python3 (dat staat op Debian altijd). Alle drie met een IPv4-voorkeur,
# want op een verse container mislukt IPv6 naar de spiegels vaak alsnog.
haal() {
    _url="$1"; _doel="$2"; _max="${3:-60}"
    if command -v curl >/dev/null 2>&1; then
        curl -4 -fsSL --max-time "$_max" -o "$_doel" "$_url" && return 0
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -4 -q -T "$_max" -O "$_doel" "$_url" && return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$_url" "$_doel" "$_max" <<'PY' && return 0
import sys, urllib.request
url, doel, max_seconden = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    with urllib.request.urlopen(url, timeout=max_seconden) as antwoord:
        with open(doel, "wb") as f:
            f.write(antwoord.read())
except Exception:
    sys.exit(1)
PY
    fi
    return 1
}

netwerk_uitleg() {
    if ! getent hosts archive.ubuntu.com >/dev/null 2>&1; then
        fout "geen DNS in de container. Zet een naamserver en probeer opnieuw:"
        printf '         pct exec %s -- sh -c "echo nameserver 1.1.1.1 > /etc/resolv.conf"\n' "${CTID:-<id>}"
    elif ! python3 -c "import socket;socket.create_connection(('archive.ubuntu.com',80),5)" >/dev/null 2>&1; then
        fout "wel DNS, geen verbinding met de pakketspiegel. Controleer de uitgaande route van de container."
    else
        waarschuwing "de spiegel is bereikbaar; de fout zit in apt zelf"
    fi
    if grep -qi "lock" "$APT_LOG" 2>/dev/null; then
        fout "een andere pakketbeheerder houdt de dpkg-lock vast (apt-daily of unattended-upgrades)."
        printf '         Wacht een minuut en probeer opnieuw:\n'
        printf '         pct exec %s -- pkill -f unattended-upgrades\n' "${CTID:-<id>}"
    fi
    if grep -qi "no space" "$APT_LOG" 2>/dev/null; then
        fout "de schijf van de container is vol; geef hem meer ruimte en probeer opnieuw."
    fi
}

# Een verse container start vaak direct apt-daily of unattended-upgrades, die de
# dpkg-lock vasthouden; elk apt-commando mislukt dan met "Could not get lock".
apt_rust() {
    for _ in $(seq 1 60); do
        _bezig=0
        for _p in /proc/[0-9]*/comm; do
            case "$(cat "$_p" 2>/dev/null)" in
                apt|apt-get|dpkg|unattended-upgr*|apt-helper) _bezig=1; break ;;
            esac
        done
        [ "$_bezig" -eq 0 ] && return 0
        sleep 2
    done
    waarschuwing "een andere pakketbeheerder blijft actief; ik probeer het toch"
    return 0
}

# --------------------------------------------------------------- systeem

stap "Systeem klaarzetten"
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=l NEEDRESTART_SUSPEND=1
. /etc/os-release 2>/dev/null || true
VERDIE=0
if [ "${ID:-}" = "ubuntu" ]; then
    # asterisk en ffmpeg zitten in Ubuntu's universe; zonder die component
    # meldt apt "no installation candidate" voor beide.
    if ! grep -rqi 'universe' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null; then
        waarschuwing "universe ontbreekt in de pakketbronnen; ik zet het erbij"
        if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then
            sed -i 's/^Components: \(.*\)/Components: \1 universe/' /etc/apt/sources.list.d/ubuntu.sources
        elif [ -f /etc/apt/sources.list ]; then
            sed -i 's/^\(deb[[:space:]].*\)$/\1 universe/' /etc/apt/sources.list
        fi
        grep -rqi 'universe' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null \
            || fout "kon universe niet toevoegen aan de pakketbronnen"
    fi
    ok "pakketbronnen: ${PRETTY_NAME:-Ubuntu}"
elif [ "${ID:-}" = "debian" ]; then
    waarschuwing "Debian ${VERSION_ID:-?}: het pakket asterisk is uit Debian gehaald (alleen nog in sid)"
fi

if ! getent hosts archive.ubuntu.com >/dev/null 2>&1; then
    waarschuwing "geen naamserver in de container; apt heeft misschien geen verbinding"
    echo "nameserver 1.1.1.1" > /etc/resolv.conf 2>/dev/null || true
fi

APT_BIJ=0
for poging in 1 2 3 4; do
    apt_rust
    if apt-get update -qq -o Acquire::ForceIPv4=true >"$APT_LOG" 2>&1; then
        APT_BIJ=1
        break
    fi
    waarschuwing "apt-get update mislukte (poging $poging van 4)"
    sleep 10
done
if [ "$APT_BIJ" -eq 1 ]; then
    ok "pakketlijsten bijgewerkt"
else
    fout "apt-get update mislukte; laatste regels:"
    tail -n 8 "$APT_LOG" | sed 's/^/         /'
    netwerk_uitleg
fi

# Eerst het gereedschap dat we zelf nodig hebben, dan de rest: zo heb je bij een
# fout meteen een werkende curl in plaats van een lange lijst mislukkingen.
if apt-get install -y -qq --no-install-recommends curl ca-certificates >>"$APT_LOG" 2>&1; then
    ok "curl en certificaten geinstalleerd"
else
    fout "kon curl niet installeren; laatste regels:"
    tail -n 8 "$APT_LOG" | sed 's/^/         /'
fi

apt_rust
# Eerst kijken of asterisk hier überhaupt bestaat. Debian heeft het pakket uit
# de distributie gehaald; dan is doorgaan zinloos en wil je meteen weten waarom.
if ! apt-cache policy asterisk 2>/dev/null | grep -q 'Candidate: [0-9]'; then
    fout "het pakket asterisk bestaat niet in deze distributie (${PRETTY_NAME:-onbekend})"
    apt-cache policy asterisk 2>/dev/null | sed -n '1,4p' | sed 's/^/         /'
    zeg  "     Debian heeft asterisk uit de distributie gehaald (het staat alleen nog in sid)."
    zeg  "     Maak de container opnieuw met Ubuntu 24.04:"
    zeg  "       pct destroy <id>   en daarna de one-liner opnieuw"
    echo "GEEN-ASTERISK-PAKKET"
    exit 1
fi

zeg "  pakketten installeren: asterisk, asterisk-modules, ffmpeg (enkele minuten)"
apt_rust
if apt-get install -y -qq --no-install-recommends asterisk asterisk-modules asterisk-config \
        ffmpeg xz-utils iproute2 procps >>"$APT_LOG" 2>&1; then
    ok "asterisk, ffmpeg en hulpmiddelen geinstalleerd"
else
    fout "kon asterisk/ffmpeg niet installeren; laatste regels:"
    tail -n 8 "$APT_LOG" | sed 's/^/         /'
fi
command -v ffmpeg >/dev/null 2>&1 || fout "ffmpeg staat niet op deze machine (nodig voor het omrekenen van 8 kHz telefoonaudio)"
command -v asterisk >/dev/null 2>&1 || fout "asterisk staat niet op deze machine"

# Zonder deze twee heeft doorwerken geen zin: dan lag het aan apt, en dat is hier
# boven al te lezen. Stop meteen, zodat de log kort blijft.
if ! command -v asterisk >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1; then
    netwerk_uitleg
    fout "gestopt: asterisk en ffmpeg zijn nodig en konden niet geinstalleerd worden."
    echo "APT-MISLUKT"
    exit 1
fi

# De modules zitten per distributie in een andere map (Ubuntu: multiarch), dus
# zoeken in plaats van aannemen.
PJSIP="$(find /usr/lib /usr/lib64 -name 'chan_pjsip.so' -print -quit 2>/dev/null)"
MODULE_MAP=""
[ -n "$PJSIP" ] && MODULE_MAP="$(dirname "$PJSIP")"
if [ -n "$MODULE_MAP" ] && [ -f "$MODULE_MAP/app_audiosocket.so" ]; then
    ok "AudioSocket en pjsip aanwezig in $MODULE_MAP"
else
    fout "AudioSocket-module (app_audiosocket.so) niet gevonden; pjsip-map: ${MODULE_MAP:-onbekend}"
fi

mkdir -p "$JARVIS" /opt/jarvis/jarvis /opt/jarvis/alarm /opt/jarvis/bin /opt/jarvis/modellen \
         /etc/jarvis /var/lib/jarvis /var/log/jarvis /var/lib/asterisk/sounds/jarvis
chmod 750 /etc/jarvis

# ------------------------------------------------------------------ code

stap "Programma ophalen"
CODE="jarvis/__init__.py jarvis/config.py jarvis/stem.py jarvis/telefoon.py jarvis/brein.py jarvis/pagina.py jarvis/jarvisd.py jarvis/instellingen.py alarm/__init__.py alarm/wacht.py tools/__init__.py tools/proefgesprek.py"
CODE_FOUTEN=0
for bestand in $CODE; do
    if haal "$RAW/$bestand" "$JARVIS/$bestand" 60; then
        ok "$bestand"
    else
        fout "kon $bestand niet ophalen van $RAW"
        CODE_FOUTEN=$((CODE_FOUTEN + 1))
    fi
done
[ "$CODE_FOUTEN" -eq 0 ] || { echo "Zonder code gaat het niet verder."; netwerk_uitleg; exit 1; }

# --------------------------------------------------------------- whisper

stap "Spraak naar tekst (whisper.cpp $WHISPER_TAG)"
if [ -x "$JARVIS/bin/whisper/whisper-server" ]; then
    ok "whisper-server staat er al"
else
    TMP="$(mktemp -d)"
    zeg "  whisper.cpp ophalen (ongeveer 30 MB)"
    if haal "https://github.com/ggml-org/whisper.cpp/releases/download/$WHISPER_TAG/whisper-bin-ubuntu-x64.tar.gz" \
        "$TMP/whisper.tar.gz" 300 \
        && mkdir -p "$JARVIS/bin/whisper" \
        && tar -xzf "$TMP/whisper.tar.gz" -C "$JARVIS/bin/whisper" --strip-components=1; then
        ok "whisper.cpp uitgepakt"
    else
        fout "whisper.cpp ophalen of uitpakken mislukte"
    fi
    rm -rf "$TMP"
fi
if [ -x "$JARVIS/bin/whisper/whisper-server" ]; then
    ok "whisper-server is uitvoerbaar"
else
    # De tarball heeft niet altijd dezelfde indeling; zoek het bestand zelf.
    GEVONDEN="$(find "$JARVIS/bin/whisper" -name 'whisper-server' -type f 2>/dev/null | head -1)"
    if [ -n "$GEVONDEN" ]; then
        chmod +x "$GEVONDEN"
        ln -sf "$GEVONDEN" "$JARVIS/bin/whisper/whisper-server" 2>/dev/null
        ok "whisper-server gevonden op $GEVONDEN"
    else
        fout "geen whisper-server in $JARVIS/bin/whisper"
    fi
fi

MODEL="$JARVIS/modellen/ggml-$STT_MODEL.bin"
if [ ! -s "$MODEL" ]; then
    zeg "  spraakmodel ggml-$STT_MODEL.bin ophalen (ongeveer 150 MB; dit duurt het langst)"
    if haal "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$STT_MODEL.bin" \
        "$MODEL" 600; then
        ok "model ggml-$STT_MODEL.bin ($(du -h "$MODEL" | cut -f1))"
    else
        fout "kon het whisper-model niet ophalen"
    fi
else
    ok "model staat er al"
fi

# ------------------------------------------------------------------ piper

stap "Spraak uit (piper $PIPER_TAG)"
if [ ! -x "$JARVIS/bin/piper/piper" ]; then
    TMP="$(mktemp -d)"
    zeg "  piper ophalen (ongeveer 20 MB)"
    if haal "https://github.com/rhasspy/piper/releases/download/$PIPER_TAG/piper_amd64.tar.gz" \
        "$TMP/piper.tar.gz" 300 \
        && mkdir -p "$JARVIS/bin/piper" \
        && tar -xzf "$TMP/piper.tar.gz" -C "$JARVIS/bin/piper" --strip-components=1; then
        ok "piper uitgepakt"
    else
        fout "piper ophalen of uitpakken mislukte"
    fi
    rm -rf "$TMP"
fi
if [ -x "$JARVIS/bin/piper/piper" ]; then
    ok "piper is uitvoerbaar"
else
    GEVONDEN="$(find "$JARVIS/bin/piper" -maxdepth 2 -name piper -type f 2>/dev/null | head -1)"
    if [ -n "$GEVONDEN" ]; then chmod +x "$GEVONDEN"; ok "piper op $GEVONDEN"
    else fout "geen piper-binary gevonden"; fi
fi

STEM_ONNX="$JARVIS/modellen/nl_NL-rdh-medium.onnx"
if [ ! -s "$STEM_ONNX" ]; then
    TMP="$(mktemp -d)"
    zeg "  Nederlandse stem ophalen (ongeveer 65 MB)"
    if haal "$PIPER_STEM_URL" "$TMP/stem.tar.gz" 300 \
        && tar -xzf "$TMP/stem.tar.gz" -C "$TMP"; then
        ONNX="$(find "$TMP" -maxdepth 1 -name '*.onnx' | head -1)"
        JSON="$(find "$TMP" -maxdepth 1 -name '*.onnx.json' | head -1)"
        if [ -n "$ONNX" ] && [ -n "$JSON" ]; then
            cp "$ONNX" "$STEM_ONNX"
            cp "$JSON" "$STEM_ONNX.json"
            ok "stem nl_NL-rdh-medium ($(du -h "$STEM_ONNX" | cut -f1))"
        else
            fout "de stem-tarball bevat geen onnx-bestand"
        fi
    else
        fout "kon de piper-stem niet ophalen"
    fi
    rm -rf "$TMP"
else
    ok "stem staat er al"
fi

# ------------------------------------------------------- instellingen

stap "Instellingen (/etc/jarvis/jarvis.env)"
if [ -f /etc/jarvis/jarvis.env ]; then
    cp /etc/jarvis/jarvis.env "/etc/jarvis/jarvis.env.bak-$(date +%Y%m%d-%H%M%S)"
    # Bij een herinstallatie blijven de bestaande waarden staan. Wat je op de
    # instellingenpagina hebt ingevuld mag niet verdwijnen omdat de installatie
    # opnieuw loopt.
    ok "bestaande instellingen blijven staan (er is een .bak-bestand gemaakt)"
else
AMI_SECRET="${AMI_SECRET:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')}"
cat > /etc/jarvis/jarvis.env <<EOF
# Jarvis — instellingen. Aanpassen met: nano /etc/jarvis/jarvis.env
# Daarna: systemctl restart jarvisd

# --- telefoonlijn (CheapConnect SIP-trunk) ---------------------------------
TRUNK_NUMMER=$TRUNK_NUMMER
TRUNK_WACHTWOORD=$TRUNK_WACHTWOORD
CHEAPCONNECT_HOST=$CHEAPCONNECT_HOST
# Naar welk nummer Jarvis jou belt, in internationaal formaat zonder + (31612345678)
MIJN_MOBIEL=$MIJN_MOBIEL

# --- brein ----------------------------------------------------------------
# Elke OpenAI-compatible chat-endpoint. Standaard Hermes op je eigen netwerk.
JARVIS_BREIN_URL=$BREIN_URL
JARVIS_BREIN_SLEUTEL=$BREIN_SLEUTEL
JARVIS_BREIN_MODEL=$BREIN_MODEL
JARVIS_BREIN_TIJD=90

# --- spraak ---------------------------------------------------------------
JARVIS_STEM=lokaal
JARVIS_TAAL=nl
JARVIS_WHISPER_BIN=$JARVIS/bin/whisper/whisper-server
JARVIS_WHISPER_MODEL=$MODEL
JARVIS_PIPER_BIN=$JARVIS/bin/piper/piper
JARVIS_PIPER_STEM=$STEM_ONNX
# Wil je spraak van een andere laag gebruiken (bijvoorbeeld de Werdens
# Media LLM-medialaag), zet dan JARVIS_STEM=remote en vul deze twee in:
JARVIS_STT_URL=
JARVIS_TTS_URL=

# --- gesprek --------------------------------------------------------------
JARVIS_BEGROETING=Met Jarvis. Waarmee kan ik helpen?
JARVIS_MAX_GESPREK=600
JARVIS_STILTE_MS=650
JARVIS_DREMPEL=450
JARVIS_MELDING_HINT=Druk op een om met mij te praten.

# --- luisteren en pagina --------------------------------------------------
JARVIS_LUISTER=127.0.0.1:9092
JARVIS_WEB=0.0.0.0:$WEB_POORT
JARVIS_LOG=/var/log/jarvis/jarvis.log

# --- Asterisk manager (zelf bellen) --------------------------------------
AMI_HOST=127.0.0.1
AMI_POORT=5038
AMI_GEBRUIKER=jarvis
AMI_WACHTWOORD=$AMI_SECRET
EOF
fi
chmod 640 /etc/jarvis/jarvis.env
ok "instellingen in orde"

# --------------------------------------------------------- asterisk-config

stap "Asterisk instellen"
mkdir -p "$JARVIS/asterisk"
SJABLONEN=0
for bestand in pjsip.conf extensions.conf manager.conf rtp.conf modules.conf logger.conf; do
    if haal "$RAW/asterisk/$bestand" "$JARVIS/asterisk/$bestand" 60; then
        SJABLONEN=$((SJABLONEN + 1))
    else
        fout "kon asterisk/$bestand niet ophalen"
    fi
done
if [ "$SJABLONEN" -eq 6 ]; then
    ok "zes sjablonen in $JARVIS/asterisk (het dashboard hergebruikt ze)"
fi

# De configuratie wordt door de programmatuur zelf geschreven, zodat de
# installatie en de instellingenpagina exact dezelfde weg volgen.
mkdir -p /etc/asterisk
if (cd "$JARVIS" && python3 -m jarvis.instellingen --asterisk); then
    ok "/etc/asterisk gevuld uit de sjablonen"
else
    fout "kon /etc/asterisk niet vullen"
fi
mkdir -p /var/lib/asterisk/sounds/jarvis
chown -R asterisk:asterisk /var/lib/asterisk/sounds/jarvis 2>/dev/null || true

if [ -z "$TRUNK_NUMMER" ] || [ -z "$TRUNK_WACHTWOORD" ] || [ -z "$MIJN_MOBIEL" ]; then
    waarschuwing "nog niet alles is ingevuld: dat doe je op http://<ip>:$WEB_POORT/instellingen"
fi

# ------------------------------------------------------------- diensten

stap "Diensten starten"
for eenheid in jarvisd.service jarvis-wacht.service jarvis-wacht.timer; do
    if haal "$RAW/systemd/$eenheid" "/etc/systemd/system/$eenheid" 60; then
        ok "$eenheid"
    else
        fout "kon systemd/$eenheid niet ophalen"
    fi
done
chmod 644 /etc/systemd/system/jarvisd.service /etc/systemd/system/jarvis-wacht.service /etc/systemd/system/jarvis-wacht.timer 2>/dev/null || true
systemctl daemon-reload

# waakhond-controles: alleen als er nog geen bestand staat
if [ ! -f /etc/jarvis/waken.json ]; then
    haal "$RAW/alarm/waken.json" /etc/jarvis/waken.json 60 \
        && ok "/etc/jarvis/waken.json (voorbeeld)" \
        || waarschuwing "geen voorbeeld voor waken.json gevonden"
fi

systemctl enable --now asterisk >/dev/null 2>&1
systemctl restart asterisk >/dev/null 2>&1 || true
for i in $(seq 1 20); do
    asterisk -rx "core show uptime" >/dev/null 2>&1 && break
    sleep 1
done
if asterisk -rx "core show uptime" >/dev/null 2>&1; then
    ok "Asterisk draait"
else
    fout "Asterisk antwoordt niet"
fi

if asterisk -rx "module show like audiosocket" 2>/dev/null | grep -qi audiosocket; then
    ok "AudioSocket-module geladen"
else
    fout "AudioSocket-module ontbreekt (app_audiosocket)"
fi

systemctl enable jarvisd >/dev/null 2>&1
systemctl restart jarvisd >/dev/null 2>&1
for i in $(seq 1 30); do
    if curl -fsS --max-time 2 "http://127.0.0.1:$WEB_POORT/health" >/dev/null 2>&1; then break; fi
    sleep 1
done
if curl -fsS --max-time 3 "http://127.0.0.1:$WEB_POORT/health" >/dev/null 2>&1; then
    ok "jarvisd antwoordt op zijn statuspagina"
else
    fout "jarvisd antwoordt niet op http://127.0.0.1:$WEB_POORT/health"
    journalctl -u jarvisd -n 15 --no-pager 2>/dev/null | sed 's/^/       /'
fi
systemctl enable --now jarvis-wacht.timer >/dev/null 2>&1 && ok "waakhond-timer actief"

# --------------------------------------------------------------- zelftest

stap "Zelftest"
if python3 -m jarvis.jarvisd --zelftest > /tmp/jarvis-zelftest.json 2>/tmp/jarvis-zelftest.err; then
    cat /tmp/jarvis-zelftest.json
    if grep -q '"klaar": true' /tmp/jarvis-zelftest.json; then
        echo "ZELFTEST OK"
    else
        echo "ZELFTEST DEELS"
    fi
else
    fout "zelftest mislukte"
    tail -n 10 /tmp/jarvis-zelftest.err | sed 's/^/       /'
fi

echo
if asterisk -rx "pjsip show registrations" 2>/dev/null | grep -qi registered; then
    echo "TRUNK GEMELD"
else
    echo "TRUNK NOG NIET GEMELD (vul je nummer en wachtwoord in op de instellingenpagina)"
    asterisk -rx "pjsip show registrations" 2>/dev/null | sed 's/^/       /'
fi

if [ "$FOUTEN" -gt 0 ]; then
    echo "FOUTEN: $FOUTEN"
    exit 1
fi
echo "KLAAR"
exit 0