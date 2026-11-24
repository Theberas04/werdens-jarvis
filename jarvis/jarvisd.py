"""jarvisd — de dienst die de telefoonlijn laat praten.

Wat hij doet:

* luistert op de AudioSocket-poort waarop Asterisk gesprekken aankondigt;
* houdt per gesprek een beurtwisseling bij: spraak erin (whisper), een antwoord
  van het brein (Hermes of een andere OpenAI-compatible endpoint), spraak eruit
  (piper);
* kan zelf bellen: een melding voorlezen en daarna een gesprek beginnen;
* heeft een statuspagina met knoppen om te bellen en te testen.

Starten:  python3 -m jarvis.jarvisd        (of: systemctl start jarvisd)
"""
import argparse
import datetime
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
import traceback

from . import pagina
from .brein import Brein, BreinFout, is_afscheid
from .config import Config
from .stem import Stem, StemFout, slin_naar_wav
from .telefoon import AsteriskAMI, AudioSocketServer, Gesprek

MAX_GESPREKKEN = 60   # hoeveel gesprekken de statuspagina onthoudt


class Jarvis:
    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self.slot = threading.Lock()
        self.gesprekken = []
        self.stem = Stem(self.cfg, self.log)
        self.brein = Brein(self.cfg, self.log)
        self.ami = AsteriskAMI(
            self.cfg["AMI_HOST"], self.cfg.int("AMI_POORT"),
            self.cfg["AMI_GEBRUIKER"], self.cfg["AMI_WACHTWOORD"], self.log,
        )
        self.server = None
        self.web = None
        self._start = time.time()
        self._laatste_melding = None
        self._melding_teller = 0
        self._asterisk = None

    # ------------------------------------------------------------------ log

    def log(self, tekst):
        regel = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tekst)
        print(regel, flush=True)
        pad = self.cfg["JARVIS_LOG"]
        try:
            os.makedirs(os.path.dirname(pad), exist_ok=True)
            with open(pad, "a", encoding="utf-8") as f:
                f.write(regel + "\n")
        except OSError:
            pass

    # ---------------------------------------------------------------- start

    def start(self):
        self.log("Jarvis start (stem=%s, brein=%s)" % (self.cfg["JARVIS_STEM"], self.cfg["JARVIS_BREIN_URL"]))
        self.stem.start()
        host, poort = self.cfg.luister_adres()
        self.server = AudioSocketServer(host, poort, self.afhandelen, self.log)
        self.server.start()
        web_host, web_poort = self.cfg.web_adres()
        self.web = pagina.start_web(self, web_host, web_poort)
        self.log("statuspagina op http://%s:%d/" % (web_host if web_host != "0.0.0.0" else "127.0.0.1", web_poort))

    def stop(self):
        self.log("Jarvis stopt")
        if self.server:
            self.server.stop()
        self.stem.stop()

    # ------------------------------------------------------------- gesprek

    def afhandelen(self, g: Gesprek):
        """Eén telefoongesprek, van opnemen tot ophangen."""
        begin = time.time()
        g.verwerk(g.lees(time_out=2.0))
        self.log("gesprek van %s (uuid %s)" % (g.adres[0], g.uuid[:8] or "?"))
        gesprek = {
            "begon": datetime.datetime.now().isoformat(timespec="seconds"),
            "van": g.adres[0],
            "uuid": g.uuid,
            "beurten": [],
            "duur": 0,
        }
        try:
            self._beurt_wissel(g, gesprek, begroeting=True)
        except Exception:
            self.log("gesprek mislukte:\n" + traceback.format_exc())
        finally:
            gesprek["duur"] = round(time.time() - begin, 1)
            with self.slot:
                self.gesprekken.append(gesprek)
                self.gesprekken = self.gesprekken[-MAX_GESPREKKEN:]
            self.log("gesprek klaar na %.1f s, %d beurt(en)" % (gesprek["duur"], len(gesprek["beurten"])))

    def _beurt_wissel(self, g: Gesprek, gesprek, begroeting=False):
        max_seconden = self.cfg.int("JARVIS_MAX_GESPREK")
        self.brein.reset()
        if begroeting:
            tekst = self.cfg["JARVIS_BEGROETING"]
            self._zeg(g, tekst)
            gesprek["beurten"].append({"rol": "jarvis", "tekst": tekst})
        while not g.gestopt and (time.time() - self._gesprek_start(g)) < max_seconden:
            spraak = self._luister(g)
            if spraak is None:
                return
            if not spraak.strip():
                self._zeg(g, "Ik hoorde niets. Zeg het nog eens.")
                continue
            gesprek["beurten"].append({"rol": "tom", "tekst": spraak})
            self.log("  Tom: %s" % spraak)
            if is_afscheid(spraak):
                self._zeg(g, "Goed, tot ziens Tom.")
                g.hang_op()
                return
            try:
                antwoord, seconden = self.brein.vraag(spraak)
            except BreinFout as fout:
                self.log("  brein: %s" % fout)
                self._zeg(g, "Ik kan mijn taalmodel even niet bereiken. Probeer het straks nog eens.")
                continue
            self.log("  Jarvis (%.1f s): %s" % (seconden, antwoord))
            gesprek["beurten"].append({"rol": "jarvis", "tekst": antwoord, "seconden": seconden})
            self._zeg(g, antwoord)
        if not g.gestopt:
            self._zeg(g, "We zijn al een tijd aan de lijn, ik hang op. Bel gerust terug.")
            g.hang_op()

    def _gesprek_start(self, g):
        if not hasattr(g, "_begon"):
            g._begon = time.time()
        return g._begon

    def _zeg(self, g: Gesprek, tekst):
        try:
            slin = self.stem.tts(tekst)
        except StemFout as fout:
            self.log("  spraak uit mislukt: %s" % fout)
            return
        g.zeg(slin)

    def _luister(self, g: Gesprek):
        """Wacht op één uiting en geef de tekst terug (None = gesprek over)."""
        drempel = self.cfg.int("JARVIS_DREMPEL")
        stilte_doel = self.cfg.int("JARVIS_STILTE_MS") / 1000.0
        min_spraak = self.cfg.int("JARVIS_MIN_SPRAAK_MS") / 1000.0
        voorloop = int(0.3 * 1000 / 20)      # 300 ms terug bewaren
        max_duur = 25.0
        buffer = []
        ring = []
        self._frames = 0
        bezig = False
        stil_ms = 0
        spraak_ms = 0
        begin = None
        while not g.gestopt:
            bericht = g.verwerk(g.lees(time_out=0.5))
            if bericht is None:
                self.log("  luisteren: verbinding is weg")
                return None
            soort, lading = bericht
            if soort == "stilte":
                # De lijn is even stil. Dat is óók stilte in de spraak: zonder
                # deze teller wacht een gesprek eeuwig op een laatste blokje dat
                # nooit komt, want de beller zwijgt gewoon.
                if bezig:
                    stil_ms += 500
                    if stil_ms >= stilte_doel * 1000 and spraak_ms >= min_spraak * 1000:
                        break
                continue
            if soort != 0x10:
                if soort == 0x03 and lading == b"0":
                    g.hang_op()
                    return None
                continue
            ring.append(lading)
            ring = ring[-voorloop:]
            niveau = self._niveau(lading)
            if self.cfg.ja("JARVIS_DEBUG"):
                self._frames = getattr(self, "_frames", 0) + 1
                if self._frames <= 8 or self._frames % 20 == 0 or niveau > drempel:
                    self.log("  debug: frame %d, %d bytes, niveau %d%s" % (
                        self._frames, len(lading), niveau, "  <- spraak" if niveau > drempel else ""))
            if niveau > drempel:
                if not bezig:
                    bezig = True
                    begin = time.time()
                    buffer = list(ring)
                spraak_ms += 20
                stil_ms = 0
                buffer.append(lading)
            elif bezig:
                stil_ms += 20
                buffer.append(lading)
                if stil_ms >= stilte_doel * 1000 and spraak_ms >= min_spraak * 1000:
                    break
                if time.time() - begin > max_duur:
                    break
        if not buffer:
            return ""
        return self._beluister(buffer)

    @staticmethod
    def _niveau(lading: bytes) -> int:
        """Ruwe luidheid (RMS) van een blokje 8 kHz slin."""
        import array
        if len(lading) < 2:
            return 0
        waarden = array.array("h")
        waarden.frombytes(lading[: len(lading) - (len(lading) % 2)])
        if not waarden:
            return 0
        # elke vierde waarde is genoeg voor een niveau-schatting
        som = 0
        n = 0
        for i in range(0, len(waarden), 4):
            som += waarden[i] * waarden[i]
            n += 1
        return int((som / max(n, 1)) ** 0.5)

    def _beluister(self, blokken):
        tijdelijk = tempfile.mkdtemp(prefix="jarvis-opname-")
        try:
            pad = os.path.join(tijdelijk, "opname.wav")
            slin_naar_wav(b"".join(blokken), pad)
            begin = time.time()
            tekst = self.stem.stt(pad)
            self.log("  gehoord in %.1f s: %s" % (time.time() - begin, tekst or "(niets)"))
            return tekst
        except StemFout as fout:
            self.log("  spraak in mislukt: %s" % fout)
            return ""
        finally:
            shutil.rmtree(tijdelijk, ignore_errors=True)

    # -------------------------------------------------------------- bellen

    def bel_melding(self, tekst: str, reden: str = "", nummer: str = ""):
        """Bel Tom en lees de melding voor."""
        nummer = nummer or self.cfg["MIJN_MOBIEL"]
        if not nummer:
            return {"gelukt": False, "fout": "geen mobiel nummer ingesteld (MIJN_MOBIEL)"}
        if self.cfg.ja("JARVIS_GEEN_BELLEN"):
            self.log("melding (niet gebeld, JARVIS_GEEN_BELLEN=1): %s" % tekst)
            self._laatste_melding = {
                "tijd": datetime.datetime.now().isoformat(timespec="seconds"),
                "tekst": tekst, "reden": reden or "test", "gelukt": True, "gesimuleerd": True,
            }
            return {"gelukt": True, "gesimuleerd": True, "melding": tekst}
        hint = (self.cfg.get("JARVIS_MELDING_HINT") or "").strip()
        voorlezen = tekst.strip()
        if hint:
            voorlezen = voorlezen + " " + hint
        try:
            slin = self.stem.tts(voorlezen)
        except StemFout as fout:
            return {"gelukt": False, "fout": "spraak maken mislukte: %s" % fout}
        self._melding_teller += 1
        naam = "melding-%s-%03d" % (datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), self._melding_teller)
        doelmap = "/var/lib/asterisk/sounds/jarvis"
        try:
            os.makedirs(doelmap, exist_ok=True)
            slin_naar_wav(slin, os.path.join(doelmap, naam + ".wav"))
        except OSError as fout:
            return {"gelukt": False, "fout": "kon de melding niet opslaan: %s" % fout}
        kanaal_variabele = {"JARVIS_MELDING": naam}
        try:
            gelukt, bericht = self.ami.bel(
                nummer, context="jarvis-melding", wachtrij="s",
                beller=self.cfg["TRUNK_NUMMER"], variabelen=kanaal_variabele,
            )
        except Exception as fout:
            return {"gelukt": False, "fout": "AMI: %s" % fout}
        self._laatste_melding = {
            "tijd": datetime.datetime.now().isoformat(timespec="seconds"),
            "tekst": tekst, "reden": reden, "gelukt": gelukt,
        }
        self.log("melding naar %s: %s (%s)" % (nummer, tekst[:80], "ok" if gelukt else bericht))
        return {"gelukt": gelukt, "fout": "" if gelukt else bericht, "melding": tekst}

    def bel_gesprek(self, nummer: str = ""):
        """Bel Tom voor een gesprek, zonder melding vooraf."""
        nummer = nummer or self.cfg["MIJN_MOBIEL"]
        if not nummer:
            return {"gelukt": False, "fout": "geen mobiel nummer ingesteld (MIJN_MOBIEL)"}
        if self.cfg.ja("JARVIS_GEEN_BELLEN"):
            return {"gelukt": True, "gesimuleerd": True}
        try:
            gelukt, bericht = self.ami.bel(
                nummer, context="jarvis-gesprek", wachtrij="s",
                beller=self.cfg["TRUNK_NUMMER"],
            )
        except Exception as fout:
            return {"gelukt": False, "fout": "AMI: %s" % fout}
        return {"gelukt": gelukt, "fout": "" if gelukt else bericht}

    # -------------------------------------------------------------- status

    def status(self, uitgebreid=False):
        uit = {
            "naam": "Jarvis",
            "versie": "1.0.0",
            "draait_sind": round(time.time() - self._start, 1),
            "telefoon_ingericht": self.cfg.telefoon_ingericht(),
            "trunk_nummer": self.cfg["TRUNK_NUMMER"],
            "mijn_mobiel": _maskeer(self.cfg["MIJN_MOBIEL"]),
            "brein": {"url": self.cfg["JARVIS_BREIN_URL"], "model": self.cfg["JARVIS_BREIN_MODEL"],
                      "naam": self.brein_naam()},
            "stem_modus": self.cfg["JARVIS_STEM"],
            "gesprekken": list(reversed(self.gesprekken[-10:])),
            "laatste_melding": self._laatste_melding,
            "asterisk": self.asterisk_info(),
        }
        if uitgebreid:
            uit["stem"] = self._veilig(self.stem.gezondheid)
            uit["brein"]["proef"] = self.brein.gezondheid()
        return uit

    def asterisk_info(self, max_leeftijd=5.0):
        """Status van Asterisk en de trunk, met een korte cache.

        De pagina vraagt dit elke paar seconden op; twee opdrachten naar
        Asterisk per keer is zonde en traag.
        """
        nu = time.time()
        if self._asterisk and nu - self._asterisk["tijd"] < max_leeftijd:
            return self._asterisk["waarde"]
        from . import instellingen
        waarde = self._veilig(instellingen.asterisk_status)
        self._asterisk = {"tijd": nu, "waarde": waarde}
        return waarde

    def breinen(self):
        """Het actieve brein en de bewaarde lijst (zonder geheimen)."""
        from . import instellingen
        return self._veilig(instellingen.breinen_overzicht)

    def brein_naam(self):
        """Naam van het actieve brein (leeg als het niet in de lijst staat)."""
        from . import instellingen
        try:
            return instellingen.breinen_overzicht()["actief"]["naam"]
        except Exception:
            return ""

    def brein_kies(self, naam):
        """Wisselen naar een bewaard brein; meteen in gebruik, geen herstart."""
        from . import instellingen
        uit = instellingen.gebruik_brein(naam, log=self.log)
        if uit.get("gelukt"):
            self.cfg.herlaad()
        return uit

    def brein_bewaar(self, naam, url=None, model=None, sleutel=None):
        from . import instellingen
        return instellingen.bewaar_brein(naam, url, model, sleutel)

    def brein_verwijder(self, naam):
        from . import instellingen
        return instellingen.verwijder_brein(naam)

    def brein_proef(self, naam=None, url=None, model=None, sleutel=None):
        """Eén echte vraag naar een brein: antwoordt het, en hoe snel?"""
        from . import instellingen
        if naam:
            rij = next((r for r in instellingen.lees_breinen()
                        if r["naam"].lower() == str(naam).strip().lower()), None)
            if not rij:
                return {"klaar": False, "fout": "onbekend brein: %s" % naam}
            url, model, sleutel = rij["url"], rij["model"], rij["sleutel"]
        proef = instellingen.test_brein(url, model, sleutel)
        self.log("breinproef %s: %s" % (naam or url or "actief",
                                        "ok in %ss" % proef.get("seconden") if proef.get("klaar")
                                        else "mislukt (%s)" % proef.get("fout")))
        return proef

    def instellingen_opslaan(self, velden, waken=None):
        """Wat de instellingenpagina doet: opschrijven, toepassen, controleren."""
        from . import instellingen
        verslag = instellingen.verwerk(velden, log=self.log)
        if not verslag.get("gelukt"):
            return verslag

        if waken is not None and str(waken).strip():
            try:
                instellingen.schrijf_waken(waken)
                verslag.setdefault("gemeld", []).append("waakhond-controles opgeslagen")
            except (ValueError, OSError) as fout:
                return {"gelukt": False, "fouten": {"waken": str(fout)}}

        # Nieuwe waarden meteen gebruiken; een herstart is niet nodig.
        self.cfg.herlaad()
        self.ami = AsteriskAMI(
            self.cfg["AMI_HOST"], self.cfg.int("AMI_POORT"),
            self.cfg["AMI_GEBRUIKER"], self.cfg["AMI_WACHTWOORD"], self.log,
        )
        self._asterisk = None
        self.log("instellingen toegepast (trunk=%s, mobiel=%s, lijn %s)" % (
            self.cfg["TRUNK_NUMMER"] or "-", _maskeer(self.cfg["MIJN_MOBIEL"]) or "-",
            "compleet" if self.cfg.telefoon_ingericht() else "onvolledig"))

        ast = self.asterisk_info(max_leeftijd=0)
        verslag["trunk_ok"] = bool(ast.get("trunk_geregistreerd"))
        verslag["trunk_detail"] = ast.get("detail", "")
        verslag["ingericht"] = self.cfg.telefoon_ingericht()
        try:
            proef = self.brein.gezondheid()
            verslag["proef"] = {"klaar": bool(proef.get("klaar")), "fout": proef.get("fout", "")}
        except Exception as fout:
            verslag["proef"] = {"klaar": False, "fout": str(fout)[:200]}
        return verslag

    @staticmethod
    def _veilig(functie):
        try:
            return functie()
        except Exception as fout:
            return {"klaar": False, "fout": str(fout)[:200]}



def _maskeer(nummer: str) -> str:
    if not nummer or len(nummer) < 6:
        return nummer
    return nummer[:4] + "****" + nummer[-2:]


def main(argv=None):
    ontleder = argparse.ArgumentParser(description="Jarvis: telefonische assistent")
    ontleder.add_argument("--zelftest", action="store_true",
                          help="controleer spraak, brein en Asterisk en stop weer")
    ontleder.add_argument("--bel", metavar="TEKST",
                          help="bel Tom en lees deze melding voor")
    ontleder.add_argument("--gesprek", action="store_true",
                          help="bel Tom voor een gesprek")
    argumenten = ontleder.parse_args(argv)

    jarvis = Jarvis()
    if argumenten.zelftest:
        jarvis.stem.start()
        print(json.dumps(jarvis.status(uitgebreid=True), indent=2, ensure_ascii=False))
        jarvis.stem.stop()
        return 0
    if argumenten.bel:
        jarvis.stem.start()
        print(json.dumps(jarvis.bel_melding(argumenten.bel, reden="handmatig"), indent=2,
                         ensure_ascii=False))
        jarvis.stem.stop()
        return 0
    if argumenten.gesprek:
        print(json.dumps(jarvis.bel_gesprek(), indent=2, ensure_ascii=False))
        return 0

    jarvis.start()
    stop = threading.Event()

    def netjes_stoppen(*_):
        stop.set()

    signal.signal(signal.SIGTERM, netjes_stoppen)
    signal.signal(signal.SIGINT, netjes_stoppen)
    while not stop.is_set():
        stop.wait(1.0)
    jarvis.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())