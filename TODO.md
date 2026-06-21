# LISZA — To Do

Work items for the bookkeeping app. See `PLAN.md` for phases and principles.

> 📚 **Bookkeeping cookbook picks** (startup-bookkeeper, financial-intelligence, document-parser, tax-receipt-autopilot, ledger-manager, monthly-reconciliation, invoice-extractor, taxhacker, find-receipts, Warm Workshop Ledger style) are tracked once in **`Finance/TODO.md`** — LISZA is a clone of the Finance engine and inherits those capabilities. Don't duplicate them here; build in Finance, port to LISZA.

## Open

- [ ] **Add journal entries** — let the user post a manual balanced journal entry (debit/credit lines), not just receipt-driven intake. Builds on `scripts/post_entry.py`.
- [ ] **Edit transactions** — allow correcting a posted transaction. ⚠️ Reconcile with Principle #2 ("we add, we don't subtract"): an "edit" should post a reversing/adjusting entry rather than mutate the original row, or be scoped to entries still in `pending_inbox` (not yet posted).
- [ ] **Reconcile to statements** — match ledger entries against a bank/card statement, flag unmatched items, mark reconciled. Ties into Phase 5 (bank-statement automation).
