# Telefoonlijn: CheapConnect en je router

## 1. Trunk bij CheapConnect

Je hebt al een account (voor sms). In het portaal
(<https://account.cheapconnect.net/>) maak je een **trunk** aan:

- Een trunk is gratis: geen abonnement, geen aansluitkosten, per seconde
  afgerekend. NL vast circa €0,0238/min, NL mobiel circa €0,0500/min.
- Je krijgt een **gebruikersnaam** (= je telefoonnummer in internationaal
  formaat, bijvoorbeeld `31612345678`) en een **wachtwoord**. Die twee vul je in
  op `http://<ip>:8099/instellingen` (of in `/etc/jarvis/jarvis.env`); na
  opslaan laadt Asterisk de trunk meteen in.
- Voor **inkomend** bellen koppel je een nummer aan de trunk. Heb je nog geen
  nummer, dan is Prepaid VoIP (± €8,95 per jaar, nummer inbegrepen) de
  goedkoopste route.
- Je belt uit met je eigen nummer als caller-ID; het nummer staat ook op
  `callerid=Jarvis <nummer>` in `pjsip.conf`, zodat je telefoon "Jarvis" toont.

De trunk gebruikt SIP over UDP op poort 5060, met G.711 (alaw/ulaw). Dat is wat
de configuratie in `asterisk/pjsip.conf` doet.

## 2. Uitgaand bellen (Jarvis belt jou)

Werkt direct na de registratie: de registratie houdt het gat in je firewall
open en `rtp_symmetric` zorgt dat het geluid terugkomt waar het vandaan kwam.
Je hoeft hiervoor **geen** poorten open te zetten.

Controleer de registratie in de container:

```bash
asterisk -rx "pjsip show registrations"
asterisk -rx "pjsip show endpoint cheapconnect"
```

Bij een probleem: `asterisk -rx "pjsip set logger on"` en dan een keer bellen;
het volledige SIP-gesprek staat dan in `/var/log/asterisk/full`.

## 3. Inkomend bellen (jij belt Jarvis)

Inkomend verkeer moet bij de container kunnen komen. Op je router:

| Poort | Protocol | Naar |
| --- | --- | --- |
| 5060 | UDP | het IP van de container |
| 10000-10200 | UDP | het IP van de container |

Geef de container het liefst een vast IP (in Proxmox: `pct set <id> --net0 ...`
met `ip=192.168.1.x/24,gw=192.168.1.1` in plaats van DHCP), anders verschuift je
portforward na een herstart.

Zit je container achter NAT en wil je dat Asterisk het juiste externe adres in
zijn SIP-berichten zet, vul dan in `/etc/asterisk/pjsip.conf` bij
`[transport-udp]` aan:

```
external_media_address = jouw.publieke.ip
external_signaling_address = jouw.publieke.ip
```

## 4. Wat het kost in de praktijk

- Een melding van 30 seconden naar een mobiel: ongeveer €0,025.
- Een gesprek van 5 minuten naar een mobiel: ongeveer €0,25.
- De waakhond belt alleen bij een overgang (stuk, of herstel als je dat
  aanzet) — niet elke minuut opnieuw. Stel `rust_seconden` in op hoe lang je
  stilte wilt na een melding.

## 5. Nummers en notatie

- Internationaal, zonder `+` en zonder `00`: `31612345678`, `31851234567`.
  Dat is het formaat dat de trunk verwacht (CheapConnect's eigen voorbeeld
  gebruikt `31...`).
- `MIJN_MOBIEL` in `jarvis.env` moet hetzelfde formaat hebben, anders wijst
  Asterisk het gesprek af.
- Staat het nummer in je telefoon als "+31 6 ...", dan komt de oproep toch
  binnen; het `+`-teken zit alleen in je adresboek.

## 6. Als er niets gebeurt

- **Geen registratie**: nummer of wachtwoord verkeerd (controleer op
  spaties), of UDP 5060 uitgaand geblokkeerd door je provider.
- **Belt wel, geen geluid**: RTP wordt geblokkeerd. Meestal is dat de router die
  "SIP ALG" aan heeft staan — zet die uit, dat lost het bijna altijd op.
- **Jarvis belt, maar de melding is stil**: kijk of het wav-bestand bestaat in
  `/var/lib/asterisk/sounds/jarvis/`, en probeer het in de container met
  `systemctl restart jarvisd` en daarna een nieuwe melding.
- **Na een herstart van de container**: `systemctl status asterisk jarvisd` en
  `journalctl -u jarvisd -n 50`.
