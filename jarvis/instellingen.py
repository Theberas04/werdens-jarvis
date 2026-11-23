"""Instellingen van het dashboard naar de machine schrijven.

De statuspagina stuurt hier wijzigingen naartoe; dit bestand is de enige plek
die /etc/jarvis/jarvis.env en /etc/asterisk/*.conf aanpast. Zo doen de
installatie en het dashboard exact hetzelfde, en kan een installatie zonder
enkele variabele en daarna ingevuld worden op de pagina.

Wat er gebeurt bij opslaan:

1. de waarden gaan regel voor regel in /etc/jarvis/jarvis.env (bestaande regels
   worden vervangen, commentaar blijft staan);
2. de Asterisk-bestanden worden opnieuw uit de sjablonen gemaakt;
3. de draaiende dienst leest de nieuwe waarden (geen herstart nodig, behalve
   voor paden);
4. Asterisk laat de nieuwe trunk en het nieuwe draaiboek inlezen.

Geheimen worden nooit terug naar de pagina gestuurd: de pagina toont alleen of
er iets staat, en een leeg veld betekent "laat staan".
"""
import json
import os
import re
import shutil
import subprocess
import time

ENV_PAD = "/etc/jarvis/jarvis.env"
WAKEN_PAD = "/etc/jarvis/waken.json"
TEMPLATE_MAP = "/opt/jarvis/asterisk"
ASTERISK_MAP = "/etc/asterisk"


def _paden():
    """Waar de bestanden staan, volgens de instellingen (handig voor tests)."""
    try:
        from .config import Config
        cfg = Config()
        return (cfg["JARVIS_ENV_PAD"] or ENV_PAD,
                cfg["JARVIS_WAKEN_PAD"] or WAKEN_PAD,
                cfg["JARVIS_TEMPLATE_MAP"] or TEMPLATE_MAP,
                cfg["JARVIS_ASTERISK_MAP"] or ASTERISK_MAP)
    except Exception:
        return ENV_PAD, WAKEN_PAD, TEMPLATE_MAP, ASTERISK_MAP

# Sleutel, label, groep, soort (tekst|geheim|keuze|getal|vlag), uitleg
VELDEN = [
    ("TRUNK_NUMMER", "Trunk-nummer", "lijn", "tekst",
     "Je CheapConnect-nummer in internationaal formaat, zonder plus: 31612345678"),
    ("TRUNK_WACHTWOORD", "Trunk-wachtwoord", "lijn", "geheim",
     "Het wachtwoord uit je CheapConnect-portaal"),
    ("CHEAPCONNECT_HOST", "CheapConnect-server", "lijn", "tekst",
     "Laat staan op sip.cheapconnect.net"),
    ("MIJN_MOBIEL", "Mijn mobiele nummer", "lijn", "tekst",
     "Het nummer dat Jarvis belt als er iets mis is: 31612345678"),

    ("JARVIS_BREIN_URL", "Brein-endpoint", "brein", "tekst",
     "OpenAI-compatible chat-endpoint, bijvoorbeeld http://192.168.1.100:8642/v1"),
    ("JARVIS_BREIN_SLEUTEL", "Brein-sleutel", "brein", "geheim",
     "De API-sleutel van dat endpoint (bij Hermes: API_SERVER_KEY)"),
    ("JARVIS_BREIN_MODEL", "Model", "brein", "tekst",
     "Modelnaam bij dat endpoint, bijvoorbeeld hermes-agent"),

    ("JARVIS_STEM", "Spraak", "spraak", "keuze:lokaal|remote",
     "lokaal = Whisper en piper in deze container; remote = een spraaklaag op je netwerk"),
    ("JARVIS_WHISPER_MODEL", "Whisper-model", "spraak", "keuze:base|small",
     "base is snel (circa 0,3x realtime), small is nauwkeuriger maar drie keer langzamer"),
    ("JARVIS_STT_URL", "Spraak-in adres (remote)", "spraak", "tekst",
     "Alleen bij remote: bijvoorbeeld http://192.168.1.100:8091"),
    ("JARVIS_TTS_URL", "Spraak-uit adres (remote)", "spraak", "tekst",
     "Alleen bij remote; mag hetzelfde adres zijn"),
    ("JARVIS_MELDING_HINT", "Afsluiter van een melding", "spraak", "tekst",
     "Wat Jarvis zegt nadat hij een melding heeft voorgelezen"),

    ("JARVIS_BEGROETING", "Begroeting", "gesprek", "tekst",
     "De eerste zin als jij hem belt"),
    ("JARVIS_STILTE_MS", "Stilte voor einde beurt", "gesprek", "getal",
     "Milliseconden stilte waarna hij denkt dat je klaar bent (650 is prettig)"),
    ("JARVIS_DREMPEL", "Spraakdrempel", "gesprek", "getal",
     "Hoe luid iets moet zijn om als spraak te tellen (450 werkt op een telefoonlijn)"),
    ("JARVIS_MAX_GESPREK", "Maximale gespreksduur", "gesprek", "getal",
     "Seconden voordat hij zelf ophangt (600)"),

    ("JARVIS_GEEN_BELLEN", "Niet echt bellen (teststand)", "melden", "vlag",
     "Aan = de waakhond en de knoppen doen alsof, zonder de telefoon te laten overgaan"),
]

GROEPEN = {
    "lijn": "Telefoonlijn (CheapConnect)",
    "brein": "Brein",
    "spraak": "Spraak",
    "gesprek": "Gesprek",
    "melden": "Meldingen",
}

GEHEIM = ("WACHTWOORD", "SLEUTEL")


def _is_geheim(sleutel):
    return any(deel in sleutel for deel in GEHEIM)


def lees_env(pad=None):
    pad = pad or _paden()[0]
    waarden = {}
    try:
        with open(pad, encoding="utf-8") as f:
            for regel in f:
                regel = regel.strip()
                if not regel or regel.startswith("#") or "=" not in regel:
                    continue
                sleutel, waarde = regel.split("=", 1)
                waarden[sleutel.strip()] = waarde.strip().strip('"').strip("'")
    except OSError:
        pass
    return waarden


def schrijf_env(wijzigingen, pad=None):
    """Waarden bijwerken zonder de rest van het bestand te slopen."""
    pad = pad or _paden()[0]
    try:
        with open(pad, encoding="utf-8") as f:
            regels = f.read().splitlines()
    except OSError:
        regels = ["# Jarvis — instellingen"]
    over = dict(wijzigingen)
    uit = []
    for regel in regels:
        kaal = regel.strip()
        if kaal and not kaal.startswith("#") and "=" in kaal:
            sleutel = kaal.split("=", 1)[0].strip()
            if sleutel in over:
                uit.append("%s=%s" % (sleutel, over.pop(sleutel)))
                continue
        uit.append(regel)
    for sleutel, waarde in over.items():
        uit.append("%s=%s" % (sleutel, waarde))
    os.makedirs(os.path.dirname(pad), exist_ok=True)
    tijdelijk = pad + ".tmp"
    with open(tijdelijk, "w", encoding="utf-8") as f:
        f.write("\n".join(uit).rstrip() + "\n")
    os.chmod(tijdelijk, 0o640)
    os.replace(tijdelijk, pad)
    return pad


def _vul(sjabloon, waarden):
    uit = sjabloon
    for sleutel, waarde in waarden.items():
        uit = uit.replace("__%s__" % sleutel, str(waarde))
    return uit


def schrijf_asterisk(waarden=None, template_map=None, doel_map=None):
    """Alle Asterisk-bestanden opnieuw uit de sjablonen maken.

    Geeft een lijst met (bestand, gelukt, melding) terug.
    """
    _, _, template_map, doel_map = _paden()
    template_map = template_map or TEMPLATE_MAP
    doel_map = doel_map or ASTERISK_MAP
    waarden = dict(waarden or lees_env())
    waarden.setdefault("AMI_WACHTWOORD", "")
    waarden.setdefault("JARVIS_LUISTER", "127.0.0.1:9092")
    koppen = {
        "TRUNK_NUMMER": waarden.get("TRUNK_NUMMER", ""),
        "TRUNK_WACHTWOORD": waarden.get("TRUNK_WACHTWOORD", ""),
        "CHEAPCONNECT_HOST": waarden.get("CHEAPCONNECT_HOST") or "sip.cheapconnect.net",
        "AMI_SECRET": waarden.get("AMI_WACHTWOORD", ""),
        "SOCKET": waarden.get("JARVIS_LUISTER") or "127.0.0.1:9092",
    }
    uitkomst = []
    if not os.path.isdir(template_map):
        return [("*", False, "geen sjablonen in %s" % template_map)]
    for naam in ("pjsip.conf", "extensions.conf", "manager.conf", "rtp.conf",
                 "modules.conf", "logger.conf"):
        bron = os.path.join(template_map, naam)
        doel = os.path.join(doel_map, naam)
        if not os.path.exists(bron):
            uitkomst.append((naam, False, "sjabloon ontbreekt"))
            continue
        try:
            with open(bron, encoding="utf-8") as f:
                inhoud = _vul(f.read(), koppen)
            tijdelijk = doel + ".tmp"
            with open(tijdelijk, "w", encoding="utf-8") as f:
                f.write(inhoud)
            os.chmod(tijdelijk, 0o640)
            try:
                shutil.chown(tijdelijk, "asterisk", "asterisk")
            except (LookupError, PermissionError):
                pass
            os.replace(tijdelijk, doel)
            uitkomst.append((naam, True, ""))
        except OSError as fout:
            uitkomst.append((naam, False, str(fout)))
    return uitkomst


def asterisk_herladen(log=print):
    """Laat Asterisk de nieuwe bestanden inlezen (geen herstart nodig)."""
    stappen = ["module reload res_pjsip.so", "manager reload", "dialplan reload"]
    gelukt = []
    for stap in stappen:
        try:
            uit = subprocess.run(["asterisk", "-rx", stap], capture_output=True,
                                 timeout=20)
            gelukt.append((stap, uit.returncode == 0))
        except (OSError, subprocess.TimeoutExpired) as fout:
            gelukt.append((stap, False))
            log("asterisk %s mislukte: %r" % (stap, fout))
    return gelukt


def asterisk_status():
    """Wat Asterisk zelf vindt: draait hij, en is de trunk geregistreerd?"""
    uit = {"draait": False, "trunk_geregistreerd": False, "detail": "", "ruwe_regels": []}
    if not shutil.which("asterisk"):
        uit["detail"] = "asterisk staat niet op deze machine"
        return uit
    try:
        pong = subprocess.run(["asterisk", "-rx", "core show uptime"], capture_output=True,
                              text=True, timeout=15)
        uit["draait"] = pong.returncode == 0 and bool(pong.stdout.strip())
        if not uit["draait"]:
            uit["detail"] = (pong.stdout + pong.stderr).strip()[:200]
            return uit
        reg = subprocess.run(["asterisk", "-rx", "pjsip show registrations"],
                             capture_output=True, text=True, timeout=15)
        regels = [r.strip() for r in reg.stdout.splitlines() if "cheapconnect" in r.lower()]
        uit["ruwe_regels"] = regels
        uit["trunk_geregistreerd"] = any("registered" in r.lower() for r in regels)
        if not uit["trunk_geregistreerd"] and regels:
            uit["detail"] = regels[0][:200]
    except (OSError, subprocess.TimeoutExpired) as fout:
        uit["detail"] = repr(fout)[:200]
    return uit


def lees_waken(pad=None):
    pad = pad or _paden()[1]
    try:
        with open(pad, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return '{\n  "rust_seconden": 1800,\n  "controles": []\n}\n'


def schrijf_waken(tekst, pad=None):
    """Alleen geldige JSON accepteren; anders liever niets opslaan."""
    pad = pad or _paden()[1]
    gegevens = json.loads(tekst)
    if not isinstance(gegevens, dict) or "controles" not in gegevens:
        raise ValueError('de inhoud moet een object zijn met een "controles"-lijst')
    if not isinstance(gegevens["controles"], list):
        raise ValueError('"controles" moet een lijst zijn')
    for controle in gegevens["controles"]:
        if not isinstance(controle, dict) or "url" not in controle:
            raise ValueError("elke controle heeft minstens een naam en een url")
    os.makedirs(os.path.dirname(pad), exist_ok=True)
    tijdelijk = pad + ".tmp"
    with open(tijdelijk, "w", encoding="utf-8") as f:
        json.dump(gegevens, f, indent=2, ensure_ascii=False)
    os.chmod(tijdelijk, 0o640)
    os.replace(tijdelijk, pad)
    return pad


def controleer(wijzigingen, huidig):
    """Controleer wat de gebruiker intypt voordat we iets opslaan."""
    fouten = {}
    for sleutel in ("TRUNK_NUMMER", "MIJN_MOBIEL"):
        waarde = wijzigingen.get(sleutel)
        if waarde is None:
            continue
        kaal = re.sub(r"[^0-9]", "", waarde)
        if waarde.strip() and not re.fullmatch(r"[0-9]{8,15}", kaal):
            fouten[sleutel] = "alleen cijfers, internationaal zonder plus (bijvoorbeeld 31612345678)"
        elif waarde.strip() and not kaal.startswith("3"):
            fouten[sleutel] = "een Nederlands nummer begint met 31; laat de plus en de nullen weg"
        else:
            wijzigingen[sleutel] = kaal

    url = wijzigingen.get("JARVIS_BREIN_URL")
    if url is not None and url.strip() and not re.match(r"^https?://", url.strip()):
        fouten["JARVIS_BREIN_URL"] = "een adres begint met http:// of https://"

    for sleutel in ("JARVIS_STILTE_MS", "JARVIS_DREMPEL", "JARVIS_MAX_GESPREK"):
        waarde = wijzigingen.get(sleutel)
        if waarde is None or not str(waarde).strip():
            continue
        if not re.fullmatch(r"[0-9]{1,6}", str(waarde).strip()):
            fouten[sleutel] = "een getal in milliseconden of seconden"
    stilte = str(wijzigingen.get("JARVIS_STILTE_MS") or "").strip()
    if stilte.isdigit() and int(stilte) < 200:
        fouten["JARVIS_STILTE_MS"] = "onder de 200 ms kapt hij je midden in een zin af"

    stem = wijzigingen.get("JARVIS_STEM")
    if stem and stem not in ("lokaal", "remote"):
        fouten["JARVIS_STEM"] = "kies lokaal of remote"

    model = wijzigingen.get("JARVIS_WHISPER_MODEL")
    if model:
        if model not in ("base", "small"):
            fouten["JARVIS_WHISPER_MODEL"] = "kies base of small"
        else:
            wijzigingen["JARVIS_WHISPER_MODEL"] = "/opt/jarvis/modellen/ggml-%s.bin" % model
    return fouten


def verwerk(wijzigingen, log=print):
    """Alles doen wat er bij opslaan hoort; geeft een verslag terug."""
    huidig = lees_env()
    wijzigingen = {k: v for k, v in wijzigingen.items() if v is not None}
    # Een leeg geheim betekent: laat staan wat er staat.
    for sleutel in list(wijzigingen):
        if _is_geheim(sleutel) and not str(wijzigingen[sleutel]).strip():
            wijzigingen.pop(sleutel)
    fouten = controleer(wijzigingen, huidig)
    if fouten:
        return {"gelukt": False, "fouten": fouten}

    namen = {sleutel for sleutel, *_ in VELDEN}
    wijzigingen = {k: v for k, v in wijzigingen.items() if k in namen}
    if not wijzigingen:
        return {"gelukt": True, "gemeld": ["niets gewijzigd"]}

    schrijf_env(wijzigingen)
    log("instellingen bijgewerkt: %s" % ", ".join(sorted(wijzigingen)))

    nieuw = dict(huidig)
    nieuw.update(wijzigingen)
    bestanden = schrijf_asterisk(nieuw)
    mislukt = [naam for naam, ok, _ in bestanden if not ok]

    herstart_nodig = any(sleutel in wijzigingen for sleutel in
                         ("JARVIS_WHISPER_MODEL", "JARVIS_STEM"))
    stappen = asterisk_herladen(log)
    return {
        "gelukt": True,
        "gemeld": ["instellingen opgeslagen"],
        "asterisk_bestanden": ["%s%s" % (naam, "" if ok else " (mislukt: %s)" % melding)
                               for naam, ok, melding in bestanden],
        "asterisk_mislukt": mislukt,
        "asterisk_stappen": ["%s: %s" % (stap, "ok" if ok else "mislukt") for stap, ok in stappen],
        "herstart_nodig": herstart_nodig,
        "tijd": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def samenvatting():
    """Wat de pagina nodig heeft: de geldende waarde per veld (nooit een geheim).

    De waarden komen uit dezelfde bron als de dienst zelf, dus inclusief de
    standaarden: een leeg veld betekent echt leeg, niet "niet in dit bestand".
    """
    try:
        from .config import Config
        cfg = Config()
    except Exception:
        cfg = None
    uit = {"velden": {}, "waarden": {}, "in_bestand": {}}
    bestand = lees_env()
    for sleutel, label, groep, soort, uitleg in VELDEN:
        waarde = (cfg[sleutel] if cfg is not None else bestand.get(sleutel, "")) or ""
        uit["velden"][sleutel] = {"label": label, "groep": groep, "soort": soort,
                                  "uitleg": uitleg, "geheim": _is_geheim(sleutel)}
        uit["in_bestand"][sleutel] = sleutel in bestand
        if _is_geheim(sleutel):
            uit["waarden"][sleutel] = "ingevuld" if waarde else ""
        elif sleutel == "JARVIS_WHISPER_MODEL":
            uit["waarden"][sleutel] = os.path.basename(str(waarde)).replace("ggml-", "").replace(".bin", "")
        else:
            uit["waarden"][sleutel] = waarde
    uit["groepen"] = GROEPEN
    return uit


def main(argv=None):
    """Kleine opdrachtregel, zodat de installatie dezelfde weg gebruikt als de pagina."""
    import argparse
    ontleder = argparse.ArgumentParser(description="Jarvis-instellingen")
    ontleder.add_argument("--asterisk", action="store_true",
                          help="schrijf /etc/asterisk/*.conf opnieuw uit de sjablonen")
    ontleder.add_argument("--toon", action="store_true", help="toon wat er is ingevuld")
    ontleder.add_argument("--status", action="store_true", help="vraag Asterisk naar de trunk")
    argumenten = ontleder.parse_args(argv)

    if argumenten.asterisk:
        for naam, gelukt, melding in schrijf_asterisk():
            print("  %s %s%s" % ("ok   " if gelukt else "[fout]", naam,
                                 "" if gelukt else " — " + melding))
        return 0
    if argumenten.status:
        print(json.dumps(asterisk_status(), indent=2, ensure_ascii=False))
        return 0
    if argumenten.toon:
        gegevens = samenvatting()
        for sleutel, waarde in gegevens["waarden"].items():
            print("%-26s %s" % (sleutel, waarde or "(leeg)"))
        return 0
    ontleder.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
