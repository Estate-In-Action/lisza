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
