# LISZA Spec 2 — Bookkeeper Dashboard (Design)

**Date:** 2026-06-29
**Status:** Approved (brainstorm), pending implementation plan
**Roadmap position:** Spec 2 of 4 (multi-tenant bookkeeper console). Spec 1
(tenancy foundation) shipped 2026-06-29. Spec 3 = per-client tiles, Spec 4 =
per-client cron/tax.

## Goal

Give the bookkeeper a single browser view of **all active clients**, rendered
as their choice of **tile / list / rolodex** layout, with a customizable card
field set. The dashboard reads a generated `dashboard.json`; it does not query
client books directly.

## Locked decisions (from brainstorming)

1. **Surface = JSON + Zo Site.** A Python generator writes a static
   `public/dashboard.json`; a vanilla-JS static front-end renders it. No
   framework, no build step (matches the niner6 static-site convention).
2. **Default layout = tile**, all three layouts available as a client-side
   toggle. (`bookkeeper_prefs.layout` already defaults to `'tile'`.)
3. **Persistence = hybrid.** Initial state seeds from `bookkeeper_prefs` baked
   into the JSON; live tweaks persist in browser `localStorage`. **No
   write-back endpoint this spec** — the seam to add one (POST →
   `bookkeeper_prefs`) is explicit and deferred.
4. **Card field set** — default fields always shown; optional fields toggle on.

## Architecture

Two units with a clean seam:

- **Generator** — `scripts/build_dashboard.py`. Reads the registry
  (`clients` + `client_summary` + `bookkeeper_prefs`) and writes one artifact:
  `public/dashboard.json`. It performs **no new computation against client
  books** — it projects the registry cache that `refresh_all()` already
  maintains. Pure Python, fully unit-testable.
- **Front-end** — `public/index.html` + `public/dashboard.js`. Vanilla JS.
  Fetches `dashboard.json`, renders the three layouts, handles layout + field
  toggles, persists live choices to `localStorage`.

### Cache extension (prerequisite)

`entity_count` and the filing cadence live in each client's **book**, not the
registry — so to keep the generator a pure registry read, `refresh_summary()`
(which already opens each book) is extended to cache two fields:

- **`entity_count`** — new column on `client_summary` (`INTEGER`), added with
  the existing additive idempotent pattern (`_has_column` guard + `ALTER
  TABLE`). `consolidates` is derived as `entity_count > 1` (not stored).
- **`next_filing_due`** — the `clients.next_filing_due` column already exists.
  `refresh_summary()` computes it from the book's
  `client_profile.filing_cadence` plus a reference date, using a deterministic
  rule:
  - `monthly` → last day of the month following the last entry's month.
  - `quarterly` → the statutory filing date of the next calendar quarter end
    (Q1→Apr 30, Q2→Jul 31, Q3→Oct 31, Q4→Jan 31).
  - `annual` → Apr 15 of the year following the book's fiscal-year end.

This is the only change to Spec 1 code; it is additive and backward-compatible.

## Data flow

```
lisza.db (clients + client_summary + bookkeeper_prefs)
      |  refresh_all()  -> refreshes cache, now incl. entity_count + next_filing_due
      v
build_dashboard.py  --writes-->  public/dashboard.json
      |  (bakes in prefs: layout, card_fields)        |
      |                                                v
      +--------------------------------->  index.html + dashboard.js  (browser)
                                                       |
                            localStorage overrides baked prefs on next load
```

### `dashboard.json` shape

```json
{
  "generated_at": "2026-06-29T12:00:00Z",
  "prefs": {
    "layout": "tile",
    "card_fields": ["cash", "open_ar", "open_ap", "last_entry"]
  },
  "clients": [
    {
      "slug": "harborside-group",
      "display_name": "Harborside Group",
      "entity_type": "llc",
      "status": "active",
      "cash": 1545642.03,
      "open_ar": 2578035.13,
      "open_ap": 1194746.72,
      "ar_count": 41,
      "ap_count": 33,
      "entity_count": 3,
      "consolidates": true,
      "last_entry": "2026-06-09",
      "next_filing_due": "2026-07-31"
    }
  ]
}
```

Only `status='active'` clients are included (`list_clients()` default). Clients
are ordered by `display_name`.

## Card field set

**Default (always rendered):**
- Display name + entity type (card header)
- Cash position
- Open AR
- Open AP
- Last entry date

**Optional (toggle on via `card_fields_json` / localStorage):**
- AR count / AP count
- Entity count + consolidates badge (⛓)
- Next filing due
- Status

The default `card_fields` baked into the JSON is
`["cash","open_ar","open_ap","last_entry"]` (the header is structural, not a
toggleable field).

## Multi-entity figures

Roster figures for the consolidating client (Harborside) need **no
consolidation logic**: the inter-company management fee nets to zero on cash,
and AR/AP carry no inter-company rows, so `client_summary` already holds true
totals. Consolidated P&L / balance-sheet views are a Spec 3 (drill-down)
concern.

## Front-end behavior

- On load: fetch `dashboard.json`; read `localStorage` for any saved overrides;
  effective state = localStorage ?? baked prefs.
- **Layout toggle** — three buttons (tile / list / rolodex); switching
  re-renders the same client data and writes the choice to `localStorage`.
- **Field toggles** — a small "fields" control lists the optional fields;
  toggling re-renders and persists to `localStorage`.
- **Rolodex** — prev/next cycles one client at a time; index need not persist.
- **Empty / stale states** — if `clients` is empty, show "No active clients."
  Show `generated_at` as a "data as of" stamp so a stale JSON is visible.
- **Click on a client** — no-op placeholder this spec (drill-down is Spec 3).

## Testing

- **Generator (TDD, pure Python):**
  - JSON includes all active clients, ordered by display name.
  - Per-client figures equal the `client_summary` cache values.
  - `prefs` block bakes in `bookkeeper_prefs` (layout + card_fields), with
    documented defaults when no prefs row exists.
  - `consolidates` is `true` iff `entity_count > 1`.
  - `next_filing_due` computes correctly for monthly / quarterly / annual.
  - Archived clients are excluded.
- **Cache extension (TDD):** `refresh_summary()` populates `entity_count` and
  `next_filing_due`; idempotent on a legacy `client_summary` lacking the new
  column.
- **Front-end:** verified manually in-browser (via the Zo local-service proxy)
  — all three layouts render, toggles persist across reload, empty state shows.

## Scope boundaries (explicitly NOT in this spec)

- **No drill-down** — clicking a client is a no-op (Spec 3).
- **No write-back service** — prefs are read-from-DB / write-to-localStorage
  only; a POST endpoint is deferred.
- **No cron / auto-refresh** — `dashboard.json` is regenerated by running
  `build_dashboard.py`; scheduling is Spec 4.
- **No publish** — build + verify locally via the proxy. The `publish_site`
  deploy to the LISZA Zo Site is an **operator-gated** step, triggered manually,
  not performed by the spec.
- **Single bookkeeper** assumed; `bookkeeper_id` PK supports multi later but no
  multi-user UX is built now.

## Files

| File | Responsibility |
|------|----------------|
| `scripts/build_dashboard.py` | Generator: registry → `public/dashboard.json` |
| `scripts/test_build_dashboard.py` | Generator TDD tests |
| `scripts/tenancy.py` (modify) | Extend `refresh_summary()`: cache `entity_count` + `next_filing_due`; add `client_summary.entity_count` column |
| `scripts/test_tenancy.py` (modify) | Tests for the cache extension |
| `public/index.html` | Front-end shell + styles |
| `public/dashboard.js` | Fetch + render + layout/field toggles + localStorage |
| `public/dashboard.json` | Generated artifact (committed, like niner6 JSON) |
