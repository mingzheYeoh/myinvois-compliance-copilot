#!/usr/bin/env bash
# Provision and deploy the MyInvois Compliance Copilot to Azure Container Apps.
#
#   bash deploy/azure/deploy.sh            # build a new image and roll it out
#   TAG=v3 bash deploy/azure/deploy.sh     # a specific tag instead of the git sha
#
# Idempotent: every step checks for what it is about to create, so a re-run after
# a failure resumes rather than erroring, and a clean re-run is a no-op plus one
# new revision. Runs the same from a laptop and from CI.
#
# Container Apps is driven through `az rest` rather than `az containerapp`. The
# extension cannot be installed on Windows: az ships a 32-bit CPython 3.14, the
# extension pins kubernetes==24.2.0 which drags in cryptography, there is no
# cp314-win32 wheel for it, and the Rust sdist fallback dies on a missing
# maturin. `az rest` is core az, needs no extension, and behaves identically on
# every machine -- worth more here than the shorter command names.
set -euo pipefail

# az streams build logs through colorama, which encodes to the console code
# page; anything non-cp1252 in a build log otherwise kills the client mid-run.
export PYTHONIOENCODING=utf-8

RG=rg-myinvois
LOC=southeastasia
ACR=myinvoisacr
WS=myinvois-logs
ENV_NAME=myinvois-env
APP=myinvois-api
PG=myinvois
APIV=2024-03-01
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"
IMAGE="$ACR.azurecr.io/$APP:$TAG"
# Public image for the first create only: the app identity cannot be granted
# AcrPull until the app exists to have one, so it cannot pull our image yet.
SEED_IMAGE=mcr.microsoft.com/k8se/quickstart:latest

# Secrets come from the environment (CI) or .env (laptop). They reach Azure only
# as Container Apps secrets -- never a file, an image layer, or a command line.
[ -f .env ] && set -a && . ./.env && set +a
for v in DATABASE_URL AZURE_OPENAI_API_KEY GROQ_API_KEY LANGCHAIN_API_KEY \
         AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT; do
    [ -n "${!v:-}" ] || { echo "missing required setting: $v" >&2; exit 1; }
done

# .env raises the token budget for development days. That override must not ride
# along into production, which takes budget.py's 150,000 default.
unset DAILY_TOKEN_BUDGET

SUB=$(az account show --query id -o tsv)
# The ARM id and the request URL are not interchangeable: managedEnvironmentId
# must be the /subscriptions/... path, and ARM rejects the https:// form.
RES="/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.App"
BASE="https://management.azure.com$RES"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

say() { printf '\n=== %s\n' "$*"; }
have() { az "$@" >/dev/null 2>&1; }
arm()  { az rest --method "$1" --url "$BASE/$2?api-version=$APIV" "${@:3}"; }

# ARM accepts the PUT immediately; the resource is not up when it returns.
wait_ready() {
    local path=$1 state
    for _ in $(seq 90); do
        state=$(arm get "$path" --query properties.provisioningState -o tsv)
        case "$state" in
            Succeeded) return 0 ;;
            Failed|Canceled) echo "provisioning $state for $path" >&2; return 1 ;;
        esac
        sleep 5
    done
    echo "timed out waiting for $path" >&2; return 1
}

say "providers"
# Registering a provider is a SUBSCRIPTION-scope action, and CI authenticates as
# a service principal scoped to this resource group -- it can neither read nor
# change provider state. Registration is a one-time setup step anyway, so this
# only acts when it positively sees an unregistered provider. If one really is
# missing, the create that needs it fails immediately afterwards with a clear
# error, which is a better failure than an AuthorizationFailed here on a laptop
# run that had nothing to do.
for p in Microsoft.ContainerRegistry Microsoft.App Microsoft.OperationalInsights; do
    case "$(az provider show -n "$p" --query registrationState -o tsv 2>/dev/null || true)" in
        Registered) echo "  $p registered" ;;
        "")        echo "  $p unreadable with this credential; assuming registered" ;;
        *)          az provider register -n "$p" --wait ;;
    esac
done

say "container registry ($ACR, Basic ~5 USD/mo)"
have acr show -n "$ACR" -g "$RG" ||
    az acr create -g "$RG" -n "$ACR" --sku Basic -l "$LOC" --admin-enabled false -o none

say "build $IMAGE"
# Built by ACR Tasks in-region. The build pulls torch and a slim base on top of
# the app; pushing the result from a laptop to Southeast Asia is the slow way.
# A tag already in the registry is not rebuilt: the tag is the git sha, so the
# content is identical, and a resumed run should not pay ten minutes to learn
# that. FORCE_BUILD=1 rebuilds anyway (uncommitted changes under an old sha).
if [ "${FORCE_BUILD:-0}" = 1 ] ||
   ! az acr repository show -n "$ACR" --image "$APP:$TAG" -o none 2>/dev/null; then
    az acr build -r "$ACR" -t "$APP:$TAG" -t "$APP:latest" -f Dockerfile . -o none
else
    echo "  $APP:$TAG already in $ACR; set FORCE_BUILD=1 to rebuild"
fi

say "log analytics ($WS)"
have monitor log-analytics workspace show -g "$RG" -n "$WS" ||
    az monitor log-analytics workspace create -g "$RG" -n "$WS" -l "$LOC" -o none
WS_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$WS" --query customerId -o tsv)
WS_KEY=$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$WS" \
         --query primarySharedKey -o tsv)

say "container apps environment ($ENV_NAME)"
if ! arm get "managedEnvironments/$ENV_NAME" -o none 2>/dev/null; then
    WS_ID="$WS_ID" WS_KEY="$WS_KEY" LOC="$LOC" python3 deploy/azure/body.py env > "$TMP/env.json"
    arm put "managedEnvironments/$ENV_NAME" --body "@$TMP/env.json" -o none
fi
wait_ready "managedEnvironments/$ENV_NAME"

say "app ($APP)"
if ! arm get "containerApps/$APP" -o none 2>/dev/null; then
    # Phase 1: exist, so there is an identity to grant AcrPull to.
    LOC="$LOC" SUB="$SUB" RG="$RG" ENV_NAME="$ENV_NAME" SEED_IMAGE="$SEED_IMAGE" \
        python3 deploy/azure/body.py seed > "$TMP/seed.json"
    arm put "containerApps/$APP" --body "@$TMP/seed.json" -o none
    wait_ready "containerApps/$APP"
fi

say "acr pull permission"
PRINCIPAL=$(arm get "containerApps/$APP" --query identity.principalId -o tsv)
ACR_ID=$(az acr show -n "$ACR" -g "$RG" --query id -o tsv)
MSYS_NO_PATHCONV=1 az role assignment create --assignee-object-id "$PRINCIPAL" \
    --assignee-principal-type ServicePrincipal --role AcrPull --scope "$ACR_ID" -o none \
    2>/dev/null || echo "  already granted"

say "deploy $IMAGE"
# Phase 2 carries the real image, the registry pull identity, every secret and
# the env vars that reference them. DAILY_TOKEN_BUDGET is deliberately absent:
# production takes budget.py's 150,000 default, and .env raises it for dev days.
LOC="$LOC" IMAGE="$IMAGE" ACR_SERVER="$ACR.azurecr.io" \
    SUB="$SUB" RG="$RG" ENV_NAME="$ENV_NAME" python3 deploy/azure/body.py app > "$TMP/app.json"
arm put "containerApps/$APP" --body "@$TMP/app.json" -o none
wait_ready "containerApps/$APP"

say "postgres egress"
# Consumption Container Apps report 40+ outbound IPs and rotate them, so a rule
# per IP is both slow to apply and wrong by tomorrow. The server-level
# "allow Azure services" rule (0.0.0.0-0.0.0.0) is the mechanism that actually
# covers this; verify it rather than fighting it.
if az postgres flexible-server firewall-rule list -g "$RG" -s "$PG" \
       --query "[?startIpAddress=='0.0.0.0' && endIpAddress=='0.0.0.0'] | length(@)" \
       -o tsv | grep -qv '^0$'; then
    echo "  allow-azure-services rule present"
else
    az postgres flexible-server firewall-rule create -g "$RG" -s "$PG" \
        -n AllowAllAzureServices --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 -o none
    echo "  created allow-azure-services rule"
fi

URL="https://$(arm get "containerApps/$APP" \
    --query properties.configuration.ingress.fqdn -o tsv)"
say "deployed $IMAGE"
echo "$URL"
curl -fsS --max-time 300 "$URL/health"; echo
