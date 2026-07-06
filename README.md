# LISZA

**AI-assisted double-entry bookkeeping.** LISZA turns the messy reality of a small
business's finances — receipt photos, emailed invoices, bank-statement CSVs — into
a clean, balanced double-entry ledger with almost no manual data entry.

It is local-first (a single SQLite ledger), auditable (every posted entry must
balance), and automation-friendly (Telegram + email intake, vision receipt
parsing, rule-based categorization, and scheduled reports).

> The ledger shipped in this repo is **fully synthetic demo data** — a generated
> example small-business book. It contains no real financial information.

## How it works

```
INTAKE                      REVIEW                     LEDGER & REPORTS
  Telegram photo  ─┐                                     double-entry
  Emailed invoice ─┼─►  vision parse ─►  pending_inbox ─►  ledger.db  ─►  weekly /
  Bank CSV/PDF    ─┘    (suggest acct,   (human/AI         (balanced     quarterly
                        amount, payee)    approve/edit)     entries)      reports +
                                                                          Sheets sync
```

- **Double-entry core** (`scripts/init_ledger.py`, `post_entry.py`) — accounts,
  entries, and balanced splits in `ledger.db`. A trigger refuses any posted entry
  whose debits ≠ credits.
- **Intake** (`telegram_intake.py`, receipt/PDF/CSV importers) — drops raw items
  into `pending_inbox` for review instead of posting blindly.
- **Vision coding** (`code_receipt.py`, `receipt_scanner.py`) — extracts vendor,
  amount, date, and a suggested account from a receipt image or PDF.
- **Categorization** (`payee_rules`) — pattern → account rules auto-suggest the
  booking for recurring payees.
- **Reporting** (`weekly_report.py`, `quarterly_reports.py`, `sheet_sync.py`) —
  trial balance, spend by category, and optional Google Sheets mirror.
- **Statement automation** (`statement_automation.py`) — scans
  `data/statement_inbox/` for CSV/PDF statements, imports new files into
  `pending_inbox`, and records `data/statement_import_manifest.json` so
  scheduled runs are idempotent.

## Quick start

```bash
# 1. Build a fresh ledger from the chart of accounts + synthetic demo data
python3 scripts/init_ledger.py
python3 scripts/seed_synthetic.py        # generates the demo book

# 2. Inspect balances
python3 scripts/weekly_report.py
```

```bash
# Optional: scan the statement inbox without posting anything to the ledger
python3 scripts/statement_automation.py --dry-run --json
```

## Configuration

Secrets are read from the environment (never committed):

| Variable | Purpose |
|---|---|
| `LISZA_DB` | Path to `ledger.db` (default: `LISZA/ledger.db`) |
| `LISZA_SHEETS_SA_JSON` | Google service-account JSON for Sheets sync (optional) |

`config.json` holds the (non-secret) Telegram chat binding — shipped as a
placeholder; it is auto-discovered on first message in a real deployment.

```bash
# Check runtime setup without printing secret values
python3 scripts/config_doctor.py
```

## Data model

`accounts` (chart of accounts) · `entries` (a transaction) · `splits` (the
balanced debit/credit lines) · `pending_inbox` (un-posted intake) · `payee_rules`
(auto-categorization). See `scripts/init_ledger.py` for the schema.
