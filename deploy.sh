#!/usr/bin/env bash
# Provisions and deploys the meal-stress-analyzer on Google Cloud Run,
# storing everything in a Google Sheet.
#
# Run this in Google Cloud Shell (shell.cloud.google.com) -- it comes
# pre-authenticated to your Google account and has gcloud, so you don't need
# to install anything locally. Just clone the repo there and run this script.
#
# What this script does NOT do for you (because it needs credentials/clicks
# only you can do): creating the Telegram bot (via @BotFather), creating and
# sharing the Google Sheet (see README, "Google Sheet aanmaken"), and the
# one-time interactive Garmin login (scripts/garmin_login_setup.py).
# Everything else -- enabling APIs, service account, secrets, building and
# deploying the container, the webhook, the daily fetch job -- this script
# sets up.
set -euo pipefail

STATE_FILE=".deploy_state.env"
[ -f "$STATE_FILE" ] && source "$STATE_FILE"

# Niet-interactief (GitHub Actions / CI): nooit om input vragen, maar alle
# waarden uit de al draaiende Cloud Run-service en Secret Manager halen.
NONINTERACTIVE="false"
if [ -n "${CI:-}" ] || [ ! -t 0 ]; then
  NONINTERACTIVE="true"
fi

# In GitHub Actions: zorg dat een waarde nooit leesbaar in de (publieke) log komt.
_mask() {
  if [ "${GITHUB_ACTIONS:-}" = "true" ] && [ -n "${1:-}" ]; then
    echo "::add-mask::$1"
  fi
}

# _ask VAR "prompt": vraagt interactief, of stopt met een duidelijke fout in CI.
_ask() {
  local var="$1" prompt="$2" value
  if [ "$NONINTERACTIVE" = "true" ]; then
    echo "FOUT: $var is niet bekend en dit script draait niet-interactief." >&2
    echo "Draai ./deploy.sh een keer handmatig in Cloud Shell zodat de service bestaat." >&2
    exit 1
  fi
  read -rp "$prompt" value
  printf -v "$var" '%s' "$value"
}

REGION="${REGION:-europe-west1}"
SERVICE_NAME="${SERVICE_NAME:-meal-stress-bot}"
SA_NAME="${SA_NAME:-meal-stress-bot-sa}"

echo "== Meal Stress Analyzer: Cloud Run deploy =="

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud niet gevonden. Draai dit script in Google Cloud Shell (shell.cloud.google.com)."
  exit 1
fi

if [ -z "${PROJECT_ID:-}" ]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "(unset)" ]; then
  _ask PROJECT_ID "GCP project-id: "
  gcloud config set project "$PROJECT_ID"
fi
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Project: $PROJECT_ID | Regio: $REGION | Service: $SERVICE_NAME"
echo

if [ "${SKIP_API_ENABLE:-}" = "1" ]; then
  echo "-- APIs inschakelen overgeslagen (SKIP_API_ENABLE=1) --"
else
echo "-- APIs inschakelen --"
gcloud services enable \
  run.googleapis.com \
  sheets.googleapis.com \
  drive.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  artifactregistry.googleapis.com \
  --project "$PROJECT_ID"
fi

echo "-- Service account --"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" \
    --project "$PROJECT_ID" --display-name="Meal stress analyzer bot"
fi
# Geen GCP IAM-rol nodig voor Sheets-toegang: dat regel je door de sheet te
# delen met het SA-e-mailadres hieronder, net als met een collega.

# Ontbrekende waarden (geen .deploy_state.env, bv. in GitHub Actions) ophalen
# uit de bestaande Cloud Run-service en Secret Manager, zodat tokens zoals
# WEBAPP_TOKEN niet stilletjes vervangen worden.
_load_from_existing_service() {
  local json key value
  json="$(gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" \
    --region "$REGION" --format=json 2>/dev/null)" || return 0
  echo "(bestaande waarden overgenomen van Cloud Run-service $SERVICE_NAME)"
  while IFS='=' read -r key value; do
    case "$key" in
      SPREADSHEET_ID|TELEGRAM_SECRET_TOKEN|SCHEDULER_SHARED_SECRET|WEBAPP_TOKEN|ALLOWED_TELEGRAM_USER_ID|TIMEZONE)
        if [ -z "${!key:-}" ]; then printf -v "$key" '%s' "$value"; fi ;;
    esac
  done < <(printf '%s' "$json" | python3 -c '
import json, sys
svc = json.load(sys.stdin)
for e in svc["spec"]["template"]["spec"]["containers"][0].get("env", []):
    if "value" in e:
        print(e["name"] + "=" + e["value"])
')
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
    TELEGRAM_BOT_TOKEN="$(gcloud secrets versions access latest --secret telegram-bot-token \
      --project "$PROJECT_ID" 2>/dev/null || true)"
  fi
}
_load_from_existing_service

echo
echo "-- Google Sheet --"
if [ -z "${SPREADSHEET_ID:-}" ]; then
  echo "Maak (als je dat nog niet deed) een leeg Google Sheet op sheets.google.com,"
  echo "en deel 'm als Editor met dit e-mailadres:"
  echo
  echo "  ${SA_EMAIL}"
  echo
  echo "De spreadsheet-id staat in de URL: https://docs.google.com/spreadsheets/d/<DIT-STUK>/edit"
  _ask SPREADSHEET_ID "Spreadsheet-id: "
fi

echo "-- Secrets --"
if [ -z "${TELEGRAM_SECRET_TOKEN:-}" ]; then
  TELEGRAM_SECRET_TOKEN="$(openssl rand -hex 32)"
fi
if [ -z "${SCHEDULER_SHARED_SECRET:-}" ]; then
  SCHEDULER_SHARED_SECRET="$(openssl rand -hex 32)"
fi
if [ -z "${WEBAPP_TOKEN:-}" ]; then
  WEBAPP_TOKEN="$(openssl rand -hex 24)"
fi
_mask "$TELEGRAM_SECRET_TOKEN"; _mask "$SCHEDULER_SHARED_SECRET"; _mask "$WEBAPP_TOKEN"

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo
  echo "Maak eerst een bot via @BotFather in Telegram als je dat nog niet deed."
  _ask TELEGRAM_BOT_TOKEN "Telegram bot-token: "
fi
if [ -z "${ALLOWED_TELEGRAM_USER_ID:-}" ]; then
  echo "Vraag je eigen numerieke user-id op via @userinfobot in Telegram."
  _ask ALLOWED_TELEGRAM_USER_ID "Jouw Telegram user-id: "
fi
TIMEZONE="${TIMEZONE:-Europe/Amsterdam}"
_mask "$TELEGRAM_BOT_TOKEN"

_upsert_secret() {
  local name="$1" value="$2"
  if gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    # Alleen een nieuwe versie als de waarde echt veranderd is (scheelt
    # secret-versies; gratis tier is 6 actieve versies).
    if [ "$(gcloud secrets versions access latest --secret "$name" --project "$PROJECT_ID" 2>/dev/null || true)" != "$value" ]; then
      printf '%s' "$value" | gcloud secrets versions add "$name" --project "$PROJECT_ID" --data-file=-
    fi
  else
    printf '%s' "$value" | gcloud secrets create "$name" --project "$PROJECT_ID" \
      --data-file=- --replication-policy=automatic
  fi
  gcloud secrets add-iam-policy-binding "$name" --project "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" --role="roles/secretmanager.secretAccessor" >/dev/null
}

_upsert_secret "telegram-bot-token" "$TELEGRAM_BOT_TOKEN"

GARMIN_SECRET_EXISTS="false"
if gcloud secrets describe garmin-tokens-b64 --project "$PROJECT_ID" >/dev/null 2>&1; then
  GARMIN_SECRET_EXISTS="true"
  gcloud secrets add-iam-policy-binding garmin-tokens-b64 --project "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" --role="roles/secretmanager.secretAccessor" >/dev/null
else
  echo "Let op: nog geen Garmin-tokens geupload (garmin-tokens-b64 secret bestaat niet)."
  echo "Meal-logging werkt al wel; /analyse pas nadat je scripts/garmin_login_setup.py en"
  echo "scripts/pack_and_upload_garmin_tokens.sh hebt gedraaid en dit script opnieuw hebt gedraaid."
fi

if [ "$NONINTERACTIVE" = "true" ]; then
  echo "(niet-interactief: $STATE_FILE wordt niet geschreven)"
else
cat > "$STATE_FILE" <<EOF
PROJECT_ID=$PROJECT_ID
REGION=$REGION
SERVICE_NAME=$SERVICE_NAME
SA_NAME=$SA_NAME
SPREADSHEET_ID=$SPREADSHEET_ID
TELEGRAM_SECRET_TOKEN=$TELEGRAM_SECRET_TOKEN
SCHEDULER_SHARED_SECRET=$SCHEDULER_SHARED_SECRET
WEBAPP_TOKEN=$WEBAPP_TOKEN
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
ALLOWED_TELEGRAM_USER_ID=$ALLOWED_TELEGRAM_USER_ID
TIMEZONE=$TIMEZONE
EOF
echo "(secrets/instellingen bewaard in $STATE_FILE zodat je dit script kan herhalen zonder alles opnieuw in te tikken -- zet dit bestand niet in git)"
fi

echo
echo "-- Bouwen en deployen naar Cloud Run --"
ENV_VARS="ALLOWED_TELEGRAM_USER_ID=${ALLOWED_TELEGRAM_USER_ID},TIMEZONE=${TIMEZONE},TELEGRAM_SECRET_TOKEN=${TELEGRAM_SECRET_TOKEN},SCHEDULER_SHARED_SECRET=${SCHEDULER_SHARED_SECRET},WEBAPP_TOKEN=${WEBAPP_TOKEN},SPREADSHEET_ID=${SPREADSHEET_ID}"
SECRET_REFS="TELEGRAM_BOT_TOKEN=telegram-bot-token:latest"
if [ "$GARMIN_SECRET_EXISTS" = "true" ]; then
  SECRET_REFS="${SECRET_REFS},GARMIN_TOKENS_B64=garmin-tokens-b64:latest"
fi

gcloud run deploy "$SERVICE_NAME" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --source . \
  --allow-unauthenticated \
  --service-account "$SA_EMAIL" \
  --set-env-vars "$ENV_VARS" \
  --set-secrets "$SECRET_REFS" \
  --min-instances=0 --max-instances=2

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
echo "Service-URL: $SERVICE_URL"

echo
echo "-- Telegram webhook instellen --"
./scripts/set_telegram_webhook.sh "$TELEGRAM_BOT_TOKEN" "$SERVICE_URL" "$TELEGRAM_SECRET_TOKEN"

echo
echo "-- Dagelijkse Garmin-fetch (Cloud Scheduler) --"
if gcloud scheduler jobs describe fetch-garmin-daily --project "$PROJECT_ID" --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http fetch-garmin-daily \
    --project "$PROJECT_ID" --location "$REGION" \
    --uri="${SERVICE_URL}/tasks/fetch-garmin" --http-method=POST \
    --update-headers="X-Scheduler-Secret=${SCHEDULER_SHARED_SECRET}" \
    --schedule="50 23 * * *" --time-zone="$TIMEZONE"
else
  gcloud scheduler jobs create http fetch-garmin-daily \
    --project "$PROJECT_ID" --location "$REGION" \
    --uri="${SERVICE_URL}/tasks/fetch-garmin" --http-method=POST \
    --headers="X-Scheduler-Secret=${SCHEDULER_SHARED_SECRET}" \
    --schedule="50 23 * * *" --time-zone="$TIMEZONE"
fi

echo
echo "== Klaar =="
echo "Stuur /start naar je bot in Telegram om te testen."
echo
if [ "$NONINTERACTIVE" = "true" ]; then
  echo "Webapp-token niet geprint (publieke log). Bestaande snelkoppeling blijft werken."
else
echo "Webapp-snelkoppeling (open deze URL op je Android-telefoon in Chrome, dan"
echo "menu > 'App toevoegen'/'Toevoegen aan startscherm'):"
echo
echo "  ${SERVICE_URL}/app?token=${WEBAPP_TOKEN}"
echo
fi
if [ "$GARMIN_SECRET_EXISTS" = "false" ]; then
  echo "Nog te doen voor /analyse werkt: Garmin-login (zie README, stap 'Garmin koppelen')."
fi
