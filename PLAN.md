# LISZA Project Plan

**What:** AI-assisted double-entry bookkeeping for a small business — automate
receipt/invoice/bank-statement intake into a clean, balanced ledger with reports.
**Storage:** local-first SQLite (`ledger.db`) + optional Google Sheets mirror.
**Demo data:** the shipped ledger is fully synthetic (see `scripts/seed_synthetic.py`).

## Principles

1. **Every posted entry must balance** — debits = credits, enforced by a DB trigger.
2. **We add, we don't subtract** — corrections are new entries, never edits-in-place.
3. **Review before posting** — raw intake lands in `pending_inbox`; nothing posts
   to the ledger until reviewed (by a human or an approval step).
4. **Secrets live in the environment**, never in the repo (`LISZA_SHEETS_SA_JSON`).

## Layout

```
LISZA/
├── coa.csv                  # chart of accounts (generic small-business)
├── config.json              # Telegram chat binding (placeholder)
├── ledger.db                # synthetic demo ledger (generated)
├── scripts/
│   ├── init_ledger.py       # schema + load chart of accounts
│   ├── seed_synthetic.py    # generate the synthetic demo book
│   ├── post_entry.py        # manual journal CLI + interactive inbox approval
│   ├── post_pending.py      # non-interactive pending_inbox approve/edit/reject backend
│   ├── telegram_intake.py   # Telegram receipt intake → pending_inbox
│   ├── code_receipt.py      # vision parse a receipt → suggested coding
│   ├── receipt_scanner.py   # PDF/text receipt line-item extraction
│   ├── ingest_txns.py       # generic bank-CSV ingest
│   ├── weekly_report.py     # trial balance + spend-by-category
│   ├── quarterly_reports.py # quarter close + Sheets export
│   └── sheet_sync.py        # bidirectional Google Sheets mirror
└── docs/                    # design specs + plans
```

## Phases

- **Phase 1 — Ledger core** ✅ double-entry schema, chart of accounts, posting + balance trigger.
- **Phase 2 — Intake** ✅ Telegram receipt intake; vision coding into `pending_inbox`.
- **Phase 3 — Review/approve UI** ✅ `post_pending.py` backend + `_Pending`
  Sheet action cells can approve/edit/reject reviewed intake rows. Telegram
  inline callbacks remain a later delivery surface (see `docs/specs/2026-05-18-receipt-classifier-ui-design.md`).
- **Phase 4 — Reporting** — weekly digest, quarterly close, Sheets mirror.
- **Phase 5 — Bank-statement automation** — scheduled CSV/PDF import → `pending_inbox`.

## Config (one-time, per deployment)

- [ ] `LISZA_SHEETS_SA_JSON` secret (Google service account) for Sheets sync.
- [ ] Telegram bot bound to the books group (auto-discovers `chat_id` → `config.json`).
- [ ] `YOUR_SPREADSHEET_ID` set in the report scripts if using Sheets.
