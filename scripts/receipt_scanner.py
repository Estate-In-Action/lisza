#!/usr/bin/env python3
"""
receipt_scanner.py — LISZA project receipt scanning and expense classification.

Extracts line items from PDF receipts (or plain text), auto-classifies categories,
builds expense reports, and optionally writes a CSV and inserts into ledger.db
pending_inbox table for human review.

PDF parsing strategy (in priority order):
  1. pdfplumber  (best text extraction + tables)
  2. PyPDF2      (lightweight fallback)
  3. pdftotext   (poppler CLI via subprocess)
  4. --text flag (manual plain-text entry)

Usage:
    python3 receipt_scanner.py receipts/
    python3 receipt_scanner.py file1.pdf file2.pdf
    python3 receipt_scanner.py --text "Adobe 54.99 2026-05-19 Software"
    python3 receipt_scanner.py --csv output.csv receipts/
    python3 receipt_scanner.py           # runs built-in self-test
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ─────────────────────────── Constants ────────────────────────────

LISZA_ROOT = Path("/home/workspace/LISZA")
DB_PATH = LISZA_ROOT / "ledger.db"

CATEGORIES = [
    "Software",
    "Travel",
    "Meals",
    "Office",
    "Equipment",
    "Services",
    "Other",
]

# Vendor keyword → category mapping (lowercase match, first hit wins)
VENDOR_RULES: list[tuple[list[str], str]] = [
    (
        ["adobe", "figma", "notion", "github", "aws", "heroku", "netlify",
         "vercel", "linear", "slack", "zoom", "microsoft", "gsuite",
         "google workspace", "dropbox", "1password", "cloudflare",
         "sendgrid", "twilio", "datadog", "sentry", "airtable", "loom"],
        "Software",
    ),
    (
        ["airline", "hotel", "airbnb", "uber", "lyft", "expedia",
         "delta", "united", "american airlines", "southwest", "marriott",
         "hilton", "hyatt", "vrbo", "amtrak", "flight", "spirit airlines",
         "jetblue", "alaska air", "frontier"],
        "Travel",
    ),
    (
        ["restaurant", "cafe", "coffee", "doordash", "grubhub", "ubereats",
         "yelp", "diner", "bistro", "grille", "sushi", "pizza", "burger",
         "starbucks", "chipotle", "subway", "mcdonald", "taco bell",
         "blue bottle", "peet", "dunkin"],
        "Meals",
    ),
    (
        ["staples", "office depot", "office max", "amazon", "target",
         "walmart", "costco", "sam's club", "uline"],
        "Office",
    ),
    (
        ["apple", "dell", "lenovo", "bestbuy", "best buy", "newegg",
         "microcenter", "micro center", "b&h", "bhphotovideo", "adorama"],
        "Equipment",
    ),
    (
        ["consulting", "freelance", "contractor", "legal", "accounting",
         "attorney", "lawyer", "cpa", "fiverr", "upwork", "toptal",
         "gusto", "rippling", "justworks"],
        "Services",
    ),
]

# ─────────────────────────── PDF text extraction ────────────────────────────

def _extract_via_pdfplumber(pdf_path: Path) -> str:
    import pdfplumber  # type: ignore
    parts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
    return "\n".join(parts)


def _extract_via_pypdf2(pdf_path: Path) -> str:
    import PyPDF2  # type: ignore
    parts: list[str] = []
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
    return "\n".join(parts)


def _extract_via_pdftotext(pdf_path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", str(pdf_path), "-"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {result.stderr.strip()}")
    return result.stdout


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract text from a PDF using the best available method."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    for extractor, label in [
        (_extract_via_pdfplumber, "pdfplumber"),
        (_extract_via_pypdf2, "PyPDF2"),
        (_extract_via_pdftotext, "pdftotext"),
    ]:
        try:
            return extractor(pdf_path)
        except ImportError:
            continue  # library not installed
        except Exception as exc:
            print(f"  [warn] {label} failed: {exc}", file=sys.stderr)

    raise RuntimeError(
        "No PDF parser available. "
        "Install pdfplumber (pip install pdfplumber), "
        "PyPDF2 (pip install PyPDF2), "
        "or poppler-utils (apt-get install poppler-utils)."
    )


# ─────────────────────────── Parsing helpers ────────────────────────────

_DATE_FMTS = [
    ("%Y-%m-%d", r"\b(\d{4}-\d{2}-\d{2})\b"),
    ("%m/%d/%Y", r"\b(\d{1,2}/\d{1,2}/\d{4})\b"),
    ("%m-%d-%Y", r"\b(\d{1,2}-\d{1,2}-\d{4})\b"),
    ("%B %d, %Y", r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4})\b"),
    ("%B %d %Y",  r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\s+\d{4})\b"),
    ("%b %d, %Y", r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+\d{4})\b"),
    ("%b %d %Y",  r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2}\s+\d{4})\b"),
]

# Amount patterns ordered by specificity (explicit labels first)
_AMOUNT_PATTERNS = [
    (r"\bTotal[:\s]+\$?\s*([\d,]+\.\d{2})\b", True),
    (r"\bAmount Due[:\s]+\$?\s*([\d,]+\.\d{2})\b", True),
    (r"\bCharged[:\s]+\$?\s*([\d,]+\.\d{2})\b", True),
    (r"\bAmount[:\s]+\$?\s*([\d,]+\.\d{2})\b", True),
    (r"\bGrand Total[:\s]+\$?\s*([\d,]+\.\d{2})\b", True),
    (r"\$\s*([\d,]+\.\d{2})\b", False),
    (r"\b([\d,]+\.\d{2})\b", False),
]

_PAYMENT_PATTERNS = [
    (r"\bvisa\b", "Visa"),
    (r"\bmastercard\b", "Mastercard"),
    (r"\bamex\b|\bamerican express\b", "Amex"),
    (r"\bpaypal\b", "PayPal"),
    (r"\bcheque\b|\bcheck\b", "Check"),
    (r"\bcash\b", "Cash"),
    (r"\bbank transfer\b|\bach\b", "Bank Transfer"),
    (r"\bapple pay\b", "Apple Pay"),
    (r"\bgoogle pay\b", "Google Pay"),
    (r"\bstripe\b", "Stripe"),
    (r"\bdiscover\b", "Discover"),
]

_LINE_ITEM_SKIP_RE = re.compile(
    r"\b(total|subtotal|tax|tip|discount|amount due|balance|change|cash|visa|mastercard|amex|receipt|invoice)\b",
    re.IGNORECASE,
)
_LINE_ITEM_RE = re.compile(r"^\s*(?P<label>[A-Za-z][A-Za-z0-9 &'\-.,/#]{2,}?)\s+\$?(?P<amount>-?\d{1,6}(?:,\d{3})*\.\d{2})\s*$")


def _parse_date(text: str) -> str:
    for fmt, pattern in _DATE_FMTS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return date.today().isoformat()


def _parse_amount(text: str) -> float:
    fallback_amounts: list[float] = []
    for pattern, authoritative in _AMOUNT_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            try:
                val = float(m.group(1).replace(",", ""))
                if authoritative:
                    return val
                fallback_amounts.append(val)
            except (ValueError, IndexError):
                continue
    return max(fallback_amounts) if fallback_amounts else 0.0


def _parse_payment(text: str) -> str:
    t_lower = text.lower()
    for pattern, method in _PAYMENT_PATTERNS:
        if re.search(pattern, t_lower):
            return method
    return "Unknown"


def _extract_vendor(text: str) -> str:
    """Heuristically extract vendor name from the first non-empty line."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        first = re.sub(r"[^A-Za-z0-9 &'\-,.]", " ", lines[0]).strip()
        # Reject lines that look like dates or addresses
        if first and len(first) < 80 and not re.match(r"^\d", first):
            return first[:60]
    return "Unknown Vendor"


def parse_line_items(text: str) -> list[dict]:
    items: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _LINE_ITEM_SKIP_RE.search(line):
            continue
        if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", line):
            continue
        match = _LINE_ITEM_RE.match(line)
        if not match:
            continue
        label = re.sub(r"\s+", " ", match.group("label")).strip(" -")
        amount = round(float(match.group("amount").replace(",", "")), 2)
        if label and amount > 0:
            items.append({"description": label[:80], "amount": amount})
    return items[:50]


# ─────────────────────────── Core API ────────────────────────────

def classify_vendor(vendor: str) -> str:
    """Map a vendor name string to one of the expense categories."""
    v_lower = vendor.lower()
    for keywords, category in VENDOR_RULES:
        if any(kw in v_lower for kw in keywords):
            return category
    return "Other"


def scan_receipt(text_content: str) -> dict:
    """
    Parse text extracted from a receipt.

    Returns:
        {
            "date": "2026-05-19",
            "vendor": "Adobe Inc",
            "amount": 54.99,
            "currency": "USD",
            "category": "Software",
            "payment_method": "Visa",
            "notes": ""
        }
    """
    text = text_content.strip()
    vendor = _extract_vendor(text)
    amount = _parse_amount(text)
    txn_date = _parse_date(text)
    category = classify_vendor(vendor)
    payment = _parse_payment(text)

    # Currency detection
    currency = "USD"
    if re.search(r"\b(EUR|€)\b", text):
        currency = "EUR"
    elif re.search(r"\b(GBP|£)\b", text):
        currency = "GBP"
    elif re.search(r"\bCAD\b", text):
        currency = "CAD"

    # Collect notable lines for notes
    line_items = parse_line_items(text)
    notable = [
        ln.strip() for ln in text.splitlines()
        if re.search(r"total|subtotal|tax|tip|discount|fee", ln, re.IGNORECASE)
    ]
    notes = " | ".join(notable[:4])

    return {
        "date": txn_date,
        "vendor": vendor,
        "amount": amount,
        "currency": currency,
        "category": category,
        "payment_method": payment,
        "line_items": line_items,
        "line_item_total": round(sum(item["amount"] for item in line_items), 2),
        "notes": notes,
    }


def scan_text_arg(raw: str) -> dict:
    """
    Parse the --text "vendor amount date [category]" shorthand.

    Examples:
        "Adobe 54.99 2026-05-19 Software"
        "Delta Airlines 342.00 2026-05-15"
        "Starbucks 8.75"
    """
    parts = raw.strip().split()
    if len(parts) < 2:
        raise ValueError(f"--text needs at least 'vendor amount', got: {raw!r}")

    # Locate the first numeric token as the amount
    amount = 0.0
    amount_idx = -1
    for i, p in enumerate(parts):
        try:
            amount = float(p.replace(",", "").lstrip("$"))
            amount_idx = i
            break
        except ValueError:
            continue

    if amount_idx == -1:
        raise ValueError(f"No numeric amount found in: {raw!r}")

    vendor = " ".join(parts[:amount_idx]).strip() or "Unknown"
    remaining = parts[amount_idx + 1 :]

    txn_date = date.today().isoformat()
    category_override: Optional[str] = None
    for tok in remaining:
        if re.match(r"\d{4}-\d{2}-\d{2}$", tok):
            txn_date = tok
        elif tok.capitalize() in CATEGORIES:
            category_override = tok.capitalize()

    return {
        "date": txn_date,
        "vendor": vendor,
        "amount": amount,
        "currency": "USD",
        "category": category_override or classify_vendor(vendor),
        "payment_method": "Unknown",
        "line_items": [],
        "line_item_total": 0.0,
        "notes": f"cli entry: {raw}",
    }


# ─────────────────────────── Ledger integration ────────────────────────────

# Category → approximate ledger account code (LISZA COA)
_CATEGORY_ACCOUNT: dict[str, str] = {
    "Software":  "540",
    "Travel":    "530",
    "Meals":     "525",
    "Office":    "510",
    "Equipment": "560",
    "Services":  "550",
    "Other":     "590",
}


def insert_into_ledger(expense: dict, conn: sqlite3.Connection) -> Optional[int]:
    """
    Insert a scanned expense as a pending_inbox row (non-destructive).
    Status is 'new' so it waits for human review before posting.
    Returns the new row ID or None on failure.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL DEFAULT (datetime('now')),
            source TEXT NOT NULL,
            raw_path TEXT,
            parsed_json TEXT,
            suggested_account TEXT,
            suggested_amount REAL,
            status TEXT NOT NULL DEFAULT 'new'
                CHECK(status IN ('new','reviewed','posted','rejected')),
            entry_id INTEGER REFERENCES entries(id),
            notes TEXT
        )
        """
    )
    payload = json.dumps({
        "vendor": expense["vendor"],
        "date": expense["date"],
        "total": expense["amount"],
        "currency": expense["currency"],
        "category_hint": expense["category"],
        "payment_method": expense["payment_method"],
        "line_items": expense.get("line_items", []),
        "line_item_total": expense.get("line_item_total", 0.0),
        "coded": True,
        "notes": expense.get("notes", ""),
    })
    suggested_acct = _CATEGORY_ACCOUNT.get(expense["category"], "590")
    try:
        cur = conn.execute(
            """
            INSERT INTO pending_inbox
                (source, parsed_json, suggested_account, suggested_amount, status, notes)
            VALUES (?, ?, ?, ?, 'new', ?)
            """,
            (
                "receipt_scanner",
                payload,
                suggested_acct,
                expense["amount"],
                f"Auto-scanned: {expense['vendor']} {expense['date']}",
            ),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.OperationalError as exc:
        print(f"  [warn] Ledger insert skipped: {exc}", file=sys.stderr)
        return None


# ─────────────────────────── Reporting ────────────────────────────

def format_report(expenses: list[dict], source_labels: list[str]) -> str:
    """Return a formatted text expense report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "=" * 70,
        "  EXPENSE REPORT",
        f"  Generated : {now}",
        f"  Receipts  : {len(expenses)}",
        "=" * 70,
        "",
        f"{'Date':<12} {'Vendor':<28} {'Amount':>10}  {'Category':<12} {'Payment'}",
        "-" * 70,
    ]

    total = 0.0
    by_category: dict[str, float] = {}
    for exp in expenses:
        vendor = exp["vendor"][:27]
        amt = exp["amount"]
        total += amt
        cat = exp["category"]
        by_category[cat] = by_category.get(cat, 0.0) + amt
        lines.append(
            f"{exp['date']:<12} {vendor:<28} ${amt:>9.2f}  {cat:<12} {exp['payment_method']}"
        )

    lines += [
        "-" * 70,
        f"{'TOTAL':<12} {'':<28} ${total:>9.2f}",
        "",
        "Category Breakdown:",
    ]
    for cat in CATEGORIES:
        if cat in by_category:
            pct = by_category[cat] / total * 100 if total else 0.0
            bar = "#" * int(pct / 5)
            lines.append(f"  {cat:<14} ${by_category[cat]:>9.2f}  {pct:5.1f}%  {bar}")

    lines += ["", "=" * 70]
    return "\n".join(lines)


def write_csv(expenses: list[dict], source_labels: list[str], csv_path: Path) -> None:
    fields = ["source", "date", "vendor", "amount", "currency",
              "category", "payment_method", "notes"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for exp, label in zip(expenses, source_labels):
            row = {**exp, "source": label}
            writer.writerow(row)
    print(f"\nCSV saved → {csv_path}")


# ─────────────────────────── CLI ────────────────────────────

def _collect_pdfs(paths: list[str]) -> list[Path]:
    pdfs: list[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            pdfs.extend(sorted(pp.glob("*.pdf")))
        elif pp.suffix.lower() == ".pdf":
            pdfs.append(pp)
        else:
            print(f"[skip] Not a PDF or directory: {p}", file=sys.stderr)
    return pdfs


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Scan PDF receipts and build an expense report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("paths", nargs="*", help="PDF files or folders")
    parser.add_argument(
        "--text", metavar="ENTRY",
        help='Manual entry: "Vendor amount [date] [category]"',
    )
    parser.add_argument("--csv", metavar="PATH", help="Save to CSV file")
    parser.add_argument(
        "--no-ledger", action="store_true",
        help="Skip inserting into ledger.db",
    )
    args = parser.parse_args(argv)

    expenses: list[dict] = []
    labels: list[str] = []

    if args.text:
        try:
            exp = scan_text_arg(args.text)
            expenses.append(exp)
            labels.append("cli:--text")
        except ValueError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            sys.exit(1)

    if args.paths:
        pdfs = _collect_pdfs(args.paths)
        if not pdfs:
            print("[warn] No PDF files found in given paths.", file=sys.stderr)
        for pdf_path in pdfs:
            print(f"[scan] {pdf_path.name} ... ", end="", flush=True)
            try:
                text = extract_text_from_pdf(pdf_path)
                exp = scan_receipt(text)
                expenses.append(exp)
                labels.append(str(pdf_path))
                print(f"OK  ${exp['amount']:.2f}  {exp['category']}")
            except Exception as exc:
                print(f"ERROR: {exc}", file=sys.stderr)

    if not expenses:
        parser.print_help()
        sys.exit(0)

    print()
    print(format_report(expenses, labels))

    if args.csv:
        write_csv(expenses, labels, Path(args.csv))

    if not args.no_ledger and DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            inserted = sum(
                1 for exp in expenses if insert_into_ledger(exp, conn) is not None
            )
            conn.close()
            print(f"\nLedger: {inserted}/{len(expenses)} expenses queued in pending_inbox.")
        except Exception as exc:
            print(f"[warn] Ledger error (non-fatal): {exc}", file=sys.stderr)


# ─────────────────────────── Self-test ────────────────────────────

def _self_test() -> None:
    print("=" * 70)
    print("SELF-TEST: receipt_scanner.py")
    print("=" * 70)
    print()

    CASES: list[tuple[str, str, float, str]] = [
        (
            "Adobe Inc\n"
            "1600 Amphitheatre Pkwy\n"
            "Date: 2026-05-19\n"
            "Invoice #INV-00123\n"
            "\n"
            "Creative Cloud All Apps  $54.99\n"
            "Tax: $4.40\n"
            "Total: $59.39\n"
            "Visa ending 4242",
            "Adobe Inc",
            59.39,
            "Software",
        ),
        (
            "Delta Airlines\n"
            "Receipt Date: 05/15/2026\n"
            "Flight DL 1234 SFO -> ATL\n"
            "Ticket: $342.00\n"
            "Baggage: $35.00\n"
            "Total Charged: $377.00\n"
            "Amex ending 3782",
            "Delta Airlines",
            377.00,
            "Travel",
        ),
        (
            "Blue Bottle Coffee\n"
            "Mission District SF\n"
            "2026-05-18 09:14\n"
            "Cortado          $5.50\n"
            "Almond Croissant $4.25\n"
            "Total $9.75\n"
            "Cash",
            "Blue Bottle Coffee",
            9.75,
            "Meals",
        ),
        (
            "Staples Business Advantage\n"
            "May 17 2026\n"
            "Printer Paper   $28.00\n"
            "Pens            $12.99\n"
            "Total: $40.99\n"
            "Mastercard",
            "Staples Business Advantage",
            40.99,
            "Office",
        ),
        (
            "Best Buy\n"
            "Order: 2026-05-10\n"
            "Dell Monitor 27in  $349.99\n"
            "Total: $349.99\n"
            "Visa",
            "Best Buy",
            349.99,
            "Equipment",
        ),
        (
            "Toptal LLC\n"
            "Invoice Date: 2026-04-30\n"
            "Freelance backend developer — April\n"
            "Total: $3200.00\n"
            "Bank Transfer",
            "Toptal LLC",
            3200.00,
            "Services",
        ),
    ]

    passed = 0
    for text, exp_vendor_prefix, exp_amount, exp_category in CASES:
        r = scan_receipt(text)
        vendor_ok = exp_vendor_prefix.lower() in r["vendor"].lower()
        amount_ok = abs(r["amount"] - exp_amount) < 0.01
        cat_ok = r["category"] == exp_category
        ok = vendor_ok and amount_ok and cat_ok
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            detail = []
            if not vendor_ok:
                detail.append(f"vendor={r['vendor']!r} (expected prefix {exp_vendor_prefix!r})")
            if not amount_ok:
                detail.append(f"amount={r['amount']:.2f} (expected {exp_amount:.2f})")
            if not cat_ok:
                detail.append(f"category={r['category']!r} (expected {exp_category!r})")
            print(f"  [{status}] {exp_vendor_prefix:<28} — {', '.join(detail)}")
            continue
        print(
            f"  [{status}] {exp_vendor_prefix:<28} "
            f"${r['amount']:.2f}  {r['category']:<12}  {r['payment_method']}"
        )

    print()
    print("--- scan_text_arg tests ---")
    TEXT_CASES = [
        ("Adobe 54.99 2026-05-19 Software", "Adobe", 54.99, "Software"),
        ("Delta Airlines 342.00 2026-05-15", "Delta Airlines", 342.00, "Travel"),
        ("Starbucks 8.75 2026-05-18", "Starbucks", 8.75, "Meals"),
        ("Best Buy 499.00 Equipment", "Best Buy", 499.00, "Equipment"),
        ("Toptal 1500.00 Services", "Toptal", 1500.00, "Services"),
    ]
    for raw, exp_v, exp_a, exp_c in TEXT_CASES:
        r = scan_text_arg(raw)
        ok = (r["vendor"] == exp_v
              and abs(r["amount"] - exp_a) < 0.01
              and r["category"] == exp_c)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"  [{status}] {raw!r:<45} → {r['vendor']} ${r['amount']:.2f} {r['category']}")

    total_tests = len(CASES) + len(TEXT_CASES)
    print()

    # Full formatted report from CASES
    expenses = [scan_receipt(t) for t, *_ in CASES]
    labels = [v for _, v, *_ in CASES]
    print(format_report(expenses, labels))

    # Ledger insert test (non-destructive — pending_inbox only)
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            row_id = insert_into_ledger(expenses[0], conn)
            conn.close()
            status = "PASS" if row_id else "WARN (no row_id)"
            print(f"[ledger-insert] pending_inbox row_id={row_id}  [{status}]")
            if row_id:
                passed += 1
                total_tests += 1
        except Exception as exc:
            print(f"[ledger-insert] SKIP ({exc})")
    else:
        print(f"[ledger-insert] SKIP (DB not found at {DB_PATH})")

    print()
    print(f"Result: {passed}/{total_tests} passed")
    if passed == total_tests:
        print("ALL PASS")
    else:
        print("SOME FAILURES — review above")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        _self_test()
    else:
        main()
