# Meal Stress Analyzer

Log je maaltijden via Telegram of via een webapp-snelkoppeling op je
startscherm, koppel dat aan je Garmin-stressdata, en zie welk eten
samenhangt met een hogere lichaamsstress na het eten.

Draait serverless op Google Cloud Run; alle data komt in een Google Sheet
terecht (makkelijk zelf in te kijken, en direct te delen of als CSV te
exporteren naar een ander tool of een AI). Geen server om te beheren, geen
kosten zolang je binnen Cloud Run's gratis tier blijft (bij een enkele
gebruiker met een paar berichten per dag kom je daar niet in de buurt).

## Hoe het werkt

1. Je logt wat je hebt gegeten, via Telegram of via de webapp-snelkoppeling
   op je startscherm -- allebei schrijven naar dezelfde spreadsheet, kies
   wat je op een moment handiger vindt. Telegram roept daarvoor direct de
   Cloud Run-service aan (een "webhook"); de webapp praat via een paar
   simpele API-routes met diezelfde service. Beide draaien alleen op het
   moment dat er iets gebeurt. Staat er geen tijd in je bericht, dan wordt
   het moment van loggen gebruikt als eettijd.
2. Elke nacht (en op elk moment dat je `/analyse` gebruikt) haalt een
   Cloud Scheduler-taak je Garmin-stressdata, hartslag en body battery op via
   de onofficiele `garminconnect`-library, en slaat zowel de ruwe API-respons
   als een geparste versie op in het Google Sheet.
3. `/analyse` vergelijkt per maaltijd de gemiddelde stress 30 min voor het
   eten met 1-2,5 uur erna, en groepeert die verschillen per woord uit je
   berichten ("pizza", "koffie", ...) zodat je ziet welke dingen vaker met een
   piek samenvallen.

Alle ruwe data (volledige Garmin-responses, volledige Telegram-berichten)
blijft in het Sheet staan, ook wat de huidige analyse niet gebruikt. Zo kun
je die data later door iets anders laten analyseren -- een AI, een notebook,
wat dan ook -- zonder dat je opnieuw hoeft te loggen: deel het Sheet, of
exporteer een tab als CSV.

Dit is een hulpmiddel om patronen te zien, geen medisch instrument: kleine
steekproef, correlatie is geen oorzaak, en de tekst-naar-voedsel-herkenning
is een simpele woordsplitser, geen voedingsdatabase.

## Architectuur

- **Cloud Run**: host de Flask-app (`main.py`), met twee ingangen die dezelfde
  code delen (`app/meal_logging.py`, `app/state.py`): de Telegram-webhook en
  een kleine PWA (installeerbare webapp) op `/app`. Schaalt naar 0 als er
  niets gebeurt, wordt wakker bij een binnenkomend bericht (via Telegram of
  de webapp) of de dagelijkse Cloud Scheduler-taak.
- **Google Sheet**: opslag, zes tabbladen. `leesmij` (het eerste tabblad --
  uitleg over elke tab/kolom, hoe de eigen `/analyse` van de bot werkt, en de
  kanttekeningen, geschreven zodat een ander tool of een AI de data zonder de
  broncode kan begrijpen). `meals` (gelogde maaltijden,
  inclusief het volledige ruwe Telegram-bericht als JSON), `garmin_stress`
  (geparste stresswaarden, een rij per meting, voor snelle analyse),
  `garmin_daily` (fetch-status per dag), `garmin_raw` (de volledige,
  onbewerkte Garmin-responses per dag: stress, hartslag, body battery, als
  JSON) en `counters` (interne id-teller). De Cloud Run-service krijgt alleen
  toegang doordat jij het Sheet met het service-account deelt, net zoals je
  een Sheet met een collega zou delen -- geen GCP-rol nodig.
- **Secret Manager**: bewaart je Telegram bot-token en je Garmin-sessietokens
  (niet je Garmin-wachtwoord -- dat komt nergens in de cloud terecht).
- **Cloud Scheduler**: triggert 1x per dag het ophalen van Garmin-data, zodat
  het archief ook opbouwt op dagen dat je niet expliciet `/analyse` aanroept.

Waarom niet puur Google Apps Script (wat ook gratis bij Workspace hoort):
Apps Script kan het Telegram- en Sheets-deel prima, maar de Garmin-inlogflow
vereist een specifieke workaround (`curl_cffi`, TLS-fingerprint van een
browser nabootsen) om langs Garmin's bot-detectie te komen. Dat zit alleen in
de Python-library, niet te doen met Apps Script's `UrlFetchApp`. Cloud Run
kan gewoon een normale Python-container draaien en past qua "geen server
beheren, gratis tier" bij wat je zocht, terwijl de opslag toch een gewoon
Sheet blijft.

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

Vraagt om je GCP project-id (of maak er een aan in de Cloud Console als je
nog geen project hebt), je Telegram bot-token en je user-id, en regelt de
rest: APIs inschakelen, een service account met minimale rechten, secrets,
de container bouwen en deployen, de Telegram-webhook instellen, en de
dagelijkse Garmin-fetch inplannen.

Onderweg vraagt het script ook om een spreadsheet-id. Los daarvan op:

- Maak een leeg Google Sheet op sheets.google.com.
- Klik Delen, en deel 'm als **Editor** met het service-account-e-mailadres
  dat `deploy.sh` net printte (iets als
  `meal-stress-bot-sa@<jouw-project-id>.iam.gserviceaccount.com`) -- exact
  zoals je 'm met een collega zou delen.
- Kopieer het stuk uit de URL tussen `/d/` en `/edit`
  (`https://docs.google.com/spreadsheets/d/`**`DIT-STUK`**`/edit`) en plak dat
  als antwoord in `deploy.sh`.

De vijf tabbladen (meals, garmin_stress, garmin_daily, garmin_raw, counters)
maakt de bot zelf aan zodra hij voor het eerst draait.

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
sudo chown -R "$(id -u):$(id -g)" garmin_tokens
./scripts/pack_and_upload_garmin_tokens.sh garmin_tokens "$(gcloud config get-value project)"
./deploy.sh
```

De `sudo chown` is nodig omdat de container als root draait: zonder die stap is
de `garmin_tokens`-map op de host eigendom van root en krijg je bij de
volgende regel `tar: .: Cannot stat: Permission denied`.

De laatste `./deploy.sh` herdeployt de service zodat hij de net geuploade
Garmin-tokens oppikt (je eerder ingevulde antwoorden staan in
`.deploy_state.env`, dus je hoeft niet alles opnieuw in te typen).

MFA-code gevraagd? Typ 'm meteen over uit je authenticator-app op het moment
zelf -- die codes zijn maar ~30 seconden geldig, een oudere code laat het
inloggen mislukken. Krijg je op de eerste twee regels `429`
("rate limited")? Dat is normaal, de library valt automatisch terug op een
volgende inlogmethode; wacht gewoon de MFA-prompt af.

### 5. Testen

Stuur `/start` naar je bot in Telegram, dan een testbericht zoals "havermout
met banaan", en check met `/maaltijden` of hij binnenkwam. `/analyse` heeft
pas genoeg data na een paar dagen loggen.

### 6. Webapp-snelkoppeling op je Android-startscherm (optioneel)

`deploy.sh` print aan het einde een URL met een geheime token erin, iets als:

```
https://meal-stress-bot-xxxx.run.app/app?token=<lange-willekeurige-code>
```

Open die URL op je telefoon in Chrome, tik op het menu (drie puntjes) en kies
"App toevoegen" of "Toevoegen aan startscherm". De token staat vast in die
link, dus je hoeft nergens in te loggen -- het icoon werkt gewoon elke keer.
Behandel die link als een wachtwoord: iedereen die 'm heeft kan mee loggen.
Kwijt of gelekt? Verwijder de `WEBAPP_TOKEN`-regel uit `.deploy_state.env` en
draai `./deploy.sh` opnieuw voor een nieuwe token, en maak een nieuwe
snelkoppeling.

## Gebruik

**Via Telegram:**
- Stuur een berichtje: `om 18:30 pizza margherita gegeten`
- `/maaltijden` - laatste 10 gelogde maaltijden met hun id
- `/verwijder <id>` - een verkeerd gelogde maaltijd wissen
- `/analyse` - analyseer welk eten samenhangt met verhoogde stress
- `/help` - uitleg in de bot zelf

**Via de webapp:** open de snelkoppeling, typ wat je gegeten hebt, tik
Loggen. De laatste 20 maaltijden staan eronder (met een wis-knop), en
"Analyseer" geeft hetzelfde rapport als `/analyse` in Telegram.

## Kosten

Bij een enkele gebruiker blijf je ruim binnen de gratis tiers: Cloud Run (2
miljoen requests/maand gratis), Cloud Scheduler (3 taken gratis), Secret
Manager (6 actieve secret-versies gratis). Google Sheets kost sowieso niks.
Er is geen always-on server die 24/7 doorloopt te betalen.

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
- Google Sheets is geen database: bij heel veel jaren aan data (honderdduizenden
  rijen in `garmin_stress`) loop je op een gegeven moment tegen Sheets' eigen
  limieten aan (10 miljoen cellen per spreadsheet, ~100 requests/100 sec).
  `/analyse` leest het stress-tabblad steeds in zijn geheel in (één keer per
  analyse, niet per maaltijd), dus dat blijft lang werken, maar bij jaren aan
  data merk je op een gegeven moment dat het trager wordt.
