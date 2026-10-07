#!/bin/bash
# Triggers an incremental Kavita library scan. Run on a schedule via cron.
# Uses the non-expiring API key to fetch a fresh JWT each run.
set -euo pipefail

set -a; . "$(dirname "$0")/../.env"; set +a

KAVITA_URL="http://localhost:${KAVITA_PORT}"
API_KEY="${KAVITA_API_KEY}"

TOKEN=$(curl -sS -X POST "${KAVITA_URL}/api/Plugin/authenticate?apiKey=${API_KEY}&pluginName=cron-scan" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")

# Scan all libraries (incremental — only new/changed files are processed)
curl -sS -o /dev/null -X POST -H "Authorization: Bearer ${TOKEN}" \
  "${KAVITA_URL}/api/Library/scan-all"
