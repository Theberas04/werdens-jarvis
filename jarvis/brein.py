"""Het brein achter de stem.

Elke OpenAI-compatible chat-endpoint werkt hier: Hermes (die kan echt iets
doen: diensten nakijken, logs lezen, een container herstarten), een eigen
llama.cpp-engine, of een cloudmodel.

De telefoon stelt zijn eigen eisen: korte zinnen, geen markdown, geen
opsommingen, en één vraag tegelijk. Daarom staat dat in de systeemafspraak en
niet in elke gebruikersvraag.
"""
import json
import re
import time
import urllib.error
import urllib.request

SYSTEEM = """Je bent Jarvis, de assistent van Tom Werdens. Je spreekt hem nu via de
telefoon. Spreek Nederlands, in korte zinnen van hooguit twee of drie zinnen.
Geen markdown, geen opsommingen, geen sterretjes, geen emoji's, geen Engelse
termen als er een Nederlands woord is: alles wat je zegt wordt voorgelezen.

Je mag gereedschap gebruiken om iets op te zoeken of te controleren (status van
diensten, een container, een bestand, het weer). Als je iets hebt nagekeken,
zeg dan het resultaat en niet hoe je eraan kwam.

Weet je iets niet zeker, zeg dat dan in één zin en bied aan om het uit te
zoeken. Verzin nooit een status, een tijd of een cijfer.

Sluit af met wat Tom nu kan zeggen of doen, als dat nuttig is. Hou het gesprek
gaande met hooguit één korte vraag."""

# Wat een gesprek beeindigt; de assistent hoeft dat niet te weten.
STOPWOORDEN = (
    "doei", "dag", "tot ziens", "ophangen", "hang op", "einde gesprek",
    "bedankt voor je hulp", "dat was het", "niets meer", "tot straks",
)


def is_afscheid(tekst: str) -> bool:
    klein = (tekst or "").lower().strip(" .!?,")
    if not klein:
        return False
    return any(klein == w or klein.startswith(w) or klein.endswith(w) for w in STOPWOORDEN)


class BreinFout(RuntimeError):
    pass


class Brein:
    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self._geschiedenis = []
        self.laatste_fout = ""

    def _url(self):
        basis = self.cfg["JARVIS_BREIN_URL"].rstrip("/")
        if not basis.endswith("/v1"):
            basis += "/v1"
        return basis + "/chat/completions"

    def vraag(self, tekst: str) -> tuple:
        """Stel een vraag; geeft (antwoord, seconden)."""
        self._geschiedenis.append({"role": "user", "content": tekst})
        berichten = [{"role": "system", "content": SYSTEEM}] + self._geschiedenis[-12:]
        lading = {
            "model": self.cfg["JARVIS_BREIN_MODEL"],
            "messages": berichten,
            "stream": False,
        }
        kop = {"Content-Type": "application/json"}
        if self.cfg["JARVIS_BREIN_SLEUTEL"]:
            kop["Authorization"] = "Bearer " + self.cfg["JARVIS_BREIN_SLEUTEL"]
        verzoek = urllib.request.Request(
            self._url(), data=json.dumps(lading).encode(), headers=kop
        )
        begin = time.time()
        try:
            with urllib.request.urlopen(
                verzoek, timeout=self.cfg.int("JARVIS_BREIN_TIJD")
            ) as antwoord:
                gegevens = json.loads(antwoord.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as fout:
            detail = fout.read()[:200].decode("utf-8", "replace")
            self.laatste_fout = "brein gaf HTTP %s: %s" % (fout.code, detail)
            raise BreinFout(self.laatste_fout)
        except Exception as fout:
            self.laatste_fout = "brein onbereikbaar: %r" % (fout,)
            raise BreinFout(self.laatste_fout)

        try:
            antwoord_tekst = gegevens["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            raise BreinFout("brein gaf een antwoord zonder tekst: %s" % str(gegevens)[:200])

        antwoord_tekst = self._spreekbaar(antwoord_tekst)
        self._geschiedenis.append({"role": "assistant", "content": antwoord_tekst})
        if len(self._geschiedenis) > 24:
            self._geschiedenis = self._geschiedenis[-24:]
        return antwoord_tekst, round(time.time() - begin, 2)

    @staticmethod
    def _spreekbaar(tekst: str) -> str:
        """Maak van een geschreven antwoord iets wat je kunt voorlezen."""
        tekst = re.sub(r"```.*?```", " ", tekst, flags=re.S)
        tekst = re.sub(r"^\s*[-*•]\s+", "", tekst, flags=re.M)
        tekst = re.sub(r"^\s*#+\s*", "", tekst, flags=re.M)
        tekst = re.sub(r"\*\*([^*]+)\*\*", r"\1", tekst)
        tekst = re.sub(r"[*_`]+", "", tekst)
        tekst = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", tekst)
        tekst = re.sub(r"\s+", " ", tekst)
        return tekst.strip()

    def reset(self):
        self._geschiedenis = []

    def gezondheid(self) -> dict:
        """Eén echte korte vraag, met de klok erbij."""
        bewaard = list(self._geschiedenis)
        self._geschiedenis = []
        try:
            antwoord, seconden = self.vraag("Antwoord in één woord: ben je er?")
            return {"klaar": True, "seconden": seconden, "antwoord": antwoord[:80]}
        except Exception as fout:
            return {"klaar": False, "fout": str(fout)[:200]}
        finally:
            self._geschiedenis = bewaard