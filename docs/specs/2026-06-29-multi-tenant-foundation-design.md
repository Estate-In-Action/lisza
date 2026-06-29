# LISZA Multi-Tenant Foundation — Design

**Date:** 2026-06-29
**Status:** Approved (design) — implementation pending
**Scope:** Spec 1 of 4 in the multi-tenant bookkeeper console roadmap
(see `TODO.md` → "Multi-tenant bookkeeper console").

## Roadmap context

The full "go big" vision decomposes into four sequential specs, each
built and tested before the next:

1. **Tenancy foundation** ← *this spec*: data model, client registry,
   isolation, migrate Client 1, seed Clients 2 & 3.
2. Bookkeeper dashboard (client overview: tile / list / rolodex).
3. Per-client tiles (Payroll, AR, AP, Admin, Historical, …).
4. Per-client automation (cron jobs, tax cadence, config flow).

This spec is the load-bearing foundation everything else sits on.

## Guiding principle

**Isolation is physical; the registry only caches, never owns.**

- Each client's books live in their own SQLite file. One client's bug or
  corruption physically cannot reach another — there is no shared query
  surface to filter wrong, so the cross-tenant data-leak bug class is
  eliminated by construction.
- The shared `lisza.db` indexes and caches client data for fast
  cross-client reads (the future dashboard) but is never the source of
  truth. Every book is self-describing and portable.

This single rule answers "where does X live?" consistently at every layer.

## Decisions (locked during brainstorming)

- **Tenancy model — Hybrid (C):** DB-per-client books + a thin shared
  `lisza.db` registry/cache.
- **Sub-entities — Entity dimension with eliminations (A+):** every client
  is entity-aware (single-business clients have one default entity, n=1);
  multi-location clients model each location as an entity; a flagged
  inter-company entry type nets out of consolidated reports.
- **Registry — Profile canonical in the book (B):** each client's
  `ledger.db` owns `client_profile` + `entities`; `lisza.db` holds a
  pointer plus a cached projection refreshed by a sync job.

## 1. File layout

```
LISZA/
├── lisza.db                       # NEW shared app-state: registry + summary cache + bookkeeper prefs
├── clients/
│   ├── guitar-works/ledger.db     # Client 1 (copied from today's ./ledger.db, not moved)
│   ├── harborside-group/ledger.db # Client 2 (umbrella corp, multi-entity)
│   └── jb-design/ledger.db        # Client 3 (solopreneur)
├── scripts/
│   ├── tenancy.py                 # NEW seam: registry CRUD, client→db resolution, summary refresh
│   └── (existing scripts gain --client resolution via resolve_db_path)
```

The current `./ledger.db` is **copied** into `clients/guitar-works/` and
kept in place until the migration is verified (Principle #2 — we add, we
don't subtract).

## 2. Per-client book — additive schema

Every existing table (`accounts`, `entries`, `splits`, `pending_inbox`,
`payee_rules`, `invoices`, `bills`, `category_overrides`,
`statement_lines`, `reconciliation_matches`) is unchanged. We add:

- **`client_profile`** (single row) — identity source of truth:
  `client_id, slug, legal_name, display_name, ein, entity_type,
  fiscal_year_end, filing_cadence, active_window, created_at`.
- **`entities`** — `id, name, type, is_default, active`. Every book has
  ≥1; simple clients get exactly one default entity.
- **`entries.entity_id`** — new NOT-NULL column, FK → `entities.id`,
  defaults to the book's default entity. The consolidation dimension.
- **`entries.is_intercompany`** — INTEGER flag, default 0. Consolidated
  reports net these out; per-entity reports show raw entries.

### Migration of an existing book

`init_ledger.py` becomes idempotent for these additions: on an existing
book it creates `client_profile`/`entities` if absent, inserts a default
entity, and backfills `entity_id` on existing `entries` to the default
entity (then enforces NOT NULL). `is_intercompany` defaults to 0.

### Reports become entity-aware

Per-entity report = filter `entity_id`. Consolidated report = aggregate
all entities, excluding `is_intercompany=1` so inter-company cash sweeps
don't double-count. `v_account_balances` gains an entity-scoped variant
(or accepts an entity filter at query time); single-entity clients are the
n=1 case of the same code path.

## 3. Shared `lisza.db` schema

```sql
clients(
  client_id TEXT PRIMARY KEY, slug TEXT UNIQUE, db_path TEXT,
  status TEXT CHECK(status IN ('active','archived')) DEFAULT 'active',
  created_at TEXT,
  -- cached projection (refreshed, never authoritative):
  display_name TEXT, entity_type TEXT, last_close_date TEXT, next_filing_due TEXT
)

client_summary(
  client_id TEXT, as_of TEXT, cash REAL, open_ar REAL, open_ap REAL,
  ar_count INTEGER, ap_count INTEGER, last_entry_date TEXT
)

bookkeeper_prefs(            -- app-state, stubbed for Spec 2
  bookkeeper_id TEXT PRIMARY KEY, layout TEXT, card_fields_json TEXT,
  default_client TEXT, updated_at TEXT
)
```

## 4. `tenancy.py` interface

The one module all client resolution routes through:

- `list_clients() -> list[ClientRow]` — registry rows.
- `resolve_db(slug) -> Path` — path to that client's `ledger.db`.
- `register_client(slug, profile…) -> client_id` — create
  `clients/<slug>/ledger.db`, write `client_profile` + default entity,
  insert the registry row.
- `refresh_summary(slug)` / `refresh_all()` — recompute
  cash/AR/AP/last-close into the `lisza.db` cache. Spec 4 schedules these.

## 5. Existing-script resolution

`init_ledger.py`, `seed_synthetic.py`, `ledger_tools.py`, and the report
scripts route their DB path through one helper `resolve_db_path(client=None)`:
a `--client <slug>` arg resolves via `tenancy.py`, falling back to the
existing `LISZA_DB` env var, then the legacy `./ledger.db`. No query
rewrites — isolation comes from *which file* you open.

## 6. The three demo clients (synthetic, realistic)

- **Guitar Works** (`guitar-works`) — manufacturer: COGS, raw-material
  inventory, equipment depreciation, wholesale AR. One entity. Re-cast
  from the current book.
- **Harborside Restaurant Group** (`harborside-group`) — owner + 3
  restaurant entities; food/bev sales, heavy payroll, a couple flagged
  inter-company cash sweeps so consolidated *and* per-location both
  reconcile.
- **J.B. Design** (`jb-design`) — solopreneur: service revenue, owner
  draws (equity), contractor/home-office expenses, no payroll, Schedule-C
  shape. One entity.

Each seeded via the `seed_synthetic.py` path, parameterized per business
type (different COA emphasis, transaction mix, seasonality) so the numbers
read realistically.

## 7. Testing

- **tenancy unit tests** — register, resolve, refresh; an **isolation
  test** proving a write to Client A never appears in Client B.
- **entity-aware report test** — per-entity sums plus a consolidated sum
  with eliminations netted out.
- Existing `test_ledger_tools` / `test_reports` stay green against the
  migrated `guitar-works` book.
- The balance trigger stays enforced per book.

## 8. Scope fence (explicitly NOT in this spec)

- No dashboard UI (Spec 2) — but `bookkeeper_prefs` is created for it.
- No tile widgets (Spec 3) — reports already produce the underlying data.
- No cron scheduling / tax filing (Spec 4) — but `refresh_summary` is
  built so Spec 4 can schedule it.
- Synthetic data only; no real client data.

## Rollback

- Drop `lisza.db` and the `clients/` tree; the original `./ledger.db`
  remains untouched, so the single-tenant app is fully intact.
