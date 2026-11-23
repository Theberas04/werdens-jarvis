"""Spraak in en uit.

Twee standen:

* ``lokaal``  — eigen whisper.cpp (spraak naar tekst) en piper (tekst naar
  spraak). Dit is de standaard: dan werkt de telefoonlijn ook als je netwerk
  verder stil ligt.
* ``remote``  — elke OpenAI-compatible spraaklaag (bijvoorbeeld de
  Werdens Media LLM-medialaag op poort 8091): ``/v1/audio/transcriptions`` en
  ``/v1/audio/speech``.

De telefoon werkt met 8 kHz signed-linear 16-bit mono. Alles wat hier naar
buiten gaat is dat formaat, zodat de AudioSocket-laag niets hoeft te weten van
samplefrequenties.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave

RATE_TELEFOON = 8000
FRAME_MS = 20
FRAME_BYTES = RATE_TELEFOON * 2 * FRAME_MS // 1000  # 320 bytes

# Namen die whisper zonder hint verhaspelt. Als initiële prompt meegegeven aan
# whisper-server, zodat "Werdens Monitor" geen "Werters monitor" wordt.
NAMEN_HINT = (
    "Werdens Monitor, VoorraadThuis, AkkoordKast, BeatSync Studio, P2000 Melder, "
    "Werdens Cut, Paperclip, Ploegbaas, Zittesj, Proxmox, Jarvis, CheapConnect, "
    "Home Assistant, containers, back-ups."
)


def slin_naar_wav(slin: bytes, pad: str, rate: int = RATE_TELEFOON) -> str:
    with wave.open(pad, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(slin)
    return pad


def _ffmpeg(*args, invoer: bytes = None) -> bytes:
    commando = ["ffmpeg", "-hide_banner", "-v", "error", *args]
    uit = subprocess.run(commando, input=invoer, capture_output=True, timeout=120)
    if uit.returncode != 0:
        raise RuntimeError("ffmpeg: " + uit.stderr.decode("utf-8", "replace")[:300])
    return uit.stdout


def wav_naar_slin8k(wav: bytes) -> bytes:
    """Elke wav/mp3/flac-bytes naar 8 kHz mono slin (telefoonformaat)."""
    return _ffmpeg("-i", "pipe:0", "-f", "s16le", "-ar", str(RATE_TELEFOON),
                   "-ac", "1", "pipe:1", invoer=wav)


def wav_naar_16k(wav_pad: str, uit_pad: str) -> str:
    _ffmpeg("-y", "-i", wav_pad, "-ar", "16000", "-ac", "1", "-f", "wav", uit_pad)
    return uit_pad


def kaal_voor_spraak(tekst: str) -> str:
    """Markdown en rare tekens eruit; wat overblijft leest piper netjes voor."""
    tekst = re.sub(r"```.*?```", " ", tekst, flags=re.S)
    tekst = re.sub(r"`([^`]*)`", r"\1", tekst)
    tekst = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", tekst)
    tekst = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", tekst)
    tekst = re.sub(r"[*_#>|]+", " ", tekst)
    tekst = re.sub(r"https?://\S+", "een link", tekst)
    tekst = re.sub(r"\s+", " ", tekst)
    return tekst.strip()


class StemFout(RuntimeError):
    pass


class Stem:
    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self._whisper = None          # subprocess.Popen
        self._whisper_poort = None
        self._laatste_fout = ""

    # ------------------------------------------------------------------ opzet

    def modus(self) -> str:
        return (self.cfg["JARVIS_STEM"] or "lokaal").strip().lower()

    def start(self):
        if self.modus() == "remote":
            self.log("spraak: remote-laag %s" % (self.cfg["JARVIS_STT_URL"] or "?"))
            return
        self._start_whisper()

    def _start_whisper(self):
        bin_pad = self.cfg["JARVIS_WHISPER_BIN"]
        model = self.cfg["JARVIS_WHISPER_MODEL"]
        poort = self.cfg.int("JARVIS_STT_POORT")
        # Staat er al een server (van een vorige start, of een gestarte dienst)?
        # Dan gebruiken we die: een tweede kan de poort toch niet binden en een
        # oude server laat je ongemerkt tegen oude ingestelde waarden praten.
        if self._luistert_er_al(poort):
            self._whisper_poort = poort
            self.log("spraak in: bestaande whisper-server op poort %d hergebruikt" % poort)
            return
        if not shutil.which(bin_pad) and not os.path.exists(bin_pad):
            raise StemFout("whisper-server niet gevonden op %s" % bin_pad)
        if not os.path.exists(model):
            raise StemFout("whisper-model niet gevonden op %s" % model)
        draaden = self.cfg.int("JARVIS_STT_DRAADEN")
        if draaden <= 0:
            draaden = max(2, min(4, len(os.sched_getaffinity(0))))
        commando = [
            bin_pad, "-m", model, "-l", self.cfg["JARVIS_TAAL"],
            "-t", str(draaden), "--host", "127.0.0.1", "--port", str(poort),
            "--prompt", NAMEN_HINT,
        ]
        self._whisper = subprocess.Popen(
            commando, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._whisper_poort = poort
        self._wacht_op_whisper(poort)

    @staticmethod
    def _luistert_er_al(poort):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sok:
            sok.settimeout(0.5)
            return sok.connect_ex(("127.0.0.1", poort)) == 0

    def _wacht_op_whisper(self, poort, seconde=30):
        eind = time.time() + seconde
        while time.time() < eind:
            if self._whisper and self._whisper.poll() is not None:
                raise StemFout("whisper-server stopte direct (code %s)" % self._whisper.returncode)
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/" % poort, timeout=1):
                    return
            except urllib.error.HTTPError:
                return  # de server luistert; 404 op / is goed
            except Exception:
                time.sleep(0.4)
        raise StemFout("whisper-server luisterde niet binnen %d s op poort %d" % (seconde, poort))

    def stop(self):
        if self._whisper and self._whisper.poll() is None:
            self._whisper.terminate()
            try:
                self._whisper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._whisper.kill()
        self._whisper = None

    # ------------------------------------------------------------ spraak in

    def stt(self, wav_pad: str) -> str:
        """Tekst uit een wav-bestand (8 of 16 kHz, maakt niet uit)."""
        if self.modus() == "remote" and self.cfg["JARVIS_STT_URL"]:
            tekst = self._stt_remote(wav_pad)
        else:
            tekst = self._stt_lokaal(wav_pad)
        tekst = re.sub(r"\s+", " ", tekst or "").strip()
        # whisper zet soms een vage aanloop voor de eigenlijke zin
        tekst = re.sub(r"^(oké|oke|eh|hmm)[,.\s]+", "", tekst, flags=re.I)
        return tekst.strip()

    def _stt_lokaal(self, wav_pad: str) -> str:
        if not self._whisper_poort:
            self._start_whisper()
        tijdelijk = tempfile.mkdtemp(prefix="jarvis-stt-")
        try:
            pad16 = wav_naar_16k(wav_pad, os.path.join(tijdelijk, "in16.wav"))
            grens = uuid.uuid4().hex
            with open(pad16, "rb") as f:
                bestand = f.read()
            body = (
                b"--" + grens.encode() + b"\r\n"
                b'Content-Disposition: form-data; name="file"; filename="in.wav"\r\n'
                b"Content-Type: audio/wav\r\n\r\n" + bestand + b"\r\n"
                b"--" + grens.encode() + b"\r\n"
                b'Content-Disposition: form-data; name="response_format"\r\n\r\njson\r\n'
                b"--" + grens.encode() + b"--\r\n"
            )
            verzoek = urllib.request.Request(
                "http://127.0.0.1:%d/inference" % self._whisper_poort,
                data=body,
                headers={"Content-Type": "multipart/form-data; boundary=" + grens},
            )
            with urllib.request.urlopen(verzoek, timeout=180) as antwoord:
                gegevens = json.loads(antwoord.read().decode("utf-8", "replace"))
            return gegevens.get("text", "")
        except Exception as fout:
            self._laatste_fout = "spraak in: %r" % (fout,)
            raise StemFout(self._laatste_fout)
        finally:
            shutil.rmtree(tijdelijk, ignore_errors=True)

    def _stt_remote(self, wav_pad: str) -> str:
        basis = self.cfg["JARVIS_STT_URL"].rstrip("/")
        if not basis.endswith("/v1"):
            basis += "/v1"
        grens = uuid.uuid4().hex
        with open(wav_pad, "rb") as f:
            bestand = f.read()
        body = (
            b"--" + grens.encode() + b"\r\n"
            b'Content-Disposition: form-data; name="file"; filename="in.wav"\r\n'
            b"Content-Type: audio/wav\r\n\r\n" + bestand + b"\r\n"
            b"--" + grens.encode() + b"\r\n"
            b'Content-Disposition: form-data; name="model"\r\n\r\nwhisper-1\r\n'
            b"--" + grens.encode() + b"\r\n"
            b'Content-Disposition: form-data; name="language"\r\n\r\n' + self.cfg["JARVIS_TAAL"].encode() + b"\r\n"
            b"--" + grens.encode() + b"--\r\n"
        )
        verzoek = urllib.request.Request(
            basis + "/audio/transcriptions", data=body,
            headers={"Content-Type": "multipart/form-data; boundary=" + grens},
        )
        with urllib.request.urlopen(verzoek, timeout=180) as antwoord:
            return json.loads(antwoord.read().decode("utf-8", "replace")).get("text", "")

    # ----------------------------------------------------------- spraak uit

    def tts(self, tekst: str) -> bytes:
        """Tekst naar telefoonaudio: 8 kHz slin, klaar om te versturen."""
        tekst = kaal_voor_spraak(tekst)
        if not tekst:
            return b""
        if self.modus() == "remote" and self.cfg["JARVIS_TTS_URL"]:
            wav = self._tts_remote(tekst)
        else:
            wav = self._tts_lokaal(tekst)
        return wav_naar_slin8k(wav)

    def _tts_lokaal(self, tekst: str) -> bytes:
        bin_pad = self.cfg["JARVIS_PIPER_BIN"]
        stem = self.cfg["JARVIS_PIPER_STEM"]
        if not os.path.exists(bin_pad):
            raise StemFout("piper niet gevonden op %s" % bin_pad)
        if not os.path.exists(stem):
            raise StemFout("piper-stem niet gevonden op %s" % stem)
        tijdelijk = tempfile.mkdtemp(prefix="jarvis-tts-")
        uit = os.path.join(tijdelijk, "uit.wav")
        try:
            uitkomst = subprocess.run(
                [bin_pad, "--model", stem, "--output_file", uit],
                input=tekst.encode("utf-8"), capture_output=True, timeout=120,
            )
            if uitkomst.returncode != 0 or not os.path.exists(uit):
                raise StemFout("piper faalde: " + uitkomst.stderr.decode("utf-8", "replace")[:200])
            with open(uit, "rb") as f:
                return f.read()
        finally:
            shutil.rmtree(tijdelijk, ignore_errors=True)

    def _tts_remote(self, tekst: str) -> bytes:
        basis = self.cfg["JARVIS_TTS_URL"].rstrip("/")
        if not basis.endswith("/v1"):
            basis += "/v1"
        body = json.dumps({
            "model": "tts-1",
            "input": tekst,
            "voice": self.cfg.get("JARVIS_TTS_STEM") or "nl_NL-mls-medium",
            "response_format": "wav",
        }).encode()
        verzoek = urllib.request.Request(
            basis + "/audio/speech", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(verzoek, timeout=180) as antwoord:
            return antwoord.read()

    # --------------------------------------------------------------- proeven

    def gezondheid(self) -> dict:
        """Echte proef, niet alleen 'binary bestaat'."""
        uit = {"modus": self.modus(), "klaar": False}
        spraak = b""
        try:
            begin = time.time()
            spraak = self.tts("Jarvis is klaar.")
            uit["tts"] = {"klaar": bool(spraak), "seconden": round(time.time() - begin, 2),
                          "bytes": len(spraak)}
        except Exception as fout:
            uit["tts"] = {"klaar": False, "fout": str(fout)[:200]}
        try:
            tijdelijk = tempfile.mkdtemp(prefix="jarvis-proef-")
            pad = os.path.join(tijdelijk, "proef.wav")
            slin_naar_wav(spraak or b"\0" * 3200, pad)
            tekst = self.stt(pad)
            uit["stt"] = {"klaar": bool(tekst), "tekst": tekst[:80]}
            shutil.rmtree(tijdelijk, ignore_errors=True)
        except Exception as fout:
            uit["stt"] = {"klaar": False, "fout": str(fout)[:200]}
        uit["klaar"] = bool(uit.get("tts", {}).get("klaar") and uit.get("stt", {}).get("klaar"))
        return uit