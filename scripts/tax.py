#!/usr/bin/env python3
"""Sales-tax / VAT engine for LISZA client books.

Three capabilities, all TDD:

  * Rate tables (`tax_rates`) — per-book named rates with a jurisdiction and a
    kind ('sales' | 'vat'), CRUD via set_rate/get_rates.

  * Taxed documents — record_tax_invoice / record_tax_bill compute the tax and
    post the split. A taxed sale credits revenue at *net* and the tax-payable
    control (230) at the *tax* portion, while the invoice/AR carries the full
    *gross* so payments, dunning, and credit notes all see the true amount owed:

        record_tax_invoice   Dr 110 A/R (gross)
                             Cr <revenue> (net)
                             Cr 230 Sales Tax Payable (tax)      [output tax]

        record_tax_bill      Dr <expense> (net)
                             Dr 230 Sales Tax Payable (tax)      [input tax]
                             Cr 200 A/P (gross)

    Amounts are 'exclusive' (the given amount is net, tax added on top) or
    'inclusive' (the given amount is gross, tax carved out).

  * Reports — tax_liability(start,end) nets output tax collected against input
    tax paid over a period (the movement on the 230 control); form_1099_report
    totals disbursements to flagged vendors and flags those at/over the $600
    reporting threshold.

Every taxed document records a row in `tax_transactions` so the liability report
never has to re-derive tax from GL splits. Corrections are new documents, never
edits-in-place (PLAN.md principle #2).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date

import tenancy
from post_entry import post_json

AR_CONTROL = "110"
AP_CONTROL = "200"
DEFAULT_TAX_ACCOUNT = "230"   # Sales Tax Payable
FORM_1099_THRESHOLD = 600.0

TAX_SCHEMA = """
CREATE TABLE IF NOT EXISTS tax_rates (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    rate_pct REAL NOT NULL,
    jurisdiction TEXT,
    kind TEXT NOT NULL DEFAULT 'sales' CHECK(kind IN ('sales','vat')),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tax_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('output','input')),
    doc_type TEXT NOT NULL,
    doc_id INTEGER,
    party TEXT,
    rate_code TEXT,
    rate_pct REAL NOT NULL,
    taxable REAL NOT NULL,
    tax REAL NOT NULL,
    tax_date TEXT NOT NULL,
    tax_account TEXT NOT NULL,
    entry_id INTEGER REFERENCES entries(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tax_txn_date ON tax_transactions(tax_date);

CREATE TABLE IF NOT EXISTS vendor_1099 (
    party TEXT PRIMARY KEY,
    box TEXT NOT NULL DEFAULT 'NEC',
    active INTEGER NOT NULL DEFAULT 1
);
"""


def ensure_tax_schema(con: sqlite3.Connection) -> None:
    con.executescript(TAX_SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    ensure_tax_schema(con)
    return con


def compute_tax(amount: float, rate_pct: float, *, inclusive: bool = False) -> dict:
    """Split an amount into net / tax / gross at a given rate.

    exclusive: amount is net, tax is added on top.
    inclusive: amount is gross, tax is carved out.
    """
    amount = round(float(amount), 2)
    rate = float(rate_pct)
    if inclusive:
        net = round(amount / (1 + rate / 100.0), 2)
        tax = round(amount - net, 2)
        gross = amount
    else:
        net = amount
        tax = round(amount * rate / 100.0, 2)
        gross = round(net + tax, 2)
    return {"net": net, "tax": tax, "gross": gross}


# ---- rate table ---------------------------------------------------------

def set_rate(slug: str, *, code: str, name: str, rate_pct: float,
             jurisdiction: str | None = None, kind: str = "sales",
             active: bool = True) -> dict:
    con = _book(slug)
    try:
        con.execute(
            "INSERT INTO tax_rates(code, name, rate_pct, jurisdiction, kind, active)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(code) DO UPDATE SET name=excluded.name,"
            " rate_pct=excluded.rate_pct, jurisdiction=excluded.jurisdiction,"
            " kind=excluded.kind, active=excluded.active",
            (code, name, float(rate_pct), jurisdiction, kind, 1 if active else 0),
        )
        con.commit()
        row = con.execute("SELECT * FROM tax_rates WHERE code=?", (code,)).fetchone()
        return dict(row)
    finally:
        con.close()


def get_rates(slug: str, *, active_only: bool = True) -> list[dict]:
    con = _book(slug)
    try:
        q = "SELECT code, name, rate_pct, jurisdiction, kind, active FROM tax_rates"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY code"
        return [dict(r) for r in con.execute(q)]
    finally:
        con.close()


def _resolve_rate(con: sqlite3.Connection, rate_code: str | None,
                  rate_pct: float | None) -> tuple[str | None, float]:
    if rate_code:
        row = con.execute(
            "SELECT rate_pct FROM tax_rates WHERE code=? AND active=1", (rate_code,)
        ).fetchone()
        if not row:
            raise ValueError(f"unknown or inactive tax rate: {rate_code}")
        return rate_code, float(row["rate_pct"])
    if rate_pct is not None:
        return None, float(rate_pct)
    raise ValueError("a rate_code or rate_pct is required")


def _check_account(con: sqlite3.Connection, code: str, want_type: str) -> None:
    row = con.execute(
        "SELECT type FROM accounts WHERE code=? AND active=1", (code,)
    ).fetchone()
    if not row:
        raise ValueError(f"account '{code}' not found")
    if row["type"] != want_type:
        raise ValueError(f"account '{code}' is {row['type']}, not {want_type}")


# ---- taxed documents ----------------------------------------------------

def record_tax_invoice(
    slug: str,
    *,
    party: str,
    amount: float,
    revenue_code: str,
    rate_code: str | None = None,
    rate_pct: float | None = None,
    inclusive: bool = False,
    issue_date: str | None = None,
    due_date: str | None = None,
    tax_account: str = DEFAULT_TAX_ACCOUNT,
    memo: str | None = None,
) -> dict:
    """Create a taxed sales invoice (stored at gross) and post its 3-way split."""
    issue = issue_date or date.today().isoformat()
    con = _book(slug)
    try:
        rc, rate = _resolve_rate(con, rate_code, rate_pct)
        _check_account(con, revenue_code, "income")
        parts = compute_tax(amount, rate, inclusive=inclusive)
        if parts["tax"] <= 0:
            raise ValueError("computed tax is $0 — use the untaxed invoice path")

        posted = post_json(
            {
                "entry_date": issue,
                "description": memo or f"Taxed invoice — {party}",
                "payee": party,
                "post": True,
                "splits": [
                    {"account": AR_CONTROL, "dr": parts["gross"], "cr": 0, "memo": "A/R"},
                    {"account": revenue_code, "dr": 0, "cr": parts["net"], "memo": "revenue"},
                    {"account": tax_account, "dr": 0, "cr": parts["tax"], "memo": "sales tax"},
                ],
            },
            str(tenancy.resolve_db(slug)),
        )
        entry_id = posted["entry_id"]
        cur = con.execute(
            "INSERT INTO invoices(party, issue_date, due_date, amount, status, entry_id, memo)"
            " VALUES (?,?,?,?, 'open', ?, ?)",
            (party, issue, due_date or issue, parts["gross"], entry_id,
             memo or "Taxed invoice"),
        )
        invoice_id = int(cur.lastrowid)
        con.execute(
            "INSERT INTO tax_transactions"
            "(kind, doc_type, doc_id, party, rate_code, rate_pct, taxable, tax,"
            " tax_date, tax_account, entry_id)"
            " VALUES ('output','invoice',?,?,?,?,?,?,?,?,?)",
            (invoice_id, party, rc, rate, parts["net"], parts["tax"], issue,
             tax_account, entry_id),
        )
        con.commit()
        return {
            "invoice_id": invoice_id, "entry_id": entry_id, "status": posted["status"],
            "rate_code": rc, "rate_pct": rate, **parts,
        }
    finally:
        con.close()


def record_tax_bill(
    slug: str,
    *,
    party: str,
    amount: float,
    expense_code: str,
    rate_code: str | None = None,
    rate_pct: float | None = None,
    inclusive: bool = False,
    bill_date: str | None = None,
    due_date: str | None = None,
    tax_account: str = DEFAULT_TAX_ACCOUNT,
    memo: str | None = None,
) -> dict:
    """Create a taxed vendor bill (stored at gross) with recoverable input tax."""
    bdate = bill_date or date.today().isoformat()
    con = _book(slug)
    try:
        rc, rate = _resolve_rate(con, rate_code, rate_pct)
        _check_account(con, expense_code, "expense")
        parts = compute_tax(amount, rate, inclusive=inclusive)
        if parts["tax"] <= 0:
            raise ValueError("computed tax is $0 — use the untaxed bill path")

        posted = post_json(
            {
                "entry_date": bdate,
                "description": memo or f"Taxed bill — {party}",
                "payee": party,
                "post": True,
                "splits": [
                    {"account": expense_code, "dr": parts["net"], "cr": 0, "memo": "expense"},
                    {"account": tax_account, "dr": parts["tax"], "cr": 0, "memo": "input tax"},
                    {"account": AP_CONTROL, "dr": 0, "cr": parts["gross"], "memo": "A/P"},
                ],
            },
            str(tenancy.resolve_db(slug)),
        )
        entry_id = posted["entry_id"]
        cur = con.execute(
            "INSERT INTO bills(party, issue_date, due_date, amount, status, entry_id, memo)"
            " VALUES (?,?,?,?, 'unpaid', ?, ?)",
            (party, bdate, due_date or bdate, parts["gross"], entry_id,
             memo or "Taxed bill"),
        )
        bill_id = int(cur.lastrowid)
        con.execute(
            "INSERT INTO tax_transactions"
            "(kind, doc_type, doc_id, party, rate_code, rate_pct, taxable, tax,"
            " tax_date, tax_account, entry_id)"
            " VALUES ('input','bill',?,?,?,?,?,?,?,?,?)",
            (bill_id, party, rc, rate, parts["net"], parts["tax"], bdate,
             tax_account, entry_id),
        )
        con.commit()
        return {
            "bill_id": bill_id, "entry_id": entry_id, "status": posted["status"],
            "rate_code": rc, "rate_pct": rate, **parts,
        }
    finally:
        con.close()


# ---- reports ------------------------------------------------------------

def tax_liability(slug: str, *, start: str, end: str) -> dict:
    """Net tax payable over [start, end]: output tax collected − input tax paid."""
    con = _book(slug)
    try:
        rows = con.execute(
            "SELECT kind, rate_code, tax_account, "
            "COALESCE(SUM(taxable),0) AS taxable, COALESCE(SUM(tax),0) AS tax "
            "FROM tax_transactions WHERE tax_date BETWEEN ? AND ? "
            "GROUP BY kind, rate_code, tax_account",
            (start, end),
        ).fetchall()
        output_tax = round(sum(r["tax"] for r in rows if r["kind"] == "output"), 2)
        input_tax = round(sum(r["tax"] for r in rows if r["kind"] == "input"), 2)
        by_rate = [
            {"kind": r["kind"], "rate_code": r["rate_code"],
             "taxable": round(r["taxable"], 2), "tax": round(r["tax"], 2)}
            for r in rows
        ]
        return {
            "start": start, "end": end,
            "output_tax": output_tax,
            "input_tax": input_tax,
            "net_payable": round(output_tax - input_tax, 2),
            "by_rate": by_rate,
        }
    finally:
        con.close()


def set_vendor_1099(slug: str, party: str, *, box: str = "NEC",
                    active: bool = True) -> None:
    con = _book(slug)
    try:
        con.execute(
            "INSERT INTO vendor_1099(party, box, active) VALUES (?,?,?)"
            " ON CONFLICT(party) DO UPDATE SET box=excluded.box, active=excluded.active",
            (party, box, 1 if active else 0),
        )
        con.commit()
    finally:
        con.close()


def form_1099_report(slug: str, *, year: int) -> dict:
    """Total disbursements to flagged vendors in `year`; flag those >= $600."""
    con = _book(slug)
    try:
        has_pay = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='payments'"
        ).fetchone()
        vendors = []
        flagged = con.execute(
            "SELECT party, box FROM vendor_1099 WHERE active=1 ORDER BY party"
        ).fetchall()
        for v in flagged:
            total = 0.0
            if has_pay:
                row = con.execute(
                    "SELECT COALESCE(SUM(amount),0) AS t FROM payments "
                    "WHERE direction='disbursement' AND party=? "
                    "AND strftime('%Y', payment_date)=?",
                    (v["party"], str(year)),
                ).fetchone()
                total = round(float(row["t"] or 0), 2)
            vendors.append({
                "party": v["party"],
                "box": v["box"],
                "total_paid": total,
                "reportable": total >= FORM_1099_THRESHOLD,
            })
        return {"year": year, "threshold": FORM_1099_THRESHOLD, "vendors": vendors}
    finally:
        con.close()


# ---- CLI ----------------------------------------------------------------

def _cli_tax_json(argv) -> None:
    """`tax-json --client <slug> --payload <json>` → JSON result.

    payload = {"action": "invoice"|"bill"|"rate"|"rates"|"liability"|"1099"|
                          "flag_1099"|"compute", ...}
    """
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="tax.py tax-json")
    ap.add_argument("--client", required=True)
    ap.add_argument("--payload")
    a = ap.parse_args(argv)
    try:
        raw = a.payload if a.payload is not None else __import__("sys").stdin.read()
        p = json.loads(raw)
        if not Path(tenancy.resolve_db(a.client)).exists():
            print(json.dumps({"error": f"client '{a.client}' not found"}))
            return
        action = p.get("action") or "rates"
        if action == "compute":
            print(json.dumps({"ok": True, **compute_tax(
                p["amount"], p["rate_pct"], inclusive=bool(p.get("inclusive")))}))
        elif action == "invoice":
            print(json.dumps({"ok": True, **record_tax_invoice(
                a.client, party=p.get("party") or "", amount=p.get("amount") or 0,
                revenue_code=str(p.get("revenue_code") or ""),
                rate_code=p.get("rate_code"), rate_pct=p.get("rate_pct"),
                inclusive=bool(p.get("inclusive")), issue_date=p.get("issue_date"),
                due_date=p.get("due_date"), memo=p.get("memo"))}))
        elif action == "bill":
            print(json.dumps({"ok": True, **record_tax_bill(
                a.client, party=p.get("party") or "", amount=p.get("amount") or 0,
                expense_code=str(p.get("expense_code") or ""),
                rate_code=p.get("rate_code"), rate_pct=p.get("rate_pct"),
                inclusive=bool(p.get("inclusive")), bill_date=p.get("bill_date"),
                due_date=p.get("due_date"), memo=p.get("memo"))}))
        elif action == "rate":
            print(json.dumps({"ok": True, "rate": set_rate(
                a.client, code=p["code"], name=p.get("name") or p["code"],
                rate_pct=p["rate_pct"], jurisdiction=p.get("jurisdiction"),
                kind=p.get("kind") or "sales")}))
        elif action == "rates":
            print(json.dumps({"ok": True, "rates": get_rates(a.client)}))
        elif action == "liability":
            print(json.dumps({"ok": True, **tax_liability(
                a.client, start=p["start"], end=p["end"])}))
        elif action == "flag_1099":
            set_vendor_1099(a.client, p["party"], box=p.get("box") or "NEC",
                            active=bool(p.get("active", True)))
            print(json.dumps({"ok": True, "party": p["party"]}))
        elif action == "1099":
            print(json.dumps({"ok": True, **form_1099_report(
                a.client, year=int(p["year"]))}))
        else:
            print(json.dumps({"error": f"unknown action: {action}"}))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))


def main() -> int:
    argv = __import__("sys").argv[1:]
    if argv and argv[0] == "tax-json":
        _cli_tax_json(argv[1:])
        return 0

    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("rates", help="list tax rates")
    r.add_argument("client_slug")

    sr = sub.add_parser("set-rate", help="add/update a tax rate")
    sr.add_argument("client_slug")
    sr.add_argument("--code", required=True)
    sr.add_argument("--name", required=True)
    sr.add_argument("--rate", type=float, required=True)
    sr.add_argument("--jurisdiction")
    sr.add_argument("--kind", choices=["sales", "vat"], default="sales")

    li = sub.add_parser("liability", help="tax liability over a period")
    li.add_argument("client_slug")
    li.add_argument("--start", required=True)
    li.add_argument("--end", required=True)

    f = sub.add_parser("form-1099", help="1099 vendor totals for a year")
    f.add_argument("client_slug")
    f.add_argument("--year", type=int, required=True)

    args = ap.parse_args()
    if args.cmd == "rates":
        print(json.dumps(get_rates(args.client_slug), indent=2))
    elif args.cmd == "set-rate":
        print(json.dumps(set_rate(
            args.client_slug, code=args.code, name=args.name, rate_pct=args.rate,
            jurisdiction=args.jurisdiction, kind=args.kind), indent=2))
    elif args.cmd == "liability":
        print(json.dumps(tax_liability(
            args.client_slug, start=args.start, end=args.end), indent=2))
    elif args.cmd == "form-1099":
        print(json.dumps(form_1099_report(args.client_slug, year=args.year), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
