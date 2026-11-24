"""Statuspagina en API van Jarvis.

Huisstijl is die van Werdens Monitor: licht, blauw accent, kaarten met een
dunne rand. Alles wat je hier ziet komt uit de echte status van de dienst; de
knoppen doen echt iets (bellen via Asterisk).
"""
import base64
import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STIJL = """
:root{
  --bg:#f4f7fb; --pane:#fff; --pane2:#f8fafc; --pane3:#eef4fb;
  --line:#e3eaf4; --txt:#0f172a; --txt2:#5a6b82;
  --accent:#2ea3f2; --accent-d:#1e73be; --goed:#17a673; --fout:#d64545; --let:#c98a00;
  --merk:linear-gradient(118deg,#5ff0e8 0%,#2ea3f2 55%,#1e73be 100%);
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wikkel{max-width:1060px;margin:0 auto;padding:22px 18px 60px}
header.kop{display:flex;align-items:center;gap:14px;margin-bottom:20px;flex-wrap:wrap}
.merk{width:46px;height:46px;border-radius:13px;background:var(--merk);display:flex;align-items:center;justify-content:center;
 color:#04202f;font-weight:800;font-size:19px;letter-spacing:.5px;box-shadow:0 6px 16px rgba(46,163,242,.28)}
h1{font-size:21px;margin:0;letter-spacing:.2px}
.tagline{color:var(--txt2);font-size:13px;margin-top:2px}
.status{display:flex;gap:8px;align-items:center;margin-left:auto;font-size:13px;color:var(--txt2)}
.stip{width:9px;height:9px;border-radius:50%;background:var(--goed);display:inline-block}
.stip.fout{background:var(--fout)} .stip.let{background:var(--let)}
.raster{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.kaart{background:var(--pane);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.kaart-kop{padding:13px 16px;border-bottom:1px solid var(--line);background:var(--pane2);
 display:flex;align-items:center;gap:8px;font-weight:650;font-size:14px}
.kaart-body{padding:15px 16px}
.rij{display:flex;justify-content:space-between;gap:12px;padding:5px 0;border-bottom:1px dashed var(--line);font-size:14px}
.rij:last-child{border-bottom:0}
.rij b{font-weight:600}
.zacht{color:var(--txt2)}
.knop{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line);background:var(--pane);
 color:var(--txt);padding:9px 14px;border-radius:10px;font:inherit;font-weight:600;cursor:pointer;text-decoration:none}
.knop:hover{border-color:var(--accent)}
.knop.primair{background:var(--accent);border-color:var(--accent);color:#fff}
.knop.primair:hover{background:var(--accent-d);border-color:var(--accent-d)}
.knop[disabled]{opacity:.55;cursor:not-allowed}
textarea{width:100%;min-height:74px;border:1px solid var(--line);border-radius:10px;padding:10px 12px;
 font:inherit;color:var(--txt);background:var(--pane);resize:vertical}
.knoppen{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}
ul.gesprek{list-style:none;margin:0;padding:0}
ul.gesprek li{border-bottom:1px dashed var(--line);padding:9px 0;font-size:14px}
ul.gesprek li:last-child{border-bottom:0}
.beurt{margin:3px 0;font-size:13.5px}
.beurt .wie{font-weight:700;color:var(--accent-d)}
.melding{margin-top:12px;padding:10px 12px;border-radius:10px;background:var(--pane3);
 border:1px solid var(--line);font-size:13.5px;white-space:pre-wrap}
.melding.fout{background:#fdf1f1;border-color:#f3c9c9}
.badge{font-size:11.5px;font-weight:700;padding:2px 8px;border-radius:99px;background:var(--pane3);
 border:1px solid var(--line);color:var(--txt2)}
code{background:var(--pane3);border:1px solid var(--line);border-radius:6px;padding:1px 6px;font-size:13px}
p.uitleg{color:var(--txt2);font-size:13.5px;margin:8px 0 0}
nav.tabs{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
nav.tabs a{padding:7px 13px;border-radius:9px;border:1px solid var(--line);background:var(--pane);
 color:var(--txt2);text-decoration:none;font-size:13.5px;font-weight:600}
nav.tabs a.aan{background:var(--accent);border-color:var(--accent);color:#fff}
.groep{border:1px solid var(--line);border-radius:14px;background:var(--pane);margin-bottom:16px;overflow:hidden}
.groep-kop{padding:13px 16px;border-bottom:1px solid var(--line);background:var(--pane2);font-weight:650;font-size:14px}
.groep-body{padding:6px 16px 16px}
.veld{display:flex;flex-direction:column;gap:5px;margin-top:14px}
.veld label{font-weight:600;font-size:13.5px}
.veld .uitleg{color:var(--txt2);font-size:12.5px;margin:0}
.veld input[type=text],.veld input[type=password],.veld input[type=number],.veld select{width:100%;
 border:1px solid var(--line);border-radius:10px;padding:9px 11px;font:inherit;background:var(--pane);color:var(--txt)}
.veld input:focus,.veld select:focus,.veld textarea:focus{outline:2px solid var(--accent);outline-offset:-1px}
.veld.fout input,.veld.fout select{border-color:var(--fout)}
.veld .foutmelding{color:var(--fout);font-size:12.5px}
.vlak{display:flex;align-items:center;gap:9px;margin-top:14px;font-size:13.5px}
.vlak input{width:auto}
.brein-rij{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;
 border:1px solid var(--line);border-radius:12px;background:var(--pane2);padding:11px 13px;margin-top:12px;flex-wrap:wrap}
.brein-rij .knoppen{margin-top:0}
.tag{display:inline-block;background:var(--accent);color:#fff;border-radius:999px;padding:1px 9px;font-size:11.5px;font-weight:650}
.klein{color:var(--txt2);font-size:12.5px;margin-top:4px}
.klein.fout{color:var(--fout)}
.let{background:#fff8e6;border:1px solid #f0dfae;border-radius:12px;padding:12px 14px;margin-bottom:16px;font-size:13.5px}
.let b{color:var(--let)}
.melding ul{margin:6px 0 0 18px;padding:0}
footer{color:var(--txt2);font-size:12.5px;margin-top:26px;text-align:center}
@media (max-width:820px){
  .raster{grid-template-columns:1fr}
  .status{margin-left:0;width:100%}
  .merk{width:40px;height:40px;font-size:17px}
  h1{font-size:19px}
}
"""

JS = """
function q(s){return document.querySelector(s)}
function esc(t){var d=document.createElement('div');d.textContent=t==null?'':t;return d.innerHTML}
function toon(tekst,isFout){
  var m=q('#melding'); m.className='melding'+(isFout?' fout':''); m.textContent=tekst; m.hidden=false;
}
function tijd(){
  fetch('/api/status').then(function(r){return r.json()}).then(function(d){
    q('#sinds').textContent = d.draait_sind + ' s';
    q('#lijn').textContent = d.telefoon_ingericht ? 'ingericht' : 'niet ingericht';
    q('#lijnstip').className = 'stip' + (d.telefoon_ingericht ? '' : ' let');
    q('#model').textContent = d.brein.model;
    q('#breinurl').textContent = d.brein.url;
    q('#breinnaam').textContent = d.brein.naam || 'niet bewaard onder een naam';
    var g = d.gesprekken || [];
    var lijst = q('#gesprekken');
    if(!g.length){ lijst.innerHTML = '<li class="zacht">Nog geen gesprekken.</li>'; }
    else {
      var uit = '';
      for(var i=0;i<g.length;i++){
        var x=g[i], kop = esc(x.begon||'') + ' &middot; ' + esc(String(x.duur||0)) + ' s &middot; ' + x.beurten.length + ' beurt(en)';
        uit += '<li><div>' + kop + '</div>';
        for(var j=0;j<x.beurten.length;j++){
          var b=x.beurten[j];
          uit += '<div class="beurt"><span class="wie">' + (b.rol==='tom'?'Tom':'Jarvis') + ':</span> ' + esc(b.tekst) + '</div>';
        }
        uit += '</li>';
      }
      lijst.innerHTML = uit;
    }
    var lm = d.laatste_melding;
    q('#laatstemelding').textContent = lm ? (lm.tijd + ' - ' + lm.tekst) : 'nog geen melding verstuurd';
  }).catch(function(e){
    q('#sinds').textContent = '?'; q('#lijnstip').className='stip fout';
    toon('De status is niet op te halen: ' + e, true);
  });
}
function meldingSturen(){
  var tekst = q('#meldingtekst').value.trim();
  if(!tekst){ toon('Typ eerst een melding.', true); return; }
  var knop = q('#belknop'); knop.disabled = true;
  toon('Bezig met bellen...', false);
  fetch('/api/melding', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tekst:tekst})})
    .then(function(r){return r.json()}).then(function(d){
      knop.disabled = false;
      if(d.gelukt){ toon('De telefoon gaat nu over bij Tom.' + (d.gesimuleerd?' (teststand: er is niet echt gebeld)':''), false); }
      else { toon('Bellen mislukte: ' + (d.fout||'onbekende fout'), true); }
      tijd();
    }).catch(function(e){ knop.disabled=false; toon('Bellen mislukte: ' + e, true); });
}
function belGesprek(){
  var knop = q('#gesprekknop'); knop.disabled = true;
  toon('Bezig met bellen...', false);
  fetch('/api/bel', {method:'POST'}).then(function(r){return r.json()}).then(function(d){
    knop.disabled = false;
    toon(d.gelukt ? 'Jarvis belt Tom nu voor een gesprek.' : ('Bellen mislukte: ' + (d.fout||'?')), !d.gelukt);
  }).catch(function(e){ knop.disabled=false; toon('Bellen mislukte: ' + e, true); });
}
function proef(){
  toon('Bezig met de proef: spraak uit, spraak in, brein en Asterisk...', false);
  fetch('/api/status?uitgebreid=1').then(function(r){return r.json()}).then(function(d){
    var regels = [];
    regels.push('spraak uit: ' + (d.stem.tts.klaar ? 'ok (' + d.stem.tts.seconden + ' s)' : 'fout - ' + d.stem.tts.fout));
    regels.push('spraak in: ' + (d.stem.stt.klaar ? 'ok, hoorde "' + d.stem.stt.tekst + '"' : 'fout - ' + d.stem.stt.fout));
    regels.push('brein: ' + (d.brein.proef.klaar ? 'ok (' + d.brein.proef.seconden + ' s)' : 'fout - ' + d.brein.proef.fout));
    regels.push('Asterisk: ' + (d.asterisk.klaar ? 'ok' : 'fout - ' + d.asterisk.fout));
    toon(regels.join('\\n'), !(d.stem.tts.klaar && d.stem.stt.klaar && d.brein.proef.klaar));
  }).catch(function(e){ toon('Proef mislukte: ' + e, true); });
}
document.addEventListener('DOMContentLoaded', function(){
  tijd(); setInterval(tijd, 5000);
  q('#belknop').addEventListener('click', meldingSturen);
  q('#gesprekknop').addEventListener('click', belGesprek);
  q('#proefknop').addEventListener('click', proef);
});
"""

PAGINA = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jarvis &middot; telefonische assistent</title>
<style>__STIJL__</style>
</head>
<body>
<div class="wikkel">
  <header class="kop">
    <div class="merk">J</div>
    <div>
      <h1>Jarvis</h1>
      <div class="tagline">Je belt hem, hij belt jou als er iets mis is.</div>
    </div>
    <div class="status"><span class="stip" id="lijnstip"></span><span id="lijn">laden...</span>
      <span class="badge">up <span id="sinds">?</span></span></div>
  </header>

  <nav class="tabs">
    <a href="/" class="aan">Status</a>
    <a href="/instellingen">Instellingen</a>
  </nav>

  __LETOP__

  <div class="raster">
    <div class="kaart">
      <div class="kaart-kop">Telefoonlijn</div>
      <div class="kaart-body">
        <div class="rij"><span>Trunk-nummer</span><b>__TRUNK__</b></div>
        <div class="rij"><span>Mijn mobiel</span><b>__MOBIEL__</b></div>
        <div class="rij"><span>AudioSocket</span><b>__LUISTER__</b></div>
        <div class="knoppen">
          <button class="knop primair" id="gesprekknop">Bel mijn telefoon</button>
          <button class="knop" id="proefknop">Doe de proef</button>
        </div>
        <p class="uitleg">Bellen gaat via je eigen Asterisk en de SIP-trunk van
        CheapConnect. De knop vraagt Asterisk om een gesprek; de rest doet deze dienst.</p>
      </div>
    </div>

    <div class="kaart">
      <div class="kaart-kop">Melding inspreken</div>
      <div class="kaart-body">
        <textarea id="meldingtekst" placeholder="Bijvoorbeeld: de container van Werdens Monitor reageert niet meer."></textarea>
        <div class="knoppen"><button class="knop primair" id="belknop">Laat Jarvis dit voorlezen</button></div>
        <p class="uitleg">Jarvis leest de tekst voor. Druk je daarna op 1, dan ga je in gesprek met hem.</p>
        <div class="rij" style="margin-top:12px"><span class="zacht">Laatste melding</span><span class="zacht" id="laatstemelding">-</span></div>
      </div>
    </div>

    <div class="kaart">
      <div class="kaart-kop">Brein</div>
      <div class="kaart-body">
        <div class="rij"><span>Gekoppeld aan</span><b id="breinnaam">&mdash;</b></div>
        <div class="rij"><span>Model</span><b id="model">?</b></div>
        <div class="rij"><span>Endpoint</span><b id="breinurl">?</b></div>
        <p class="uitleg">Standaard is dit Hermes: die kan diensten nakijken, logboeken lezen en
        een container herstarten. Elk OpenAI-compatible endpoint werkt.</p>
      </div>
    </div>

    <div class="kaart">
      <div class="kaart-kop">Laatste gesprekken</div>
      <div class="kaart-body"><ul class="gesprek" id="gesprekken"></ul></div>
    </div>
  </div>

  <div class="melding" id="melding" hidden></div>

  <p class="uitleg">API: <code>POST /api/melding</code> met <code>{"tekst": "..."}</code>,
  <code>POST /api/bel</code>, <code>GET /api/status</code>. De waakhond gebruikt dezelfde route.</p>
  <footer>Jarvis &middot; eigen telefoonlijn in je eigen netwerk &middot; &copy; Werdens Media</footer>
</div>
<script>__JS__</script>
</body>
</html>"""


NAV = """<nav class="tabs">
    <a href="/" class="%s">Status</a>
    <a href="/instellingen" class="%s">Instellingen</a>
  </nav>"""

JS_INSTELLINGEN = """
function q(s){return document.querySelector(s)}
function toon(regels,isFout){
  var m=q('#melding');
  m.className='melding'+(isFout?' fout':'');
  var ul=document.createElement('ul');
  for(var i=0;i<regels.length;i++){ var li=document.createElement('li'); li.textContent=regels[i]; ul.appendChild(li); }
  m.innerHTML=''; m.appendChild(ul); m.hidden=false;
}
function verzamel(){
  var uit={};
  var velden=document.querySelectorAll('[data-sleutel]');
  for(var i=0;i<velden.length;i++){
    var el=velden[i], sleutel=el.getAttribute('data-sleutel');
    if(el.type==='checkbox'){ uit[sleutel]=el.checked?'1':'0'; continue; }
    var waarde=(el.value||'').trim();
    var geheim=el.getAttribute('data-geheim')==='1';
    if(geheim && !waarde){ continue; }
    uit[sleutel]=waarde;
  }
  return uit;
}
function bewaar(){
  var velden=verzamel();
  var waken=q('#waken').value;
  var oud=document.querySelectorAll('.veld.fout');
  for(var i=0;i<oud.length;i++){ oud[i].className='veld'; }
  var knop=q('#bewaarknop'); knop.disabled=true;
  toon(['Bezig met opslaan...'],false);
  fetch('/api/instellingen',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({velden:velden,waken:waken})})
    .then(function(r){return r.json()}).then(function(d){
      knop.disabled=false;
      if(!d.gelukt){
        var regels=[];
        for(var k in (d.fouten||{})){
          regels.push(k+': '+d.fouten[k]);
          var vak=document.querySelector('[data-sleutel="'+k+'"]');
          if(vak){ var w=vak.closest('.veld'); if(w) w.className='veld fout'; }
        }
        if(!regels.length) regels.push(d.fout||'opslaan mislukte');
        toon(regels,true);
        return;
      }
      var uit=['Opgeslagen.']; 
      if(d.asterisk_mislukt && d.asterisk_mislukt.length){ uit.push('Let op: '+d.asterisk_mislukt.join(', ')+' kon niet geschreven worden.'); }
      if(d.trunk_ok){ uit.push('De trunk is geregistreerd bij CheapConnect.'); }
      else { uit.push('De trunk is nog niet geregistreerd: controleer nummer en wachtwoord hierboven.'+(d.trunk_detail?(' ('+d.trunk_detail+')'):'')); }
      if(d.proef){ if(d.proef.klaar){ uit.push('Proef: spraak en brein doen het.'); } else { uit.push('Proef mislukte: '+(d.proef.fout||'onbekend')); } }
      if(d.herstart_nodig){ uit.push('Voor het nieuwe spraakmodel is een herstart nodig: systemctl restart jarvisd in de container.'); }
      toon(uit,!d.trunk_ok);
      laadStatus();
    }).catch(function(e){ knop.disabled=false; toon(['Opslaan mislukte: '+e],true); });
}
function laadStatus(){
  fetch('/api/status').then(function(r){return r.json()}).then(function(d){
    var l=q('#lijnstatus');
    if(!l) return;
    if(d.asterisk && d.asterisk.draait){
      l.textContent = d.asterisk.trunk_geregistreerd ? 'Asterisk draait, trunk geregistreerd.' : 'Asterisk draait, trunk nog niet geregistreerd.';
    } else {
      l.textContent = 'Asterisk antwoordt niet op deze machine.';
    }
  }).catch(function(){});
}
function breinMelding(tekst, isFout){
  var m=q('#brein-melding'); if(!m) return;
  m.hidden=false; m.className='klein'+(isFout?' fout':''); m.textContent=tekst;
}
function breinenLaad(){
  var vak=q('#breinen'); if(!vak) return;
  fetch('/api/breinen').then(function(r){return r.json()}).then(function(d){
    q('#brein-actief').textContent = d.actief.naam ? (d.actief.naam+' ('+(d.actief.model||'geen model')+')')
      : ((d.actief.url||'nog niet ingesteld')+' ('+(d.actief.model||'geen model')+')');
    vak.textContent='';
    var lijst=d.bewaard||[];
    if(!lijst.length){
      var leeg=document.createElement('div'); leeg.className='klein';
      leeg.textContent='Nog geen bewaarde breinen. Vul hierboven een endpoint in en bewaar het onder een naam.';
      vak.appendChild(leeg); return;
    }
    for(var i=0;i<lijst.length;i++){ vak.appendChild(breinRij(lijst[i])); }
  }).catch(function(e){ breinMelding('Kon de breinenlijst niet ophalen: '+e, true); });
}
function breinRij(b){
  var rij=document.createElement('div'); rij.className='brein-rij';
  var links=document.createElement('div');
  var naam=document.createElement('b'); naam.textContent=b.naam; links.appendChild(naam);
  if(b.actief){ var tag=document.createElement('span'); tag.className='tag'; tag.textContent='actief';
    links.appendChild(document.createTextNode(' ')); links.appendChild(tag); }
  var onder=document.createElement('div'); onder.className='klein';
  onder.textContent=b.url+' \u00b7 '+(b.model||'geen model')+(b.sleutel_ingevuld?' \u00b7 sleutel ingevuld':' \u00b7 geen sleutel');
  links.appendChild(onder); rij.appendChild(links);
  var knoppen=document.createElement('div'); knoppen.className='knoppen';
  var paren=[['gebruik','Gebruik'],['proef','Test'],['weg','Verwijder']];
  for(var i=0;i<paren.length;i++){
    var knop=document.createElement('button'); knop.className='knop'; knop.textContent=paren[i][1];
    knop.setAttribute('data-doe',paren[i][0]);
    knop.addEventListener('click',(function(actie,k){ return function(){ breinDoe(actie,b.naam,k); };})(paren[i][0],knop));
    knoppen.appendChild(knop);
  }
  rij.appendChild(knoppen);
  return rij;
}
function breinDoe(actie, naam, knop){
  if(knop) knop.disabled=true;
  if(actie==='proef'){ breinMelding('Bezig met testen...', false); }
  fetch('/api/breinen',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({actie:actie, naam:naam})})
    .then(function(r){return r.json()}).then(function(d){
      if(knop) knop.disabled=false;
      if(actie==='proef'){
        breinMelding(d.klaar?('Antwoord in '+d.seconden+' s: '+d.antwoord):('Mislukte: '+(d.fout||'onbekend')), !d.klaar);
        return;
      }
      if(!d.gelukt){ breinMelding(d.fout||'Mislukte', true); return; }
      if(actie==='gebruik'){
        breinMelding('Jarvis gebruikt nu '+d.naam+'.', false);
        var u=q('#JARVIS_BREIN_URL'), m=q('#JARVIS_BREIN_MODEL');
        if(u) u.value=d.url||''; if(m) m.value=d.model||'';
      } else {
        breinMelding('Verwijderd: '+d.naam, false);
      }
      breinenLaad(); laadStatus();
    }).catch(function(e){ if(knop) knop.disabled=false; breinMelding('Mislukte: '+e, true); });
}
function breinBewaar(){
  var naam=(q('#brein-naam').value||'').trim();
  if(!naam){ breinMelding('Geef eerst een naam.', true); return; }
  var u=q('#JARVIS_BREIN_URL'), m=q('#JARVIS_BREIN_MODEL'), s=q('#JARVIS_BREIN_SLEUTEL');
  breinMelding('Bezig met bewaren...', false);
  fetch('/api/breinen',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({actie:'bewaren', naam:naam, url:(u?u.value:''), model:(m?m.value:''), sleutel:(s?s.value:'')})})
    .then(function(r){return r.json()}).then(function(d){
      if(!d.gelukt){ breinMelding(d.fout||'Mislukte', true); return; }
      q('#brein-naam').value='';
      breinMelding((d.bijgewerkt?'Bijgewerkt: ':'Bewaard: ')+d.naam, false);
      breinenLaad();
    }).catch(function(e){ breinMelding('Mislukte: '+e, true); });
}
document.addEventListener('DOMContentLoaded', function(){
  laadStatus();
  breinenLaad();
  q('#bewaarknop').addEventListener('click', bewaar);
  q('#brein-bewaar').addEventListener('click', breinBewaar);
  q('#brein-proef').addEventListener('click', function(){ breinDoe('proef', ''); });
});
"""

INSTELLINGEN_BIJZONDER = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jarvis &middot; instellingen</title>
<style>__STIJL__</style>
</head>
<body>
<div class="wikkel">
  <header class="kop">
    <div class="merk">J</div>
    <div>
      <h1>Instellingen</h1>
      <div class="tagline">Vul hier je nummer en je gegevens in; de rest gebeurt vanzelf.</div>
    </div>
  </header>
  __NAV__
  <div class="let">Asterisk: <b id="lijnstatus">status ophalen...</b> Na opslaan leest Asterisk de nieuwe
  trunk meteen in &mdash; je hoeft niets te herstarten.</div>
  __FORMULIER__
  <div class="groep">
    <div class="groep-kop">Brein: aan welke Hermes is hij gekoppeld</div>
    <div class="groep-body">
      <p class="uitleg">Hierboven staan het adres, de sleutel en het model van het brein dat nu gebruikt wordt.
      Bewaar die combinatie hieronder onder een naam, dan wissel je met één klik: deze Hermes, een tweede Hermes
      in je netwerk, of een testmodel. Wisselen werkt meteen &mdash; een lopend gesprek daarna gebruikt het nieuwe
      brein, zonder herstart.</p>
      <div class="klein">Nu actief: <b id="brein-actief">ophalen...</b></div>
      <div id="breinen"></div>
      <div class="veld">
        <label for="brein-naam">Huidige brein bewaren als</label>
        <input type="text" id="brein-naam" placeholder="bijvoorbeeld: Hermes in de woonkamer">
        <span class="uitleg">Bewaar wat hierboven staat (endpoint, sleutel, model) onder een naam die je herkent.</span>
      </div>
      <div class="knoppen">
        <button class="knop" id="brein-bewaar">Bewaar huidige brein</button>
        <button class="knop" id="brein-proef">Test het actieve brein</button>
      </div>
      <div class="klein" id="brein-melding" hidden></div>
    </div>
  </div>
  <div class="groep">
    <div class="groep-kop">Waakhond: wat moet er in de gaten gehouden worden</div>
    <div class="groep-body">
      <p class="uitleg">Elke regel is een controle: een naam en een adres dat HTTP 200 teruggeeft.
      Gaat er één van goed naar stuk, dan belt Jarvis.</p>
      <div class="veld"><label for="waken">Controles (JSON)</label>
        <textarea id="waken" spellcheck="false">__WAKEN__</textarea>
        <span class="uitleg">Velden per controle: naam, url, code, tijd, dringend, meld_herstel, extra.</span>
      </div>
    </div>
  </div>
  <div class="knoppen"><button class="knop primair" id="bewaarknop">Opslaan en trunk herladen</button></div>
  <div class="melding" id="melding" hidden></div>
  <p class="uitleg">De sleutels en wachtwoorden worden nooit meer aan deze pagina teruggegeven: laat een
  geheim veld leeg om te bewaren wat er staat.</p>
  <footer>Jarvis &middot; &copy; Werdens Media</footer>
</div>
<script>__JS__</script>
</body>
</html>"""


def _veld_html(sleutel, meta, waarde):
    import html as _html
    soort = meta["soort"]
    naam = _html.escape(sleutel)
    label = _html.escape(meta["label"])
    uitleg = _html.escape(meta["uitleg"])
    if soort.startswith("keuze"):
        opties = soort.split(":", 1)[1].split("|")
        rijen = ""
        for optie in opties:
            gekozen = " selected" if str(waarde) == optie else ""
            rijen += '<option value="%s"%s>%s</option>' % (_html.escape(optie), gekozen, _html.escape(optie))
        veld = '<select data-sleutel="%s" id="%s">%s</select>' % (naam, naam, rijen)
    elif soort == "vlag":
        aangevinkt = " checked" if str(waarde) in ("1", "ja", "true", "aan") else ""
        return ('<div class="veld"><label class="vlak"><input type="checkbox" data-sleutel="%s" id="%s"%s>'
                '%s</label><span class="uitleg">%s</span></div>'
                % (naam, naam, aangevinkt, label, uitleg))
    elif meta["geheim"]:
        hint = "ingevuld &mdash; laat leeg om te bewaren" if waarde else "nog niet ingevuld"
        veld = ('<input type="password" data-sleutel="%s" data-geheim="1" id="%s" placeholder="%s" autocomplete="new-password">'
                % (naam, naam, hint))
    elif soort == "getal":
        veld = '<input type="number" data-sleutel="%s" id="%s" value="%s">' % (
            naam, naam, _html.escape(str(waarde)))
    else:
        veld = '<input type="text" data-sleutel="%s" id="%s" value="%s">' % (
            naam, naam, _html.escape(str(waarde)))
    return ('<div class="veld"><label for="%s">%s</label>%s<span class="uitleg">%s</span></div>'
            % (naam, label, veld, uitleg))


def _formulier_html(samenvatting):
    import html as _html
    uit = ""
    per_groep = {}
    for sleutel, meta in samenvatting["velden"].items():
        per_groep.setdefault(meta["groep"], []).append((sleutel, meta))
    for groep, titel in samenvatting["groepen"].items():
        velden = per_groep.get(groep)
        if not velden:
            continue
        uit += ('<div class="groep"><div class="groep-kop">%s</div><div class="groep-body">'
                % _html.escape(titel))
        for sleutel, meta in velden:
            uit += _veld_html(sleutel, meta, samenvatting["waarden"].get(sleutel, ""))
        uit += "</div></div>"
    return uit


def _instellingen_pagina(jarvis):
    from . import instellingen
    samenvatting = instellingen.samenvatting()
    tekst = INSTELLINGEN_BIJZONDER.replace("__STIJL__", STIJL).replace("__JS__", JS_INSTELLINGEN)
    tekst = tekst.replace("__NAV__", NAV % ("", "aan"))
    tekst = tekst.replace("__FORMULIER__", _formulier_html(samenvatting))
    waken = html.escape(instellingen.lees_waken())
    return tekst.replace("__WAKEN__", waken)


def _pagina(jarvis):
    cfg = jarvis.cfg
    tekst = PAGINA.replace("__STIJL__", STIJL).replace("__JS__", JS)
    tekst = tekst.replace("__TRUNK__", html.escape(cfg["TRUNK_NUMMER"] or "niet ingesteld"))
    mobiel = cfg["MIJN_MOBIEL"] or "niet ingesteld"
    if len(mobiel) > 6:
        mobiel = mobiel[:4] + "****" + mobiel[-2:]
    tekst = tekst.replace("__MOBIEL__", html.escape(mobiel))
    tekst = tekst.replace("__LUISTER__", html.escape(cfg["JARVIS_LUISTER"]))
    tekst = tekst.replace("__NAV__", "")   # de tabbalk staat er al in deze pagina
    tekst = tekst.replace("__LETOP__", _let_op_html(cfg))
    return tekst


def _let_op_html(cfg):
    """Bovenaan de statuspagina zeggen wat er nog ingesteld moet worden."""
    ontbreekt = []
    if not cfg["TRUNK_NUMMER"] or not cfg["TRUNK_WACHTWOORD"]:
        ontbreekt.append("het nummer en wachtwoord van je telefoonlijn")
    if not cfg["MIJN_MOBIEL"]:
        ontbreekt.append("het nummer dat Jarvis mag bellen")
    if not cfg["JARVIS_BREIN_SLEUTEL"] and "127.0.0.1" not in (cfg["JARVIS_BREIN_URL"] or ""):
        ontbreekt.append("de sleutel van je brein (Hermes)")
    if not ontbreekt:
        return ""
    lijst = ", ".join(ontbreekt)
    return ('<div class="let"><b>Nog niet ingericht.</b> Vul nog in: %s. '
            'Dat doe je op de pagina <a href="/instellingen">Instellingen</a>; '
            'opnieuw installeren is niet nodig.</div>' % html.escape(lijst))


def maak_handler(jarvis):
    cfg = jarvis.cfg
    wachtwoord = (cfg.get("JARVIS_WEB_WACHTWOORD") or "").strip()

    class Handler(BaseHTTPRequestHandler):
        server_version = "Jarvis/1.0"

        def _mag(self):
            if not wachtwoord:
                return True
            kop = self.headers.get("Authorization", "")
            if not kop.startswith("Basic "):
                return False
            try:
                _, _, gegeven = base64.b64decode(kop[6:]).decode("utf-8").partition(":")
                return gegeven == wachtwoord
            except Exception:
                return False

        def _stuur(self, code, lichaam, soort="application/json; charset=utf-8"):
            gegevens = lichaam.encode("utf-8") if isinstance(lichaam, str) else lichaam
            self.send_response(code)
            self.send_header("Content-Type", soort)
            self.send_header("Content-Length", str(len(gegevens)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(gegevens)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, code, gegevens):
            self._stuur(code, json.dumps(gegevens, ensure_ascii=False))

        def _body(self):
            try:
                lengte = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(lengte) or b"{}")
            except Exception:
                return {}

        def log_message(self, vorm, *args):
            return

        def do_GET(self):
            pad = self.path.split("?")[0]
            if pad == "/health":
                return self._json(200, {"status": "ok", "versie": "1.0.0"})
            if not self._mag():
                return self._stuur(401, "inloggen nodig", "text/plain; charset=utf-8")
            if pad in ("/", "/index.html"):
                return self._stuur(200, _pagina(jarvis), "text/html; charset=utf-8")
            if pad == "/api/status":
                uitgebreid = "uitgebreid=1" in self.path
                return self._json(200, jarvis.status(uitgebreid=uitgebreid))
            if pad == "/api/breinen":
                return self._json(200, jarvis.breinen())
            if pad == "/instellingen":
                return self._stuur(200, _instellingen_pagina(jarvis), "text/html; charset=utf-8")
            return self._json(404, {"fout": "onbekende route"})

        def do_POST(self):
            if not self._mag():
                return self._stuur(401, "inloggen nodig", "text/plain; charset=utf-8")
            pad = self.path.split("?")[0]
            gegevens = self._body()
            if pad == "/api/melding":
                tekst = (gegevens.get("tekst") or "").strip()
                if not tekst:
                    return self._json(400, {"gelukt": False, "fout": "geen tekst meegegeven"})
                return self._json(200, jarvis.bel_melding(
                    tekst, reden=gegevens.get("reden", ""), nummer=gegevens.get("nummer", "")))
            if pad == "/api/bel":
                return self._json(200, jarvis.bel_gesprek(gegevens.get("nummer", "")))
            if pad == "/api/breinen":
                actie = (gegevens.get("actie") or "").strip().lower()
                naam = gegevens.get("naam") or ""
                if actie in ("bewaren", "bewaar", "opslaan"):
                    return self._json(200, jarvis.brein_bewaar(
                        naam, gegevens.get("url"), gegevens.get("model"), gegevens.get("sleutel")))
                if actie in ("gebruiken", "gebruik", "kies"):
                    return self._json(200, jarvis.brein_kies(naam))
                if actie in ("verwijderen", "weg", "verwijder"):
                    return self._json(200, jarvis.brein_verwijder(naam))
                if actie in ("proef", "test", "testen"):
                    return self._json(200, jarvis.brein_proef(
                        naam, gegevens.get("url"), gegevens.get("model"), gegevens.get("sleutel")))
                return self._json(400, {"gelukt": False,
                                        "fout": "onbekende actie: %s" % (actie or "(leeg)")})
            if pad == "/api/instellingen":
                return self._json(200, jarvis.instellingen_opslaan(
                    gegevens.get("velden") or {}, gegevens.get("waken")))
            return self._json(404, {"fout": "onbekende route"})

    return Handler


def start_web(jarvis, host, poort):
    server = ThreadingHTTPServer((host, poort), maak_handler(jarvis))
    server.daemon_threads = True
    draad = threading.Thread(target=server.serve_forever, name="web", daemon=True)
    draad.start()
    return server