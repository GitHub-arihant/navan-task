# Architecture

## Layers

| Layer | Folder | Job |
|---|---|---|
| HTTP | `api/` | Parse the request and pick the status code. Nothing else. |
| Rules | `services/` | Duplicates, snapshot, rollups, 409s. Raise domain errors from `errors.py`. |
| SQL | `repositories/` | Queries only. |

`main.py` turns domain errors into HTTP responses. Every write runs inside `db.write_transaction()`: one request is one DB transaction, started with `BEGIN IMMEDIATE`.

## Tables and models

Shared fields are real columns on `transactions`: `type`, `external_id`, `currency`, `occurred_at`, `total_cents`. Type-specific data lives in **child tables per type**, not in a JSON blob:

| Type | Tables |
|---|---|
| flight | `flight_details` (origin, destination, fare) + `flight_coupons` (one row per segment: `flight_number`, carrier, from, to, departure, arrival) |
| hotel | `hotel_details` (property `name`, `street`, `city`, `country`, check-in/out, nights, room rate) + `hotel_extras` |
| rail | `rail_legs` (one row per train; a change of train = two rows) |
| navan_fee | `navan_fee_details` (`fee_kind` CHECK in `trip_fee`/`agent_call_fee`, description, `related_external_id`) |

- **Taxes and fees** are separate tables: `transaction_tax_lines` (name, `rate`, amount) and `transaction_fee_lines` (name, amount). They are never mixed.
- **Child rows** carry `position` to keep the client's order. They use `ON DELETE CASCADE`, so deleting a transaction removes them.
- **Why child tables:**
  - The database itself enforces the structure (NOT NULL, CHECK on `type` and `fee_kind`).
  - Every field can be queried, for example "all legs through Lille".
  - A flight can never be stored with a hotel's fields.
- **The cost:** more tables and joins on GET by id. That is fine at one row per detail table and a few rows per list.
- **Requests:** a union of four Pydantic models, chosen by the `type` field. An unknown `type` or `fee_kind`, or `{}` metadata, is a 400.
- **Money** is integer cents in the DB (scale 2) and `Decimal` in Python. The API returns exact strings (`"685.70"`). More than 2 decimal places is a 400. Tax rates are stored as normalized decimal text (`"0.07"`), so rollups can group by them exactly.

## `external_id` uniqueness and concurrent POSTs

- `transactions.external_id` has a **unique index**, and that index is the guarantee. There is no Python pre-check.
- A duplicate INSERT raises `IntegrityError`, and the whole write rolls back: no header, no lines.
- The service then looks up the existing row and returns **409** with `existing_id`.
- Two overlapping POSTs:
  - `BEGIN IMMEDIATE` makes the second wait for the write lock (the busy timeout is 5s).
  - It then hits the unique index and gets the 409.
  - The tests fire 10 simultaneous POSTs at a real server and get exactly one 201.
- On Postgres this would be `INSERT ... ON CONFLICT (external_id) DO NOTHING RETURNING id`, falling back to a select when nothing is returned.

## Invoice snapshot

- Creating an invoice copies each transaction's full API representation (header, tax lines, fee lines, metadata) into `invoice_lines.snapshot`, as JSON text.
- It also stores the line `total_cents` and the invoice-level `invoice_tax_rollup` (grouped by name + rate) and `invoice_fee_rollup` (grouped by name). Rows are in order of first appearance.
- Totals and rollups are computed from the snapshots, so they always agree with the stored lines.
- `GET /invoices/{id}` reads **only invoice tables**, so a later change to a transaction cannot change an existing invoice.
- `PATCH /invoices/{id}` rebuilds the snapshot from the *current* transactions in one write: delete the old lines and rollups, insert the new ones. Any 404 or 409 leaves the invoice untouched.

**Freezing the transaction side.** PATCH or DELETE of a transaction that is on an invoice returns 409. The "is it invoiced?" check and the write run under the same `BEGIN IMMEDIATE` lock, so an invoice cannot be created between the two. The `invoice_lines.transaction_id` foreign key is `ON DELETE RESTRICT`, so even raw SQL cannot delete a billed transaction.

## Preventing double billing

A transaction can be on **at most one invoice**. The brief does not require this; I added it on purpose, because billing a trip twice is the costliest mistake an invoicing API can make. It is enforced twice:

1. **The service checks first** and returns a clear 409 `transaction_already_invoiced`, with `conflicts: [{transaction_id, invoice_id}]` naming the existing invoice.
2. **A UNIQUE index on `invoice_lines.transaction_id` is the database backstop** (migration 0002).
   - It also wins races. In the test, 5 simultaneous POSTs for one transaction give one 201 and four 409s.
   - If the index ever fires, its `IntegrityError` is mapped to the same 409. A test switches off the service check to prove this.

Deleting the invoice (or PATCHing the transaction off it) frees the transaction again. The same index makes "is this transaction invoiced?" a single index lookup.

## High volume

**In place:**

- **Indexes:**
  - Unique `external_id`.
  - Primary keys for GET by id.
  - `transaction_id` on every child table; `invoice_id` on lines and rollups.
  - Unique `invoice_lines.transaction_id` for the invoiced check.
- **Bounded lists:**
  - Every list is one `SELECT ... ORDER BY id LIMIT ? OFFSET ?`, with `limit` at most 100. Over 100 is a 400. A test captures the SQL to prove it.
  - Lists return header summaries only, with no child rows.
  - The query fetches `limit + 1` rows to compute `next_offset`, so there is no `COUNT(*)`.

**What breaks first:**

1. **Concurrent writes.** SQLite allows one writer at a time. Every write takes the write lock up front, so heavy concurrent PATCH + POST traffic forms a queue rather than corrupting data. Lists stay correct (never a torn `tax_lines` list, last write wins), but throughput is capped. Past the 5s busy timeout, a request fails with "database is locked". This is the first real limit, and the fix is Postgres with row-level locks.
2. **Duplicate POST storms** are cheap: the loser is turned away by the unique index and rolls back.
3. **Deep `OFFSET` pages** get slower, because the database still walks every skipped row. The fix is keyset pagination (`WHERE id > ?`).

## What I'd change for production

- **Postgres:**
  - `SELECT ... FOR UPDATE` on the rows being changed instead of the whole-database `BEGIN IMMEDIATE` lock.
  - `INSERT ... ON CONFLICT` for duplicates.
  - A real `NUMERIC` column for money.
- **Invoice statuses and credit notes:** draft, issued, void. Issued invoices are never deleted or rebuilt; corrections become credit notes.
- **An audit log** of every change to a transaction or invoice.
- **Keyset (cursor) pagination** for deep lists.
- **An `Idempotency-Key` header on `POST /invoices`**, so a client retrying after a timeout gets the same invoice back instead of a 409.
