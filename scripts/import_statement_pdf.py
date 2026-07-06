#!/usr/bin/env python3
"""
import_statement_pdf.py — parse card statement PDFs into LISZA pending_inbox.

Current parser target: Citi / Costco-style credit card statements, adapted from
the working Finance parser but routed into LISZA's review-first inbox instead of
posting directly.

Usage:
  python3 import_statement_pdf.py <pdf_or_dir> [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

import fitz  # pymupdf

from ingest_txns import (
    SOURCE_NAME,
    collect_paths,
    connect_db,
    dedupe_ref,
    find_account_code,
    infer_categorization,
    infer_entry_accounts,
    infer_payment_account_code,
    insert_pending,
    load_accounts,
    load_payee_rules,
)

DATE_RE = re.compile(r"^\d{2}/\d{2}$")
AMOUNT_RE = re.compile(r"^(minus)?\$[\d,]+\.\d{2}$")

SECTION_MARKERS = {
    "payments, credits and adjustments": "PAYMENT",
    "standard purchases": "PURCHASE",
    "fees charged": "FEES",
    "interest charged": "INTEREST",
    "cash advances": "CASH",
}

SKIP_LINES = {
    "no activity", "sale", "date", "post", "description", "amount",
    "account summary", "cardholder summary", "new charges",
    "total fees for this period", "total interest for this period",
}

SECTION_CATEGORY_HINT = {
    "PAYMENT": "payment",
    "PURCHASE": "purchase",
    "FEES": "bank fee",
    "INTEREST": "interest",
    "CASH": "cash advance",
}


def extract_pdf_text(path: Path) -> str:
    doc = fitz.open(str(path))
    text = "".join(page.get_text() for page in doc)
    doc.close()
    return text


def parse_billing_period(text: str) -> tuple[date, date] | None:
    match = re.search(r"Billing Period[:\s]+(\d{2}/\d{2}/\d{2})-(\d{2}/\d{2}/\d{2})", text)
    if not match:
        return None
    start = datetime.strptime(match.group(1), "%m/%d/%y").date()
    end = datetime.strptime(match.group(2), "%m/%d/%y").date()
    return start, end


def parse_amount_str(text: str) -> tuple[float, bool]:
    is_credit = text.startswith("minus")
    value = float(re.sub(r"[^\d.]", "", text))
    return value, is_credit


def assign_year(mm_dd: str, start: date, end: date) -> date:
    month, day = int(mm_dd[:2]), int(mm_dd[3:])
    for year in (end.year, start.year):
        try:
            candidate = date(year, month, day)
            if start <= candidate <= end:
                return candidate
        except ValueError:
            pass
    return date(end.year, month, day)


def parse_transactions(text: str, start: date, end: date) -> list[dict]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    txns: list[dict] = []
    section = None
    in_area = False
    i = 0

    while i < len(lines):
        line = lines[i]
        upper = line.upper()
        lower = line.lower()

        if "ACCOUNT SUMMARY" in upper:
            in_area = True
        if in_area and ("DAYS IN BILLING CYCLE" in upper or "2025 TOTALS" in upper or "2026 TOTALS" in upper):
            break
        if not in_area:
            i += 1
            continue

        for marker, name in SECTION_MARKERS.items():
            if marker in lower:
                section = name
                i += 1
                break
        else:
            if lower in SKIP_LINES or lower.startswith("page "):
                i += 1
                continue

            if section and DATE_RE.match(line):
                date1 = line
                i += 1
                if i >= len(lines):
                    break

                if DATE_RE.match(lines[i]):
                    date2 = lines[i]
                    i += 1
                    tx_type = section if section != "PAYMENT" else "PURCHASE"
                else:
                    date2 = None
                    tx_type = section

                desc_parts: list[str] = []
                while i < len(lines) and not AMOUNT_RE.match(lines[i]):
                    probe = lines[i].lower()
                    if DATE_RE.match(lines[i]) or probe in SKIP_LINES:
                        break
                    if any(marker in probe for marker in SECTION_MARKERS):
                        break
                    desc_parts.append(lines[i])
                    i += 1

                if i < len(lines) and AMOUNT_RE.match(lines[i]):
                    amount, is_credit = parse_amount_str(lines[i])
                    i += 1
                    desc = " ".join(desc_parts).strip()
                    post_date = assign_year(date2 or date1, start, end)
                    txn_date = assign_year(date1, start, end)
                    txns.append({
                        "txn_date": txn_date.isoformat(),
                        "post_date": post_date.isoformat(),
                        "description": desc,
                        "amount": amount,
                        "is_credit": is_credit,
                        "section": tx_type,
                    })
                continue
            i += 1

    return txns


def to_import_row(txn: dict, source_file: str) -> dict:
    is_debit = not txn["is_credit"]
    raw_category = SECTION_CATEGORY_HINT.get(txn["section"], txn["section"].lower())
    account_label = "Business Credit Card"
    if txn["section"] == "PAYMENT":
        account_label = "Business Credit Card"
    row_hash = hashlib.sha256(
        f"{source_file}|{txn['txn_date']}|{txn['post_date']}|{txn['description']}|{txn['amount']:.2f}|{txn['section']}".encode()
    ).hexdigest()[:16]
    return {
        "txn_date": txn["txn_date"],
        "post_date": txn["post_date"],
        "description": txn["description"],
        "amount": txn["amount"],
        "signed_amount": -txn["amount"] if is_debit else txn["amount"],
        "is_debit": is_debit,
        "direction": "debit" if is_debit else "credit",
        "raw_category": raw_category,
        "normalized_category": raw_category.title(),
        "account": account_label,
        "cardholder": "",
        "source_file": source_file,
        "row_hash": row_hash,
    }


def process_pdf(path: Path) -> tuple[list[dict], tuple[date, date] | None]:
    text = extract_pdf_text(path)
    period = parse_billing_period(text)
    if not period:
        return [], None
    txns = parse_transactions(text, period[0], period[1])
    return [to_import_row(txn, path.name) for txn in txns], period


def import_pdfs(paths: list[Path], dry_run: bool = False) -> int:
    con = connect_db()
    accounts = load_accounts()
    payee_rules = load_payee_rules(con)
    inserted_total = 0
    skipped_total = 0

    try:
        for path in paths:
            rows, period = process_pdf(path)
            if not period:
                print(f"  WARN: no billing period found in {path.name}")
                continue
            print(f"\nReading {path.name}…")
            print(f"  Parsed {len(rows)} rows (billing {period[0]}–{period[1]})")

            inserted = 0
            skipped = 0
            for row in rows:
                categorization = infer_categorization(
                    accounts,
                    payee_rules,
                    row["description"],
                    row["raw_category"],
                    row["is_debit"],
                )
                suggested_account = categorization.get("account_code")
                payment_account = infer_payment_account_code(accounts, row["account"], row["description"])
                if row["raw_category"] == "payment":
                    suggested_account = find_account_code(accounts, "Business Credit Card")
                    payment_account = find_account_code(accounts, "Business Checking")
                    categorization = {"account_code": suggested_account, "source": "statement_section", "confidence": 0.9, "raw_category": row["raw_category"]}
                elif row["raw_category"] == "bank fee":
                    suggested_account = find_account_code(accounts, "Bank & Merchant Fees")
                    categorization = {"account_code": suggested_account, "source": "statement_section", "confidence": 0.9, "raw_category": row["raw_category"]}
                elif row["raw_category"] == "interest":
                    suggested_account = find_account_code(accounts, "Interest Income") if not row["is_debit"] else find_account_code(accounts, "Bank & Merchant Fees")
                    categorization = {"account_code": suggested_account, "source": "statement_section", "confidence": 0.9, "raw_category": row["raw_category"]}
                suggested_debit, suggested_credit = infer_entry_accounts(accounts, row, suggested_account, payment_account)

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
                    import_kind="statement_pdf",
                    categorization=categorization,
                ):
                    inserted += 1
                else:
                    skipped += 1

            if not dry_run:
                con.commit()
            print(f"  {'Would insert' if dry_run else 'Inserted'}: {inserted}  Skipped (dup): {skipped}")
            inserted_total += inserted
            skipped_total += skipped

        print(
            f"\n{'Dry run complete. Would insert' if dry_run else 'Done. Inserted'} "
            f"{inserted_total} rows"
            + ("" if dry_run else f" ({skipped_total} duplicates skipped)")
        )
        return 0
    finally:
        con.close()


def resolve_pdf_paths(target: str) -> list[Path]:
    path = Path(target)
    if path.is_dir():
        return sorted(path.glob("*.pdf"))
    return [path]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", help="PDF file or directory of PDFs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return import_pdfs(resolve_pdf_paths(args.target), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
