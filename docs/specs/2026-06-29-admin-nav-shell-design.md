# Spec 4 — Nav Shell + House-Tenant Admin

**Date:** 2026-06-29
**Status:** Approved (design checkpoint passed)
**Predecessors:** multi-tenant foundation, bookkeeper dashboard, client detail, payroll engine (3A/3B/3C)
**Successors:** CRM (Spec 5), Client Management (Spec 6), Payroll Ingestion (separate cycle, locked C/Both)

## Purpose

The console today is one screen: a client roster plus a per-client view
(Overview / Reports / Categorize / Payroll). The operator wants three new
top-level sections — **Admin**, **CRM**, **Client Management** — each "left
open to be fully customized/configurable."

This spec builds the **nav shell** (all four destinations real) and **one
section fully — Admin** — using the house-tenant model. CRM and Client
Management ship as honest "coming next" stubs so the nav is real without
half-building two subsystems. Admin proves the shell at the lowest risk because
it reuses the entire existing console with near-zero new engine code.

## The house-tenant idea

The bookkeeper becomes their own tenant: a special book at
`clients/_house/ledger.db`, registered with `kind='house'` and hidden from the
client roster. Admin then points the **existing** console machinery at that one
book:

- **Overview / Reports** → the bookkeeper's own P&L / Balance Sheet / cash.
- **AR** → the fees their clients owe them (their own invoices, their own book).
- **Payroll / 941 / W-2** → the same engine from Spec 3B/3C, their own staff.

Because `_house` gets the *identical* schema as any client book (COA, ledger
core, `book_schema`), every existing engine works against it untouched. That is
the entire reason Admin is the cheapest section to build first.

## Scope

**In scope**
- A top-level nav with four destinations: **Books** (existing roster + console,
  unchanged), **Admin**, **CRM** (stub), **Client Management** (stub).
- Registry: one additive column `kind` on `clients`; `register_client` gains a
  `kind` param; an idempotent `ensure_house()` that registers `_house`.
- `list_clients` excludes `kind='house'` so `_house` never appears in Books.
- Admin renders the existing Overview / Reports / Categorize / Payroll tabs
  pointed at `_house`.
- A configurable "housekeeping" surface: a stored tile/field config for
  `_house` with a sane default and a clean extension seam.
- Tests: migration idempotency, roster hiding, `_house` resolvability, Admin
  render against the house book.

**Out of scope**
- Building CRM or Client Management beyond a "coming next" placeholder.
- Payroll ingestion (spreadsheet/time-punch importer) — its own locked cycle.
- Any new accounting engine, report type, or tax math — Admin is pure reuse.
- New per-`_house` housekeeping tiles beyond proving the extension seam exists.

## Registry changes (the only schema touch)

`clients` today has no `kind`. Add it via the existing additive-migration
pattern in `_ensure_registry_columns`, identical in spirit to the
`entity_count` precedent:

```sql
-- in _ensure_registry_columns, guarded by book_schema._has_column
ALTER TABLE clients ADD COLUMN kind TEXT NOT NULL DEFAULT 'client';
```

Idempotent, defaults every existing row to `'client'`, touches no data.

- `register_client(..., kind: str = 'client')` writes `kind` into the
  `clients` INSERT.
- `ensure_house()` — registers slug `_house`, `display_name='House (My Books)'`,
  `kind='house'`; idempotent (INSERT OR REPLACE already used).
- `list_clients(status='active')` adds `AND kind != 'house'` to its WHERE so the
  roster never shows the house book.
- `resolve_db('_house')` already returns `clients/_house/ledger.db` with no
  special-casing — the canonical path derivation handles it for free.

## Implementation check (flagged risk)

The zo.space `/api/lisza` slug resolver validates `?client=<slug>` to be
path-traversal-safe. The validator must **permit the leading-underscore
`_house`** while the roster still hides it. `_house` is never user-typed — it's
reached only via the Admin nav — so the resolver allows it and the roster
filter (above) keeps it out of Books. Confirm the validator regex admits
`_house` (and *only* a fixed allow-list of underscore-prefixed system slugs,
not arbitrary `_`-prefixed input) when wiring the Admin route.

## Nav shell shape

```
┌─ Nav ────────────────────────────────────────────┐
│  Books   Admin   CRM*   Client Management*        │   (* = stub this cycle)
└──────────────────────────────────────────────────┘
  Books  → existing roster + per-client console (unchanged:
           layout switcher tiles/list/rolodex, field toggles)
  Admin  → existing Overview/Reports/Categorize/Payroll, bound to _house,
           + housekeeping config surface
  CRM,
  Client Management → "Coming next" placeholder
```

The layout switcher and field toggles stay inside Books exactly as today; the
shell wraps them, it does not move them.

## Housekeeping config surface

"Fully configurable" is honored without over-building: `_house` carries its own
tile/field config (reusing the `bookkeeper_prefs.card_fields_json` shape or a
parallel `_house`-scoped row), with a sensible default config and a documented
extension seam so future housekeeping-specific tiles can be added without
forking the console. This cycle proves the seam with the default; it does not
ship bespoke housekeeping tiles.

## Testing

- **Migration idempotency** — running `_ensure_registry_columns` twice leaves a
  single `kind` column; existing rows default to `'client'`.
- **Roster hiding** — after `ensure_house()`, `list_clients()` returns the real
  clients and *not* `_house`.
- **House resolvability** — `resolve_db('_house')` →
  `clients/_house/ledger.db`; the house book has the full schema (accounts,
  entries, splits, invoices, bills, payroll tables).
- **Admin render** — headless check that the Admin view renders the four tabs
  against the house book (Overview/Reports/Categorize/Payroll) with no error,
  including the empty-state path when `_house` has no data yet.
- **Stub honesty** — CRM and Client Management render the placeholder, not a
  blank or a crash.

## Out-of-scope deferrals (logged, not built)

- CRM (Lead funnel, intake/in-process/renewal forms) — Spec 5; model on Frappe
  Books' `Lead → Party` conversion (see TODO ERP backlog reference).
- Client Management (add/archive client, modify terms, contracts) — Spec 6;
  likely shares the unified Party substrate with CRM.
- Payroll ingestion (finished-register import + raw hours/time-punch calc) —
  separate locked C/Both cycle under the Payroll tab.
- Any bespoke housekeeping tiles beyond the default config + seam.
