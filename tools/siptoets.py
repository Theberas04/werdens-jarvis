#!/usr/bin/env python3
"""Siptoets: wat antwoordt CheapConnect op de trunkgegevens in deze container?

Doet hetzelfde als Asterisk bij het aanmelden (REGISTER + digest), maar los
ervan — zodat je zeker weet of het aan Asterisk ligt of aan de gegevens. Er
wordt niets van het wachtwoord geprint: alleen lengtes, statuscodes en de
realm uit de uitdaging.

    python3 /opt/jarvis/tools/siptoets.py            # zoals het is ingesteld
    python3 /opt/jarvis/tools/siptoets.py --varianten  # ook andere schrijfwijzen van het nummer
"""
import hashlib
import os
import re
import socket
import sys
import time
import uuid

ENV = os.environ.get("SIPTOETS_ENV", "/etc/jarvis/jarvis.env")
PJSIP = os.environ.get("SIPTOETS_PJSIP", "/etc/asterisk/pjsip.conf")


def lees_env(pad=ENV):
    waarden = {}
    try:
        with open(pad, encoding="utf-8") as f:
            for regel in f:
                regel = regel.strip()
                if not regel or regel.startswith("#") or "=" not in regel:
                    continue
                sleutel, waarde = regel.split("=", 1)
                waarden[sleutel.strip()] = waarde.strip().strip('"').strip("'")
    except OSError as fout:
        print("kan %s niet lezen: %s" % (pad, fout))
    return waarden


def lees_pjsip(pad=PJSIP):
    """Het wachtwoord zoals Asterisk het ziet (uit het gegenereerde bestand)."""
    uit = {}
    try:
        with open(pad, encoding="utf-8") as f:
            for regel in f:
                regel = regel.split(";")[0].strip()
                if "=" in regel:
                    sleutel, waarde = regel.split("=", 1)
                    uit.setdefault(sleutel.strip(), waarde.strip())
    except OSError:
        pass
    return uit


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def registreer(host, poort, gebruiker, wachtwoord, wacht_op=6):
    """Eén REGISTER-poging met digest-antwoord. Geeft (code, uitdaging) terug."""
    uri = "sip:%s:%d" % (host, poort)
    for poging in range(2):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
        lokaal = sock.getsockname()
        tak = uuid.uuid4().hex[:12]
        kop = [
            "REGISTER %s SIP/2.0" % uri,
            "Via: SIP/2.0/UDP %s:%d;branch=z9hG4bK%s;rport" % (lokaal[0], lokaal[1], tak),
            "Max-Forwards: 70",
            "From: <sip:%s@%s>;tag=%s" % (gebruiker, host, tak),
            "To: <sip:%s@%s>" % (gebruiker, host),
            "Call-ID: %s" % uuid.uuid4().hex,
            "CSeq: %d REGISTER" % (poging + 1),
            "Contact: <sip:%s@%s:%d>" % (gebruiker, lokaal[0], lokaal[1]),
            "Expires: 120",
            "User-Agent: siptoets",
        ]
        if poging == 1:
            kop.append("Authorization: " + _machtiging(gebruiker, wachtwoord, uri, uitdaging))
        kop.append("Content-Length: 0")
        try:
            sock.sendto(("\r\n".join(kop) + "\r\n\r\n").encode(), (host, poort))
            antwoord, eind = "", time.time() + wacht_op
            while time.time() < eind:
                sock.settimeout(max(0.4, eind - time.time()))
                try:
                    dat = sock.recv(65535).decode("utf-8", "replace")
                except socket.timeout:
                    break
                antwoord = dat
                gevonden = re.search(r"SIP/2\.0 (\d{3})", dat.split("\r\n", 1)[0])
                if gevonden and int(gevonden.group(1)) >= 200:
                    break
            if not antwoord:
                return ("geen antwoord", "")
        except OSError as fout:
            return ("%s" % type(fout).__name__, "")
        finally:
            sock.close()
        code = re.search(r"SIP/2\.0 (\d{3})", antwoord.split("\r\n", 1)[0])
        code = code.group(1) if code else "?"
        if code.startswith("1"):
            return ("geen antwoord", "alleen %s (provisional)" % code)
        if code not in ("401", "407"):
            return (code, "")
        uitdaging = _uitdaging(antwoord)
        if not uitdaging.get("nonce"):
            return (code, "geen digest-uitdaging")
    return (code, uitdaging.get("realm", ""))


def _uitdaging(antwoord):
    m = re.search(r"(?:WWW|Proxy)-Authenticate:\s*Digest\s*([^\r\n]+)", antwoord, re.I)
    if not m:
        return {}
    paren = re.findall(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', m.group(1))
    return {k: (a or b) for k, a, b in paren}


def _machtiging(gebruiker, wachtwoord, uri, uitdaging):
    realm = uitdaging.get("realm", "")
    nonce = uitdaging.get("nonce", "")
    qop = (uitdaging.get("qop") or "").lower()
    ha1 = md5("%s:%s:%s" % (gebruiker, realm, wachtwoord))
    ha2 = md5("REGISTER:%s" % uri)
    if "auth" in qop:
        nc, cnonce = "00000001", uuid.uuid4().hex[:16]
        antwoord = md5("%s:%s:%s:%s:auth:%s" % (ha1, nonce, nc, cnonce, ha2))
        extra = ', qop=auth, nc=%s, cnonce="%s"' % (nc, cnonce)
    else:
        antwoord = md5("%s:%s:%s" % (ha1, nonce, ha2))
        extra = ""
    return ('Digest username="%s", realm="%s", nonce="%s", uri="%s", '
            'response="%s", algorithm=MD5%s' % (gebruiker, realm, nonce, uri, antwoord, extra))


def main():
    env = lees_env()
    conf = lees_pjsip()
    nummer = (env.get("TRUNK_NUMMER") or "").strip()
    wachtwoord = env.get("TRUNK_WACHTWOORD") or ""
    host = (env.get("CHEAPCONNECT_HOST") or "voip.cheapconnect.net").strip()

    print("nummer in de instellingen : %s" % (nummer or "(leeg)"))
    print("wachtwoord in de instellingen: %d tekens" % len(wachtwoord))
    print("wachtwoord in pjsip.conf     : %d tekens%s" % (
        len(conf.get("password", "")),
        "" if conf.get("password", "") == wachtwoord else "   ← VERSCHIL!"))
    print("server                    : %s" % host)
    print("auth-gebruikersnaam in pjsip: %s" % conf.get("username", "(geen)"))
    print("contact_user in pjsip     : %s" % conf.get("contact_user", "(niet gezet)"))
    print()

    if not nummer or not wachtwoord:
        print("Nummer of wachtwoord ontbreekt in %s — eerst in de app invullen." % ENV)
        return 1

    pogingen = [(host, nummer)]
    if "--varianten" in sys.argv:
        kandidaten = []
        if nummer.startswith("31"):
            kandidaten = ["+" + nummer, "0031" + nummer[2:], "0" + nummer[2:], nummer[2:]]
        elif nummer.startswith("0"):
            kandidaten = ["31" + nummer[1:], "+31" + nummer[1:], nummer[1:]]
        for naam in kandidaten:
            if all(naam != g for _, g in pogingen):
                pogingen.append((host, naam))
        andere = ("sip.cheapconnect.net" if host.startswith("voip")
                  else "voip.cheapconnect.net")
        for _, gebruiker in list(pogingen):
            pogingen.append((andere, gebruiker))

    print("%-28s %-16s %-6s %s" % ("server", "gebruikersnaam", "code", "realm / opmerking"))
    print("-" * 78)
    gelukt = []
    for doel, gebruiker in pogingen:
        code, detail = registreer(doel, 5060, gebruiker, wachtwoord)
        print("%-28s %-16s %-6s %s" % (doel, gebruiker, code, detail))
        if code == "200":
            gelukt.append((doel, gebruiker))

    print()
    if gelukt:
        print("GELUKT met: %s" % ", ".join("%s als %s" % (d, g) for d, g in gelukt))
        print("Zet die combinatie in de app en de trunk registreert.")
    else:
        print("Alles geweigerd (401). De gegevens zelf kloppen niet bij CheapConnect:")
        print("het wachtwoord van dit SIP-account, of het nummer als inlognaam.")
    return 0


if __name__ == "__main__":
    sys.exit(main())