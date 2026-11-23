# Installatie en beheer

## Snelweg (Proxmox)

```bash
TRUNK_NUMMER=... TRUNK_WACHTWOORD=... MIJN_MOBIEL=... BREIN_SLEUTEL=... \
bash <(curl -fsSL https://raw.githubusercontent.com/Theberas04/werdens-jarvis/main/jarvis.sh)
```

Opties die je als omgevingsvariabele kunt meegeven:

| Variabele | Standaard | Wat het doet |
| --- | --- | --- |
| `CTID` | eerste vrije vanaf 140 | container-ID |
| `CTNAME` | `jarvis` | hostnaam |
| `CORES` | 4 | Whisper op 4 cores is ongeveer 0,3x realtime |
| `RAM` | 2048 | meer geheugen = een groter Whisper-model |
| `DISK` | 8 | ruimte voor de modellen (± 1 GB) |
| `STT_MODEL` | `base` | `small` is nauwkeuriger, ± 3x langzamer |
| `WEB_POORT` | 8099 | statuspagina |
| `BREIN_URL` | `http://192.168.1.100:8642/v1` | OpenAI-compatible endpoint |
| `BREIN_SLEUTEL` | leeg | bijvoorbeeld de Hermes `API_SERVER_KEY` |
| `BREIN_MODEL` | `hermes-agent` | modelnaam bij dat endpoint |
| `DEBIAN`, `BRIDGE`, `TEMPLATE_STORAGE`, `ROOTFS_STORAGE` | 13, vmbr0, local, local-lvm | Proxmox-keuzes |
| `FETCH_ONLY=1` | 0 | alleen controleren of alle bestanden bestaan |

Draai je geen Proxmox: zet een Debian-LXC/VM neer en voer `guest-setup.sh` als
root uit. Die verwacht `REPO_RAW` (waar de bestanden staan) en dezelfde
variabelen.

## Bijwerken

Bestanden in de container verversen (code, config, systemd):

```bash
CTID=140 bash -c "$(curl -fsSL https://raw.githubusercontent.com/Theberas04/werdens-jarvis/main/deploy/update-from-github.sh)"
```

Of met de hand: haal `guest-setup.sh` opnieuw op en draai hem; hij bewaart je
`jarvis.env` en `waken.json` (er komt een `.bak-`bestand naast te staan).

## Wat waar staat in de container

| Pad | Wat |
| --- | --- |
| `/opt/jarvis` | de programmatuur (Python, alleen de standaardbibliotheek) |
| `/opt/jarvis/bin/whisper` | whisper.cpp (`whisper-server`) |
| `/opt/jarvis/bin/piper` | piper + de stem `nl_NL-rdh-medium.onnx` |
| `/opt/jarvis/modellen` | het Whisper-model |
| `/etc/jarvis/jarvis.env` | alle instellingen |
| `/etc/jarvis/waken.json` | wat de waakhond controleert |
| `/etc/asterisk/*.conf` | trunk, dialplan, manager, RTP |
| `/var/lib/asterisk/sounds/jarvis` | de wav-bestanden van meldingen |
| `/var/log/jarvis/jarvis.log` | het gespreksjournaal |

## Diensten

```bash
systemctl status jarvisd        # de assistent
systemctl status asterisk       # de telefooncentrale
systemctl list-timers jarvis-wacht.timer   # de waakhond
journalctl -u jarvisd -f        # meelezen tijdens een gesprek
```

## Handige commando's

```bash
# Wie belt er, en wat zegt hij?
python3 -m jarvis.jarvisd --zelftest

# Een melding laten voorlezen (belt echt)
python3 -m jarvis.jarvisd --bel "De back-up is klaar."

# Bellen voor een gesprek, zonder melding
python3 -m jarvis.jarvisd --gesprek

# De waakhond één ronde laten doen, zonder te bellen
python3 -m alarm.wacht --proef
python3 -m alarm.wacht --toon
```

## Een eigen brein kiezen

Jarvis praat met elke OpenAI-compatible chat-endpoint. Twee routes die allebei
werken:

**Hermes** (aanbevolen: kan echt iets doen)

```
JARVIS_BREIN_URL=http://192.168.1.100:8642/v1
JARVIS_BREIN_SLEUTEL=<API_SERVER_KEY uit ~/.hermes/.env>
JARVIS_BREIN_MODEL=hermes-agent
```

**Je eigen engine** (bijvoorbeeld een llama.cpp-server op je netwerk)

```
JARVIS_BREIN_URL=http://192.168.1.100:11434/v1
JARVIS_BREIN_SLEUTEL=wllm-local
JARVIS_BREIN_MODEL=<modelnaam>
```

Let op de snelheid: een klein model op CPU is bruikbaar voor losse vragen, maar
een Hermes-turn met gereedschap duurt op zo'n model minuten — en dat hoor je aan
de telefoon als lange stiltes. Voor gesprekken is een cloudmodel of Hermes de
betere keuze.

## Spraak van een andere laag gebruiken

Heb je al een spraaklaag draaien (bijvoorbeeld de Werdens Media LLM-medialaag),
dan hoef je Whisper en piper niet lokaal te draaien:

```
JARVIS_STEM=remote
JARVIS_STT_URL=http://192.168.1.100:8091
JARVIS_TTS_URL=http://192.168.1.100:8091
```

Beide routes worden verwacht op `/v1/audio/transcriptions` en
`/v1/audio/speech`. Let op de stem: een meerspreker-piperstem klinkt voor een
mens misschien redelijk, maar Whisper maakt er wartaal van — doe altijd de
round-tripproef (`--zelftest`) voordat je zo'n laag aan de telefoon hangt.

## Beveiliging

De statuspagina staat standaard open op je LAN. Wil je een wachtwoord, zet dan
`JARVIS_WEB_WACHTWOORD` in `jarvis.env`; de pagina en de API vragen dan om
HTTP Basic-auth. Zet de pagina niet rechtstreeks op internet: de knop "bel mijn
telefoon" is een belknop, en de gesprekslogboeken zijn persoonlijk.
