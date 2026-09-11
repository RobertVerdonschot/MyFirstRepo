#!/usr/bin/env bash
# Tars + base64-encodes a local Garmin tokenstore directory and uploads it as
# a Secret Manager secret, which the Cloud Run service reads as GARMIN_TOKENS_B64.
#
# Usage: ./scripts/pack_and_upload_garmin_tokens.sh <tokenstore-dir> <gcp-project-id> [secret-name]
set -euo pipefail

TOKENSTORE_DIR="${1:?Usage: $0 <tokenstore-dir> <gcp-project-id> [secret-name]}"
PROJECT_ID="${2:?Usage: $0 <tokenstore-dir> <gcp-project-id> [secret-name]}"
SECRET_NAME="${3:-garmin-tokens-b64}"

TMP_TAR="$(mktemp)"
trap 'rm -f "$TMP_TAR" "${TMP_TAR}.b64"' EXIT

tar -czf "$TMP_TAR" -C "$TOKENSTORE_DIR" .
base64 -w0 "$TMP_TAR" > "${TMP_TAR}.b64"

if gcloud secrets describe "$SECRET_NAME" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud secrets versions add "$SECRET_NAME" --project "$PROJECT_ID" --data-file="${TMP_TAR}.b64"
else
  gcloud secrets create "$SECRET_NAME" --project "$PROJECT_ID" \
    --data-file="${TMP_TAR}.b64" --replication-policy=automatic
fi

echo "Garmin-tokens geupload naar Secret Manager secret '${SECRET_NAME}' in project ${PROJECT_ID}."
echo "Draai ./deploy.sh opnieuw (of 'gcloud run services update ...') zodat de service de nieuwe versie oppikt."
