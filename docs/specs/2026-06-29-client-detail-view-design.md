# Spec 3A — Client Detail View + Presentation Tiles

**Date:** 2026-06-29
**Status:** Approved (design checkpoint passed)
**Predecessors:** Spec 1 (multi-tenant foundation), Spec 2 (bookkeeper dashboard)
**Successors:** Spec 3B (payroll engine), Spec 3C (payroll tile + tax forms)

## Purpose

Give the bookkeeper a drill-in **per-client detail page**, reached by clicking a
client on the Spec 2 dashboard. The page presents five tiles. Four of them
(AR, AP, admin, historical) run over data the client books already hold; the
fifth (payroll) is a placeholder slot that 3B/3C fill in. This spec ships a
fully navigable per-client experience and establishes the detail-page shell.

## Scope

**In scope**
- A per-client JSON generator that opens each client `ledger.db` (read-only) and
  writes `public/clients/<slug>.json`.
- A detail view in the existing vanilla-JS front-end, routed by URL hash
  (`#client/<slug>`), with a back control to the dashboard.
- Four data-backed tiles: AR, AP, admin, historical.
- A payroll placeholder tile.
- pytest coverage for the generator; headless render check for the front-end.

**Out of scope**
- Any payroll computation, employee model, or tax forms (3B/3C).
- Any write/edit/posting actions — the detail view is strictly read-only.
- Authentication, multi-bookkeeper access control.

## Architecture

Follows Spec 2's static-JSON pattern (Zo Sites are static; no live backend).

```
ledger.db (per client)  --build_client_detail.py-->  public/clients/<slug>.json
                                                              |
dashboard.js  --click tile--> #client/<slug> --fetch--> render detail tiles
```

- `scripts/build_client_detail.py`
  - `build_client_detail(slug) -> dict` — opens `tenancy.resolve_db(slug)`,
    reads invoices/bills/entries/splits/accounts/entities/client_profile,
    returns the detail payload. Opens no other client's book.
  - `write_client_detail(slug, path=None) -> Path` — writes
    `public/clients/<slug>.json`.
  - `write_all() -> int` — iterates `tenancy.list_clients()`, writes each.
  - CLI entry: `python3 build_client_detail.py` writes all active clients.

- **As-of date.** Aging and the "current period" are computed against the
  client's **last posted `entry_date`** (synthetic data ends ~2026-06-09), not
  wall-clock `today`. This keeps the demo's aging buckets meaningful.

- **Front-end.** Extend `public/dashboard.js`:
  - Hash router: empty/`#` -> dashboard grid; `#client/<slug>` -> detail page.
  - Detail page renders a header (display name, entity type, status, back link)
    and the five tiles.
  - Reuse the existing `esc()` helper for every interpolated string.
  - No new dependencies; same inline-style design language as the dashboard.

## Detail JSON shape (`public/clients/<slug>.json`)

```json
{
  "generated_at": "2026-06-29T...Z",
  "slug": "guitar-works",
  "display_name": "Guitar Works",
  "entity_type": "llc",
  "status": "active",
  "as_of": "2026-06-09",
  "ar": {
    "open_total": 179352.57, "open_count": 23,
    "aging": {"current": 0.0, "d1_30": 0.0, "d31_60": 0.0,
              "d61_90": 0.0, "d90_plus": 0.0},
    "top_open": [{"party": "...", "due_date": "...", "amount": 0.0,
                  "days_past_due": 0}]
  },
  "ap": { "...same shape as ar, over unpaid bills..." },
  "admin": {
    "legal_name": "...", "ein_masked": "••-•••1234",
    "fiscal_year_end": "12-31", "filing_cadence": "quarterly",
    "next_filing_due": "2026-07-31",
    "entities": [{"name": "...", "type": "..."}]
  },
  "historical": {
    "span": {"first": "2024-...", "last": "2026-06-09"},
    "monthly": [{"month": "2025-07", "revenue": 0.0,
                 "expense": 0.0, "net": 0.0, "entries": 0}],
    "entry_count": 0
  },
  "payroll": {"status": "pending", "message": "Payroll engine ships in 3B/3C"}
}
```

## Tile definitions

1. **AR** — from `invoices WHERE status='open'`. `open_total`, `open_count`,
   aging buckets by `due_date` vs `as_of`
   (current = not yet due; d1_30/d31_60/d61_90/d90_plus past due), and the top
   open invoices (by amount, capped, e.g. 8).
2. **AP** — same computation over `bills WHERE status='unpaid'`.
3. **Admin** — `client_profile` fields. EIN masked to last 4
   (`••-•••NNNN`); `null` if no EIN. `entities` lists
   active entities (single-entity clients show one row; Harborside shows 3).
4. **Historical** — last 12 months ending at `as_of`. Revenue = credit-normal
   movement on revenue accounts (type `revenue`); expense = debit-normal
   movement on expense accounts (type `expense`); net = revenue - expense.
   Plus total `entry_count` and the full data `span`.
5. **Payroll** — static placeholder object; the front-end renders a muted
   "coming soon" card.

## Account classification

Revenue/expense splits are classified by joining `splits.account` to
`accounts.type`. Sign handling mirrors `reports_entity.account_balance`:
revenue accounts are credit-normal (revenue = `SUM(cr - dr)`), expense accounts
are debit-normal (expense = `SUM(dr - cr)`), counting only `entries.status =
'posted'`.

## Testing

- `scripts/test_build_client_detail.py`:
  - aging buckets sum to `open_total` for both AR and AP;
  - a known-due invoice lands in the correct bucket;
  - EIN masking shows only last 4 (and `null` passes through);
  - monthly trend has <=12 rows, newest within the data span, net = rev - exp;
  - payroll placeholder present;
  - generator opens only the requested client's DB (isolation).
- Front-end: headless node render harness feeds a real `clients/<slug>.json`
  through the detail renderer; assert all five tiles render and strings are
  escaped (proxy tunnel was unreliable in Spec 2).

## Risks / notes

- Synthetic books end mid-2026; using `as_of = last entry` avoids an all-empty
  aging view. Documented so 3B/3C use the same convention.
- `next_filing_due` already lives in the registry summary (Spec 2); the admin
  tile reads it from the client book's profile for a self-contained detail JSON.
