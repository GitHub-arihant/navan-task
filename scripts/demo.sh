#!/usr/bin/env bash
# Curl walkthrough of every endpoint, including duplicate create and the 409 cases.
#
# Starts its own server on a throwaway SQLite file (so ids are predictable and
# your data/navan.db is untouched), checks every status code, then shuts down.
# Needs: the .venv from the README, curl, jq.
#
#   ./scripts/demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8765}"
BASE="http://127.0.0.1:$PORT"
SEEDS="fixtures/task-b/seed-payloads.json"
TMP_DIR="$(mktemp -d)"

DATABASE_URL="sqlite:///$TMP_DIR/demo.db" .venv/bin/uvicorn app.main:create_app --factory \
  --port "$PORT" --log-level warning &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null; rm -rf "$TMP_DIR"' EXIT

for _ in $(seq 1 50); do
  curl -sf "$BASE/health" >/dev/null && break
  sleep 0.2
done

seed() { jq -c ".$1" "$SEEDS"; }

# call METHOD PATH EXPECTED_STATUS [JSON_BODY] -> prints the status, leaves the body in $BODY
call() {
  local method=$1 path=$2 expected=$3 body=${4:-}
  local args=(-s -w '\n%{http_code}' -X "$method" "$BASE$path")
  [ -n "$body" ] && args+=(-H 'Content-Type: application/json' -d "$body")
  local response
  response=$(curl "${args[@]}")
  local status=${response##*$'\n'}
  BODY=${response%$'\n'*}
  printf '%-7s %-40s -> %s\n' "$method" "$path" "$status"
  if [ "$status" != "$expected" ]; then
    echo "  expected $expected, got $status: $BODY" >&2
    exit 1
  fi
}
show() { echo "  $(jq -c "$1" <<<"$BODY")"; }

echo "== Create one transaction of each type"
for name in flight hotel rail trip_fee agent_call_fee; do
  call POST /transactions 201 "$(seed "$name")"
  show '{id, type, total}'
done

echo "== Duplicate external_id: same body, then a different body with the same external_id"
call POST /transactions 409 "$(seed flight)"
show '{error, existing_id}'
call POST /transactions 409 "$(seed duplicate_flight)"
show '{error, existing_id}'
call GET "/transactions?limit=100" 200
echo "  rows with that external_id: $(jq '[.items[] | select(.external_id == "flight-TXL-LHR-2026-03-11")] | length' <<<"$BODY")"

echo "== Read"
call GET /transactions/1 200
show '{id, type, coupons: [.metadata.coupons[].flight_number]}'
call GET /transactions/2 200
show '{name: .metadata.name, address: .metadata.address}'
call GET /transactions/3 200
show '{legs: [.metadata.legs[] | "\(.origin) -> \(.destination)"]}'
call GET "/transactions?limit=2" 200
show '{ids: [.items[].id], next_offset}'
call GET "/transactions?limit=1000" 400
show '{error}'

echo "== PATCH a transaction that is not invoiced yet"
call PATCH /transactions/3 200 '{"occurred_at": "2026-03-13T09:05:00Z"}'
show '{id, occurred_at}'

echo "== Invoice a single transaction, then delete it so the trip can be invoiced"
call POST /invoices 201 '{"transaction_id": 2}'
show '{id, total}'
call POST /invoices 409 '{"transaction_ids": [1, 2, 3, 4, 5]}'
show '{error, conflicts}'
call DELETE /invoices/1 200
show '.'

echo "== Trip invoice (flight + hotel + rail + both Navan fees)"
call POST /invoices 201 '{"transaction_ids": [1, 2, 3, 4, 5]}'
show '{id, currency, total, tax_rollup, fee_rollup}'
call GET /invoices/2 200
show '{id, total, lines: [.lines[] | {transaction_id, total}]}'
call GET /invoices 200
show '{ids: [.items[].id], next_offset}'

echo "== Invoiced transactions are frozen"
call PATCH /transactions/1 409 '{"total": 999.99}'
show '{error, conflicts}'
call DELETE /transactions/1 409
show '{error, conflicts}'

echo "== Other invoice errors"
call POST /transactions 201 "$(jq -c '.trip_fee | .currency = "USD" | .external_id = "navan-trip-fee-usd"' "$SEEDS")"
show '{id, currency}'
call PATCH /invoices/2 409 '{"transaction_ids": [1, 2, 3, 4, 5, 6]}'
show '{error}'
call POST /invoices 409 '{"transaction_ids": [6, 6]}'
show '{error}'
call POST /invoices 404 '{"transaction_ids": [6, 999]}'
show '{error, missing_ids}'

echo "== PATCH the invoice: drop the agent call fee; it is rebuilt from current data"
call PATCH /invoices/2 200 '{"transaction_ids": [1, 2, 3, 4]}'
show '{id, total}'
call DELETE /transactions/5 200
show '.'

echo "== DELETE the invoice: transactions stay and become editable again"
call DELETE /invoices/2 200
show '.'
call GET /invoices/2 404
call GET /transactions/1 200
call PATCH /transactions/1 200 '{"total": 230}'
show '{id, total}'

echo "Done: every status code matched."
