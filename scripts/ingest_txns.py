"""
ingest_txns.py — Load bank/CC CSV exports into LISZA/data/lisza.duckdb

Supports two CSV layouts:
  A) Bank credit-card statements
       Date, Description, Debit, Credit, Category, Name, Card
  B) Bank checking / card / savings
       Posting Date, Transaction Date, Amount, Credit Debit Indicator,
       type, Type Group, ..., Description, Category, Card Ending

Usage:
  python3 ingest_txns.py <csv_file_or_directory> [--dry-run]

Drop CSV files in LISZA/data/uploads/ then run this script.
Existing rows with the same (txn_date, description, amount, account) are skipped.
"""

import csv
import sys
import os
import hashlib
import re
from datetime import datetime, date
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "lisza.duckdb"
UPLOADS_DIR = Path(__file__).parent.parent / "data" / "uploads"

# ── Category normalizer (matches existing ledger COA) ───────────────────────
CATEGORY_MAP = {
    "restaurants/dining": "Entertainment & Recreation",
    "dining":             "Entertainment & Recreation",
    "groceries":          "Food & Personal Care",
    "grocery":            "Food & Personal Care",
    "gasoline/fuel":      "Transportation",
    "fuel":               "Transportation",
    "gas":                "Transportation",
    "healthcare/medical": "Medical & Healthcare",
    "medical":            "Medical & Healthcare",
    "home improvement":   "Repairs, Maintenance & Supplies - Household",
    "general merchandise":"Miscellaneous Expenses",
    "merchandise":        "Miscellaneous Expenses",
    "clothing/shoes":     "Clothing & Accessories",
    "clothing":           "Clothing & Accessories",
    "online services":    "Dues and Subscriptions",
    "subscriptions":      "Dues and Subscriptions",
    "utilities":          "Utilities",
    "travel":             "Travel & Lodging",
    "entertainment":      "Entertainment & Recreation",
    "education":          "Education",
    "insurance":          "Insurance",
    "mortgage":           "Housing (Mortgage or Rent)",
    "rent":               "Housing (Mortgage or Rent)",
    "atm/cash":           "Cash on Hand",
    "transfer":           "Internal Transfer",
    "payment":            "CC Payment",
    "paycheck":           "Employment Income",
    "salary":             "Employment Income",
    "deposit":            "Miscellaneous Income",
    "interest":           "Interest Earned",
}

def normalize_category(raw: str) -> str:
    if not raw:
        return "Uncategorized"
    key = raw.strip().lower()
    for k, v in CATEGORY_MAP.items():
        if k in key:
            return v
    return raw.strip().title()

def parse_amount(val: str) -> float:
    return float(re.sub(r"[^\d.\-]", "", val or "0") or "0")

def parse_date(val: str) -> str:
    val = val.strip().strip('"')
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return val

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

def parse_bank_cc(reader, source_file: str) -> list[dict]:
    rows = []
    for r in reader:
        txn_date = parse_date(r.get("Date", ""))
        description = (r.get("Description") or "").strip()
        debit = parse_amount(r.get("Debit", "0"))
        credit = parse_amount(r.get("Credit", "0"))
        amount = debit if debit else -credit
        is_debit = debit > 0
        category = r.get("Category", "").strip()
        cardholder = (r.get("Name") or "").strip()
        card = (r.get("Card") or "").strip()
        account = f"BANK-CC-{card}" if card else "BANK-CC"
        rows.append({
            "txn_date": txn_date,
            "post_date": txn_date,
            "description": description,
            "amount": round(amount, 2),
            "is_debit": is_debit,
            "raw_category": category,
            "normalized_category": normalize_category(category),
            "account": account,
            "cardholder": cardholder,
            "source_file": source_file,
            "row_hash": row_hash(txn_date, description, amount, account),
        })
    return rows

def parse_checking(reader, source_file: str) -> list[dict]:
    rows = []
    for r in reader:
        post_date = parse_date(r.get("Posting Date") or r.get("Transaction Date") or "")
        txn_date_raw = r.get("Transaction Date") or r.get("Posting Date") or ""
        txn_date = parse_date(txn_date_raw)
        amount_raw = parse_amount(r.get("Amount", "0"))
        indicator = (r.get("Credit Debit Indicator") or "").strip().upper()
        if indicator == "DEBIT":
            amount = amount_raw
            is_debit = True
        elif indicator == "CREDIT":
            amount = -amount_raw
            is_debit = False
        else:
            # Signed amount — negative = debit for checking
            amount = amount_raw
            is_debit = amount_raw > 0
        description = (r.get("Description") or r.get("Merchant") or "").strip()
        category = (r.get("Category") or "").strip()
        card_ending = (r.get("Card Ending") or "").strip()
        account_type = (r.get("Type Group") or r.get("type") or "").strip()
        account = f"BANK-CHK-{card_ending}" if card_ending else "BANK-CHK"
        rows.append({
            "txn_date": txn_date,
            "post_date": post_date,
            "description": description,
            "amount": round(amount, 2),
            "is_debit": is_debit,
            "raw_category": category,
            "normalized_category": normalize_category(category),
            "account": account,
            "cardholder": "",
            "source_file": source_file,
            "row_hash": row_hash(txn_date, description, amount, account),
        })
    return rows

def load_csv(path: Path) -> list[dict]:
    # Try UTF-8, fall back to latin-1
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, encoding=enc, newline="") as f:
                lines = f.readlines()

            # Some bank CC files have a 2-line preamble before real headers.
            # Skip lines until we hit the actual header row.
            start = 0
            for i, line in enumerate(lines):
                stripped = line.strip().strip('"')
                if stripped.lower().startswith("date,") or stripped.lower().startswith("posting date,"):
                    start = i
                    break

            import io
            content = "".join(lines[start:])
            reader = csv.DictReader(io.StringIO(content))
            headers = reader.fieldnames or []
            fmt = detect_format(headers)
            if fmt == "bank_cc":
                return parse_bank_cc(reader, path.name)
            elif fmt == "checking":
                return parse_checking(reader, path.name)
            else:
                print(f"  WARN: Unknown format for {path.name} — headers: {headers}")
                return []
        except UnicodeDecodeError:
            continue
    print(f"  ERROR: Could not decode {path.name}")
    return []

def init_db(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS raw_transactions (
        id               VARCHAR PRIMARY KEY,
        txn_date         DATE,
        post_date        DATE,
        description      VARCHAR,
        amount           DOUBLE,
        is_debit         BOOLEAN,
        raw_category     VARCHAR,
        normalized_category VARCHAR,
        account          VARCHAR,
        cardholder       VARCHAR,
        source_file      VARCHAR,
        ingested_at      TIMESTAMP DEFAULT now()
    )
    """)

def ingest(paths: list[Path], dry_run: bool = False):
    try:
        import duckdb
    except ImportError:
        print("ERROR: duckdb not installed. Run: pip install duckdb")
        sys.exit(1)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    init_db(conn)

    total_loaded = 0
    total_skipped = 0

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
            # Check for duplicate
            existing = conn.execute(
                "SELECT 1 FROM raw_transactions WHERE id = ?", [row["row_hash"]]
            ).fetchone()
            if existing:
                skipped += 1
                continue

            if not dry_run:
                conn.execute("""
                INSERT INTO raw_transactions
                  (id, txn_date, post_date, description, amount, is_debit,
                   raw_category, normalized_category, account, cardholder, source_file)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, [
                    row["row_hash"], row["txn_date"], row["post_date"],
                    row["description"], row["amount"], row["is_debit"],
                    row["raw_category"], row["normalized_category"],
                    row["account"], row["cardholder"], row["source_file"],
                ])
                inserted += 1
            else:
                print(f"  [DRY] {row['txn_date']} {row['description'][:50]} {row['amount']}")
                inserted += 1

        print(f"  {'Would insert' if dry_run else 'Inserted'}: {inserted}  Skipped (dup): {skipped}")
        total_loaded += inserted
        total_skipped += skipped

    if not dry_run:
        count = conn.execute("SELECT COUNT(*) FROM raw_transactions").fetchone()[0]
        print(f"\nDone. Total in DB: {count} rows ({total_loaded} new, {total_skipped} duplicates skipped)")
    else:
        print(f"\nDry run complete. Would insert {total_loaded} rows.")

    conn.close()

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        paths = []
        for a in args:
            target = Path(a)
            if target.is_dir():
                paths += list(target.glob("*.csv")) + list(target.glob("*.CSV"))
            elif target.is_file():
                paths.append(target)
            else:
                print(f"WARN: {target} not found, skipping")
    else:
        # Default: scan uploads directory
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        paths = list(UPLOADS_DIR.glob("*.csv")) + list(UPLOADS_DIR.glob("*.CSV"))
        if not paths:
            print(f"No CSV files found in {UPLOADS_DIR}")
            print("Usage: python3 ingest_txns.py <file_or_directory> [--dry-run]")
            sys.exit(0)

    print(f"Found {len(paths)} CSV file(s) to process{' (dry run)' if dry_run else ''}:")
    for p in paths:
        print(f"  {p.name}")

    ingest(paths, dry_run=dry_run)
