# LISZA Change Log — CR Registry

Canonical index of every Change Request against the LISZA bookkeeping platform.
Narrative detail lives in `../OPS_LOG.md`; this file is the one-row-per-CR registry.

**Posture (risk-tiered by ledger blast radius).** LISZA keeps real client books, so
governance is tiered by whether a change can touch **posted ledger data or a client's
money**, not by feature size:

- **Ledger-affecting** changes (anything that posts, edits, reverses, or reconciles
  GL entries, sub-ledgers, or payments; schema migrations on a client DB; the balance
  trigger) require a design note first, a stated rollback, and green tests **before**
  they run against any real client book. Corrections are always new entries, never
  edits-in-place (PLAN.md principle #2).
- **Read-only / additive** changes (new reports, importers that only park rows in
  `pending_inbox`, UI, tooling, docs) are logged as a CR with a rollback but ship on the
  standing add/edit authority — nothing they do reaches a posted P&L without human review.
- **Data-handling hard rule** (carried from project origin): no real personal data ever
  reaches the public GitHub repo. Client books live under `clients/<slug>/ledger.db`,
  never committed; only synthetic/demo data is shippable.

### CR template
```markdown
### CR-NNN — <title>
- **Date:** <YYYY-MM-DD>
- **Tier:** ledger-affecting | read-only/additive
- **What / Why:** <change> / <problem solved>
- **Risk:** <blast radius; low/med/high, and whether it touches posted data>
- **Rollback:** <exact revert step>
- **Verify:** <how confirmed — tests, live check>
- **Status:** proposed | executed | rolled-back
```

---

### CR-009 — Cost centers / accounting dimensions: tag ledger lines, report P&L per dimension — read-only/additive
- **Date:** 2026-07-12
- **Tier:** read-only/additive — adds two new tables and an overlay tag; **posts no GL
  entries and changes no posting path**. Tagging annotates existing posted lines; reports
  are pure aggregation over posted splits.
- **What / Why:** LISZA could produce a book-level P&L but had no way to slice it by
  cost center, project, department, or class — a bookkeeper couldn't answer "what did the
  West region / the Apollo project actually earn." Added `scripts/cost_centers.py`. Design
  follows the house convention (budgeting/period_close): **additive tables, the core
  `splits`/`entries` are never touched.** A `cost_centers` registry (`code` UNIQUE, `name`,
  `kind` ∈ cost_center|project|department|class, `active`) holds the taggable values; a
  `split_dimensions` sidecar (`split_id` PK → `cost_center`) maps posted lines to a
  dimension. Tagging is **post-hoc** — `tag_split`/`tag_entry` annotate after posting — so no
  poster (payments, tax, credit notes, closing entries) changes and untagged lines simply
  fall into an `(unassigned)` bucket. `set_cost_center` upserts the registry;
  `deactivate_cost_center` is a **soft-delete** (row + historical tags preserved,
  add-don't-subtract). `dimension_report(start, end)` groups posted income (cr−dr) and
  expense (dr−cr) per cost center + an unassigned bucket; `cost_center_pnl(code, …)` gives a
  single-center per-account breakdown. Wired `cost_center_post` (POST: add/deactivate/tag/
  untag/tag_entry) + `cost_center` (GET: list/report/pnl) modes on `/api/lisza`, and a
  **Cost Centers** section (registry form + active-dimensions list + tag-a-journal-entry +
  dimension P&L report) in `/lisza/workspace`.
- **Risk:** low — no posted data is written or mutated; the sidecar only references existing
  split ids and validates the center is active before tagging. Reports read `status='posted'`
  splits only. A re-tag is an idempotent upsert (moves the line, no duplicate); a deactivated
  center's past tags remain and still report until re-tagged/untagged. Worst case is a
  mis-tagged line, corrected by re-tagging — never a ledger imbalance.
- **Rollback:** remove `scripts/cost_centers.py` + `test_cost_centers.py`; drop the two
  `cost_center*` route modes and the Cost Centers NAV tuple + dispatch branch +
  `CostCenterSection` in `/lisza/workspace`. The `cost_centers` / `split_dimensions` tables
  are inert once the readers are gone; they can be left in place or dropped per book.
- **Verify:** `cd scripts && python3 -m pytest test_cost_centers.py -q` → 17 passed (registry
  set/list/kind-filter, soft-delete keeps row, tag assign, unknown-split/unknown-center/
  inactive-center guards, idempotent reassign, untag, tag_entry all-legs, report income+
  expense grouping, unassigned bucket, period bounds, posted-only, single-center pnl, CLI).
  Full suite **338 passed**. Live on `jb-design` via the preview API: added cost center WEST;
  `report` showed unassigned expense 28,112.80 / centers []; `POST tag_entry` on journal #481
  tagged 2 lines; `report` then showed WEST expense 3,287.66 and unassigned expense 24,825.14
  (28,112.80 − 3,287.66 — total expense conserved, just reallocated). `/lisza/workspace`
  served HTTP 200 after the UI edit (TSX compiled).
- **Status:** executed

### CR-008 — Period close: roll income/expense into retained earnings and lock the fiscal period — ledger-affecting
- **Date:** 2026-07-12
- **Tier:** ledger-affecting (posts a balanced closing journal that zeroes every P&L account
  into retained earnings 300, then locks the window against further posting)
- **What / Why:** LISZA could post, invoice, bill, tax, dun, and credit-note, but a book never
  *closed* — income and expense accumulated forever with no period boundary, no retained-earnings
  roll, and nothing stopping a backdated posting into a "finished" month. Added
  `scripts/period_close.py`. Core: `close_period(client, start, end, memo=)` sums posted splits
  per account over the window, computes net income (income − expense), posts ONE balanced closing
  entry that debits each income account and credits each expense account by its period balance and
  books the net to retained earnings 300 (`Cr 300` on a profit, `Dr 300` on a loss), then writes an
  `accounting_periods` row with `status='closed'`. A period lock guard was added to `post_json`: any new entry
  dated inside a closed window is rejected (corrections must be a new reversing entry in an open
  period — PLAN.md principle #2, never edits-in-place). `close-json` CLI subcommand +
  `period_summary` (preview income/expense/net before committing) + `list_periods`. Wired
  `period_close` (POST) and `period` (GET: `action=summary|periods`) modes on `/api/lisza`, and a
  **Close** section (NAV + dispatch + `PeriodSection`, preview-then-close UX) in `/lisza/workspace`.
- **Risk:** medium — posts a real multi-way GL entry and then *locks* posting for the window, so
  it changes what future postings are allowed. Blast radius bounded: the DB balance trigger makes a
  non-balancing close impossible; the close reads only `status='posted'` splits; re-closing an
  already-closed period is rejected; the lock only blocks NEW entries dated in-window, never mutates
  existing ones. A close is not silently reversible — reopening requires a deliberate reversing
  entry, matching the principle that corrections are always new entries.
- **Rollback:** remove `scripts/period_close.py` + `test_period_close.py`; drop the `period_close`
  + `period` route modes and the Close NAV tuple + dispatch branch + `PeriodSection` in
  `/lisza/workspace`; remove the period-lock guard block from `post_json`. Already-posted closing
  entries remain valid ledger entries; to undo a specific close, post a reversing entry against
  journal (the closing entry_id) in a reopened period.
- **Verify:** `cd scripts && python3 -m pytest test_period_close.py -q` → 14 passed (income/expense
  sum, net-income roll on profit + on loss, balanced closing split, retained-earnings side flips
  with sign, closed-period status write, re-close rejection, post-into-closed-period rejection,
  post-into-open-period allowed, preview matches committed totals, out-of-window exclusion,
  posted-only filter, list_periods, CLI). Full suite green. Live: `/lisza/workspace` HTTP 200;
  `POST ?mode=period_close` closed `harborside-group` 2026-01 → entry_id 1841, net_income
  122,965.16, closed_income 211,080.63, closed_expense 88,115.47; verified the closing split ties
  (Dr 400 202,230.78 + Dr 410 8,849.85 = 211,080.63; Cr 500 53,785.37 + Cr 520 5,400.00 + Cr 555
  25,846.16 + Cr 556 3,083.94 + Cr 300 122,965.16 = 211,080.63 — balanced); `GET ?mode=period
  &action=periods` shows the window `status: closed`.
- **Status:** executed

### CR-007 — Tax engine: sales-tax / VAT rate tables, taxed documents, liability report, 1099 — ledger-affecting
- **Date:** 2026-07-12
- **Tier:** ledger-affecting (a taxed invoice/bill posts a balanced multi-way GL split that
  moves the sales-tax-payable control account 230)
- **What / Why:** LISZA could invoice and bill, but every document was tax-free — a
  bookkeeper had to hand-key the tax split, and there was no rate table, no output-vs-input
  liability view, and no 1099 support. Added `scripts/tax.py`. Core: `compute_tax(amount,
  rate_pct, inclusive=)` → `{net, tax, gross}` (exclusive adds tax on top; inclusive carves
  it out of the total). A per-book `tax_rates` table (`set_rate`/`get_rates`, code +
  jurisdiction + `sales|vat` kind). **Taxed documents post their own balanced split and are
  stored at gross** so downstream payments / dunning / credit-notes see the true receivable:
  `record_tax_invoice` posts `Dr 110 A/R gross / Cr <revenue> net / Cr 230 tax` and creates
  the invoice at gross; `record_tax_bill` posts `Dr <expense> net / Dr 230 input tax / Cr 200
  A/P gross`. Every taxed line also writes a `tax_transactions` row (kind `output|input`) so
  the liability report reads tax straight from a ledger rather than re-deriving it from
  splits. `tax_liability(start, end)` nets output − input into `net_payable` with a by-rate
  breakdown. 1099: `set_vendor_1099` flags a vendor, `form_1099_report(year)` totals that
  vendor's disbursements from the `payments` table and marks it reportable at ≥ $600. Rate
  resolution accepts either a stored `rate_code` or an ad-hoc `rate_pct`; revenue/expense
  offset accounts are type-checked. Wired `tax_post` (POST dispatcher: invoice/bill/rate/
  flag_1099) + `tax` (GET: rates/liability/1099) modes on `/api/lisza`, and a **Tax** section
  (NAV + dispatch + `TaxSection` with Rates / Taxed Invoice / Liability / 1099 sub-tabs) in
  `/lisza/workspace`.
- **Risk:** low-to-medium — posts real GL entries, but the DB balance trigger makes a
  non-balancing journal impossible, `compute_tax` rounds each leg so the split always ties,
  a $0-tax line is rejected (routes back to the untaxed path), and non-income/expense offset
  accounts are rejected by the type check. Reads are pure aggregation over `tax_transactions`
  / `payments`. Uses the existing sales-tax-payable account 230 (already seeded) — no COA
  change.
- **Rollback:** remove `scripts/tax.py` + `test_tax.py`; drop the two `tax*` route modes and
  the Tax NAV tuple + dispatch branch + `TaxSection`/`TaxRates`/`TaxInvoice`/`TaxLiability`/
  `Tax1099` components in `/lisza/workspace`. No existing posting path changes; already-posted
  taxed documents remain valid ledger entries (corrections are new entries, never
  edits-in-place).
- **Verify:** `cd scripts && python3 -m pytest test_tax.py -q` → 16 passed (exclusive +
  inclusive compute, rate roundtrip, 3-way output split, stored-at-gross receivable,
  inclusive carve-out, rate-code resolution, unknown/missing-rate guards, non-revenue-account
  guard, input-tax bill split, liability output − input net, out-of-period exclusion, 1099
  threshold + flag, unflagged-vendor exclusion, CLI); full suite **307 passed**. Live:
  `/lisza/workspace` HTTP 200; `POST ?mode=tax_post` set rate CA 7.25% and posted a $1,000 @CA
  taxed invoice on `jb-design` (invoice #159, journal #486, net 1000 / tax 72.5 / gross
  1072.5); `GET ?mode=tax&action=liability` returned output_tax 72.5 / net_payable 72.5;
  `GET action=rates` and `action=1099` returned clean JSON.
- **Status:** executed

### CR-006 — Dunning / late-fee escalation ladder — ledger-affecting
- **Date:** 2026-07-12
- **Tier:** ledger-affecting (a late fee posts a balanced GL entry that increases A/R)
- **What / Why:** the AR reminder planner (`ar_ap_workflows.py`) only *surfaced* overdue
  invoices — there was no escalation ladder and no way to charge a late fee except a
  hand-keyed journal. Added `scripts/dunning.py`: a per-book, configurable escalation
  policy (`dunning_policy`, defaulting to reminder@1d / first_notice@15d 1.5% /
  second_notice@30d 1.5% / final_demand@60d flat $25) and a `dunning_fees` sub-ledger.
  Two layers, deliberately split: **`dunning_ladder()` is read-only** (mirrors the
  prep-only AR planner) — for each overdue open invoice it reports days-overdue, the stage
  reached, the fee that stage would charge, and whether that stage was already assessed;
  **`assess_late_fee()` is the only ledger-affecting call** — it posts `Dr 110 A/R / Cr
  <late-fee income>` and records the charge. A late fee is a *new charge*, never an edit to
  the invoice (PLAN.md principle #2). `UNIQUE(invoice_id, stage)` makes assessment
  idempotent so a daily re-run can never double-bill a stage. Fee income defaults to new COA
  **445 Late Fee & Finance Charge Income** when present, else falls back to 490 Other Income;
  the income account is type-checked. Balance math reuses `payments._allocated_so_far`, so an
  invoice already netted by payments/credits shows its true remaining balance. Added COA row
  445 to `coa.csv` (new books only). Wired `dunning_assess` (POST) + `dunning` (GET ladder)
  modes on `/api/lisza` and a **Dunning** section (NAV + dispatch + `DunningSection`, an
  overdue table with one-click per-row assess) in `/lisza/workspace`.
- **Risk:** low-to-medium — posts real GL entries, but the DB balance trigger makes a
  non-balancing journal impossible, the idempotency guard prevents double-billing, and
  non-income offset accounts are rejected. Read-only ladder never mutates a book. The COA
  addition affects new books only; existing demo books fall back to 490 (verified live —
  entry booked to 490 on `jb-design`).
- **Rollback:** remove `scripts/dunning.py` + `test_dunning.py`; revert the 445 row in
  `coa.csv`; drop the two `dunning*` route modes and the Dunning NAV tuple + dispatch branch
  + `DunningSection` in `/lisza/workspace`. No existing posting path changes; already-assessed
  late fees remain valid ledger entries (corrections are new entries, never edits-in-place).
- **Verify:** `cd scripts && python3 -m pytest test_dunning.py -q` → 15 passed (ordered
  policy, ladder stage/suggested-fee, ignores not-yet-due + paid invoices, Dr-A/R/Cr-income
  posting, percent + flat fees, per-stage idempotency, non-income-account guard, not-overdue
  guard, dunning-state totals, list, CLI); full suite **291 passed**. Live: `/lisza/workspace`
  HTTP 200; `GET /api/lisza?mode=dunning` returned the overdue ladder; end-to-end
  `POST ?mode=dunning_assess` charged a $25 final-demand fee to `jb-design` invoice #7 (journal
  #485, booked to 490), and the idempotent re-POST was rejected 400.
- **Status:** executed

### CR-005 — Credit notes / debit notes / vendor credits (reversing credit documents) — ledger-affecting
- **Date:** 2026-07-12
- **Tier:** ledger-affecting (posts a reversing GL entry against the AR/AP control account)
- **What / Why:** the money loop stopped at cash. A customer over-charge, goodwill
  discount, or returned purchase had no first-class document — bookkeepers hand-keyed an
  ad-hoc journal and the invoice/bill sub-ledger never reflected it. Added
  `scripts/credit_notes.py`: per-book `credit_notes` + `credit_note_allocations` tables and
  `record_credit_note()`. Two kinds mirror `payments.py`: **customer** credit memo posts
  `Dr <revenue> / Cr 110 A/R`, **vendor** credit (debit note) posts `Dr 200 A/P / Cr
  <expense>`. Offset account is type-checked (customer needs an income account, vendor an
  expense account). Allocations relieve open invoices/bills and flip them to `paid` once
  fully covered; an unallocated remainder sits as an on-account credit (full amount always
  hits the control account, exactly like an over-payment). Honours PLAN.md principle #2 —
  the original invoice/bill and its entry are never mutated; the credit is a new offsetting
  document. **Cross-module:** an invoice's true balance now nets payments **and** credits, so
  `credit_notes._relieved_so_far` and `payments._allocated_so_far` both sum the two
  allocation tables (each guarded by a table-exists check). Wired `credit_apply` (POST) +
  `credit_notes` (GET) modes on `/api/lisza` and a **Credit Notes** section (NAV + dispatch +
  `CreditNotesSection`) in `/lisza/workspace`, mirroring the Payments lane.
- **Risk:** medium — posts real GL entries, but the DB balance trigger makes a non-balancing
  journal impossible and the allocation guards make over-relieving an invoice/bill impossible.
  The added credit-awareness in `payments._allocated_so_far` is backward-safe: a book with no
  `credit_note_allocations` table skips it and behaves exactly as before (verified — 12
  payments tests still green).
- **Rollback:** remove `scripts/credit_notes.py` + `test_credit_notes.py`; revert the
  `_allocated_so_far` change in `payments.py` (restore the single-table query); drop the two
  `credit*` route modes and the Credit Notes NAV tuple + dispatch branch + `CreditNotesSection`
  in `/lisza/workspace`. No existing posting path changes; already-issued credit notes remain
  valid ledger entries (corrections are new entries, never edits-in-place).
- **Verify:** `cd scripts && python3 -m pytest test_credit_notes.py test_payments.py -q` → 24
  passed (reversing-entry direction for both kinds, apply-marks-paid, partial balance,
  credit+payment cross-settle, offset-type guard, over-allocation guards, CLI); full suite
  **276 passed**. Live: `/lisza/workspace` HTTP 200 after the edit; end-to-end
  `POST /api/lisza?mode=credit_apply` posted credit note #1 (journal #484) on the `jb-design`
  demo book and `GET ?mode=credit_notes` read it back.
- **Status:** executed

### CR-004 — Recurring / subscription invoicing (templates → approval-gated generation) — ledger-affecting
- **Date:** 2026-07-11
- **Tier:** ledger-affecting (generation posts a balanced A/R journal — `Dr 110 / Cr <revenue>` — when approved)
- **What / Why:** LISZA had no way to bill the same customer on a schedule; bookkeepers
  re-keyed every subscription/retainer invoice by hand each period. Added
  `scripts/recurring_invoicing.py`: per-book `recurring_invoice_templates` (party, amount,
  frequency weekly|monthly|quarterly|annual, revenue code, anchor day, net terms,
  `auto_approve`) plus a `recurring_invoice_runs` ledger keyed `UNIQUE(template_id,
  period_key)` for idempotency. Three operator modes per the brief ("let bookkeepers do them
  manually while also offering to automate"): **Manual** (no template), **Review** (template
  drafts a proposal, nothing posts until a human approves), **Auto** (`auto_approve=1` posts
  on schedule). `generate_due()` posts only when `approve` or the template auto-approves,
  skips any period already in the runs table (no double-billing), reserves a formatted doc
  number via `number_series`, and advances `next_run_date`. Month-end anchoring is clamped
  (`anchor_day=31` → Feb 28/29). Also wired an advisory `recurring_invoice_due` job into
  `automation_profile.plan_due_jobs` (planner only — never mutates books), `recurring`/`recurring_add`/`recurring_generate`
  modes on `/api/lisza`, and an **Invoices | Recurring** sub-tab in the Sales section of
  `/lisza/workspace` (template form, due-now preview, Review-drafts / Generate-&-post controls).
- **Risk:** medium — posts real GL entries, but only through the approval gate; the DB balance
  trigger makes a non-balancing journal impossible, and the per-period unique key makes a
  double-post impossible. The planner + preview + UI-list surfaces are pure reads.
- **Rollback:** remove `scripts/recurring_invoicing.py` + `test_recurring_invoicing.py`;
  revert the `_recurring_due`/`plan_due_jobs` block in `automation_profile.py` (and its two
  new tests); drop the three `recurring*` route modes and the Sales "Recurring" sub-tab +
  `RecurringPanel`. No existing posting path is touched; already-generated invoices remain
  valid ledger entries (Rule: corrections are new entries, never edits-in-place).
- **Verify:** `cd scripts && python3 -m pytest test_recurring_invoicing.py -q` → 27 passed
  (schedule math, template CRUD, approved-posts-balanced, per-period idempotency,
  review-vs-auto gating, end-date inert, client isolation, preview writes-nothing); full
  suite **263 passed**. Live checks: `/lisza/workspace` HTTP 200 after the edit;
  `GET /api/lisza?mode=recurring` returns clean JSON.
- **Status:** executed
- **⚠ WAL ordering note:** `generate_due()` must complete its separate-connection writes
  (`post_json`, `number_series.reserve_next`) **before** the book connection opens its own
  write txn — reversing the order deadlocks under WAL (the uncommitted invoice INSERT holds
  the write lock the other connections need). Mirrors `payments.py`. This bit during dev (5
  tests hung on `database is locked`); fixed by reordering.

### CR-003 — XLSX bank/card import alongside the existing CSV path — read-only/additive
- **Date:** 2026-07-11
- **Tier:** read-only/additive (parks rows in `pending_inbox`; nothing posts without review)
- **What / Why:** clients export bank/card activity as `.xlsx` as often as `.csv`, but
  intake only accepted CSV. Rather than write a parallel importer, added
  `ingest_txns.xlsx_to_csv_text()` (openpyxl, `read_only=True, data_only=True`) that
  transcodes a workbook to CSV text and feeds it through the **same** parse → dedup →
  categorize → review pipeline, so dedup and categorization behave identically for both
  formats. Wired `ingest_xlsx()`, an `import-xlsx` CLI subcommand (file or base64 payload),
  an `import_xlsx` POST mode on `/api/lisza`, and an `.xlsx` file picker in the Import panel.
- **Risk:** low — additive intake path; imported rows land as `pending_inbox.status='new'`
  and reach the ledger only on approval. No posted-data surface touched.
- **Rollback:** revert `scripts/ingest_txns.py` to pre-CR-003, remove `test_ingest_xlsx.py`,
  drop the `import_xlsx` route mode + UI picker. CSV path is unchanged.
- **Verify:** `cd scripts && python3 -m pytest test_ingest_xlsx.py -q` → 5 passed; full
  suite **234 passed**. Live API POST confirmed (inserted:1; re-POST dedup inserted:0/skipped:1;
  test row cleaned from the jb-design inbox).
- **Status:** executed
- **⚠ Operational note:** zo.space routes spawn bare `python3` = **3.12**
  (`/usr/local/bin/python3`); the test suite runs under 3.11. `openpyxl` was installed for
  **both** so the route works in production. Any future route-spawned dependency needs the
  same double-install or the route 500s while tests stay green. Pins recorded in
  `requirements.txt`.

### CR-002 — Cash flow statement (direct method) — read-only/additive
- **Date:** 2026-07-11
- **Tier:** read-only/additive (report only; no posting)
- **What / Why:** the reporting suite shipped P&L / Balance Sheet / Trial Balance but no
  formal Cash Flow Statement. Added direct-method `statement_suite.cash_flow()`: cash-account
  centric (101/102/103/106), it buckets each entry's cash delta into operating / investing /
  financing by the **type of its non-cash counterpart**, splits proportionally by counterpart
  magnitude, and reconciles `opening + net_change − closing == 0` by construction (cash-to-cash
  transfers net to zero and are excluded). Surfaced via `/api/lisza/report?type=cash_flow` and a
  "Cash Flow" pill in the Reports tab.
- **Risk:** low — pure read over posted `entries`/`splits`; writes nothing.
- **Rollback:** revert `scripts/statement_suite.py`, remove the `cash_flow` report branch and
  the Reports-tab pill.
- **Verify:** 4 unit tests green; live API verified for all four client books — every one
  reports `reconciled: 0`.
- **Status:** executed

### CR-001 — Close the money loop: payment application & reconciliation — ledger-affecting
- **Date:** 2026-07-11
- **Tier:** ledger-affecting (posts balanced cash journals; relieves sub-ledgers)
- **What / Why:** every LISZA pipeline stopped at "open invoice" / "unpaid bill" — nothing
  recorded and applied a payment, so the books could never close a cycle. Added
  `scripts/payments.py`: **receipts** (customer→you) and **disbursements** (you→vendor),
  each allocatable across many invoices/bills in one payment, with partial payments,
  on-account/unapplied balances, and over-allocation guards. Each payment posts a **balanced**
  cash journal — receipt `Dr cash / Cr 110 AR`, disbursement `Dr 200 AP / Cr cash` — **and**
  relieves the sub-ledger row to `paid` in the same step, so AR/AP and cash can never disagree.
  Wired `/api/lisza` modes `payment_apply` / `payments` / `open_items` and a v2 **Payments tab**.
  (AR/AP aging reports validated live alongside this — buckets current / 1–30 / 31–60 / 61–90 / 90+.)
- **Risk:** medium — this is the first path that posts GL entries from the console. Mitigated by:
  balance enforced by the DB trigger (a non-balancing journal cannot post), allocation guarded
  against over-application, and corrections done as new entries (never edits-in-place).
- **Rollback:** revert `scripts/payments.py` and the three `/api/lisza` payment modes + Payments
  tab. Posted payment journals, if any exist against a real book, are reversed with offsetting
  entries (add-don't-subtract), never deleted.
- **Verify:** payment unit + integration tests green within the full suite (**234 passed**);
  each client book still balances after a receipt/disbursement round-trip.
- **Status:** executed

---

*Log opened 2026-07-11. Work predating this entry is captured in `docs/specs/` design
notes and the git history (`git log`); this registry begins at the v2 money-loop release.*
