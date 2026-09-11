# Meal Stress Analyzer

Log je maaltijden via Telegram, koppel dat aan je Garmin-stressdata, en zie
welk eten samenhangt met een hogere lichaamsstress na het eten.

Draait volledig serverless op Google Cloud Run + Firestore: geen server om te
beheren, geen kosten zolang je binnen de gratis tier blijft (bij een enkele
gebruiker met een paar berichten per dag kom je daar niet in de buurt).

## Hoe het werkt

1. Je stuurt een berichtje naar je eigen Telegram-bot met wat je hebt
   gegeten. Telegram roept daarvoor direct de Cloud Run-service aan (een
   "webhook"), die alleen draait op het moment dat er iets gebeurt. Staat er
   geen tijd in je bericht, dan wordt het tijdstip van het Telegram-bericht
   gebruikt als eettijd.
2. Elke nacht (en op elk moment dat je `/analyse` gebruikt) haalt een
   Cloud Scheduler-taak je Garmin-stressdata, hartslag en body battery op via
   de onofficiele `garminconnect`-library, en slaat zowel de ruwe API-respons
   als een geparste versie op in Firestore.
3. `/analyse` vergelijkt per maaltijd de gemiddelde stress 30 min voor het
   eten met 1-2,5 uur erna, en groepeert die verschillen per woord uit je
   berichten ("pizza", "koffie", ...) zodat je ziet welke dingen vaker met een
   piek samenvallen.

Alle ruwe data (volledige Garmin-responses, volledige Telegram-berichten)
blijft in Firestore staan, ook wat de huidige analyse niet gebruikt. Zo kun
je die data later door iets anders laten analyseren -- een AI, een notebook,
wat dan ook -- zonder dat je opnieuw hoeft te loggen.

Dit is een hulpmiddel om patronen te zien, geen medisch instrument: kleine
steekproef, correlatie is geen oorzaak, en de tekst-naar-voedsel-herkenning
is een simpele woordsplitser, geen voedingsdatabase.

## Architectuur

- **Cloud Run**: host de Flask-webapp (`main.py`). Schaalt naar 0 als er
  niets gebeurt, wordt wakker bij een binnenkomend Telegram-bericht of de
  dagelijkse Cloud Scheduler-taak.
- **Firestore**: opslag. Collecties `meals` (gelogde maaltijden, inclusief
  het volledige ruwe Telegram-bericht), `garmin_daily` (geparste
  stresswaarden per dag, voor snelle analyse) en `garmin_raw` (de volledige,
  onbewerkte Garmin-responses per dag: stress, hartslag, body battery).
- **Secret Manager**: bewaart je Telegram bot-token en je Garmin-sessietokens.
- **Cloud Scheduler**: triggert 1x per dag het ophalen van Garmin-data, zodat
  het archief ook opbouwt op dagen dat je niet expliciet `/analyse` aanroept.

Waarom niet puur Google Apps Script (wat ook gratis bij Workspace hoort):
Apps Script kan het Telegram-deel prima, maar de Garmin-inlogflow vereist een
specifieke workaround (`curl_cffi`, TLS-fingerprint van een browser nabootsen)
om langs Garmin's bot-detectie te komen. Dat is er alleen in de
Python-library, niet te doen met Apps Script's `UrlFetchApp`. Cloud Run kan
gewoon een normale Python-container draaien en past qua "geen server
beheren, gratis tier" bij wat je zocht.

## Eenmalige setup

Je hebt geen lokale installatie nodig: doe dit in **Google Cloud Shell**
(shell.cloud.google.com, in de browser, al ingelogd met je Google-account).

### 1. Telegram-bot aanmaken (in de Telegram-app)

- Chat met **@BotFather** -> `/newbot` -> volg de stappen -> je krijgt een token.
- Chat met **@userinfobot** -> geeft je eigen numerieke user-id.

### 2. Code ophalen (in Cloud Shell)

```bash
git clone -b claude/meal-stress-analyzer-n0g9ak https://github.com/RobertVerdonschot/MyFirstRepo.git
cd MyFirstRepo
```

### 3. Deployen

```bash
./deploy.sh
```

Vraagt om je GCP project-id (of maakt er een aan als je die nog niet hebt --
zie de Cloud Console als je nog geen project hebt), je Telegram bot-token en
je user-id, en regelt de rest: APIs inschakelen, Firestore-database, een
service account met minimale rechten, secrets, de container bouwen en
deployen, de Telegram-webhook instellen, en de dagelijkse Garmin-fetch
inplannen.

Op dit punt werkt meal-logging al. `/analyse` geeft nog een foutmelding tot
je ook Garmin gekoppeld hebt:

### 4. Garmin koppelen

Interactief inloggen (kan om een MFA-code vragen) kan niet vanuit Cloud Run
zelf -- dat moet ergens met een terminal, dus ook hier gewoon in Cloud Shell.
`garminconnect` vereist Python 3.12+, dat Cloud Shell's systeem-Python niet
per se heeft, dus dit draait in een tijdelijke container (Cloud Shell heeft
Docker al klaarstaan):

```bash
docker run --rm -it -v "$PWD:/work" -w /work python:3.12-slim bash -c \
  "pip install -q garminconnect && python scripts/garmin_login_setup.py garmin_tokens"
./scripts/pack_and_upload_garmin_tokens.sh garmin_tokens "$(gcloud config get-value project)"
./deploy.sh
```

De laatste `./deploy.sh` herdeployt de service zodat hij de net geuploade
Garmin-tokens oppikt (je eerder ingevulde antwoorden staan in
`.deploy_state.env`, dus je hoeft niet alles opnieuw in te typen).

### 5. Testen

Stuur `/start` naar je bot in Telegram, dan een testbericht zoals "havermout
met banaan", en check met `/maaltijden` of hij binnenkwam. `/analyse` heeft
pas genoeg data na een paar dagen loggen.

## Gebruik

- Stuur een berichtje: `om 18:30 pizza margherita gegeten`
- `/maaltijden` - laatste 10 gelogde maaltijden met hun id
- `/verwijder <id>` - een verkeerd gelogde maaltijd wissen
- `/analyse` - analyseer welk eten samenhangt met verhoogde stress
- `/help` - uitleg in de bot zelf

## Kosten

Bij een enkele gebruiker blijf je ruim binnen de gratis tiers: Cloud Run (2
miljoen requests/maand gratis), Firestore (1 GB opslag, 1000 writes/dag
gratis), Cloud Scheduler (3 taken gratis), Secret Manager (6 actieve
secret-versies gratis). Er is geen always-on server die 24/7 doorloopt te
betalen.

## Beperkingen

- De tijd-herkenning snapt alleen expliciete tijden ("18:30", "om 8 uur",
  "8u") en "gisteren". Relatieve dingen als "net" of "een uurtje geleden"
  worden niet begrepen -- dan valt het terug op het tijdstip van je
  Telegram-bericht.
- De voedsel-herkenning is een simpele woordsplitser met een stopwoordenlijst,
  geen voedingsdatabase: "brood" en "boterham" worden niet als hetzelfde
  gezien.
- Een woord telt pas mee in het "signaal"-overzicht van `/analyse` als het
  in minstens 2 maaltijden voorkomt met genoeg Garmin-data eromheen.
- Als je Garmin-horloge rond etenstijd niet gedragen wordt, mist er data en
  wordt die maaltijd overgeslagen in de analyse.
- De onofficiele Garmin-library kan stoppen te werken als Garmin iets aan hun
  backend verandert. Faalt de login blijvend, herhaal dan stap 4.
