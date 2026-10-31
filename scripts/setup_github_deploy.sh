#!/usr/bin/env bash
# Eenmalig, in Google Cloud Shell: geeft GitHub Actions in deze repo het recht
# om te deployen, zonder dat er ergens een service-account-key wordt gemaakt
# (Workload Identity Federation). Daarna kan Claude (of jijzelf) deployen door
# de "Deploy"-workflow op GitHub te starten.
#
# Draai dit pas nadat ./deploy.sh een keer gelukt is.
set -euo pipefail

REPO="${REPO:-RobertVerdonschot/MyFirstRepo}"
REGION="${REGION:-europe-west1}"
RUNTIME_SA_NAME="${SA_NAME:-meal-stress-bot-sa}"
DEPLOYER_SA_NAME="github-deployer"
POOL_ID="github"
PROVIDER_ID="github-oidc"

PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "(unset)" ]; then
  echo "Geen project ingesteld. Je projecten:"
  gcloud projects list --format="table(projectId,name)"
  read -rp "Typ de PROJECT_ID waar de bot in draait: " PROJECT_ID
  gcloud config set project "$PROJECT_ID"
fi
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
DEPLOYER_SA="${DEPLOYER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "Project: $PROJECT_ID ($PROJECT_NUMBER) | Repo: $REPO"

# Dit script bouwt voort op een eerste geslaagde ./deploy.sh.
if ! gcloud iam service-accounts describe "$RUNTIME_SA" --project "$PROJECT_ID" >/dev/null 2>&1 \
   || ! gcloud run services describe "${SERVICE_NAME:-meal-stress-bot}" --project "$PROJECT_ID" \
        --region "$REGION" >/dev/null 2>&1; then
  echo
  echo "FOUT: in project $PROJECT_ID draait de bot nog niet (geen service account"
  echo "$RUNTIME_SA of geen Cloud Run-service). Draai eerst ./deploy.sh, of kies"
  echo "het juiste project met: gcloud config set project <PROJECT_ID>"
  exit 1
fi

echo "-- APIs --"
gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  --project "$PROJECT_ID"

echo "-- Deploy-service-account --"
if ! gcloud iam service-accounts describe "$DEPLOYER_SA" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$DEPLOYER_SA_NAME" --project "$PROJECT_ID" \
    --display-name="GitHub Actions deployer (meal-stress-bot)"
fi

# Rollen op projectniveau. run.admin is nodig voor --allow-unauthenticated
# (IAM-policy op de service), run.sourceDeveloper voor bouwen via Cloud Build
# vanuit broncode, secretmanager.admin omdat deploy.sh secrets bijwerkt en er
# toegang op zet, cloudscheduler.admin voor de dagelijkse Garmin-taak.
for role in \
  roles/run.admin \
  roles/run.sourceDeveloper \
  roles/serviceusage.serviceUsageConsumer \
  roles/secretmanager.admin \
  roles/cloudscheduler.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOYER_SA}" --role="$role" --condition=None >/dev/null
  echo "  $role"
done

# "Mag zich voordoen als": de service draait als RUNTIME_SA, de build als de
# Compute default SA.
for sa in "$RUNTIME_SA" "$COMPUTE_SA"; do
  gcloud iam service-accounts add-iam-policy-binding "$sa" --project "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOYER_SA}" --role="roles/iam.serviceAccountUser" >/dev/null
  echo "  serviceAccountUser op $sa"
done

echo "-- Workload Identity Federation --"
if ! gcloud iam workload-identity-pools describe "$POOL_ID" --project "$PROJECT_ID" \
    --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$POOL_ID" --project "$PROJECT_ID" \
    --location=global --display-name="GitHub Actions"
fi
# Alleen tokens van deze ene repo, en alleen bij push of handmatig starten
# (dus niet vanuit pull requests van anderen).
CONDITION="assertion.repository=='${REPO}' && assertion.event_name in ['push','workflow_dispatch']"
if ! gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" --project "$PROJECT_ID" \
    --location=global --workload-identity-pool="$POOL_ID" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" --project "$PROJECT_ID" \
    --location=global --workload-identity-pool="$POOL_ID" \
    --display-name="GitHub OIDC" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.event_name=assertion.event_name" \
    --attribute-condition="$CONDITION"
fi

gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" --project "$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${REPO}" >/dev/null

echo
echo "== Klaar =="
echo "Plak deze regels terug naar Claude (zijn geen geheimen):"
echo
echo "GCP_PROJECT_ID=${PROJECT_ID}"
echo "GCP_WIF_PROVIDER=projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
echo "GCP_DEPLOYER_SA=${DEPLOYER_SA}"
