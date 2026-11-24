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
- **`Rejected` in `pjsip show registrations`, met `Fatal response '401'` in
  `/var/log/asterisk/full`**: de server krijgt je inloggegevens en wijst ze af.
  Gebruikersnaam is je nummer in internationaal formaat zonder `+`
  (`31463690256`); het wachtwoord is dat van de **trunk** in het portaal — niet
  je portaal-wachtwoord en niet de sms-API-sleutel. Twee dingen die CheapConnect
  zelf noemt: hun FAQ gebruikt `voip.cheapconnect.net` als SIP-server (de
  trunk-handleiding `sip.cheapconnect.net`), en de naam `asterisk` is op hun
  platform gereserveerd — daarom zet `asterisk/pjsip.conf` een eigen
  `user_agent=JarvisPBX`. Let op: die hoort in de sectie `[global]`. Zet je hem
  in `[transport-udp]`, dan is het geen geldige optie, laadt de transport niet
  en verdwijnt de registratie helemaal uit `pjsip show registrations`.
- **Je slaat op zonder het wachtwoord opnieuw te typen, en het blijft
  `Rejected`**: het veld Trunk-wachtwoord wordt nooit voorgevuld (een geheim
  hoort niet terug op het scherm) en een leeg veld betekent "laat staan wat er
  staat". Het journaal noemt bij het opslaan alleen wat écht veranderd is:
  `journalctl -u jarvisd -n 20` en kijk of `TRUNK_WACHTWOORD` in de regel
  `instellingen bijgewerkt: …` staat. Staat hij er niet bij, dan is er aan het
  wachtwoord niets veranderd — hoe vaak je ook op opslaan drukt.
- **Hetzelfde nummer staat ook op een telefoon of in de app**: CheapConnect
  staat één SIP-registratie per nummer toe. Twee toestellen op hetzelfde nummer
  nemen elkaars lijn over, dus de registratie klapt om zodra de ander zich
  aanmeldt. Vraag in het portaal een gratis extra SIP-account aan (begint met
  `777…`, onder Prepaid VoIP → SIP accounts) en gebruik dat hier, of een apart
  trunk-account.
- **`Rejected` blijft staan terwijl het probleem al verholpen is**: na een
  `Fatal response` schrijft Asterisk `stopping outbound registration` en doet
  hij geen nieuwe poging meer. Het `(exp. …s ago)` loopt dan op en de status
  bevriest. Herladen met `asterisk -rx 'module reload res_pjsip.so'` (of
  `systemctl restart asterisk`) en daarna opnieuw kijken.
- **Belt wel, geen geluid**: RTP wordt geblokkeerd. Meestal is dat de router die
  "SIP ALG" aan heeft staan — zet die uit, dat lost het bijna altijd op.
- **Jarvis belt, maar de melding is stil**: kijk of het wav-bestand bestaat in
  `/var/lib/asterisk/sounds/jarvis/`, en probeer het in de container met
  `systemctl restart jarvisd` en daarna een nieuwe melding.
- **Na een herstart van de container**: `systemctl status asterisk jarvisd` en
  `journalctl -u jarvisd -n 50`.
