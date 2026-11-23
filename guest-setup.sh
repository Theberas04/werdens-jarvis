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
ok()   { printf '  ok    %s\n' "$*"; }
waarschuwing() { printf '  letop %s\n' "$*"; }
fout() { printf '  [fout] %s\n' "$*"; FOUTEN=$((FOUTEN + 1)); }
stap() { printf '\n== %s\n' "$*"; }

# --------------------------------------------------------------- systeem

stap "Systeem klaarzetten"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1 || waarschuwing "apt-get update gaf een fout"
if apt-get install -y -qq --no-install-recommends asterisk ffmpeg curl ca-certificates xz-utils \
        iproute2 procps >/dev/null 2>&1; then
    ok "asterisk, ffmpeg en hulpmiddelen geinstalleerd"
else
    fout "kon de pakketten niet installeren"
fi
mkdir -p "$JARVIS" /opt/jarvis/jarvis /opt/jarvis/alarm /opt/jarvis/bin /opt/jarvis/modellen \
         /etc/jarvis /var/lib/jarvis /var/log/jarvis /var/lib/asterisk/sounds/jarvis
chmod 750 /etc/jarvis

# ------------------------------------------------------------------ code

stap "Programma ophalen"
CODE="jarvis/__init__.py jarvis/config.py jarvis/stem.py jarvis/telefoon.py jarvis/brein.py jarvis/pagina.py jarvis/jarvisd.py jarvis/instellingen.py alarm/__init__.py alarm/wacht.py tools/__init__.py tools/proefgesprek.py"
CODE_FOUTEN=0
for bestand in $CODE; do
    if curl -fsSL --max-time 60 -o "$JARVIS/$bestand" "$RAW/$bestand"; then
        ok "$bestand"
    else
        fout "kon $bestand niet ophalen van $RAW"
        CODE_FOUTEN=$((CODE_FOUTEN + 1))
    fi
done
[ "$CODE_FOUTEN" -eq 0 ] || { echo "Zonder code gaat het niet verder."; exit 1; }

# --------------------------------------------------------------- whisper

stap "Spraak naar tekst (whisper.cpp $WHISPER_TAG)"
if [ -x "$JARVIS/bin/whisper/whisper-server" ]; then
    ok "whisper-server staat er al"
else
    TMP="$(mktemp -d)"
    if curl -fsSL --max-time 300 -o "$TMP/whisper.tar.gz" \
        "https://github.com/ggml-org/whisper.cpp/releases/download/$WHISPER_TAG/whisper-bin-ubuntu-x64.tar.gz" \
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
    if curl -fsSL --max-time 600 -o "$MODEL" \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$STT_MODEL.bin"; then
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
    if curl -fsSL --max-time 300 -o "$TMP/piper.tar.gz" \
        "https://github.com/rhasspy/piper/releases/download/$PIPER_TAG/piper_amd64.tar.gz" \
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
    if curl -fsSL --max-time 300 -o "$TMP/stem.tar.gz" "$PIPER_STEM_URL" \
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
AMI_SECRET="${AMI_SECRET:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')}"
if [ -f /etc/jarvis/jarvis.env ]; then
    cp /etc/jarvis/jarvis.env "/etc/jarvis/jarvis.env.bak-$(date +%Y%m%d-%H%M%S)"
    ok "oude instellingen bewaard"
fi
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
chmod 640 /etc/jarvis/jarvis.env
ok "instellingen geschreven"

# --------------------------------------------------------- asterisk-config

stap "Asterisk instellen"
mkdir -p "$JARVIS/asterisk"
SJABLONEN=0
for bestand in pjsip.conf extensions.conf manager.conf rtp.conf modules.conf logger.conf; do
    if curl -fsSL --max-time 60 -o "$JARVIS/asterisk/$bestand" "$RAW/asterisk/$bestand"; then
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
    if curl -fsSL --max-time 60 -o "/etc/systemd/system/$eenheid" "$RAW/systemd/$eenheid"; then
        ok "$eenheid"
    else
        fout "kon systemd/$eenheid niet ophalen"
    fi
done
chmod 644 /etc/systemd/system/jarvisd.service /etc/systemd/system/jarvis-wacht.service /etc/systemd/system/jarvis-wacht.timer 2>/dev/null || true
systemctl daemon-reload

# waakhond-controles: alleen als er nog geen bestand staat
if [ ! -f /etc/jarvis/waken.json ]; then
    curl -fsSL --max-time 60 -o /etc/jarvis/waken.json "$RAW/alarm/waken.json" 2>/dev/null \
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