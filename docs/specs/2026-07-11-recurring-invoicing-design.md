# Spec — Recurring / Subscription Invoicing

**Date:** 2026-07-11
**Status:** Approved (operator greenlit as "the first real automation")
**Predecessors:** Sales pipeline (quote→order→invoice), `payments.py` (money loop),
`automation_profile.py` (advisory due-job planner), `number_series.py` (document numbering)
**Successors:** Dunning/late-fee escalation; online payment collection

## Purpose

Give LISZA schedule-driven auto-generation of invoices for the predictable,
repeating client need — the same customer, the same amount, on the same day each
period (rent, retainers, subscriptions, monthly service fees). This is the
**first real automation** in LISZA: it closes the loop between "a bookkeeper does
this by hand every month" and "the system proposes it and a human approves — or
lets it run hands-off."

It deliberately mirrors LISZA's existing posture: the planner is **advisory**, and
nothing hits a client's book without either explicit per-run approval or a
per-template `auto_approve` opt-in. This is the same discipline as the
`pending_inbox` import gate and `automation_profile`'s `approval_required: True`.

## The manual-or-automate contract

The operator's framing — *"allow bookkeepers to do them manually while also
offering to automate them"* — maps to three modes per template:

| Mode | Behavior |
|------|----------|
| **Manual** | Template stored; planner surfaces "due now", bookkeeper clicks **Generate** each period. |
| **Review** (default) | Planner proposes the invoice with a filled draft; bookkeeper approves/edits/skips before it posts. |
| **Auto** | `auto_approve=1`; the generator creates + posts the invoice on the due date with no human step. Hands-off clients. |

A template can be flipped between modes at any time. Auto is opt-in per template —
never a global default.

## Data model (per book, in `ledger.db`)

```sql
CREATE TABLE IF NOT EXISTS recurring_invoice_templates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    party         TEXT NOT NULL,
    amount        REAL NOT NULL,
    memo          TEXT,
    revenue_code  TEXT NOT NULL REFERENCES accounts(code),   -- Cr revenue on post
    net_terms     INTEGER NOT NULL DEFAULT 30,               -- due_date = issue + net_terms
    frequency     TEXT NOT NULL CHECK(frequency IN
                    ('weekly','monthly','quarterly','annual')),
    anchor_day    INTEGER,                                   -- day-of-month (1..31 or -1=EOM)
    start_date    TEXT NOT NULL,
    end_date      TEXT,                                      -- NULL = open-ended
    next_run_date TEXT NOT NULL,
    auto_approve  INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1,
    series_key    TEXT NOT NULL DEFAULT 'invoice',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Audit + idempotency: one row per generated invoice.
CREATE TABLE IF NOT EXISTS recurring_invoice_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id   INTEGER NOT NULL REFERENCES recurring_invoice_templates(id),
    period_key    TEXT NOT NULL,        -- e.g. '2026-07' — dedup guard
    invoice_id    INTEGER REFERENCES invoices(id),
    document_number TEXT,               -- from number_series.reserve_next
    generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    approved_by   TEXT,                 -- 'auto' or a bookkeeper handle
    UNIQUE(template_id, period_key)     -- hard idempotency: never double-bill a period
);
```

The `UNIQUE(template_id, period_key)` constraint is the load-bearing guarantee:
re-running the generator for an already-billed period is a no-op, so the DAG/agent
firing twice, or a manual + scheduled overlap, can never double-invoice a client.

## Schedule math

A pure, testable function with no I/O:

```python
def next_period(frequency, anchor_day, current) -> date
def period_key(frequency, issue_date) -> str   # '2026-07' | '2026-Q3' | '2026-W28' | '2026'
```

Month-end anchoring rule: `anchor_day == -1` (or an anchor greater than the
month's length) resolves to the last calendar day of the target month — so a
"bill on the 31st" template lands on Feb 28/29 correctly. Reuse
`_last_day_of_month` from `automation_profile.py`.

## Generation flow

`generate_due(slug, as_of, *, approve=False, template_id=None)`:

1. Select active templates where `next_run_date <= as_of` (optionally one).
2. For each, compute `period_key`; if a `recurring_invoice_runs` row exists for
   `(template_id, period_key)` → **skip** (idempotent).
3. Build the draft: `issue_date = next_run_date`, `due_date = issue + net_terms`,
   `party/amount/memo` from the template.
4. **Decide to post**:
   - `approve=True` **or** `template.auto_approve==1` → reserve a document number
     via `number_series.reserve_next(slug, series_key, document_type='invoice')`,
     `INSERT INTO invoices(...)` (status `open`), post the balanced AR entry
     (Dr `110` AR / Cr `revenue_code`) through the same `post_entry` path a manual
     invoice uses, write the `recurring_invoice_runs` row, and advance
     `next_run_date = next_period(...)`.
   - otherwise → return the draft as a **proposal** (no writes), so the UI can show
     "Generate / Edit / Skip".
5. Return a per-template result: `{template_id, period_key, action:
   generated|proposed|skipped, invoice_id?, document_number?}`.

The advisory planner (`automation_profile` / `ar_ap_workflows`) is extended to emit
a `recurring_invoice_due` job (status `due_now`/`upcoming`, `approval_required:
True`) so due templates surface in the same due-job feed as filings and AR
reminders — but the planner itself still never writes.

## CLI contract (`scripts/recurring_invoicing.py`)

Mirrors `payments.py`'s dual human/JSON surface:

```
recurring_invoicing.py list <slug>
recurring_invoicing.py add <slug> --party ... --amount ... --frequency monthly \
    --anchor-day 1 --net-terms 30 --revenue-code 400 [--auto] [--start 2026-08-01]
recurring_invoicing.py preview <slug> [--as-of DATE]      # what would generate
recurring_invoicing.py generate <slug> [--as-of DATE] [--approve] [--template ID]
recurring_invoicing.py generate-json <slug> ...           # machine contract for the API
```

## API + UI

- **API** (`/api/lisza`): read mode `recurring` (list templates + due preview);
  POST modes `recurring_add`, `recurring_generate` (shells the CLI, same pattern as
  `payment_apply`).
- **UI** (`/lisza/workspace` → Sales): a **Recurring** tab listing templates with
  their mode badge, plus due items wired into the existing **manual/automate
  drawer** — "3 recurring invoices due · Generate all / Review each / Skip."

## Scheduling the automation

LISZA has no HOPE-style minute-DAG. The scheduled trigger is a **Zo automation**
(one per-day fire) that calls `recurring_invoicing.py generate <slug>` for each
active book. Review-mode templates surface as proposals in the due-job feed (no
auto-post); only `auto_approve` templates post unattended. This keeps the
scheduler dumb and the approval gate in the app, where the bookkeeper lives.

## Tests (`scripts/test_recurring_invoicing.py`)

- Schedule math: monthly/quarterly/weekly/annual advance; month-end anchoring
  (31st → Feb 28/29); `period_key` formatting per frequency.
- Idempotency: second `generate_due` for the same period is a no-op (UNIQUE guard).
- Posting: approved generation inserts an `open` invoice + a **balanced** Dr110/Cr-rev
  entry; `next_run_date` advances exactly one period.
- Mode gating: Review mode with `approve=False` returns a proposal and writes
  nothing; Auto mode posts without approval.
- Isolation: generating for client A never touches client B's book.
- End date: template past `end_date` is inert.

## Guardrails

- Advisory planner never mutates a book (unchanged invariant).
- No unattended post without a per-template `auto_approve` opt-in.
- `UNIQUE(template_id, period_key)` makes double-billing structurally impossible.
- Synthetic/demo books only for seeding; no real client PII.
