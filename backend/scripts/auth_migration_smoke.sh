#!/usr/bin/env bash
# Auth smoke checklist. Prints curl commands; does not run them unless RUN=1.
#
# Usage:
#   BASE_URL=http://localhost:8000 EMAIL=admin@example.com PASSWORD=secret ./scripts/auth_migration_smoke.sh
#   RUN=1 BASE_URL=... EMAIL=... PASSWORD=... ./scripts/auth_migration_smoke.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
EMAIL="${EMAIL:-}"
PASSWORD="${PASSWORD:-}"
API_KEY="${API_KEY:-}"
RUN="${RUN:-0}"
SMOKE_OAUTH_BFF="${SMOKE_OAUTH_BFF:-0}"
SMOKE_LOGIN_SHADOW="${SMOKE_LOGIN_SHADOW:-0}"
SMOKE_REGISTER="${SMOKE_REGISTER:-0}"
REGISTER_USER_ID="${REGISTER_USER_ID:-}"
REGISTER_PASSWORD="${REGISTER_PASSWORD:-Test123!}"
REGISTER_EMAIL="${REGISTER_EMAIL:-}"
COOKIE_JAR="${COOKIE_JAR:-/tmp/autoflow_auth_smoke_cookies.txt}"
OAUTH_COOKIE_JAR="${OAUTH_COOKIE_JAR:-/tmp/autoflow_auth_smoke_oauth_cookies.txt}"

api() { echo "${BASE_URL%/}/api/v1$1"; }

# Use "$@" (no eval): -d "user&pass" must not be split by shell on '&'.
run_curl() {
  echo ""
  printf '# curl'
  printf ' %q' "$@"
  echo ""
  if [[ "$RUN" == "1" ]]; then
    curl -sS -w '\nHTTP_CODE:%{http_code}\n' "$@"
  fi
}

echo "=== Auth smoke (RUN=${RUN}) ==="
echo "BASE_URL=${BASE_URL}"

run_curl "$(api /healthz)"
run_curl "$(api /healthz/oauth)"

if [[ -n "$EMAIL" && -n "$PASSWORD" ]]; then
  rm -f "$COOKIE_JAR"
  run_curl -c "$COOKIE_JAR" -X POST "$(api /auth/login)" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "username=${EMAIL}" \
    --data-urlencode "password=${PASSWORD}"
  run_curl -b "$COOKIE_JAR" "$(api /users/me)"
  run_curl -b "$COOKIE_JAR" "$(api /me/menu-config)" || true
  run_curl -b "$COOKIE_JAR" -X POST "$(api /auth/logout)"
  run_curl -b "$COOKIE_JAR" "$(api /users/me)" || true
else
  echo ""
  echo "# Skip login flow: set EMAIL and PASSWORD to run P0-3..P0-7"
fi

if [[ -n "$API_KEY" ]]; then
  run_curl -H "Authorization: Bearer ${API_KEY}" "$(api /users/me)"
fi

if [[ "$SMOKE_LOGIN_SHADOW" == "1" && -n "$EMAIL" && -n "$PASSWORD" ]]; then
  echo ""
  echo "# --- Login shadow: expect oauth_access_token when AUTH_LEGACY_OAUTH_SHADOW_ENABLED=true ---"
  echo "# After RUN=1, inspect jar: grep oauth_access_token ${COOKIE_JAR}"
  if [[ "$RUN" == "1" && -f "$COOKIE_JAR" ]]; then
    grep -E 'oauth_access_token|session' "$COOKIE_JAR" || echo "# (no matching cookies in jar)"
  fi
fi

if [[ "$SMOKE_REGISTER" == "1" && -n "$REGISTER_USER_ID" ]]; then
  echo ""
  echo "# --- POST /users/register (OAUTH_REGISTER_ENABLED=true on server) ---"
  reg_email="${REGISTER_EMAIL:-${REGISTER_USER_ID}@example.com}"
  run_curl -X POST "$(api /users/register)" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${reg_email}\",\"password\":\"${REGISTER_PASSWORD}\"}"
fi

if [[ "$SMOKE_OAUTH_BFF" == "1" && -n "$EMAIL" && -n "$PASSWORD" ]]; then
  echo ""
  echo "# --- OAuth BFF login (OAUTH_BFF_LOGIN_ENABLED=true on server) ---"
  rm -f "$OAUTH_COOKIE_JAR"
  run_curl -c "$OAUTH_COOKIE_JAR" -X POST "$(api /auth/login/oauth)" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"channel\":\"siaweb\"}"
  run_curl -b "$OAUTH_COOKIE_JAR" "$(api /users/me)"
  run_curl -b "$OAUTH_COOKIE_JAR" -X POST "$(api /auth/logout/oauth)"
  run_curl -b "$OAUTH_COOKIE_JAR" "$(api /users/me)" || true
fi

echo ""
echo "Done. See backend/docs/auth-migration/03-smoke-tests.md for expected results."
