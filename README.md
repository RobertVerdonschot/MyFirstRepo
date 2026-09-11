# Meal Stress Analyzer

Log je maaltijden via Telegram, koppel dat aan je Garmin-stressdata, en zie
welk eten samenhangt met een hogere lichaamsstress na het eten.

## Hoe het werkt

1. Je stuurt een berichtje naar je eigen Telegram-bot met wat je hebt
   gegeten. Staat er geen tijd in, dan wordt het tijdstip van het bericht
   gebruikt als eettijd.
2. `/analyse` haalt (via de onofficiele `garminconnect`-library) je
   stress-metingen van je Garmin Fenix op voor de relevante dagen, en
   vergelijkt per maaltijd de gemiddelde stress 30 min voor het eten met de
   gemiddelde stress 1-2,5 uur erna.
3. Die verschillen worden gegroepeerd per woord uit je berichten ("pizza",
   "koffie", ...) zodat je ziet welke dingen vaker met een piek samenvallen.

Dit is een hulpmiddel om patronen te zien, geen medisch instrument: kleine
steekproef, correlatie is geen oorzaak, en de tekst-naar-voedsel-herkenning
is een simpele woordsplitser, geen voedingsdatabase.

## Waarom deze opzet

- **Telegram in plaats van WhatsApp**: WhatsApp heeft geen gratis officiele
  manier om je eigen berichten uit te lezen. De officiele Business API kost
  geld en vereist een Meta-zakelijk-account; de onofficiele libraries loggen
  in met je eigen account via QR-code, moeten continu draaien en zijn tegen
  WhatsApp's voorwaarden (risico op accountbeperking). Telegram's bot-API is
  gratis, officieel, en werkt net zo makkelijk vanaf je telefoon.
- **Long polling, geen webhook**: de bot hoeft niet vanaf het internet
  bereikbaar te zijn. Dat betekent geen publiek IP, geen domeinnaam, geen
  poort-forwarding nodig -- hij kan gewoon thuis draaien.
- **Garmin**: Garmin heeft geen publieke consumenten-API. `garminconnect` is
  een onofficiele, veelgebruikte library die met je normale Garmin
  Connect-account inlogt. Kan in theorie stoppen te werken als Garmin iets
  wijzigt in hun backend.

## Waar laten draaien

Omdat de bot alleen uitgaande verbindingen nodig heeft (polling naar
Telegram, en op verzoek naar Garmin), is elke altijd-actieve machine met
internettoegang genoeg. Twee redelijke opties:

- **Iets dat je toch al thuis aan hebt staan** (Raspberry Pi, NAS, oude
  laptop): geen extra kosten, jij beheert het, geen open poorten nodig.
- **Een goedkope VPS** (bijv. Hetzner, DigitalOcean, een paar euro/maand):
  handig als je thuis niets always-on hebt draaien.

Beide draaien met dezelfde Docker-opzet hieronder.

## Setup

1. Maak een Telegram-bot via [@BotFather](https://t.me/BotFather), noteer
   het token. Zoek je eigen numerieke Telegram user-id op via
   [@userinfobot](https://t.me/userinfobot).
2. `cp .env.example .env` en vul in: `TELEGRAM_BOT_TOKEN`,
   `ALLOWED_TELEGRAM_USER_ID`, `GARMIN_EMAIL`, `GARMIN_PASSWORD`, en je
   `TIMEZONE` (staat al goed op Europe/Amsterdam).
3. Eenmalig lokaal inloggen bij Garmin (dit kan om een MFA-code vragen, dus
   moet interactief in een terminal, niet in de achtergrond-container):

   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python scripts/garmin_login_setup.py
   ```

   Dit zet een sessie-token in `data/garmin_tokens`. De bot zelf heeft
   daarna je Garmin-wachtwoord niet meer nodig.
4. Start de bot:

   ```bash
   docker compose up -d --build
   ```

5. Stuur `/start` naar je bot in Telegram.

## Gebruik

- Stuur een berichtje: `om 18:30 pizza margherita gegeten`
- `/maaltijden` - laatste 10 gelogde maaltijden met hun id
- `/verwijder <id>` - een verkeerd gelogde maaltijd wissen
- `/analyse` - analyseer welk eten samenhangt met verhoogde stress
- `/help` - uitleg in de bot zelf

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
