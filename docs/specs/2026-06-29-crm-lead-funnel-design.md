# Spec 5 — CRM (Lead Funnel + Lead→Client Conversion)

**Date:** 2026-06-29
**Status:** Approved (operator: "move on to CRM")
**Predecessor:** Nav shell + house-tenant Admin (Spec 4)
**Successor:** Client Management (Spec 6) — shares this Party substrate

## Purpose

The nav shell shipped CRM as a "coming next" stub. This spec replaces it with a
real lead funnel: capture a prospect, move it through stages, and **convert a won
lead into a client** (which mints the client's ledger book via the existing
`register_client`). CRM and Client Management together form the unified **Party
substrate** the Spec-4 design anticipated: a *lead* is a pre-client Party that
becomes a *client* row on conversion.

## Where leads live (the substrate decision)

A lead has **no ledger book yet**, so it cannot live in the DB-per-client model.
It lives in the shared registry `lisza.db` as a new `leads` table. Conversion is
the bridge: `convert_lead` calls `register_client`, which creates
`clients/<slug>/ledger.db` + the `clients` registry row, then back-links the lead
to that `client_id`. Nothing in the per-client books changes.

## Lead lifecycle

```
intake ──▶ in_process ──▶ won  (converted → client book minted)
   │            │
   └────────────┴──────▶ lost
```

- `intake` — captured, not yet worked.
- `in_process` — actively being onboarded / quoted.
- `won` — converted to a client (terminal; carries `converted_client_id`).
- `lost` — did not convert (terminal, but re-openable to `intake`/`in_process`).

A `won` lead with a `converted_client_id` is **locked** — its stage can't change
and it can't be re-converted (idempotency guard).

## Schema (registry-only, additive)

```sql
CREATE TABLE IF NOT EXISTS leads (
    lead_id         TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    contact_name    TEXT,
    email           TEXT,
    phone           TEXT,
    entity_type     TEXT,
    stage           TEXT NOT NULL DEFAULT 'intake'
                    CHECK(stage IN ('intake','in_process','won','lost')),
    source          TEXT,
    est_monthly_fee REAL,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    converted_client_id TEXT REFERENCES clients(client_id),
    converted_at    TEXT
);
```

Created idempotently by `ensure_crm_schema()`; never touches `clients` or any
client book.

## Engine — `scripts/crm.py`

Pure-Python, testable, mirrors `tenancy.py` conventions (registry path helpers,
`sqlite3`, no global state):

| Function | Behavior |
|----------|----------|
| `ensure_crm_schema(con)` | idempotent `CREATE TABLE IF NOT EXISTS leads` |
| `add_lead(display_name, *, contact_name, email, phone, entity_type, source, est_monthly_fee, notes) -> lead_id` | insert, stage defaults `intake` |
| `list_leads(stage=None) -> list[dict]` | all leads (optionally one stage), newest first |
| `get_lead(lead_id) -> dict \| None` | single lead |
| `update_lead(lead_id, **fields)` | patch editable fields + bump `updated_at`; rejects unknown columns; refuses to edit a converted lead |
| `set_stage(lead_id, stage)` | validate against allow-list; refuse if lead is `won`+converted |
| `convert_lead(lead_id, slug, *, display_name=None, **register_kwargs) -> client_id` | guard: not already converted, slug not taken; call `register_client`; set `stage='won'`, `converted_client_id`, `converted_at`; idempotent re-convert raises `ValueError` |
| `funnel_summary() -> dict` | `{counts:{stage:n}, est_pipeline: sum est_monthly_fee of open stages, total}` |

CLI `main()` subcommands → JSON on stdout for the route to spawn:
`add`, `list`, `get`, `stage`, `convert`, `funnel`.

## Route wiring (`/api/lisza`, server-side only)

- **GET `mode=crm`** → read via `bun:sqlite` (readonly): `{leads, funnel}`.
- **POST** with `mode=crm_add | crm_stage | crm_convert` → spawn
  `python3 scripts/crm.py <subcommand> ...`, return its JSON. POST is new to this
  route (today it's read-only); branch on `c.req.method`.
- Reuse existing CORS block. Convert validates slug with the same
  `[a-z0-9-]+` rule already used for client slugs (no system underscore slugs).

## Page wiring (`/lisza/console`, server-side only)

Replace the CRM `<ComingNext/>` stub with `<CrmSection/>`:
- **Funnel header**: four stage counts + estimated monthly pipeline.
- **Add-lead form**: name (required), contact, email, phone, entity type,
  source, est. monthly fee, notes.
- **Lead list grouped by stage**: each row shows name/contact/fee; stage-advance
  buttons (intake→in_process→won/lost); a **Convert** action on `in_process`
  leads that prompts for a slug then posts `crm_convert` and, on success, the
  lead shows as won with a link into the new client's Books view.

Styling reuses the existing console tokens (no new design system).

## Testing (`scripts/test_crm.py`, TDD)

- schema idempotency (create twice → one table)
- add → list round-trip; stage filter
- `set_stage` rejects invalid stage; rejects on converted lead
- `update_lead` patches; rejects unknown column; rejects converted lead
- `convert_lead` mints a client (registry row + book exists), back-links the
  lead, flips stage to `won`
- `convert_lead` is idempotent-guarded: second convert raises
- `convert_lead` rejects a slug already registered
- `funnel_summary` counts per stage + sums est pipeline over open stages

All tests isolate via a temp `LISZA_HOME` (monkeypatch), like `test_tenancy.py`.

## Out of scope (deferred)

- Annual-renewal forms for *existing* clients — folds into Client Management
  (Spec 6) where client terms live; CRM here is new-lead acquisition.
- Email/calendar integration, automated follow-ups.
- Document/contract attachments (Spec 6 contracts).
