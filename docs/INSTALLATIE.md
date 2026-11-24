# Installatie en beheer

## Waarom Ubuntu 24.04 en geen Debian

Debian heeft het pakket `asterisk` uit de distributie gehaald — het staat nog in
`sid` en `experimental`, maar niet in bookworm en niet in trixie. Op Debian stopt
`apt-get install asterisk` dus met "no installation candidate". Ubuntu 24.04
levert asterisk 20.6 inclusief `app_audiosocket.so` (de AudioSocket die het
gesprek naar jarvisd brengt), `chan_pjsip`, `res_pjsip_outbound_registration` en
`res_rtp_asterisk`. Het installatiescript kiest daarom een
`ubuntu-24.04-standard`-template en controleert na het installeren of die
modules inderdaad op schijf staan.

## Snelweg (Proxmox)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Theberas04/werdens-jarvis/main/jarvis.sh)
```

De installatie vraagt niets: je vult je nummer, wachtwoord en brein daarna in op
`http://<ip>:8099/instellingen`. De variabelen hieronder hoef je alleen te
gebruiken als je alles in één keer wilt meegeven of een tweede installatie
scriptmatig doet.

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

Draai je geen Proxmox: zet een Ubuntu 24.04 LXC/VM neer en voer `guest-setup.sh` als
root uit. Die verwacht `REPO_RAW` (waar de bestanden staan) en dezelfde
variabelen.

## Instellingen: pagina of bestand

De pagina `/instellingen` schrijft naar `/etc/jarvis/jarvis.env`, maakt de
Asterisk-bestanden opnieuw uit de sjablonen in `/opt/jarvis/asterisk/` en laat
Asterisk de nieuwe trunk inlezen (`module reload res_pjsip.so`, `manager reload`,
`dialplan reload`). De draaiende dienst leest de nieuwe waarden meteen; een
herstart is alleen nodig als je met de hand in het bestand schrijft.

Wat de pagina nooit doet: een wachtwoord of sleutel terugsturen. Een geheim veld
leeg laten betekent "laat staan".

## Het brein op een andere Hermes zetten (vanaf de Proxmox-host)

Draait er al een Hermes in een andere container (bijvoorbeeld de ICT-AI), dan
hoef je de sleutel niet over te typen. Dit script leest `API_SERVER_KEY` uit de
`.env` van die container en zet de drie brein-regels in `/etc/jarvis/jarvis.env`
van Jarvis, met een back-up en een proefaanroep na de herstart:

```bash
curl -fsSL https://raw.githubusercontent.com/Theberas04/werdens-jarvis/main/tools/zet-brein-140.sh | bash
```

Standaard: Jarvis in container `140`, brein `http://192.168.1.248:8642/v1`,
model `hermes-agent`. Anders geef je het mee:

```bash
curl -fsSL https://raw.githubusercontent.com/Theberas04/werdens-jarvis/main/tools/zet-brein-140.sh | bash -s -- 140 http://192.168.1.248:8642/v1
```

De laatste regel van de uitvoer is de proefaanroep: **HTTP 200** betekent dat de
sleutel klopt en de dienst ermee draait, **401** betekent dat de sleutel in de
`.env` van de Hermes-container niet de sleutel is die zijn gateway gebruikt.
Terugdraaien: het `.bak-`bestand naast `/etc/jarvis/jarvis.env` terugzetten en
`systemctl restart jarvisd jarvis-wacht`.

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
| `/opt/jarvis/asterisk/` | de sjablonen voor de Asterisk-configuratie |
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

In de container, vanuit de map van de programmatuur:

```bash
cd /opt/jarvis

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

## Een eigen brein kiezen (en tussen meerdere wisselen)

Onderaan de instellingenpagina staat het paneel **Brein: aan welke Hermes is hij
gekoppeld**. Daar zie je waar hij nu mee praat (naam, endpoint, model, of er een
sleutel staat) en kun je:

- **de huidige combinatie bewaren** onder een naam die je herkent ("Hermes in de
  woonkamer", "testmodel");
- **wisselen met één klik** — de gekozen gegevens gaan naar
  `/etc/jarvis/jarvis.env` en de draaiende dienst gebruikt ze meteen, zonder
  herstart (een gesprek dat daarna begint, gebruikt het nieuwe brein);
- **testen** met één echte korte vraag: je ziet het antwoord, de seconden en het
  model dat antwoordde — handig om te weten of een endpoint bereikbaar is voordat
  je iemand belt;
- **een brein verwijderen** (het laatste bewaarde brein blijft staan).

De bewaarde lijst staat in `/etc/jarvis/breinen.json` (rechten 640, de sleutels
horen erbij). Sleutels gaan nooit terug naar de pagina: je ziet alleen of er een
sleutel is ingevuld. Wisselen kan ook in de API:

```
curl -s -XPOST http://<ip>:8099/api/breinen -H 'Content-Type: application/json' \
     -d '{"actie":"gebruiken","naam":"Mijn Hermes"}'
# acties: bewaren, gebruiken, verwijderen, proef   (GET /api/breinen toont de lijst)
```

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

## Wat de waakhond controleert (op de pagina)

Onderaan de instellingenpagina staat de inhoud van `waken.json` als tekst. Elke
controle heeft minimaal een `naam` en een `url`; verder `code` (verwacht
antwoord, standaard 200), `tijd` (timeout in seconden), `dringend` (negeert de
stille uren), `meld_herstel` (bel ook als de dienst het weer doet) en `extra`
(een zin die Jarvis erbij voorleest). Onzin wordt niet opgeslagen: de pagina
weigert het en zegt wat er mis is.

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
