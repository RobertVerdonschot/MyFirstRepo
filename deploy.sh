#!/usr/bin/env bash
# Provisions and deploys the meal-stress-analyzer on Google Cloud Run + Firestore.
#
# Run this in Google Cloud Shell (shell.cloud.google.com) -- it comes
# pre-authenticated to your Google account and has gcloud, so you don't need
# to install anything locally. Just clone the repo there and run this script.
#
# What this script does NOT do for you (because it needs credentials only
# you have): creating the Telegram bot (via @BotFather in the Telegram app)
# and the one-time interactive Garmin login (scripts/garmin_login_setup.py).
# Everything else -- enabling APIs, Firestore, service account, secrets,
# building and deploying the container, the webhook, the daily fetch job --
# this script sets up.
set -euo pipefail

STATE_FILE=".deploy_state.env"
[ -f "$STATE_FILE" ] && source "$STATE_FILE"

REGION="${REGION:-europe-west1}"
SERVICE_NAME="${SERVICE_NAME:-meal-stress-bot}"
SA_NAME="${SA_NAME:-meal-stress-bot-sa}"

echo "== Meal Stress Analyzer: Cloud Run deploy =="

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud niet gevonden. Draai dit script in Google Cloud Shell (shell.cloud.google.com)."
  exit 1
fi

PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "(unset)" ]; then
  read -rp "GCP project-id: " PROJECT_ID
  gcloud config set project "$PROJECT_ID"
fi
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Project: $PROJECT_ID | Regio: $REGION | Service: $SERVICE_NAME"
echo

echo "-- APIs inschakelen --"
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  artifactregistry.googleapis.com \
  --project "$PROJECT_ID"

echo "-- Firestore database (Native mode) --"
if ! gcloud firestore databases describe --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud firestore databases create --project "$PROJECT_ID" --location="$REGION" --type=firestore-native
else
  echo "Bestaat al, overslaan."
fi

echo "-- Service account --"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" \
    --project "$PROJECT_ID" --display-name="Meal stress analyzer bot"
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/datastore.user" --condition=None >/dev/null

echo "-- Secrets --"
if [ -z "${TELEGRAM_SECRET_TOKEN:-}" ]; then
  TELEGRAM_SECRET_TOKEN="$(openssl rand -hex 32)"
fi
if [ -z "${SCHEDULER_SHARED_SECRET:-}" ]; then
  SCHEDULER_SHARED_SECRET="$(openssl rand -hex 32)"
fi

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo
  echo "Maak eerst een bot via @BotFather in Telegram als je dat nog niet deed."
  read -rp "Telegram bot-token: " TELEGRAM_BOT_TOKEN
fi
if [ -z "${ALLOWED_TELEGRAM_USER_ID:-}" ]; then
  echo "Vraag je eigen numerieke user-id op via @userinfobot in Telegram."
  read -rp "Jouw Telegram user-id: " ALLOWED_TELEGRAM_USER_ID
fi
TIMEZONE="${TIMEZONE:-Europe/Amsterdam}"

_upsert_secret() {
  local name="$1" value="$2"
  if gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --project "$PROJECT_ID" --data-file=-
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

cat > "$STATE_FILE" <<EOF
PROJECT_ID=$PROJECT_ID
REGION=$REGION
SERVICE_NAME=$SERVICE_NAME
SA_NAME=$SA_NAME
TELEGRAM_SECRET_TOKEN=$TELEGRAM_SECRET_TOKEN
SCHEDULER_SHARED_SECRET=$SCHEDULER_SHARED_SECRET
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
ALLOWED_TELEGRAM_USER_ID=$ALLOWED_TELEGRAM_USER_ID
TIMEZONE=$TIMEZONE
EOF
echo "(secrets/instellingen bewaard in $STATE_FILE zodat je dit script kan herhalen zonder alles opnieuw in te tikken -- zet dit bestand niet in git)"

echo
echo "-- Bouwen en deployen naar Cloud Run --"
ENV_VARS="ALLOWED_TELEGRAM_USER_ID=${ALLOWED_TELEGRAM_USER_ID},TIMEZONE=${TIMEZONE},TELEGRAM_SECRET_TOKEN=${TELEGRAM_SECRET_TOKEN},SCHEDULER_SHARED_SECRET=${SCHEDULER_SHARED_SECRET},PROJECT_ID=${PROJECT_ID}"
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
    --headers="X-Scheduler-Secret=${SCHEDULER_SHARED_SECRET}" \
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
if [ "$GARMIN_SECRET_EXISTS" = "false" ]; then
  echo
  echo "Nog te doen voor /analyse werkt: Garmin-login (zie README, stap 'Garmin koppelen')."
fi
