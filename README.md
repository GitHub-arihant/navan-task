# Navan take-home B: travel transactions and invoices

A small HTTP API that stores flight, hotel, rail and Navan-fee transactions and builds frozen invoices from them. It uses FastAPI and SQLAlchemy, with a SQLite **file** as the database.

**Why Python and not Java/Spring.** I built this in Python/FastAPI because it is the stack I use in production, so I can defend every design decision in it line by line. The design maps directly to Java/Spring:

| Here | Java/Spring |
|---|---|
| `api/` | controllers |
| `services/` | `@Service` classes |
| `repositories/` | Spring Data / JDBC repositories |
| Alembic | Flyway |
| SQLite `BEGIN IMMEDIATE` | Postgres `SELECT ... FOR UPDATE` row locks and `INSERT ... ON CONFLICT` |

The design itself is explained in [ARCHITECTURE.md](ARCHITECTURE.md).

## Run

You need Python 3.12+ (developed on 3.14). Setup, once:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Run the API, in one command:

```bash
.venv/bin/uvicorn app.main:create_app --factory --port 8000
```

- On startup the app runs the database migrations (`alembic upgrade head`) and creates `./data/navan.db`.
- To use another file, set `DATABASE_URL=sqlite:///path/to/file.db`. In-memory databases are refused.
- Interactive API docs are at http://localhost:8000/docs.
- No API keys or secrets are needed; the service makes no LLM or vendor calls.

## Test

Run the tests, in one command:

```bash
.venv/bin/pytest
```

- 90 tests. They go through the real HTTP API, and each one gets its own temporary SQLite file.
- The concurrency tests start a real Uvicorn server and fire simultaneous requests at it.
- The tests numbered 1–10 in the brief are labelled "Test N" in the test files.

## Demo

[scripts/demo.sh](scripts/demo.sh) walks through every curl below against its own throwaway database and checks every status code. It needs `curl` and `jq`.

```bash
./scripts/demo.sh
```

## API behavior in short

- **Money:**
  - Send amounts as JSON numbers or strings with at most 2 decimal places.
  - Responses always return exact strings, e.g. `"685.70"`.
  - Amounts are stored as integer cents and handled as `Decimal`, never `float`.
  - Negative amounts are rejected.
- **Validation errors are 400, never 422.** That covers an unknown `type` or `fee_kind`, missing metadata fields, and a third decimal place.
- **Errors** always have the shape `{"error": "<code>", "detail": "<message>", ...}`.
- **Duplicate `external_id`:** **409** `duplicate_external_id` with `existing_id`. Nothing is written, even if the body differs.
- **Invoices are frozen snapshots.** Each line stores a copy of the transaction as it was when invoiced, and `GET /invoices/{id}` never reads the live transactions.
- **No double billing:**
  - A transaction can be on **at most one invoice**. Invoicing it again returns **409** `transaction_already_invoiced`, and `conflicts` names the invoice it is on.
  - Deleting that invoice frees the transaction.
  - This rule goes beyond the brief, on purpose.
- **Invoiced transactions are frozen:** PATCH or DELETE returns **409**.
- **Other invoice errors:** listing the same id twice is **409** `duplicate_transaction_id`. Mixed currencies are **409** `mixed_currency`. Unknown ids are **404** with `missing_ids`.
- **DELETE** returns **200** with `{"id", "deleted": true, "detail"}`.
- **Lists:**
  - `?limit=` defaults to 50 and allows at most 100. `limit=1000` returns **400**; it is not silently capped.
  - Use `?offset=` to move through the results; it defaults to 0.
  - Each item is a summary (header fields only).
  - `next_offset` is `null` on the last page.

## Example curls

These assume a **fresh database** and the server on port 8000, so the transaction ids are 1 to 5. They use `jq` to read the request bodies from the fixtures. The outputs shown are trimmed.

```bash
BASE=http://localhost:8000
SEEDS=fixtures/task-b/seed-payloads.json
```

### Create one transaction of each type

```bash
jq '.flight'         $SEEDS | curl -s -X POST $BASE/transactions -H 'Content-Type: application/json' -d @-   # id 1
jq '.hotel'          $SEEDS | curl -s -X POST $BASE/transactions -H 'Content-Type: application/json' -d @-   # id 2
jq '.rail'           $SEEDS | curl -s -X POST $BASE/transactions -H 'Content-Type: application/json' -d @-   # id 3
jq '.trip_fee'       $SEEDS | curl -s -X POST $BASE/transactions -H 'Content-Type: application/json' -d @-   # id 4
jq '.agent_call_fee' $SEEDS | curl -s -X POST $BASE/transactions -H 'Content-Type: application/json' -d @-   # id 5
```

Each call returns `201` with the stored transaction: its server `id`, `tax_lines`, `fee_lines` and typed `metadata`.

### Duplicate create: the same `external_id` twice stores one row

```bash
jq '.flight'           $SEEDS | curl -s -X POST $BASE/transactions -H 'Content-Type: application/json' -d @-
jq '.duplicate_flight' $SEEDS | curl -s -X POST $BASE/transactions -H 'Content-Type: application/json' -d @-
# 409 {"error":"duplicate_external_id","detail":"...","existing_id":1}   (both calls)

# Show that only one row exists:
curl -s "$BASE/transactions?limit=100" | jq '[.items[] | select(.external_id == "flight-TXL-LHR-2026-03-11")] | length'
# 1
sqlite3 data/navan.db "SELECT count(*) FROM transactions WHERE external_id = 'flight-TXL-LHR-2026-03-11'"
# 1
```

### Get and list transactions

```bash
curl -s $BASE/transactions/1 | jq '.metadata.coupons'           # flight coupons with flight_number
curl -s $BASE/transactions/2 | jq '.metadata | {name, address}' # hotel name and address
curl -s $BASE/transactions/3 | jq '.metadata.legs | length'     # 2 rail legs
curl -s "$BASE/transactions?limit=2&offset=0"
# {"items":[{"id":1,...},{"id":2,...}],"limit":2,"offset":0,"next_offset":2}
curl -s "$BASE/transactions?limit=1000"
# 400 {"error":"validation_error",...}
```

### Patch a transaction (not invoiced yet)

```bash
curl -s -X PATCH $BASE/transactions/3 -H 'Content-Type: application/json' \
  -d '{"occurred_at": "2026-03-13T09:05:00Z"}'
# 200, full transaction
```

- You can send any of `total`, `occurred_at`, `tax_lines`, `fee_lines` and `metadata`.
- Lists and metadata are replaced as a whole, not merged.
- `metadata` is validated against the stored `type`.
- Sending `type`, `external_id` or `currency` returns `400`.

### Create an invoice by transaction id

**Note:** a transaction can only be on one invoice. To try both fixture examples (single id, then the trip) with the same seeds, **delete the first invoice in between**. Otherwise the trip invoice correctly returns `409`.

```bash
# Single transaction id
curl -s -X POST $BASE/invoices -H 'Content-Type: application/json' -d '{"transaction_id": 2}'
# 201 {"id":1,"currency":"EUR","total":"331.70",...}

# Trying the trip now fails, because the hotel is already billed:
curl -s -X POST $BASE/invoices -H 'Content-Type: application/json' -d '{"transaction_ids": [1, 2, 3, 4, 5]}'
# 409 {"error":"transaction_already_invoiced","conflicts":[{"transaction_id":2,"invoice_id":1}],...}

curl -s -X DELETE $BASE/invoices/1
# 200 {"id":1,"deleted":true,"detail":"Invoice 1 deleted"}

# Trip: flight + hotel + rail + trip fee + agent call fee
curl -s -X POST $BASE/invoices -H 'Content-Type: application/json' -d '{"transaction_ids": [1, 2, 3, 4, 5]}'
# 201 {"id":2,"currency":"EUR","total":"685.70",
#      "tax_rollup":[{"name":"Airport tax","rate":null,"amount":"15.00"},{"name":"VAT","rate":"0.07","amount":"21.70"}],
#      "fee_rollup":[{"name":"YQ","amount":"25.00"},{"name":"Booking fee","amount":"4.00"}],
#      "lines":[{"transaction_id":1,"type":"flight","total":"220.00","snapshot":{...}}, ...]}
```

### Get and list invoices

```bash
curl -s $BASE/invoices/2 | jq '{total, lines: [.lines[] | {transaction_id, total}]}'
curl -s "$BASE/invoices?limit=10"
# {"items":[{"id":2,"currency":"EUR","total":"685.70",...}],"limit":10,"offset":0,"next_offset":null}
```

### Patch or delete an invoiced transaction: 409

```bash
curl -s -X PATCH $BASE/transactions/1 -H 'Content-Type: application/json' -d '{"total": 999.99}'
# 409 {"error":"transaction_already_invoiced","conflicts":[{"transaction_id":1,"invoice_id":2}],...}
curl -s -X DELETE $BASE/transactions/1
# 409, same body; the transaction is still there
```

### Patch an invoice: replace its transactions and rebuild the snapshot

```bash
curl -s -X PATCH $BASE/invoices/2 -H 'Content-Type: application/json' -d '{"transaction_ids": [1, 2, 3, 4]}'
# 200 {"id":2,"total":"670.70",...}   rebuilt from the current transactions, frozen again

curl -s -X DELETE $BASE/transactions/5
# 200 {"id":5,"deleted":true,"detail":"Transaction 5 deleted"}   (no longer invoiced, so it can be deleted)
```

PATCH takes the same body as create.

| Problem | Response |
|---|---|
| Unknown invoice or transaction id | `404` |
| Transaction on *another* invoice | `409` `transaction_already_invoiced` |
| Mixed currencies | `409` `mixed_currency` |

On any error the invoice stays exactly as it was.

### Delete an invoice

```bash
curl -s -X DELETE $BASE/invoices/2
# 200 {"id":2,"deleted":true,"detail":"Invoice 2 deleted"}
curl -s $BASE/invoices/2          # 404
curl -s $BASE/transactions/1      # 200: the transactions are untouched, and editable again
```

## Project layout

```
app/
  main.py          app factory, error -> HTTP mapping, /docs cleanup
  db.py            engine, SQLite pragmas, BEGIN IMMEDIATE write_transaction()
  models.py        tables          money.py / timestamps.py   conversions
  errors.py        domain errors (no HTTP)
  schemas/         request models (one per transaction type) and response/error models
  api/             thin HTTP handlers
  services/        rules: duplicates, snapshot, rollups, 409s, pagination
  repositories/    SQL only
migrations/        Alembic: 0001 schema, 0002 one-invoice-per-transaction
tests/             pytest, all through the HTTP API
scripts/demo.sh    curl walkthrough
```
