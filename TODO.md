# LISZA — To Do

Work items for the bookkeeping app. See `PLAN.md` for phases and principles.

> 📚 **Bookkeeping cookbook picks** (startup-bookkeeper, financial-intelligence, document-parser, tax-receipt-autopilot, ledger-manager, monthly-reconciliation, invoice-extractor, taxhacker, find-receipts, Warm Workshop Ledger style) are tracked once in **`Finance/TODO.md`** — LISZA is a clone of the Finance engine and inherits those capabilities. Don't duplicate them here; build in Finance, port to LISZA.

## Open

- [x] **Add journal entries** — let the user post a manual balanced journal entry (debit/credit lines), not just receipt-driven intake. Builds on `scripts/post_entry.py`.
- [x] **Edit transactions** — allow correcting a posted transaction. ⚠️ Reconcile with Principle #2 ("we add, we don't subtract"): an "edit" should post a reversing/adjusting entry rather than mutate the original row, or be scoped to entries still in `pending_inbox` (not yet posted).
- [x] **Reconcile to statements** — match ledger entries against a bank/card statement, flag unmatched items, mark reconciled. Ties into Phase 5 (bank-statement automation).

## Multi-tenant bookkeeper console (operator vision, 2026-06-29)

> Big shift: LISZA today is **single-tenant** (one synthetic `ledger.db`). The
> vision below turns it into a **multi-client console a human bookkeeper drives**
> — multiple client books, a customizable dashboard, per-client tiles, and
> per-client automation. Treat each block as a step; design before building, and
> keep everything on synthetic data until the operator says otherwise.

### Step 1 — Bookkeeper dashboard (front end, customizable)
- [ ] **Client overview dashboard** — bookkeeper lands on a view of **all active clients**, presented as their choice of layout: **tile** (a card per client with a few key figures — e.g. cash position, open AR, open AP, last-close date, next filing due), **list** (dense table), or **rolodex** (one client at a time, flip through). Layout is a user preference the bookkeeper can change.
- [ ] **Customizable by the bookkeeper** — let them pick which fields show on a client card, reorder/hide tiles, and set the default layout. Preferences persist per bookkeeper.

### Step 2 — Multiple realistic demo clients
> Foundation (Spec 1) implemented 2026-06-29 — DB-per-client + lisza.db registry + entity dimension. Dashboard/tiles/cron remain.
- [x] **Client 1 = Guitar Manufacturing Plant** — re-cast the *existing* synthetic book as this client (manufacturer: COGS, inventory, equipment depreciation).
- [x] **Client 2 = Umbrella Corp w/ several restaurants** — new synthetic book modelling a **multi-entity** parent: several restaurant locations rolling up to one owner. Figure out the consolidation scenario (per-location books + a parent/consolidated view; inter-company eliminations).
- [x] **Client 3 = Solopreneur** — new synthetic book for a one-person business (simple Schedule-C shape, owner draws, minimal payroll). 
- [x] Generate each via the `seed_client.py` path, tweaked per business type so the numbers read realistically (different COA emphasis, transaction mix, seasonality).

### Step 3 — Per-client tiles (drill-down)
Decide the standard tile set a bookkeeper needs per client. Candidate set:
- [ ] **Payroll** — employees, pay runs, liabilities.
- [ ] **AR** (accounts receivable) — open invoices, aging.
- [ ] **AP** (accounts payable) — bills due, aging.
- [ ] **Admin / master data** — that client's profile (legal name, EIN, entity type, fiscal year, filing cadence, contacts, bank accounts, COA).
- [ ] **Historical** — bookkeeper has an **active window** (last month / quarter / year / 2 years, configurable); everything older is "prior/historical" and surfaced here read-only. (Respects Principle #2 — history is never mutated.)
- [ ] Research **what other tiles general small businesses need** (e.g. cash flow, sales tax liability, fixed assets, trial balance, P&L/Balance Sheet, reconciliation status) and propose the default set.

### Step 4 — Per-client automation + config
- [ ] **Per-client cron jobs** — reports generated on that client's cadence/need; **tax prep + filing on the client's schedule** (monthly / quarterly / annual depending on the client).
- [ ] **Per-client config flow** — a guided setup that **asks the bookkeeper what to configure for each client**: which reports, filing cadence, sales-tax jurisdictions, active-window length, payroll schedule, etc. Stored as the client's automation profile and consumed by the cron layer.
