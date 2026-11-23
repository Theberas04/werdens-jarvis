"""Waakhond: kijkt of je diensten nog leven en laat Jarvis bellen als dat niet zo is.

Draait als systemd-timer (elke minuut). Elke controle is een URL die een
bepaalde statuscode moet geven. Gaat er één van goed naar stuk, dan belt Jarvis
en leest voor wat er aan de hand is. Herstelt de dienst, dan belt hij alleen als
je dat per controle hebt aangezet.

Instellingen in /etc/jarvis/waken.json:

{
  "rust_seconden": 1800,
  "stil_van": "23:30",
  "stil_tot": "07:00",
  "controles": [
    {"naam": "Werdens Monitor", "url": "http://192.168.1.50:8080/health",
     "code": 200, "tijd": 8, "dringend": true},
    {"naam": "VoorraadThuis", "url": "http://192.168.1.51:8000/", "code": 200,
     "tijd": 8, "meld_herstel": true}
  ]
}
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

STANDAARD_PADEN = {
    "waken": "/etc/jarvis/waken.json",
    "staat": "/var/lib/jarvis/wacht-staat.json",
    "jarvis": "http://127.0.0.1:8099",
}


def lees_json(pad, standaard):
    try:
        with open(pad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return standaard


def bewaar_json(pad, gegevens):
    os.makedirs(os.path.dirname(pad), exist_ok=True)
    tijdelijk = pad + ".tmp"
    with open(tijdelijk, "w", encoding="utf-8") as f:
        json.dump(gegevens, f, indent=2, ensure_ascii=False)
    os.replace(tijdelijk, pad)


def controleer(controle):
    """Geeft (goed, detail) terug."""
    url = controle["url"]
    verwacht = int(controle.get("code", 200))
    tijd = float(controle.get("tijd", 8))
    verzoek = urllib.request.Request(url, headers={"User-Agent": "Jarvis-waakhond/1.0"})
    try:
        with urllib.request.urlopen(verzoek, timeout=tijd) as antwoord:
            code = antwoord.status
            if code == verwacht:
                return True, "HTTP %d" % code
            return False, "HTTP %d (verwachtte %d)" % (code, verwacht)
    except urllib.error.HTTPError as fout:
        if fout.code == verwacht:
            return True, "HTTP %d" % fout.code
        return False, "HTTP %d (verwachtte %d)" % (fout.code, verwacht)
    except urllib.error.URLError as fout:
        return False, "niet bereikbaar (%s)" % (fout.reason,)
    except Exception as fout:
        return False, "fout: %r" % (fout,)


def in_stille_uren(cfg):
    try:
        van = cfg.get("stil_van") or ""
        tot = cfg.get("stil_tot") or ""
        if not van or not tot:
            return False
        nu = datetime.datetime.now().strftime("%H:%M")
        if van <= tot:
            return van <= nu < tot
        return nu >= van or nu < tot
    except Exception:
        return False


def meld(jarvis_url, tekst, reden, nummer=""):
    lading = json.dumps({"tekst": tekst, "reden": reden, "nummer": nummer}).encode()
    verzoek = urllib.request.Request(
        jarvis_url.rstrip("/") + "/api/melding", data=lading,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(verzoek, timeout=120) as antwoord:
        return json.loads(antwoord.read().decode("utf-8", "replace"))


def een_ronde(cfg, staat, proef=False, nu=None, jarvis_url=None):
    """Één controle-ronde; geeft de nieuwe staat en wat er gebeurde.

    Een storing wordt één keer gemeld: bij de overgang van goed naar stuk, of
    bij de allereerste meting als die meteen stuk is (dan is er iets mis sinds
    de installatie en wil je het ook weten). De tijd van de laatste melding
    blijft bewaard, anders zou de rusttijd nooit werken.
    """
    nu = nu or datetime.datetime.now()
    jarvis_url = jarvis_url or cfg.get("jarvis_url") or STANDAARD_PADEN["jarvis"]
    gebeurtenissen = []
    for controle in cfg.get("controles", []):
        naam = controle.get("naam") or controle.get("url", "?")
        goed, detail = controleer(controle)
        vorige = dict(staat.get(naam, {}))
        was_goed = vorige.get("goed")
        staat[naam] = dict(vorige)
        staat[naam].update({"goed": goed, "detail": detail,
                            "laatst": nu.isoformat(timespec="seconds")})
        eerste = was_goed is None
        if eerste:
            gebeurtenissen.append({"naam": naam, "soort": "eerste-meting",
                                   "detail": detail, "goed": goed})

        if not goed and was_goed is not False:
            rust = int(controle.get("rust_seconden", cfg.get("rust_seconden", 1800)))
            laatste = float(vorige.get("laatste_melding_ts") or 0)
            if nu.timestamp() - laatste < rust and not eerste:
                gebeurtenissen.append({"naam": naam, "soort": "stil-herstoring",
                                       "detail": detail, "goed": goed})
                continue
            if in_stille_uren(cfg) and not controle.get("dringend"):
                gebeurtenissen.append({"naam": naam, "soort": "stil-nacht",
                                       "detail": detail, "goed": goed})
                continue
            tekst = "Tom, met Jarvis. %s reageert niet meer. %s." % (
                naam, detail.rstrip("."))
            if controle.get("extra"):
                tekst += " " + str(controle["extra"])
            if proef:
                gebeurtenissen.append({"naam": naam, "soort": "zou-bellen",
                                       "tekst": tekst, "goed": goed})
            else:
                try:
                    antwoord = meld(jarvis_url, tekst, reden="waakhond")
                    staat[naam]["laatste_melding_ts"] = nu.timestamp()
                    gebeurtenissen.append({"naam": naam, "soort": "gebeld",
                                           "tekst": tekst, "antwoord": antwoord,
                                           "goed": goed})
                except Exception as fout:
                    gebeurtenissen.append({"naam": naam, "soort": "bel-fout",
                                           "tekst": tekst, "fout": str(fout), "goed": goed})
            continue

        if was_goed is False and goed:
            tekst = "Tom, met Jarvis. %s doet het weer." % naam
            if not controle.get("meld_herstel"):
                gebeurtenissen.append({"naam": naam, "soort": "hersteld",
                                       "detail": detail, "goed": goed})
                continue
            if proef:
                gebeurtenissen.append({"naam": naam, "soort": "zou-bellen-herstel",
                                       "tekst": tekst, "goed": goed})
                continue
            try:
                antwoord = meld(jarvis_url, tekst, reden="herstel")
                gebeurtenissen.append({"naam": naam, "soort": "herstel-gebeld",
                                       "tekst": tekst, "antwoord": antwoord,
                                       "goed": goed})
            except Exception as fout:
                gebeurtenissen.append({"naam": naam, "soort": "bel-fout",
                                       "tekst": tekst, "fout": str(fout), "goed": goed})
    return staat, gebeurtenissen


def main(argv=None):
    ontleder = argparse.ArgumentParser(description="Jarvis-waakhond")
    ontleder.add_argument("--waken", default=STANDAARD_PADEN["waken"])
    ontleder.add_argument("--staat", default=STANDAARD_PADEN["staat"])
    ontleder.add_argument("--jarvis", default="", help="adres van jarvisd (standaard uit waken.json)")
    ontleder.add_argument("--proef", action="store_true",
                          help="laat zien wat er zou gebeuren, zonder te bellen")
    ontleder.add_argument("--toon", action="store_true", help="toon de huidige staat")
    argumenten = ontleder.parse_args(argv)

    cfg = lees_json(argumenten.waken, {"controles": []})
    staat = lees_json(argumenten.staat, {})

    if argumenten.toon:
        print(json.dumps(staat, indent=2, ensure_ascii=False))
        return 0
    if not cfg.get("controles"):
        print("[fout] geen controles in %s" % argumenten.waken)
        return 2

    nieuwe_staat, gebeurtenissen = een_ronde(cfg, staat, proef=argumenten.proef,
                                              jarvis_url=argumenten.jarvis)
    bewaar_json(argumenten.staat, nieuwe_staat)
    for g in gebeurtenissen:
        print(json.dumps(g, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())