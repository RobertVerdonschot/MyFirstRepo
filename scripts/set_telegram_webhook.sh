#!/usr/bin/env bash
# Points your Telegram bot's webhook at the deployed Cloud Run service.
# Usage: ./scripts/set_telegram_webhook.sh <bot-token> <cloud-run-url> <secret-token>
set -euo pipefail

BOT_TOKEN="${1:?Usage: $0 <bot-token> <cloud-run-url> <secret-token>}"
SERVICE_URL="${2:?Usage: $0 <bot-token> <cloud-run-url> <secret-token>}"
SECRET_TOKEN="${3:?Usage: $0 <bot-token> <cloud-run-url> <secret-token>}"

curl -sS -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"${SERVICE_URL}/telegram-webhook\", \"secret_token\": \"${SECRET_TOKEN}\"}"
echo
