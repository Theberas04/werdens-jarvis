"""De telefoonlijn: AudioSocket (praten) en AMI (zelf bellen).

Asterisk praat met ons via het AudioSocket-protocol: een TCP-verbinding per
gesprek, met blokjes van drie bytes kop (type + lengte) en dan de lading.

* ``0x00``  verbinding beeindigen
* ``0x01``  UUID van het gesprek (16 bytes)
* ``0x03``  DTMF-toets (1 byte ascii)
* ``0x10``  audio: signed linear 16-bit, 8 kHz, mono, little-endian

Wij sturen alleen ``0x10`` terug, in blokjes van 20 ms (320 bytes), netjes op
tempo. Sneller sturen laat Asterisk zijn buffer vollopen en dan hapert de stem.

Zelf bellen gaat via de Asterisk Manager Interface: een regelgebaseerd
tekstprotocol op poort 5038. We sturen één ``Originate`` en lezen het antwoord.
"""
import queue
import socket
import threading
import time

TYPE_EINDE = 0x00
TYPE_UUID = 0x01
TYPE_DTMF = 0x03
TYPE_AUDIO = 0x10

FRAME_MS = 20
FRAME_BYTES = 320  # 8 kHz * 2 bytes * 20 ms


class AudioSocketFout(RuntimeError):
    pass


def lees_blok(verbinding, logger=None):
    """Lees één bericht; geeft (type, lading) of None bij einde verbinding."""
    kop = _lees_precies(verbinding, 3)
    if not kop:
        return None
    soort = kop[0]
    lengte = int.from_bytes(kop[1:3], "big")
    lading = _lees_precies(verbinding, lengte) if lengte else b""
    if lading is None:
        return None
    return soort, lading


def _lees_precies(verbinding, aantal):
    brokken = []
    over = aantal
    while over > 0:
        try:
            brok = verbinding.recv(over)
        except (ConnectionResetError, OSError):
            return None
        if not brok:
            return None
        brokken.append(brok)
        over -= len(brok)
    return b"".join(brokken)


def maak_audio_blok(slin: bytes) -> bytes:
    return bytes([TYPE_AUDIO]) + len(slin).to_bytes(2, "big") + slin


class AudioSocketServer:
    """Luistert op een TCP-poort waarop Asterisk gesprekken aankondigt."""

    def __init__(self, host, poort, bij_gesprek, log=print):
        self.host = host
        self.poort = poort
        self.bij_gesprek = bij_gesprek
        self.log = log
        self._stop = threading.Event()
        self._sok = None
        self._draden = []

    def start(self):
        self._sok = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sok.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sok.bind((self.host, self.poort))
        self._sok.listen(8)
        self._sok.settimeout(1.0)
        draad = threading.Thread(target=self._lus, name="audiosocket", daemon=True)
        draad.start()
        self._draden.append(draad)
        self.log("telefoon: luistert op %s:%d (AudioSocket)" % (self.host, self.poort))

    def _lus(self):
        while not self._stop.is_set():
            try:
                verbinding, adres = self._sok.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            verbinding.settimeout(1.0)
            draad = threading.Thread(
                target=self._veilig, args=(verbinding, adres), daemon=True
            )
            draad.start()
            self._draden.append(draad)

    def _veilig(self, verbinding, adres):
        try:
            self.bij_gesprek(Gesprek(verbinding, adres, self.log))
        except Exception as fout:  # nooit de luisteraar laten sterven
            self.log("telefoon: gesprek van %s eindigde met fout: %r" % (adres[0], fout))
        finally:
            try:
                verbinding.close()
            except OSError:
                pass

    def stop(self):
        self._stop.set()
        try:
            self._sok.close()
        except (AttributeError, OSError):
            pass


class Gesprek:
    """Eén telefoongesprek: audio erin, audio eruit, op tempo."""

    def __init__(self, verbinding, adres, log=print):
        self.verbinding = verbinding
        self.adres = adres
        self.log = log
        self.uuid = ""
        self._schrijfslot = threading.Lock()
        self._spreekslot = threading.Lock()
        self._stop = threading.Event()
        self.gestopt = False
        self.dtmf = queue.Queue()
        self._buffer = b""          # wat we al binnenkregen maar nog niet konden lezen

    # --------------------------------------------------------------- lezen

    def _pak_frame(self):
        """Haal één compleet bericht uit de buffer, of None."""
        if len(self._buffer) < 3:
            return None
        soort = self._buffer[0]
        lengte = int.from_bytes(self._buffer[1:3], "big")
        if len(self._buffer) < 3 + lengte:
            return None
        lading = self._buffer[3:3 + lengte]
        self._buffer = self._buffer[3 + lengte:]
        return soort, lading

    def lees(self, time_out=0.5):
        """Volgend bericht, ("stilte", b"") bij niets, of None als het klaar is.

        Een time-out is geen afgebroken gesprek: halve blokjes worden in de
        buffer bewaard tot ze compleet zijn. Anders lijkt elke stilte op de lijn
        een opgehangen telefoon.
        """
        eind = time.monotonic() + time_out
        while True:
            frame = self._pak_frame()
            if frame is not None:
                return frame
            rest = eind - time.monotonic()
            if rest <= 0:
                return ("stilte", b"")
            self.verbinding.settimeout(rest)
            try:
                brok = self.verbinding.recv(8192)
            except socket.timeout:
                return ("stilte", b"")
            except OSError:
                return None
            if not brok:
                return None
            self._buffer += brok

    def verwerk(self, soort_en_lading):
        if soort_en_lading is None:
            self.gestopt = True
            return None
        soort, lading = soort_en_lading
        if soort == TYPE_UUID:
            self.uuid = lading.hex()
        elif soort == TYPE_DTMF:
            try:
                self.dtmf.put(lading.decode("ascii", "ignore"))
            except Exception:
                pass
        elif soort == TYPE_EINDE:
            self.gestopt = True
            return None
        return soort, lading

    # ------------------------------------------------------------- schrijven

    def zeg(self, slin: bytes, stopbaar=None):
        """Speel audio af op tempo. Geeft True terug als het helemaal ging."""
        if not slin:
            return True
        with self._spreekslot:
            begin = time.monotonic()
            gezonden = 0
            for i in range(0, len(slin), FRAME_BYTES):
                blok = slin[i:i + FRAME_BYTES]
                if len(blok) < FRAME_BYTES:
                    blok = blok + b"\0" * (FRAME_BYTES - len(blok))
                if self.gestopt or (stopbaar and stopbaar()):
                    return False
                try:
                    with self._schrijfslot:
                        self.verbinding.sendall(maak_audio_blok(blok))
                except OSError as fout:
                    self.log("audio versturen mislukte na %d blokken: %r" % (gezonden, fout))
                    self.gestopt = True
                    return False
                gezonden += 1
                doel = begin + gezonden * FRAME_MS / 1000.0
                wacht = doel - time.monotonic()
                if wacht > 0:
                    time.sleep(wacht)
            return True

    def hang_op(self):
        self.gestopt = True
        try:
            with self._schrijfslot:
                self.verbinding.sendall(bytes([TYPE_EINDE, 0, 0]))
        except OSError:
            pass


class AsteriskAMI:
    """Klein stukje AMI: genoeg om een gesprek op te zetten."""

    def __init__(self, host, poort, gebruiker, wachtwoord, log=print):
        self.host = host
        self.poort = poort
        self.gebruiker = gebruiker
        self.wachtwoord = wachtwoord
        self.log = log

    def _verbind(self, time_out=8):
        sok = socket.create_connection((self.host, self.poort), timeout=time_out)
        sok.settimeout(time_out)
        bestand = sok.makefile("rwb", buffering=0)
        begroeting = bestand.readline().decode("utf-8", "replace")
        if "Asterisk Call Manager" not in begroeting:
            sok.close()
            raise AudioSocketFout("geen AMI-begroeting van %s:%d" % (self.host, self.poort))
        self._stuur(bestand, {
            "Action": "Login",
            "Username": self.gebruiker,
            "Secret": self.wachtwoord,
            "Events": "off",
        })
        antwoord = self._lees_antwoord(bestand)
        if antwoord.get("Response") != "Success":
            sok.close()
            raise AudioSocketFout("AMI-login geweigerd: %s" % antwoord.get("Message", "?"))
        return sok, bestand

    @staticmethod
    def _stuur(bestand, velden):
        regels = "".join("%s: %s\r\n" % (k, v) for k, v in velden.items())
        bestand.write((regels + "\r\n").encode())

    @staticmethod
    def _lees_antwoord(bestand, tijd=15):
        velden = {}
        eind = time.time() + tijd
        while time.time() < eind:
            regel = bestand.readline()
            if not regel:
                break
            regel = regel.decode("utf-8", "replace").strip()
            if not regel:
                if velden:
                    return velden
                continue
            if ":" in regel:
                sleutel, _, waarde = regel.partition(":")
                velden[sleutel.strip()] = waarde.strip()
        return velden

    def bel(self, nummer, context="jarvis-melding", wachtrij="s", beller="", variabelen=None):
        """Zet een gesprek op naar ``nummer`` en laat de dialplan-context lopen."""
        sok, bestand = self._verbind()
        try:
            velden = {
                "Action": "Originate",
                "Channel": "PJSIP/%s@cheapconnect" % nummer,
                "Context": context,
                "Exten": wachtrij,
                "Priority": "1",
                "Timeout": "45000",
                "Async": "true",
                "ActionID": "jarvis-%d" % int(time.time()),
            }
            if beller:
                velden["CallerID"] = beller
            for sleutel, waarde in (variabelen or {}).items():
                velden["Variable"] = "%s=%s" % (sleutel, waarde)
            self._stuur(bestand, velden)
            antwoord = self._lees_antwoord(bestand)
            gelukt = antwoord.get("Response") == "Success"
            if not gelukt:
                self.log("AMI: bellen naar %s geweigerd: %s" % (nummer, antwoord.get("Message", "?")))
            return gelukt, antwoord.get("Message", "")
        finally:
            try:
                self._stuur(bestand, {"Action": "Logoff"})
            except OSError:
                pass
            sok.close()

    def gezondheid(self, time_out=5):
        try:
            sok, bestand = self._verbind(time_out)
        except Exception as fout:
            return {"klaar": False, "fout": str(fout)[:200]}
        try:
            self._stuur(bestand, {"Action": "Ping"})
            antwoord = self._lees_antwoord(bestand, tijd=5)
            return {"klaar": antwoord.get("Response") == "Success"}
        finally:
            sok.close()