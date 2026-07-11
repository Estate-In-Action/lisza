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
