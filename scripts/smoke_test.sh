#!/usr/bin/env sh
set -eu

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"

curl --fail --silent --show-error "${BASE_URL}/healthz"
curl --fail --silent --show-error "${BASE_URL}/" >/dev/null
curl --fail --silent --show-error "${BASE_URL}/professors/" >/dev/null
curl --fail --silent --show-error "${BASE_URL}/search/" >/dev/null

echo "Smoke checks passed for ${BASE_URL}"

