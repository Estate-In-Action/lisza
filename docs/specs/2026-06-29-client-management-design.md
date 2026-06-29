# Spec 6 — Client Management (Roster, Terms, Contracts)

**Date:** 2026-06-29
**Status:** Approved (operator: "then Client Management")
**Predecessor:** CRM lead funnel (Spec 5) — shares the Party substrate
**Successor:** Payroll ingestion (separate cycle)

## Purpose

CRM acquires *new* leads and converts a won one into a client (minting its
ledger book). Client Management is the other half of the Party substrate: the
surface for managing **existing** clients — add a client directly (walk-in, not
via a lead), **archive/restore** a client whose engagement paused or ended,
**modify engagement terms** (fee, filing cadence, fiscal-year-end, legal name),
and keep a lightweight **contract trail** (engagement letters / renewals).

CRM's spec deferred annual-renewal forms and contract attachments here on
purpose — this is where client *terms* live.

## Where the data lives (substrate decision)

Same principle as tenancy: **isolation is physical; the registry only caches,
never owns truth.**

| Datum | Home | Why |
|-------|------|-----|
| Archive **status** | registry `clients.status` | a roster-level view the bookkeeper owns; already exists (`active`/`archived`) |
| Engagement **terms** (fee, cadence, FYE, legal name, EIN, entity type, display name) | per-book `client_profile` | terms belong to the client's own book; registry caches only `display_name`/`entity_type` |
| **Contracts** | per-book `contracts` (new) | a client's documents belong in the client's book, not the shared registry |

Adding a client reuses `tenancy.register_client` unchanged (mints the book +
registry row). Archive/restore flips `clients.status` only — **the book is never
deleted** (Rule #5: we add knowledge, we don't subtract it; archive ≠ delete).

## Schema (additive)

`client_profile` gains one additive column (idempotent `ALTER`):

```sql
ALTER TABLE client_profile ADD COLUMN monthly_fee REAL;
```

New per-book table:

```sql
CREATE TABLE IF NOT EXISTS contracts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL,
    effective_date TEXT,
    end_date       TEXT,
    monthly_fee    REAL,
    status         TEXT NOT NULL DEFAULT 'draft'
                   CHECK(status IN ('draft','active','expired','terminated')),
    notes          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Created idempotently by `ensure_client_mgmt_schema(con)`; never touches the
registry or another client's book.

## Engine — `scripts/client_mgmt.py`

Pure-Python, testable, mirrors `tenancy.py`/`crm.py` conventions.

| Function | Behavior |
|----------|----------|
| `ensure_client_mgmt_schema(con)` | idempotent: add `monthly_fee` column + create `contracts` |
| `list_all(status=None) -> list[dict]` | roster: registry rows (excl. house) merged with each book's terms; optional `status` filter (`active`/`archived`) |
| `get_client(slug) -> dict \| None` | one client: registry row + `client_profile` terms + summary |
| `add_client(slug, display_name, *, legal_name, entity_type, ein, fiscal_year_end, filing_cadence, monthly_fee) -> client_id` | validate slug `[a-z0-9-]+` (no system slugs); reject taken slug; call `register_client`; persist `monthly_fee` |
| `archive_client(slug)` / `restore_client(slug)` | flip `clients.status`; refuse on house/unknown slug; idempotent |
| `update_terms(slug, **fields)` | patch editable book terms (`display_name`, `legal_name`, `ein`, `entity_type`, `fiscal_year_end`, `filing_cadence`, `monthly_fee`); mirror `display_name`/`entity_type` to the registry cache; reject unknown fields |
| `list_contracts(slug) -> list[dict]` | contracts for a client, newest first |
| `add_contract(slug, title, *, effective_date, end_date, monthly_fee, notes, status) -> id` | insert a contract row |
| `set_contract_status(slug, contract_id, status)` | validate against allow-list; update |

CLI `main()` subcommands → JSON on stdout: `list`, `get`, `add`, `archive`,
`restore`, `terms`, `contracts`, `contract-add`, `contract-status`. Like
`crm.py`, `main()` catches `ValueError` → `{"error": msg}` exit 0 so the route
always gets JSON.

## Route wiring (`/api/lisza`, server-side only)

- **GET `mode=clients`** → spawn `python3 client_mgmt.py list` (reads through
  Python so additive schema self-applies on first call), return `{clients}`.
- **GET `mode=client&slug=…`** → `python3 client_mgmt.py get <slug>`.
- **POST** `mode=cm_add | cm_archive | cm_restore | cm_terms | cm_contract_add
  | cm_contract_status` → spawn the matching subcommand, return its JSON.
- Reuse existing CORS + `slugOk` (`SYSTEM_SLUGS` allow-list still rejects
  `_house`).

## Page wiring (`/lisza/console`, server-side only)

Replace the Client-Management `<ComingNext/>` stub with `<ClientsSection/>`:
- **Roster header**: active vs. archived counts; an active/archived filter.
- **Add-client form**: slug (required), display name (required), entity type,
  legal name, EIN, fiscal-year-end, filing cadence, monthly fee.
- **Client list**: each row shows name / slug / fee / cadence / status; an
  inline **Edit terms** panel; an **Archive**/**Restore** button; a **Contracts**
  expander listing contract rows with an add-contract form and status control.

Styling reuses existing console tokens.

## Testing (`scripts/test_client_mgmt.py`, TDD)

- schema idempotency (run twice → one `contracts` table, one `monthly_fee` col)
- `add_client` mints book + registry row, persists `monthly_fee`; rejects taken
  slug; rejects system slug
- `list_all` shows the new client with merged terms; `status` filter works
- `archive_client` flips status, hides from `list_all(status='active')`, book
  still exists on disk; `restore_client` reverses it
- `update_terms` patches book + mirrors registry cache; rejects unknown field
- `add_contract` / `list_contracts` round-trip; `set_contract_status` validates
- house tenant never appears and can't be archived

All tests isolate via a temp `LISZA_HOME` (monkeypatch), like `test_crm.py`.

## Out of scope (deferred)

- File/PDF upload for contracts (records are structured rows now; binary
  attachments later).
- Auto-deriving "current terms" from the active contract (terms and contracts
  are kept as two surfaces; no coupling yet).
- Billing/invoice generation from `monthly_fee` (Books already has invoices).
