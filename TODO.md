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

## Competitive feature backlog (market-parity targets, 2026-06-29)

> Aspirational feature set drawn from a market scan of pro bookkeeping platforms.
> These are **direction, not committed scope** — each becomes its own
> brainstorm → spec → plan when prioritized, and everything stays on synthetic
> data until the operator says otherwise. Several overlap existing roadmap items
> (cross-referenced inline); the Finance-engine inheritance note at the top of
> this file still applies (build shared capabilities in Finance, port to LISZA).

### 1. Core automation & data processing
- [ ] **Bank & credit-card reconciliation** — real-time sync that auto-matches bank/card transactions against the general ledger. *(Extends "Reconcile to statements" above + PLAN Phase 5.)*
- [ ] **Transaction categorization** — AI that learns vendor patterns and auto-assigns transactions to the correct GL / tax code. *(Builds on `payee_rules`; AI layer is the new part.)*
- [ ] **Document & receipt capture (OCR)** — extract line-item data from invoices/receipts to eliminate manual entry. *(Finance inherits `document-parser` / `invoice-extractor` / `taxhacker`; port to LISZA.)*
- [ ] **Accounts Payable (AP) & Receivable (AR)** — automated bill-approval routing, recurring invoice generation, automated client payment reminders. *(Feeds Step 3 AP/AR tiles.)*

### 2. Practice & workflow management
- [ ] **Task & capacity management** — track month-end close status, manage deadlines, distribute workload across a bookkeeping team. *(Multi-bookkeeper; `bookkeeper_prefs.bookkeeper_id` already anticipates multi-user.)*
- [ ] **Client portals & document requests** — secure hub to gather W-9s / onboarding docs and send persistent automated follow-ups for missing records.

### 3. Integrations & scalability
- [ ] **Ecosystem compatibility** — two-way sync with core ledgers (QuickBooks Online, Xero) plus e-commerce platforms and payment processors.
- [ ] **Customized reporting** — automated real-time financial statements + variance reports, easily shared with clients. *(Spec 2 dashboard is the first slice; per-client P&L/BS lands in Step 3.)*

### Competitive benchmark (reference — incumbents to learn from / interoperate with)
- **QuickBooks Online (QBO)** — North-American SMB standard. Strengths: deep report customization (classes/locations/multi-entity), AI reconciliation/anomaly flagging, 750+ app ecosystem, native 1099 + sales-tax filing, scales solo → multi-entity. Weaknesses: aggressive price hikes ($38→$275/mo), multi-user gated to top tiers, slow scripted support. **Implication for LISZA:** QBO two-way sync is table-stakes for integration; our wedge is price + responsiveness + AI automation depth.
- **Xero** — modern collaboration-first alternative. Strengths: Hubdoc receipt extraction, bulk transaction coding, multi-currency engine, **no per-seat fee**, clean jargon-light UI, strong cash-flow visibility. Weaknesses: laggy feeds at smaller banks/credit unions (3–5 day sync), shallow project/job costing. **Implication:** no-per-seat pricing + clean UI are the bar for our bookkeeper console.
- **FreshBooks** — client-facing, project/billing-centric. Strengths: proposal→e-sign→invoice pipeline, built-in time tracking, client collaboration hub, near-zero learning curve, strong human phone support, cheap solo tier. Weaknesses: weak core accounting engine, poor inventory scaling, expensive per-user growth (+$11/user). **Implication:** good model for the solopreneur client persona (J.B. Design) but not for multi-entity (Harborside).

> **Positioning questions to resolve before committing integration scope** (from the scan): target industry mix (e-commerce / construction / professional services), expected team size per client book (drives multi-user + per-seat strategy), and inventory-vs-services emphasis (drives COGS/inventory depth vs billing/time-tracking depth).
