# LISZA Ops Log

Chronological "what changed recently" log for the LISZA bookkeeping platform. Newest
entry on top. Read the last few entries before starting work; append your own at session
end. The one-row-per-change registry is `docs/CHANGE_LOG.md`; narrative detail lives here.

**Context:** LISZA is a multi-tenant AI bookkeeping platform. A real test client is
onboarding as a proof of concept, which makes a stable, bookkeeper-usable **v2 the
release baseline**. It keeps real client books, so the operative discipline is: nothing
reaches a posted P&L without human review, and no real personal data ever reaches the
public repo.

---

### 2026-07-12 — Period close: rolling a fiscal month into retained earnings and locking it (CR-008)

Last of the operator's 1–5 workflow, and the piece that turns LISZA from "a ledger you keep
posting to forever" into "a book you can actually close." Until now income and expense
accumulated with no period boundary — no retained-earnings roll, and nothing stopping a
backdated posting into a month you'd already reported on. `scripts/period_close.py` (TDD, 14
tests, RED→GREEN) closes that.

The mechanic is standard double-entry close, done as one balanced journal:

- **Sum the P&L over the window.** `close_period(client, start, end)` reads only
  `status='posted'` splits between the dates and totals each income and expense account.
- **Roll the net into retained earnings.** It posts a single closing entry that debits every
  income account and credits every expense account by its period balance, then books net income
  to retained earnings 300 — `Cr 300` on a profit, `Dr 300` on a loss. The sign of the retained
  side flips with the result, and every leg is drawn from the same summed balances so the entry
  ties by construction (the DB balance trigger would reject it otherwise).
- **Lock the window.** A `periods` row is written `status='closed'`, and a guard added to
  `post_json` now rejects any new entry dated inside a closed window. This is the accounting
  invariant made physical: you don't edit a closed period, you post a reversing entry into an
  open one (PLAN.md principle #2).

The UI deliberately splits **preview** (a read-only GET summary of income/expense/net) from
**close** (the irreversible POST), so a bookkeeper sees exactly what the closing entry will do
before committing — and a close can't be casually undone, since reopening requires a deliberate
reversing entry.

Full suite green. Live on `harborside-group`: page HTTP 200; closed 2026-01 → entry_id 1841,
net income $122,965.16 (closed income 211,080.63, closed expense 88,115.47). Hand-checked the
closing split ties — Dr 400 202,230.78 + Dr 410 8,849.85 = 211,080.63 against Cr 500 53,785.37
+ Cr 520 5,400.00 + Cr 555 25,846.16 + Cr 556 3,083.94 + Cr 300 122,965.16 = 211,080.63,
balanced — and the period now reads `status: closed` via the API. Details in
`docs/CHANGE_LOG.md` CR-008.

### 2026-07-12 — Tax engine: rate tables, taxed documents, liability report, 1099 (CR-007)

Third of the operator's 1–5 workflow. LISZA could invoice and bill, but every document was
tax-free — the tax split was hand-keyed, there was no rate table, no way to see what you owed
the tax authority, and no 1099 support. `scripts/tax.py` (TDD, 16 tests, RED→GREEN) closes
that.

The spine is `compute_tax(amount, rate_pct, inclusive=)` → `{net, tax, gross}` — exclusive
adds tax on top of a net amount, inclusive carves it back out of a gross total. Each leg is
rounded so the resulting journal always ties. On top of that:

- **Rate table** (`tax_rates`, per book): a `set_rate`/`get_rates` pair keyed by code, with a
  jurisdiction and a `sales|vat` kind, so "CA 7.25%" or "UK VAT 20%" is stored once and
  referenced by code.
- **Taxed documents stored at gross.** The load-bearing choice: a taxed invoice posts its own
  three-way split `Dr 110 A/R gross / Cr <revenue> net / Cr 230 sales-tax-payable` and books
  the invoice at **gross** — so payments, dunning, and credit-notes all see the true amount
  the customer owes, tax included. The mirror bill posts `Dr <expense> net / Dr 230 input tax
  / Cr 200 A/P gross`, where input tax *reduces* the liability you'll remit.
- **Liability report from its own ledger.** Every taxed line also writes a `tax_transactions`
  row tagged `output` (you collected) or `input` (you paid). `tax_liability(start, end)` then
  reads tax straight from that table — output − input = `net_payable`, with a by-rate
  breakdown — instead of re-deriving tax by reverse-engineering GL splits.
- **1099.** `set_vendor_1099` flags a contractor; `form_1099_report(year)` totals that
  vendor's disbursements from the `payments` table and marks it reportable at the ≥ $600
  federal threshold. Unflagged vendors never appear.

Rate resolution accepts either a stored `rate_code` or an ad-hoc `rate_pct`, and the
revenue/expense offset account is type-checked so you can't credit a sale into an asset
account. No COA change — it reuses the sales-tax-payable account 230 that new books already
seed. Wired `tax_post` (POST: invoice/bill/rate/flag_1099) + `tax` (GET: rates/liability/1099)
on `/api/lisza`, and a **Tax** section in `/lisza/workspace` with Rates / Taxed Invoice /
Liability / 1099 sub-tabs (the invoice form offers a rate-code dropdown *or* a manual %,
mirroring the backend's either/or).

Full suite **307 passed**. Live on `jb-design`: page HTTP 200; set rate CA 7.25%, posted a
$1,000 @CA taxed invoice (invoice #159, journal #486 — net 1000 / tax 72.5 / gross 1072.5),
and the liability report returned output_tax 72.5 / net_payable 72.5. Details in
`docs/CHANGE_LOG.md` CR-007.

### 2026-07-12 — Dunning: turning the overdue list into an escalation ladder with real late fees (CR-006)

Second of the operator's 1–5 workflow. The AR reminder planner (`ar_ap_workflows.py`) could
already *see* overdue invoices, but it stopped there — no tiered escalation, and the only way to
charge a late fee was a hand-keyed journal. `scripts/dunning.py` (TDD, 15 tests, RED→GREEN)
closes that.

Two layers kept deliberately apart, matching the "prepare review actions only" contract of the
existing planner:

- **`dunning_ladder()` — read-only.** For each overdue open invoice it reports days-overdue, the
  escalation *stage* reached, the fee that stage would charge, and whether that stage was already
  assessed. Nothing posts. A nightly job could surface escalations without ever touching a book.
- **`assess_late_fee()` — the one ledger-affecting call.** Charges a single stage's fee to one
  invoice: posts `Dr 110 A/R / Cr <late-fee income>` and records it in `dunning_fees`. A late fee
  is a **new charge**, never an edit to the invoice (principle #2) — the customer genuinely owes
  more, so A/R legitimately rises and finance-charge income is booked.

The load-bearing bit of design is **idempotency**: a dunning ladder re-runs every day, so
`UNIQUE(invoice_id, stage)` guarantees a stage bills exactly once no matter how many times the
job fires. The default ladder is reminder@1d (courtesy, no fee) → first_notice@15d 1.5% →
second_notice@30d 1.5% → final_demand@60d flat $25, and it's per-book configurable via
`dunning_policy`. Balance math reuses `payments._allocated_so_far`, so an invoice already relieved
by payments or credit notes shows its true remaining balance in the ladder.

Fee income wants its own P&L line, so I added COA **445 Late Fee & Finance Charge Income** to
`coa.csv` (new books get it; the assess call falls back to 490 Other Income for existing books and
type-checks whatever account it lands on). Wired `dunning_assess` (POST) + `dunning` (GET ladder)
on `/api/lisza`, and a **Dunning** section in `/lisza/workspace` — an overdue table with a
one-click "Assess fee" per row that flips to "assessed" once charged.

Full suite **291 passed**. Live: page HTTP 200; `GET ?mode=dunning` returned jb-design's overdue
ladder; end-to-end `POST ?mode=dunning_assess` charged a $25 final-demand fee to invoice #7
(journal #485, booked to 490), and the idempotent re-POST was correctly rejected. Details in
`docs/CHANGE_LOG.md` CR-006.

### 2026-07-12 — Credit notes / vendor credits: closing the reverse side of the money loop (CR-005)

First of the operator's 1–5 feature workflow ("1 – 5 as a workflow, then finish bucket A + B
before bucket C"). Payments (CR-earlier) closed the *forward* money loop — cash in relieves an
invoice, cash out relieves a bill. Credit notes close the *reverse* side: a customer over-charge
or goodwill discount, or a returned purchase, now has a first-class document that posts a
reversing journal instead of a hand-keyed ad-hoc entry.

Built `scripts/credit_notes.py` TDD (12 tests, RED→GREEN). It is deliberately the mirror image
of `payments.py`: same `_book`/allocation/relieve-to-paid shape, but where payments post cash,
credit notes post the reversal —

- **customer** credit memo → `Dr <revenue> / Cr 110 A/R` (reverses the sale, drops the receivable)
- **vendor** credit / debit note → `Dr 200 A/P / Cr <expense>` (reverses the cost, drops the payable)

The offset account is type-checked against the kind (customer must use an income account, vendor
an expense account) so you can't mis-post the reversal. Unapplied credit sits on-account exactly
like an over-payment (full amount always hits the control account).

The one genuinely new bit of design: an invoice's balance now has **two** sub-ledgers relieving
it — payments *and* credits. So both modules' balance math sums both allocation tables, each
behind a `sqlite_master` table-exists guard. That keeps `payments.py` backward-safe (a book that
never issued a credit skips the credit table and behaves exactly as before — all 12 payments
tests still green) while making `credit + payment settles one invoice` work (proven by a
cross-module test).

Wired end-to-end: `credit_apply` (POST) + `credit_notes` (GET) on `/api/lisza`, and a **Credit
Notes** section in `/lisza/workspace` (kind toggle, open-item apply table, revenue/expense offset
picker, recent-credits history) mirroring the Payments lane. Full suite **276 passed**; page HTTP
200; live POST posted credit note #1 (journal #484) on the `jb-design` demo book and the GET read
it straight back. Details in `docs/CHANGE_LOG.md` CR-005.

### 2026-07-11 — First real automation: recurring / subscription invoicing (CR-004)

LISZA's first true automation — the operator asked to "let bookkeepers do recurring invoices
manually while also offering to automate them." Until now every subscription/retainer invoice
was re-keyed by hand each period. Shipped `scripts/recurring_invoicing.py` (TDD, 27 tests),
built around three operator modes that map straight to the brief:

- **Manual** — no template; the existing hand-entry path is untouched.
- **Review** — a template drafts a proposal each period; **nothing posts until a human
  approves**. This is the default and the safe one.
- **Auto** — `auto_approve=1` lets a template post itself on schedule.

Two per-book tables: `recurring_invoice_templates` (party, amount, weekly|monthly|quarterly|
annual, revenue code, anchor day, net terms, auto-approve) and `recurring_invoice_runs`, keyed
`UNIQUE(template_id, period_key)` so a period can never be billed twice. `generate_due()` posts
a balanced A/R journal (`Dr 110 / Cr <revenue>`) only when approved (or auto), reserves a
formatted doc number via `number_series`, records the run, and advances `next_run_date`.
Month-end anchoring clamps (anchor_day 31 → Feb 28/29). `preview_due()` is read-only.

The advisory planner gained a `recurring_invoice_due` job (`automation_profile.plan_due_jobs`)
— it surfaces "N recurring invoices due" but **never mutates a book**; posting stays behind the
console's approval gate. Wired `recurring` / `recurring_add` / `recurring_generate` modes on
`/api/lisza` and an **Invoices | Recurring** sub-tab in the Sales section of `/lisza/workspace`
(template form, due-now preview with Review/Auto badges, Review-drafts and Generate-&-post
controls).

**WAL gotcha (cost me 5 hung tests):** `generate_due()` opens the book connection for its
invoice INSERT, but `post_json` and `number_series.reserve_next` each open their own connection
to the same SQLite file. If the book's write txn is already open, those calls deadlock on
`database is locked`. Fix (mirrors `payments.py`): do all separate-connection writes **first**,
then open the book's write txn. Ordering is load-bearing, commented in the code.

Full suite **263 passed**. `/lisza/workspace` serves 200; `GET /api/lisza?mode=recurring`
returns clean JSON. Additive and reversible — original API route saved at `/tmp/lisza_route.ts`.

### 2026-07-11 — v2 "money loop" release: payments, cash flow, and XLSX import (CR-001/002/003)

Closed the gap that made LISZA a viewer rather than a bookkeeping system: **every pipeline
stopped at "open invoice" / "unpaid bill" — nothing recorded and applied a payment**, so a
book could never complete a cycle. A real test client is coming on as a proof of concept, so
v2 needed to be something a bookkeeper can actually run end to end. Three changes shipped,
TDD throughout, full suite **234 passed**.

- **CR-001 — Payment application & reconciliation (`scripts/payments.py`).** Receipts
  (customer pays you) and disbursements (you pay a vendor), each allocatable across many
  invoices/bills in one payment; partial payments, on-account/unapplied balances, and
  over-allocation guards. The load-bearing design choice: payment and posting are **one atomic
  step** — every payment posts a balanced cash journal (receipt `Dr cash / Cr 110 AR`;
  disbursement `Dr 200 AP / Cr cash`) *and* relieves the sub-ledger row to `paid` together, so
  AR/AP and the GL can never drift apart. This is the first console path that posts GL entries;
  the DB balance trigger makes a non-balancing journal impossible to post, and corrections are
  new entries, never edits-in-place. Wired `/api/lisza` modes `payment_apply`/`payments`/`open_items`
  + a v2 **Payments tab**. AR/AP aging (current / 1–30 / 31–60 / 61–90 / 90+) validated live alongside.

- **CR-002 — Cash flow statement (direct method).** `statement_suite.cash_flow()` completes the
  statement suite (P&L / Balance Sheet / Trial Balance were already live). Cash-account-centric:
  each entry's cash delta is bucketed into operating / investing / financing by the **type of its
  non-cash counterpart**, split proportionally by counterpart magnitude; cash-to-cash transfers net
  to zero. `opening + net_change − closing == 0` holds by construction. Surfaced via
  `/api/lisza/report?type=cash_flow` and a "Cash Flow" pill in the Reports tab — verified live for
  all four client books, every one reconciled to 0.

- **CR-003 — XLSX bank/card import.** Clients export `.xlsx` as often as `.csv`. Rather than a
  parallel importer, `ingest_txns.xlsx_to_csv_text()` transcodes a workbook to CSV text and feeds
  the **same** parse → dedup → categorize → review pipeline — so a whole class of "works for CSV,
  subtly broken for XLSX" bugs can't exist. Imports park in `pending_inbox` as `status='new'`;
  nothing reaches the posted P&L until approved. CLI `import-xlsx` (file or base64), `/api/lisza`
  `import_xlsx` POST mode, and an `.xlsx` picker in the Import panel. `requirements.txt` created with
  the validated pins (duckdb, PyMuPDF, gspread, openpyxl, pandas, pytest).

**⚠ Operational heads-up (recurring gotcha):** zo.space routes spawn bare `python3` = **3.12**
(`/usr/local/bin/python3`), while the test suite runs under 3.11. `openpyxl` had to be installed for
**both** for the XLSX route to work in production. Any future route-spawned dependency needs the same
double-install, or the route 500s while the tests stay green.

**Remaining backlog** stays committed and phased in `TODO.md`: bucket A (online payment collection
Stripe/PayPal, recurring invoicing, credit/debit notes, dunning, client portal, quote e-sign),
bucket B (tax engine, cost centers, period close, deferred rev, FX, live bank feeds), bucket C
(ERP breadth — CRM, consolidation, inventory, manufacturing, projects, POS, e-commerce; new modules
confirm-first before file creation), bucket D (RBAC+2FA, e-invoicing, branded PDF, REST API/webhooks).

Verify: `cd scripts && python3 -m pytest -q` → 234 passed. Live-API checks recorded per-CR in
`docs/CHANGE_LOG.md`.
