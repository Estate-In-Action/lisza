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
> Implemented 2026-06-29 (Spec 2) — generator `build_dashboard.py` → `public/dashboard.json`; vanilla-JS front-end (tile default + list/rolodex toggle); prefs hybrid (DB-seed + localStorage). Drill-down + write-back deferred to Spec 3/later.
- [x] **Client overview dashboard** — bookkeeper lands on a view of **all active clients**, presented as their choice of layout: **tile** (a card per client with a few key figures — e.g. cash position, open AR, open AP, last-close date, next filing due), **list** (dense table), or **rolodex** (one client at a time, flip through). Layout is a user preference the bookkeeper can change.
- [x] **Customizable by the bookkeeper** — let them pick which fields show on a client card, reorder/hide tiles, and set the default layout. Preferences persist per bookkeeper.

### Step 2 — Multiple realistic demo clients
> Foundation (Spec 1) implemented 2026-06-29 — DB-per-client + lisza.db registry + entity dimension. Dashboard/tiles/cron remain.
- [x] **Client 1 = Guitar Manufacturing Plant** — re-cast the *existing* synthetic book as this client (manufacturer: COGS, inventory, equipment depreciation).
- [x] **Client 2 = Umbrella Corp w/ several restaurants** — new synthetic book modelling a **multi-entity** parent: several restaurant locations rolling up to one owner. Figure out the consolidation scenario (per-location books + a parent/consolidated view; inter-company eliminations).
- [x] **Client 3 = Solopreneur** — new synthetic book for a one-person business (simple Schedule-C shape, owner draws, minimal payroll). 
- [x] Generate each via the `seed_client.py` path, tweaked per business type so the numbers read realistically (different COA emphasis, transaction mix, seasonality).

### Step 3 — Per-client tiles (drill-down)
> Progress 2026-07-06 — client detail JSON and console now surface Payroll, AR, AP, Admin, Historical, read-only Inspection, Cash Flow, P&L / Balance Sheet, Reconciliation, and Filing / Tax tiles. Default-tile research is closed into this first standard set.
Decide the standard tile set a bookkeeper needs per client. Candidate set:
- [x] **Payroll** — employees, pay runs, liabilities.
- [x] **AR** (accounts receivable) — open invoices, aging.
- [x] **AP** (accounts payable) — bills due, aging.
- [x] **Admin / master data** — that client's profile (legal name, EIN, entity type, fiscal year, filing cadence, contacts, bank accounts, COA).
- [x] **Historical** — bookkeeper has an **active window** (last month / quarter / year / 2 years, configurable); everything older is "prior/historical" and surfaced here read-only. (Respects Principle #2 — history is never mutated.)
- [x] Research **what other tiles general small businesses need** and propose the default set.
  - **Default tile set:** Cash / bank balances, Reconciliation status, AR, AP, Payroll, P&L, Balance Sheet, Trial Balance, Admin, Historical, Inspection.
  - **Conditional tiles:** Cash-flow forecast (when AR/AP timing is meaningful), Sales-tax liability (when taxable jurisdictions are configured), Fixed assets (when depreciable assets exist), Project/time billing (service clients), Inventory/COGS (manufacturing/retail/restaurant clients).
  - **Why:** Xero's accounting dashboard centers bank balances, invoices, bills, fixed assets, and reconciliation prompts; QuickBooks centers cash flow, bills, reconciliation, P&L/cash-flow reporting, and tax organization; FreshBooks centers invoices, expenses/receipts, time tracking, clients, payments, and financial reports. LISZA's default should therefore bias toward bookkeeper operating risk first: unreconciled cash, money owed/owing, payroll/tax obligations, and statement readiness.

### Step 4 — Per-client automation + config
> Progress 2026-07-06 — registry now has `client_automation_profiles`, CLI/API profile get/set, an advisory due-job planner, durable workflow approval rows, generated workflow payloads, and a browser-side profile/control panel. Cron execution remains approval-gated and report-prep-only; no tax/payment/ledger actions are automatic.
- [x] **Per-client cron jobs** — reports generated on that client's cadence/need; **tax prep + filing on the client's schedule** (monthly / quarterly / annual depending on the client).
- [x] **Per-client config flow** — a guided setup that **asks the bookkeeper what to configure for each client**: which reports, filing cadence, sales-tax jurisdictions, active-window length, payroll schedule, etc. Stored as the client's automation profile and consumed by the cron layer.
  - [x] Registry-level automation profile scaffold: reports enabled, filing cadence, sales-tax jurisdictions, active window, payroll schedule, delivery channel.
  - [x] CLI config writer for bookkeeper setup (`scripts/automation_profile.py get|set`).
  - [x] Advisory planner that turns profiles into due/upcoming jobs without running tax/payment actions automatically (`scripts/automation_profile.py plan`).
  - [x] Dashboard client-detail workflow panel with local profile drafts and due-job queue.
  - [x] API write-back endpoint for browser profile drafts.
  - [x] Durable workflow approval queue and audit trail (`workflow_jobs`, `workflow_events`).
  - [x] Real scheduler/cron runner that consumes due jobs after operator approval.
  - [x] Safe execution boundary: approved jobs generate report-prep receipts only; tax filing, payments, ledger writes, and external delivery stay disabled.
  - [x] Guided setup checklist + suggested profile defaults in client detail/API (`automation_profile.py setup`, `automation_setup`).

## Competitive feature backlog (market-parity targets, 2026-06-29)

> Aspirational feature set drawn from a market scan of pro bookkeeping platforms.
> These are **direction, not committed scope** — each becomes its own
> brainstorm → spec → plan when prioritized, and everything stays on synthetic
> data until the operator says otherwise. Several overlap existing roadmap items
> (cross-referenced inline); the Finance-engine inheritance note at the top of
> this file still applies (build shared capabilities in Finance, port to LISZA).

### 1. Core automation & data processing
- [x] **Bank & credit-card reconciliation** — real-time sync that auto-matches bank/card transactions against the general ledger. *(First slice shipped: deterministic exact/date-window statement matching, duplicate-entry guard, and review payload metadata; live bank-feed sync remains a later integration.)*
- [x] **Transaction categorization** — AI that learns vendor patterns and auto-assigns transactions to the correct GL / tax code. *(First slice shipped: richer `payee_rules` metadata, rule usage learning counters, tax-code/confidence payloads, and review-visible categorization evidence; true model retraining remains a later extension.)*
- [x] **Document & receipt capture (OCR)** — extract line-item data from invoices/receipts to eliminate manual entry. *(First slice shipped: receipt text/PDF scanner extracts line items, line-item totals, category hints, and queues them in `pending_inbox`; vision/OCR hardening and invoice-specific extraction remain later extensions.)*
- [x] **Accounts Payable (AP) & Receivable (AR)** — automated bill-approval routing, recurring invoice generation, automated client payment reminders. *(First slice shipped: approval-gated AR reminder and AP bill-review workflow jobs with receipt-only execution; actual external reminder delivery, vendor payments, and recurring invoice generation remain later extensions.)*

### 2. Practice & workflow management
- [x] **Task & capacity management** — track month-end close status, manage deadlines, distribute workload across a bookkeeping team. *(First slice shipped as the durable Due Work queue with pending/approved/completed/skipped states; multi-bookkeeper assignment remains a later extension.)*
- [x] **Client portals & document requests** — secure hub to gather W-9s / onboarding docs and send persistent automated follow-ups for missing records. *(First slice shipped as registry-backed document requests, client-detail portal tile, API modes, and workflow follow-up jobs; actual file upload/auth hardening remains a later production step.)*

### 3. Integrations & scalability
- [x] **Ecosystem compatibility** — two-way sync with core ledgers (QuickBooks Online, Xero) plus e-commerce platforms and payment processors. *(First slice shipped: read-only QBO/Xero-friendly export contract for accounts, journal lines, invoices, and bills with manifest field maps; live OAuth/API two-way sync remains later integration work.)*
- [x] **Customized reporting** — automated real-time financial statements + variance reports, easily shared with clients. *(First slice shipped: client-level P&L variance report generator with JSON/CSV artifacts; richer branded report packs and delivery automation remain later extensions.)*

### Competitive benchmark (reference — incumbents to learn from / interoperate with)
- **QuickBooks Online (QBO)** — North-American SMB standard. Strengths: deep report customization (classes/locations/multi-entity), AI reconciliation/anomaly flagging, 750+ app ecosystem, native 1099 + sales-tax filing, scales solo → multi-entity. Weaknesses: aggressive price hikes ($38→$275/mo), multi-user gated to top tiers, slow scripted support. **Implication for LISZA:** QBO two-way sync is table-stakes for integration; our wedge is price + responsiveness + AI automation depth.
- **Xero** — modern collaboration-first alternative. Strengths: Hubdoc receipt extraction, bulk transaction coding, multi-currency engine, **no per-seat fee**, clean jargon-light UI, strong cash-flow visibility. Weaknesses: laggy feeds at smaller banks/credit unions (3–5 day sync), shallow project/job costing. **Implication:** no-per-seat pricing + clean UI are the bar for our bookkeeper console.
- **FreshBooks** — client-facing, project/billing-centric. Strengths: proposal→e-sign→invoice pipeline, built-in time tracking, client collaboration hub, near-zero learning curve, strong human phone support, cheap solo tier. Weaknesses: weak core accounting engine, poor inventory scaling, expensive per-user growth (+$11/user). **Implication:** good model for the solopreneur client persona (J.B. Design) but not for multi-entity (Harborside).

> **Positioning questions to resolve before committing integration scope** (from the scan): target industry mix (e-commerce / construction / professional services), expected team size per client book (drives multi-user + per-seat strategy), and inventory-vs-services emphasis (drives COGS/inventory depth vs billing/time-tracking depth).

## ERP module backlog (LedgerSMB-parity targets, 2026-06-29)

> Full double-entry ERP module set the operator wants LISZA to eventually cover
> (drawn from LedgerSMB). **Direction, not committed scope** — each module is its
> own brainstorm → spec → plan → build cycle, synthetic data only until told
> otherwise. Many map onto modules already in flight (cross-referenced inline);
> they raise LISZA from a bookkeeping console toward a small-business ERP.

- [x] **General Ledger & Journal Entry** — manual journals, adjusting/closing entries, full audit trail of every posting. *(First slice shipped: balanced manual journals, reversing/replacement adjustments, and `journal_audit` events; closing workflows remain a later extension.)*
- [x] **Sales** — customers, quotations, sales orders, invoices (quote → order → invoice pipeline). *(First slice shipped: per-client quote → sales order → open invoice pipeline; payment collection and ledger posting remain later workflow steps.)*
- [x] **Purchasing** — vendors, purchase orders, vendor invoices/bills. *(First slice shipped: per-client purchase order → unpaid vendor bill pipeline; vendor payments, approvals UI, and inventory receipt handling remain later extensions.)*
- [x] **Multiple currencies** — multi-currency transactions with FX gain/loss handling. *(First slice shipped: FX rate table and per-entry source-currency metadata/translation; realized FX gain/loss posting remains a later extension.)*
- [x] **Contact Management** — unified people/orgs directory spanning customers, vendors, leads. *(First slice shipped: per-client unified party directory with customer/vendor/both/lead roles, role filters, and archive semantics; CRM registry integration remains later work.)*
- [x] **Cash Management** — checks, receipts, bank reconciliation, cash position. *(First slice shipped: cash-position summary across cash accounts, open AR/AP, and unreconciled statement exposure; checks/receipts UI remains later work.)*
- [x] **Time tracking** — billable hours captured and rolled into invoices. *(First slice shipped: billable time entries roll into open invoices without ledger posting; payroll/time-punch integration remains later work.)*
- [x] **Fixed Assets** — asset register, depreciation schedules, disposal accounting. *(First slice shipped: asset register and straight-line depreciation schedules; disposal accounting and auto-posted depreciation entries remain later work.)*
- [x] **Inventory Management & Light Manufacturing** — stock tracking, assemblies/bills-of-materials, COGS. *(First slice shipped: stock item register, receive/consume/build movement ledger, and on-hand valuation; BOM assemblies and COGS posting remain later work.)*
- [x] **Reporting** — full statement suite (P&L, Balance Sheet, Trial Balance) over all the above. *(First slice shipped: reusable P&L, balance sheet, and trial-balance statement suite; branded report packs remain later work.)*
- [x] **Budgeting** — budgets by project/department with variance reports. *(First slice shipped: period budget lines by account with actual-vs-budget variance reports; project/department dimensions remain later work.)*

### Reference: Frappe Books (frappe/books) — patterns worth borrowing (2026-06-29)

> AGPL-3.0 Vue/Electron/SQLite double-entry app the operator flagged for ideas.
> Don't adopt their framework — borrow these proven structural patterns:
>
> - **Schema-driven model layer** — every entity (Account, Invoice, Payment, Party…) is a declarative JSON schema (fields, types, links, sections, `quickEditFields`, `keywordFields`); the framework derives DB + forms + validation from it. **This is the answer to the operator's "leave it open to be fully configurable" requirement** for Admin/CRM/Client Management: "configurable" = edit a schema, not write code. Candidate for a lightweight LISZA field-registry.
> - **Unified `Party` + `role` (Customer / Supplier / Both)** — one contact table with a role flag, not three stores. Confirms the backlog note that Contact Management is the shared substrate under CRM + Client Management + Sales/Purchasing.
> - **`Lead` → `Party` conversion** — Lead has a status funnel (Open → Replied → Interested → Opportunity → Quotation → Converted) and Party carries a `fromLead` reference. **This is exactly the CRM "convert onlooker → customer → client" flow** the operator described; model the CRM section on it.
> - **Single immutable `AccountingLedgerEntry`** — every document posts to one ledger table (`party, account, debit, credit, referenceType, referenceName, reverted, reverts`). Adjustments are **reversal entries, never edits/deletes** — matches LISZA's add-don't-subtract rule and validates the untracked `ledger_tools.py` reversal/adjusting-entry direction.
> - **Tree chart-of-accounts** (`isTree`, `parentAccount`, `rootType` = Asset/Liability/Equity/Income/Expense) — confirms LISZA's `income` (not `revenue`) choice; a parent-account tree is the upgrade path when the flat COA outgrows itself.
> - **`NumberSeries` as a first-class entity** — document numbering (invoice #, journal #) is configurable data, not hardcoded.

### New workstream — Frappe Books alignment (2026-07-09)

- [x] **Phase 1: accounting workspace shell** — move LISZA from dashboard-first
  to document-first UX. First slice: persistent left nav, dashboard as one
  destination, read-only indexes for Invoices / Bills / Journal Entries / Party
  directory, no ledger-engine rewrite. Implemented 2026-07-10 as the live
  proof shell at `https://dadadanja.zo.space/lisza/workspace`; legacy route
  remains available at `https://dadadanja.zo.space/lisza/console` while the new
  shell proves itself. Implementation note:
  `docs/plans/2026-07-09-frappe-books-alignment.md`
- [ ] **Phase 2: make v2 the canonical LISZA surface** — v2 is now the build line.
  Public/showpiece links should point to `/lisza/workspace`; v1 console/demo
  remains available only as a deprecated comparison path. Next build slices:
  document detail pages, lightweight schema registry for document fields,
  number-series helpers, and first approval-gated write actions.

## Accounting-suite gap scan — 6-package review (2026-07-11)

> Operator handed six open-source accounting/ERP packages and asked: review all
> feature sets, and if we don't have them, put them into development. This is the
> **de-duplicated gap list** — features present across the reviewed packages that
> LISZA does not yet have even as a first slice. Sources scanned: **LedgerSMB,
> Akaunting, Dolibarr, Invoice Ninja, Bigcapital, ERPNext**.
>
> Same rules as the backlogs above: **direction, not committed scope** — each
> becomes its own brainstorm → spec → plan → build, synthetic data only until the
> operator says otherwise. Where a gap is a shared bookkeeping primitive, the
> **Finance-inheritance rule applies** (build in Finance, port to LISZA) — flagged
> `[Finance-first]` below. Items that merely deepen an existing "later extension"
> note are marked `(extends: …)`.

> **First wave shipped 2026-07-11** — the money loop now closes (open invoice →
> payment → aging → cash flow). Payment application/reconciliation, AR/AP aging,
> and the cash flow statement are live in v2 (see checked items below). XLSX bank
> import added alongside the existing CSV path. Remaining A/B/C/D items stay the
> committed backlog; C-bucket new modules still confirm-first before file creation.

### A. Money movement & billing depth (highest leverage; mostly `[Finance-first]`)
- [x] **Payment application & reconciliation** — record a payment and allocate it across one/many invoices or bills; partial payments, deposits, over/underpayment handling. `[Finance-first]` *(extends: Sales/Purchasing pipelines stop at "open invoice"/"unpaid bill")* — Bigcapital, ERPNext, LedgerSMB. *(Shipped 2026-07-11: `scripts/payments.py` — receipts/disbursements, multi-invoice/bill allocation, partial + on-account/unapplied handling, over-allocation guards, posts balanced cash journal + relieves sub-ledger to paid. Wired: `/api/lisza` `payment_apply`/`payments`/`open_items` modes + v2 Payments tab. Tested end-to-end.)*
- [ ] **Online payment collection** — payment-gateway integrations (Stripe/PayPal/etc.) so a client can pay an invoice online; record the receipt back to the ledger. — Invoice Ninja, Akaunting, Dolibarr, ERPNext, Bigcapital.
- [x] **Recurring / subscription invoicing** — schedule-driven auto-generation of invoices (and bills). *(extends: AP/AR note "recurring invoice generation remains later")* — Invoice Ninja, Akaunting, ERPNext, Dolibarr. *(Shipped 2026-07-11 CR-004: `scripts/recurring_invoicing.py` — templates + `recurring_invoice_runs` idempotency ledger; Manual/Review/Auto modes; approval-gated `generate_due` posts `Dr110/Cr revenue`; advisory `recurring_invoice_due` planner job; `/api/lisza` `recurring`/`recurring_add`/`recurring_generate` + Sales "Recurring" sub-tab. 27 tests; full suite 263 passed.)*
- [ ] **Credit notes / debit notes / vendor credits** — first-class credit documents that post reversing/offsetting ledger entries (fits add-don't-subtract). `[Finance-first]` — Bigcapital, ERPNext, Invoice Ninja.
- [ ] **Dunning / late-fee escalation** — tiered automated reminder ladder + late fees. *(extends: AR reminder workflow exists; escalation/fees do not)* — Invoice Ninja, Akaunting.
- [ ] **Client payment portal** — client-facing view to see and pay open invoices. *(extends: client portal is doc-request only today)* — Invoice Ninja, Akaunting, ERPNext.
- [ ] **Estimate/quote acceptance + e-signature** — client approves a quote to convert it to an order/invoice. — Invoice Ninja, FreshBooks-style.

### B. Accounting depth (statement & compliance completeness; `[Finance-first]`)
- [x] **AR / AP aging reports** — bucketed receivables/payables aging (0–30/31–60/…). `[Finance-first]` — Bigcapital, ERPNext, LedgerSMB. *(Live: `/api/lisza/report?type=ar_aging|ap_aging` — current/1–30/31–60/61–90/90+ buckets, surfaced in the Reports tab and per-client AR/AP tiles.)*
- [x] **Cash flow statement** — formal CFS alongside the shipped P&L / BS / TB suite. `[Finance-first]` *(extends: reporting suite)* — Bigcapital, ERPNext. *(Shipped 2026-07-11: direct-method `statement_suite.cash_flow()` — cash-account-centric, buckets each entry's cash delta into operating/investing/financing by non-cash counterpart type, reconciles opening+net=closing; `/api/lisza/report?type=cash_flow` + Reports tab "Cash Flow" pill. 4 unit tests.)*
- [ ] **Tax engine** — sales-tax/VAT rate tables, tax-inclusive/exclusive lines, tax templates, tax-liability report, 1099 support. `[Finance-first]` — all six.
- [ ] **Cost centers / accounting dimensions** — tag postings by project/department/class for dimensional reporting. `[Finance-first]` *(extends: budgeting notes "project/department dimensions remain later")* — ERPNext, LedgerSMB.
- [ ] **Period / fiscal-year closing** — closing vouchers that roll income & expense into retained earnings and lock the period. `[Finance-first]` *(extends: GL note "closing workflows remain a later extension")* — ERPNext, LedgerSMB.
- [ ] **Deferred revenue / deferred expense schedules** — recognize prepaid/unearned amounts over time. `[Finance-first]` — ERPNext.
- [ ] **FX revaluation & realized/unrealized gain-loss posting** — actually post FX gain/loss, not just store source-currency metadata. `[Finance-first]` *(extends: multi-currency first slice)* — ERPNext.
- [ ] **Live bank feeds** — real bank/card sync (Plaid or equivalent) into the reconciliation lane. *(extends: reconciliation ships with deterministic matching only)* — Bigcapital, ERPNext, Akaunting.

### C. ERP breadth (new modules; per-module confirm before building)
- [ ] **CRM opportunity pipeline** — lead → opportunity → quotation with stages/forecast. *(extends: party directory has a `lead` role but no pipeline)* — Dolibarr, ERPNext.
- [ ] **Multi-company / consolidated financials** — group-level statements across client entities. — ERPNext, Akaunting, Dolibarr.
- [ ] **Warehouse & stock depth** — multi-location stock, batch/serial tracking, stock transfers, landed-cost allocation. *(extends: inventory movement ledger)* — ERPNext, Dolibarr.
- [ ] **Manufacturing** — BOM assemblies, work orders, COGS posting on build. *(extends: inventory note "BOM assemblies and COGS posting remain later")* — ERPNext, Dolibarr, LedgerSMB.
- [ ] **Project accounting & profitability** — project P&L rolling up time, expenses, and billing. *(extends: time tracking rolls into invoices but no project P&L)* — ERPNext, Dolibarr, Invoice Ninja.
- [ ] **Expense claims / employee reimbursements** — submit → approve → reimburse expense reports. — ERPNext, Dolibarr.
- [ ] **POS** — point-of-sale sales capture for retail/restaurant client personas (Harborside). — Dolibarr, ERPNext.
- [ ] **E-commerce / marketplace connectors** — sync orders from external storefronts. — Dolibarr, ERPNext, Akaunting.

### D. Platform & configurability (cross-cutting)
- [ ] **Role-based access control + 2FA** — per-user roles/permissions on client books and multi-bookkeeper access. — ERPNext, Dolibarr, Akaunting.
- [ ] **E-invoicing standards** — PEPPOL / UBL / GST-style structured e-invoice output. — ERPNext, Dolibarr.
- [ ] **Branded document/PDF template designer** — customizable invoice/statement templates. — Invoice Ninja, Akaunting.
- [ ] **Public REST API + webhooks + app marketplace** — programmable integration surface. *(extends: read-only QBO/Xero export contract)* — Akaunting, ERPNext, Invoice Ninja.
- [ ] **Number series & tree chart-of-accounts** — already captured in the Frappe Books reference above; re-confirmed as gaps by this scan (configurable numbering; parent-account tree).

> **Recommended first wave** (if the operator greenlights): bucket **A + B**, built
> Finance-first and ported — they complete the money-movement loop the current
> pipelines stop short of (open invoice → payment → aging → close) and are shared
> primitives every client book needs, versus bucket C which is per-vertical ERP
> breadth. Awaiting operator priority call before any new module files (Rule #7).

### Insights — peer benchmarking (2026-07-11)

- [ ] **Insights tab — wire a real peer-benchmark source.** Prototype shipped in
  v2 (`/lisza/workspace` → Insights): source selector + NAICS vertical selector +
  spend-mix-vs-peer-median bars + advisor read, all on **illustrative sample
  medians** and clearly marked "Prototype." Nothing to change in the code
  meanwhile. Whenever you want to pick it back up, the open question is just:
  which public source to wire first (Census CBP / BLS / Economic Census / IRS
  SOI) and then the ledger-category → NAICS-line mapping.
