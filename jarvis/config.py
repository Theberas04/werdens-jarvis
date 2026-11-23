"""Instellingen van Jarvis.

De waarden komen uit /etc/jarvis/jarvis.env (regel ``SLEUTEL=waarde``), en
omgevingsvariabelen gaan daar overheen. Zo kan een test op een andere machine
draaien zonder die bestand aan te raken.
"""
import os
from pathlib import Path

ENV_PADEN = [
    Path("/etc/jarvis/jarvis.env"),
    Path(__file__).resolve().parent.parent / "jarvis.env",
]


def _lees_alles(extra_uit_omgeving=None):
    """De vaste paden, plus het bestand dat JARVIS_ENV_PAD aanwijst.

    In de container is dat hetzelfde bestand; bij een test of een installatie
    op een andere plek kan het een eigen bestand zijn. Wat daar staat gaat
    voor, want dat is het bestand dat de instellingenpagina schrijft.
    """
    waarden = _lees(ENV_PADEN)
    pad = (extra_uit_omgeving or {}).get("JARVIS_ENV_PAD") or waarden.get("JARVIS_ENV_PAD")
    if pad:
        eigen = Path(pad)
        if eigen not in ENV_PADEN:
            extra = _lees([eigen])
            extra.pop("JARVIS_ENV_PAD", None)
            waarden.update(extra)
    return waarden


def _lees(paden):
    waarden = {}
    for pad in paden:
        try:
            tekst = pad.read_text(encoding="utf-8")
        except OSError:
            continue
        for regel in tekst.splitlines():
            regel = regel.strip()
            if not regel or regel.startswith("#") or "=" not in regel:
                continue
            sleutel, waarde = regel.split("=", 1)
            waarden[sleutel.strip()] = waarde.strip().strip('"').strip("'")
    return waarden


class Config:
    """Een platte verzameling instellingen met verstandige standaarden."""

    STANDAARD = {
        # luisteren (AudioSocket van Asterisk)
        "JARVIS_LUISTER": "127.0.0.1:9092",
        # statuspagina en API
        "JARVIS_WEB": "0.0.0.0:8099",
        # spraak: "lokaal" (eigen binaries) of "remote" (OpenAI-compatible laag)
        "JARVIS_STEM": "lokaal",
        "JARVIS_STT_URL": "",
        "JARVIS_TTS_URL": "",
        "JARVIS_TAAL": "nl",
        # lokale whisper.cpp
        "JARVIS_WHISPER_BIN": "/opt/jarvis/bin/whisper/whisper-server",
        "JARVIS_WHISPER_MODEL": "/opt/jarvis/modellen/ggml-small.bin",
        "JARVIS_STT_POORT": "8097",
        "JARVIS_STT_DRAADEN": "0",
        # lokale piper
        "JARVIS_PIPER_BIN": "/opt/jarvis/bin/piper/piper",
        "JARVIS_PIPER_STEM": "/opt/jarvis/modellen/nl_NL-rdh-medium.onnx",
        # brein: elke OpenAI-compatible chat-endpoint
        "JARVIS_BREIN_URL": "http://127.0.0.1:8642/v1",
        "JARVIS_BREIN_SLEUTEL": "",
        "JARVIS_BREIN_MODEL": "hermes-agent",
        "JARVIS_BREIN_TIJD": "90",
        # gesprek
        "JARVIS_BEGROETING": "Met Jarvis. Waarmee kan ik helpen?",
        "JARVIS_MAX_GESPREK": "600",
        "JARVIS_STILTE_MS": "650",
        "JARVIS_DREMPEL": "450",
        "JARVIS_MIN_SPRAAK_MS": "350",
        "JARVIS_DEBUG": "0",
        "JARVIS_MELDING_HINT": "Druk op een om met mij te praten.",
        # telefonie
        "TRUNK_NUMMER": "",
        "TRUNK_WACHTWOORD": "",
        "CHEAPCONNECT_HOST": "sip.cheapconnect.net",
        "MIJN_MOBIEL": "",
        "JARVIS_KIESTIJD": "45",
        # Asterisk Manager Interface (zelf bellen)
        "AMI_HOST": "127.0.0.1",
        "AMI_POORT": "5038",
        "AMI_GEBRUIKER": "jarvis",
        "AMI_WACHTWOORD": "",
        # meldingen
        "JARVIS_MELDING_MAP": "/var/lib/jarvis/meldingen",
        "JARVIS_ENV_PAD": "/etc/jarvis/jarvis.env",
        "JARVIS_WAKEN_PAD": "/etc/jarvis/waken.json",
        "JARVIS_TEMPLATE_MAP": "/opt/jarvis/asterisk",
        "JARVIS_ASTERISK_MAP": "/etc/asterisk",
        "JARVIS_GEEN_BELLEN": "0",
        "JARVIS_LOG": "/var/log/jarvis/jarvis.log",
    }

    def __init__(self, overschrijf=None):
        self._overschrijf = dict(overschrijf) if overschrijf else {}
        self._w = dict(self.STANDAARD)
        self._w.update(_lees_alles({k: v for k, v in os.environ.items() if k in self.STANDAARD}))
        self._w.update({k: v for k, v in os.environ.items() if k in self.STANDAARD})
        if overschrijf:
            self._w.update(overschrijf)

    def __getitem__(self, sleutel):
        return self._w[sleutel]

    def get(self, sleutel, standaard=None):
        return self._w.get(sleutel, standaard)

    def int(self, sleutel):
        try:
            return int(str(self._w[sleutel]).strip() or 0)
        except (TypeError, ValueError):
            return int(self.STANDAARD[sleutel])

    def ja(self, sleutel):
        return str(self._w.get(sleutel, "")).strip().lower() in ("1", "ja", "true", "yes", "aan")

    def luister_adres(self):
        host, _, poort = self["JARVIS_LUISTER"].rpartition(":")
        return host or "127.0.0.1", int(poort or 9092)

    def web_adres(self):
        host, _, poort = self["JARVIS_WEB"].rpartition(":")
        return host or "0.0.0.0", int(poort or 8099)

    def telefoon_ingericht(self):
        return bool(self["TRUNK_NUMMER"] and self["TRUNK_WACHTWOORD"] and self["MIJN_MOBIEL"])

    def als_dict(self):
        """Alles wat de statuspagina mag zien; geheimen blijven eruit."""
        verborgen = ("WACHTWOORD", "SLEUTEL")
        return {
            k: ("" if any(v in k for v in verborgen) else v)
            for k, v in self._w.items()
        }

    def herlaad(self):
        """Lees /etc/jarvis/jarvis.env opnieuw (na het opslaan op de pagina).

        De dienst hoeft daarvoor niet te herstarten: alles wat met de omgeving
        te maken heeft (nummers, adressen, sleutels, drempels) wordt per gebruik
        uit deze verzameling gelezen.
        """
        nieuw = dict(self.STANDAARD)
        nieuw.update(_lees_alles({k: v for k, v in os.environ.items() if k in self.STANDAARD}))
        nieuw.update({k: v for k, v in os.environ.items() if k in self.STANDAARD})
        if self._overschrijf:
            nieuw.update(self._overschrijf)
        self._w = nieuw
        return self.als_dict()