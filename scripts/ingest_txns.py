#!/usr/bin/env python3
"""
ingest_txns.py — import bank/card CSV rows into LISZA pending_inbox.

This is an intake script, not a posting script. It parses supported CSV exports,
normalizes each transaction, suggests a bookkeeping account, and stores the row in
ledger.db pending_inbox for review before any journal entry is posted.

Supported CSV layouts:
  A) Credit card statements
       Date, Description, Debit, Credit, Category, Name, Card
  B) Checking / savings / card exports
       Posting Date, Transaction Date, Amount, Credit Debit Indicator, ...,
       Description, Category, Card Ending

Usage:
  python3 ingest_txns.py <csv_file_or_directory> [--dry-run]

If no path is provided, the script scans LISZA/data/uploads/ for CSV files.
Duplicates are skipped by a stable per-row hash stored in pending_inbox.raw_path.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from tenancy import resolve_db_path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = resolve_db_path(client=os.environ.get("LISZA_CLIENT"))
UPLOADS_DIR = ROOT / "data" / "uploads"
COA_PATH = ROOT / "coa.csv"

SOURCE_NAME = "bank_import"
NEW_STATUS = "new"

# Raw category/value heuristics -> LISZA account names
CATEGORY_ACCOUNT_MAP = {
    "restaurants/dining": "Meals & Entertainment",
    "dining": "Meals & Entertainment",
    "restaurant": "Meals & Entertainment",
    "groceries": "Miscellaneous Expense",
    "grocery": "Miscellaneous Expense",
    "gasoline/fuel": "Travel",
    "fuel": "Travel",
    "gas": "Travel",
    "healthcare/medical": "Miscellaneous Expense",
    "medical": "Miscellaneous Expense",
    "home improvement": "Office Supplies",
    "general merchandise": "Miscellaneous Expense",
    "merchandise": "Miscellaneous Expense",
    "clothing/shoes": "Miscellaneous Expense",
    "clothing": "Miscellaneous Expense",
    "online services": "Software & Subscriptions",
    "subscriptions": "Software & Subscriptions",
    "software": "Software & Subscriptions",
    "utilities": "Utilities",
    "travel": "Travel",
    "entertainment": "Meals & Entertainment",
    "education": "Continuing Education",
    "insurance": "Insurance",
    "mortgage": "Rent",
    "rent": "Rent",
    "atm/cash": "Cash on Hand",
    "transfer": "Business Checking",
    "payment": "Business Credit Card",
    "paycheck": "Service Revenue",
    "salary": "Salaries & Wages",
    "deposit": "Other Income",
    "interest": "Interest Income",
    "fee": "Bank & Merchant Fees",
    "bank fee": "Bank & Merchant Fees",
}


def normalize_category(raw: str) -> str:
    if not raw:
        return "Uncategorized"
    key = raw.strip().lower()
    for needle, label in CATEGORY_ACCOUNT_MAP.items():
        if needle in key:
            return label
    return raw.strip().title()


def parse_amount(val: str) -> float:
    cleaned = re.sub(r"[^\d.\-]", "", val or "0")
    return float(cleaned or "0")


def parse_date(val: str) -> str:
    raw = (val or "").strip().strip('"')
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return raw


def detect_format(headers: list[str]) -> str:
    h = [x.strip().lower() for x in headers]
    if "debit" in h and "credit" in h and "date" in h:
        return "bank_cc"
    if "posting date" in h or "credit debit indicator" in h:
        return "checking"
    return "unknown"


def row_hash(txn_date: str, description: str, amount: float, account: str) -> str:
    raw = f"{txn_date}|{description}|{amount:.2f}|{account}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def parse_bank_cc(reader: csv.DictReader, source_file: str) -> list[dict]:
    rows: list[dict] = []
    for row in reader:
        debit = parse_amount(row.get("Debit", "0"))
        credit = parse_amount(row.get("Credit", "0"))
        if debit == 0 and credit == 0:
            continue

        amount = debit if debit else credit
        is_debit = debit > 0
        txn_date = parse_date(row.get("Date", ""))
        description = (row.get("Description") or "").strip()
        raw_category = (row.get("Category") or "").strip()
        account = (row.get("Card") or row.get("Name") or "Business Credit Card").strip()
        cardholder = (row.get("Name") or "").strip()

        rows.append({
            "txn_date": txn_date,
            "post_date": txn_date,
            "description": description,
            "amount": amount,
            "signed_amount": -amount if is_debit else amount,
            "is_debit": is_debit,
            "direction": "debit" if is_debit else "credit",
            "raw_category": raw_category,
            "normalized_category": normalize_category(raw_category),
            "account": account,
            "cardholder": cardholder,
            "source_file": source_file,
            "row_hash": row_hash(txn_date, description, amount, account),
        })
    return rows


def parse_checking(reader: csv.DictReader, source_file: str) -> list[dict]:
    rows: list[dict] = []
    for row in reader:
        amount = abs(parse_amount(row.get("Amount", "0")))
        if amount == 0:
            continue

        indicator = (row.get("Credit Debit Indicator") or "").strip().upper()
        is_debit = indicator.startswith("D")
        txn_date = parse_date(row.get("Transaction Date") or row.get("Posting Date") or "")
        post_date = parse_date(row.get("Posting Date") or row.get("Transaction Date") or "")
        description = (row.get("Description") or "").strip()
        raw_category = (row.get("Category") or "").strip()
        account = (
            row.get("Card Ending")
            or row.get("Type Group")
            or row.get("type")
            or "Business Checking"
        ).strip()

        rows.append({
            "txn_date": txn_date,
            "post_date": post_date,
            "description": description,
            "amount": amount,
            "signed_amount": -amount if is_debit else amount,
            "is_debit": is_debit,
            "direction": "debit" if is_debit else "credit",
            "raw_category": raw_category,
            "normalized_category": normalize_category(raw_category),
            "account": account,
            "cardholder": "",
            "source_file": source_file,
            "row_hash": row_hash(txn_date, description, amount, account),
        })
    return rows


def parse_csv_content(text: str, source_file: str = "upload.csv") -> list[dict]:
    """Parse raw CSV text into normalized transaction rows.

    Bank/card exports often prefix a few preamble lines before the header, so we
    scan for the first recognizable header row. Unknown layouts return [].
    """
    lines = text.splitlines(keepends=True)
    start = 0
    for i, line in enumerate(lines):
        lower = line.strip().strip('"').lower()
        if lower.startswith("date,") or lower.startswith("posting date,"):
            start = i
            break

    content = "".join(lines[start:])
    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []
    fmt = detect_format(headers)
    if fmt == "bank_cc":
        return parse_bank_cc(reader, source_file)
    if fmt == "checking":
        return parse_checking(reader, source_file)
    return []


def load_csv(path: Path) -> list[dict]:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with path.open(encoding=enc, newline="") as f:
                text = f.read()
        except UnicodeDecodeError:
            continue
        rows = parse_csv_content(text, path.name)
        if not rows:
            print(f"  WARN: Unknown/empty format for {path.name}")
        return rows
    print(f"  ERROR: Could not decode {path.name}")
    return []


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def load_accounts() -> list[dict]:
    with COA_PATH.open() as f:
        return list(csv.DictReader(f))


def find_account_code(accounts: list[dict], name: str, fallback: str | None = None) -> str | None:
    target = name.strip().lower()
    for row in accounts:
        if row["name"].strip().lower() == target:
            return row["code"]
    return fallback


def infer_payment_account_code(accounts: list[dict], raw_account: str, description: str) -> str | None:
    text = f"{raw_account} {description}".lower()
    if any(token in text for token in ("cash", "atm")):
        return find_account_code(accounts, "Cash on Hand")
    if any(token in text for token in ("checking", "ach", "bank", "savings", "debit")):
        return find_account_code(accounts, "Business Checking")
    if "2" in text and "card" in text:
        return find_account_code(accounts, "Business Credit Card 2") or find_account_code(accounts, "Business Credit Card")
    if any(token in text for token in ("card", "visa", "mastercard", "amex", "credit")):
        return find_account_code(accounts, "Business Credit Card")
    return find_account_code(accounts, "Business Checking")


def infer_entry_accounts(
    accounts: list[dict],
    row: dict,
    suggested_account: str | None,
    payment_account: str | None,
) -> tuple[str | None, str | None]:
    if not suggested_account or not payment_account:
        return None, None

    desc = row["description"].lower()
    account_text = row["account"].lower()
    checking_code = find_account_code(accounts, "Business Checking")
    cc_codes = {
        code
        for code in (
            find_account_code(accounts, "Business Credit Card"),
            find_account_code(accounts, "Business Credit Card 2"),
        )
        if code
    }

    # Card-statement payment rows should reduce card liability against checking,
    # not reflect as DR checking / CR card.
    if (
        not row["is_debit"]
        and suggested_account in cc_codes
        and ("payment" in desc or "thank you" in desc)
        and any(token in account_text for token in ("card", "visa", "mastercard", "amex", "credit"))
    ):
        return suggested_account, checking_code or payment_account

    if row["is_debit"]:
        if suggested_account == payment_account and suggested_account in cc_codes:
            return suggested_account, checking_code or payment_account
        return suggested_account, payment_account

    return payment_account, suggested_account


def load_payee_rules(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT pattern, account_code
           FROM payee_rules
           WHERE active = 1
           ORDER BY priority ASC, id ASC"""
    ).fetchall()


def infer_account_code(
    accounts: list[dict],
    payee_rules: list[sqlite3.Row],
    description: str,
    raw_category: str,
    is_debit: bool,
) -> str | None:
    desc = description.lower()
    for rule in payee_rules:
        if rule["pattern"] and rule["pattern"].lower() in desc:
            return rule["account_code"]

    normalized_name = normalize_category(raw_category)
    exact = find_account_code(accounts, normalized_name)
    if exact:
        return exact

    if is_debit:
        if any(token in desc for token in ("fee", "service charge", "merchant fee", "overdraft")):
            return find_account_code(accounts, "Bank & Merchant Fees")
        if any(token in desc for token in ("subscription", "aws", "google", "microsoft", "adobe", "zoom", "slack")):
            return find_account_code(accounts, "Software & Subscriptions")
        if any(token in desc for token in ("airbnb", "hotel", "uber", "lyft", "flight", "airlines")):
            return find_account_code(accounts, "Travel")
        return find_account_code(accounts, "Miscellaneous Expense")

    if any(token in desc for token in ("interest", "apy")):
        return find_account_code(accounts, "Interest Income")
    return find_account_code(accounts, "Other Income")


def dedupe_ref(source_file: Path, txn_hash: str) -> str:
    return f"{source_file.resolve()}#{txn_hash}"


def pending_payload(
    row: dict,
    suggested_account: str | None,
    payment_account: str | None,
    suggested_debit: str | None,
    suggested_credit: str | None,
    import_kind: str = "bank_csv",
) -> dict:
    return {
        "import_kind": import_kind,
        "source_file": row["source_file"],
        "txn_date": row["txn_date"],
        "post_date": row["post_date"],
        "description": row["description"],
        "amount": row["amount"],
        "signed_amount": row["signed_amount"],
        "direction": row["direction"],
        "is_debit": row["is_debit"],
        "raw_category": row["raw_category"],
        "normalized_category": row["normalized_category"],
        "account_label": row["account"],
        "cardholder": row["cardholder"],
        "row_hash": row["row_hash"],
        "suggested_account_code": suggested_account,
        "suggested_payment_account_code": payment_account,
        "suggested_debit_account_code": suggested_debit,
        "suggested_credit_account_code": suggested_credit,
    }


def insert_pending(
    con: sqlite3.Connection,
    source_path: Path,
    row: dict,
    suggested_account: str | None,
    payment_account: str | None,
    suggested_debit: str | None,
    suggested_credit: str | None,
    import_kind: str = "bank_csv",
) -> bool:
    ref = dedupe_ref(source_path, row["row_hash"])
    existing = con.execute(
        "SELECT id FROM pending_inbox WHERE source = ? AND raw_path = ?",
        (SOURCE_NAME, ref),
    ).fetchone()
    if existing:
        return False

    payload = pending_payload(
        row,
        suggested_account,
        payment_account,
        suggested_debit,
        suggested_credit,
        import_kind=import_kind,
    )
    notes = (
        f"{row['direction']} import · payment {payment_account or '-'}"
        f" · splits {suggested_debit or '-'}->{suggested_credit or '-'}"
    )
    con.execute(
        """INSERT INTO pending_inbox
           (source, raw_path, parsed_json, suggested_account, suggested_amount, status, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            SOURCE_NAME,
            ref,
            json.dumps(payload, separators=(",", ":")),
            suggested_account,
            row["amount"],
            NEW_STATUS,
            notes,
        ),
    )
    return True


INBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_inbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    source      TEXT NOT NULL,
    raw_path    TEXT,
    parsed_json TEXT,
    suggested_account TEXT,
    suggested_amount  REAL,
    status      TEXT NOT NULL DEFAULT 'new'
                CHECK(status IN ('new','reviewed','posted','rejected')),
    entry_id    INTEGER REFERENCES entries(id),
    notes       TEXT
);
"""


def ensure_inbox_schema(con: sqlite3.Connection) -> None:
    """Self-heal: register_client builds the ledger core but not pending_inbox.

    Imports land in the inbox for review, so the table must exist on the target
    client book before we write. Idempotent — backfills older books too.
    """
    con.executescript(INBOX_SCHEMA)


def text_dedupe_ref(source_file: str, txn_hash: str) -> str:
    """Dedup key for text-based (uploaded) imports — no filesystem path to resolve."""
    return f"upload://{source_file}#{txn_hash}"


def _insert_pending_row(
    con: sqlite3.Connection,
    ref: str,
    row: dict,
    suggested_account: str | None,
    payment_account: str | None,
    suggested_debit: str | None,
    suggested_credit: str | None,
    import_kind: str = "bank_csv",
) -> None:
    payload = pending_payload(
        row, suggested_account, payment_account,
        suggested_debit, suggested_credit, import_kind=import_kind,
    )
    notes = (
        f"{row['direction']} import · payment {payment_account or '-'}"
        f" · splits {suggested_debit or '-'}->{suggested_credit or '-'}"
    )
    con.execute(
        """INSERT INTO pending_inbox
           (source, raw_path, parsed_json, suggested_account, suggested_amount, status, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            SOURCE_NAME, ref,
            json.dumps(payload, separators=(",", ":")),
            suggested_account, row["amount"], NEW_STATUS, notes,
        ),
    )


def ingest_text(
    csv_text: str,
    db_path: str | Path,
    source_file: str = "upload.csv",
    dry_run: bool = False,
) -> dict:
    """Import CSV text into a client's pending_inbox (review gate, nothing posted).

    The console 'Import Data' path: text in, suggested codings out, every row
    parked as status='new' for the existing approve/reject flow. Returns counts.
    """
    rows = parse_csv_content(csv_text, source_file)

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    try:
        ensure_inbox_schema(con)
        accounts = load_accounts()
        payee_rules = load_payee_rules(con)

        inserted = 0
        skipped = 0
        for row in rows:
            suggested_account = infer_account_code(
                accounts, payee_rules, row["description"],
                row["raw_category"], row["is_debit"],
            )
            payment_account = infer_payment_account_code(
                accounts, row["account"], row["description"])
            suggested_debit, suggested_credit = infer_entry_accounts(
                accounts, row, suggested_account, payment_account)

            ref = text_dedupe_ref(source_file, row["row_hash"])
            exists = con.execute(
                "SELECT 1 FROM pending_inbox WHERE source = ? AND raw_path = ?",
                (SOURCE_NAME, ref),
            ).fetchone()
            if exists:
                skipped += 1
                continue
            if not dry_run:
                _insert_pending_row(
                    con, ref, row, suggested_account, payment_account,
                    suggested_debit, suggested_credit,
                )
            inserted += 1

        if not dry_run:
            con.commit()
        return {"parsed": len(rows), "inserted": inserted, "skipped": skipped}
    finally:
        con.close()


def ingest(paths: list[Path], dry_run: bool = False) -> int:
    if not DB_PATH.exists():
        print(f"ERROR: ledger db not found at {DB_PATH}")
        print("Run: python3 scripts/init_ledger.py")
        return 1

    con = connect_db()
    accounts = load_accounts()
    payee_rules = load_payee_rules(con)

    total_inserted = 0
    total_skipped = 0

    try:
        for path in paths:
            print(f"\nReading {path.name}…")
            rows = load_csv(path)
            if not rows:
                print("  No rows parsed.")
                continue

            print(f"  Parsed {len(rows)} rows")
            inserted = 0
            skipped = 0

            for row in rows:
                suggested_account = infer_account_code(
                    accounts,
                    payee_rules,
                    row["description"],
                    row["raw_category"],
                    row["is_debit"],
                )
                payment_account = infer_payment_account_code(accounts, row["account"], row["description"])
                suggested_debit, suggested_credit = infer_entry_accounts(
                    accounts,
                    row,
                    suggested_account,
                    payment_account,
                )
                ref = dedupe_ref(path, row["row_hash"])
                exists = con.execute(
                    "SELECT 1 FROM pending_inbox WHERE source = ? AND raw_path = ?",
                    (SOURCE_NAME, ref),
                ).fetchone()
                if exists:
                    skipped += 1
                    continue

                if dry_run:
                    print(
                        "  [DRY]",
                        row["txn_date"],
                        f"{row['description'][:48]:48}",
                        f"{row['amount']:>9.2f}",
                        f"acct={suggested_account or '-'}",
                        f"pay={payment_account or '-'}",
                        f"dr={suggested_debit or '-'}",
                        f"cr={suggested_credit or '-'}",
                    )
                    inserted += 1
                    continue

                if insert_pending(
                    con,
                    path,
                    row,
                    suggested_account,
                    payment_account,
                    suggested_debit,
                    suggested_credit,
                ):
                    inserted += 1
                else:
                    skipped += 1

            if not dry_run:
                con.commit()
            print(f"  {'Would insert' if dry_run else 'Inserted'}: {inserted}  Skipped (dup): {skipped}")
            total_inserted += inserted
            total_skipped += skipped

        if dry_run:
            print(f"\nDry run complete. Would insert {total_inserted} rows.")
        else:
            total = con.execute(
                "SELECT COUNT(*) FROM pending_inbox WHERE source = ?",
                (SOURCE_NAME,),
            ).fetchone()[0]
            print(
                f"\nDone. pending_inbox now has {total} {SOURCE_NAME} rows "
                f"({total_inserted} new, {total_skipped} duplicates skipped)"
            )
        return 0
    finally:
        con.close()


def collect_paths(args: list[str]) -> list[Path]:
    if args:
        paths: list[Path] = []
        for raw in args:
            target = Path(raw)
            if target.is_dir():
                paths.extend(sorted(target.glob("*.csv")))
                paths.extend(sorted(target.glob("*.CSV")))
            elif target.is_file():
                paths.append(target)
            else:
                print(f"WARN: {target} not found, skipping")
        return paths

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(UPLOADS_DIR.glob("*.csv")) + sorted(UPLOADS_DIR.glob("*.CSV"))


def _cli_import_text(argv) -> int:
    """`import-text --client <slug> [--payload <json> | stdin]` → JSON result.

    payload = {"csv": "<raw text>", "source_file": "upload.csv"}.
    """
    import argparse

    from tenancy import resolve_db

    ap = argparse.ArgumentParser(prog="ingest_txns.py import-text")
    ap.add_argument("--client", required=True)
    ap.add_argument("--payload")
    a = ap.parse_args(argv)
    try:
        raw = a.payload if a.payload is not None else sys.stdin.read()
        payload = json.loads(raw)
        csv_text = payload.get("csv")
        if not csv_text:
            print(json.dumps({"error": "payload is missing 'csv' text"}))
            return 0
        source_file = payload.get("source_file") or "upload.csv"
        db_path = resolve_db(a.client)
        if not db_path.exists():
            print(json.dumps({"error": f"client '{a.client}' not found"}))
            return 0
        result = ingest_text(csv_text, db_path=db_path, source_file=source_file)
        print(json.dumps({"ok": True, **result}))
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "import-text":
        return _cli_import_text(sys.argv[2:])

    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    paths = collect_paths(args)

    if not paths:
        print(f"No CSV files found in {UPLOADS_DIR}")
        print("Usage: python3 ingest_txns.py <file_or_directory> [--dry-run]")
        return 0

    print(f"Found {len(paths)} CSV file(s) to process{' (dry run)' if dry_run else ''}:")
    for path in paths:
        print(f"  {path.name}")

    return ingest(paths, dry_run=dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
