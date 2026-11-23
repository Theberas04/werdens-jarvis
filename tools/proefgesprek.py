"""Proefgesprek: doe alsof je Asterisk bent, zonder telefoon.

Verbinden met jarvisd op de AudioSocket-poort, de begroeting opvangen, een
opgenomen vraag insturen en het antwoord bewaren. Zo test je de hele keten
(spraak in, brein, spraak uit) op een machine waar geen telefoon aan hangt.

    python3 -m tools.proefgesprek --vraag vraag.wav --uit antwoord.wav

De ``vraag.wav`` mag elk formaat hebben; hij wordt naar 8 kHz mono omgerekend.
Het antwoord is ruwe telefoonaudio (8 kHz mono) in een wav.
"""
import argparse
import os
import socket
import sys
import time
import wave

TYPE_UUID = 0x01
TYPE_AUDIO = 0x10
FRAME_BYTES = 320          # 20 ms 8 kHz slin


def wav_naar_slin(pad):
    import subprocess
    uit = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", pad, "-f", "s16le", "-ar", "8000", "-ac", "1", "pipe:1"],
        capture_output=True,
    )
    if uit.returncode != 0:
        raise SystemExit("kon %s niet omrekenen: %s" % (pad, uit.stderr.decode()[:200]))
    return uit.stdout


def slin_naar_wav(slin, pad):
    with wave.open(pad, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(slin)


def lees(verbinding, time_out):
    verbinding.settimeout(time_out)
    try:
        kop = verbinding.recv(3)
        if not kop:
            return None
        while len(kop) < 3:
            meer = verbinding.recv(3 - len(kop))
            if not meer:
                return None
            kop += meer
        lengte = int.from_bytes(kop[1:3], "big")
        lading = b""
        while len(lading) < lengte:
            brok = verbinding.recv(lengte - len(lading))
            if not brok:
                return None
            lading += brok
        return kop[0], lading
    except socket.timeout:
        return None


def main(argv=None):
    ontleder = argparse.ArgumentParser(description="Proefgesprek met Jarvis")
    ontleder.add_argument("--host", default="127.0.0.1")
    ontleder.add_argument("--poort", type=int, default=9092)
    ontleder.add_argument("--vraag", required=True, help="wav met jouw vraag")
    ontleder.add_argument("--uit", default="antwoord.wav", help="waar het antwoord heen gaat")
    ontleder.add_argument("--wacht", type=float, default=25.0,
                          help="hoeveel seconden antwoord opvangen")
    argumenten = ontleder.parse_args(argv)

    vraag = wav_naar_slin(argumenten.vraag)
    verbinding = socket.create_connection((argumenten.host, argumenten.poort), timeout=10)
    print("verbonden met %s:%d" % (argumenten.host, argumenten.poort))

    verbinding.sendall(bytes([TYPE_UUID]) + (16).to_bytes(2, "big") + bytes(range(16)))

    # De begroeting komt eerst; die vangen we op tot het stil wordt.
    begroeting = b""
    while True:
        bericht = lees(verbinding, 1.5)
        if bericht is None:
            break
        if bericht[0] == TYPE_AUDIO:
            begroeting += bericht[1]
    print("begroeting: %.1f s audio" % (len(begroeting) / 16000.0))

    # Nu onze vraag insturen, op tempo.
    begin = time.monotonic()
    for i in range(0, len(vraag), FRAME_BYTES):
        blok = vraag[i:i + FRAME_BYTES]
        verbinding.sendall(bytes([TYPE_AUDIO]) + len(blok).to_bytes(2, "big") + blok)
        doel = begin + (i / FRAME_BYTES + 1) * 0.02
        wacht = doel - time.monotonic()
        if wacht > 0:
            time.sleep(wacht)
    print("vraag verstuurd: %.1f s audio" % (len(vraag) / 16000.0))

    # Antwoord opvangen.
    antwoord = b""
    eind = time.time() + argumenten.wacht
    while time.time() < eind:
        bericht = lees(verbinding, 1.0)
        if bericht is None:
            continue
        if bericht[0] == TYPE_AUDIO:
            antwoord += bericht[1]
        elif bericht[0] == 0x00:
            break

    slin_naar_wav(begroeting, argumenten.uit.replace(".wav", "-begroeting.wav"))
    slin_naar_wav(antwoord, argumenten.uit)
    print("begroeting bewaard: %s" % argumenten.uit.replace(".wav", "-begroeting.wav"))
    print("antwoord bewaard:   %s (%.1f s audio)" % (argumenten.uit, len(antwoord) / 16000.0))
    if len(antwoord) < 4000:
        print("weinig audio terug — kijk in het journaal van jarvisd")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())