#!/usr/bin/env python3
"""Generate a multi-year fully-synthetic demo book for LISZA.

Fake parties, amounts, dates — ZERO real financial data. Deterministic (seeded).
Posts balanced double-entry across ~3.5 years (2023-01 → 2026-06) and populates
AR (client invoices, some open → aging) and AP (vendor bills, some unpaid).

Run AFTER init_ledger.py:  python3 scripts/seed_synthetic.py
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "ledger.db"
SEED = 42
START = date(2023, 1, 5)
END = date(2026, 6, 28)

CLIENTS = ["Maple & Co Consulting", "Riverside Studios LLC", "Northgate Retail",
           "Bluepeak Ventures", "Harborline Design", "Summit Yoga Collective",
           "Greenfield Landscaping", "Tidewater Marketing", "Oakhaus Interiors",
           "Lantern Logistics"]
VENDORS = ["Corner Cafe", "Acme Software Co", "Metro Print Shop", "Cloudhost Inc",
           "Bluebird Office Supplies", "Citywide Internet", "Fleet Fuel Stop",
           "Evergreen Cleaning", "Pinnacle Accounting Tools", "Daily Grind Roasters",
           "Skyline Coworking", "Rapid Courier", "Lumen Utilities", "Apex Insurance"]


def _codes(con, typ):
    return [r[0] for r in con.execute(
        "SELECT code FROM accounts WHERE type=? AND active=1 ORDER BY code", (typ,))]


def _prefer(codes, wanted):
    hit = [c for c in wanted if c in codes]
    return hit or codes[:2]


def _entry(con, d, desc, payee, src="synthetic"):
    cur = con.execute(
        "INSERT INTO entries(entry_date, description, payee, source, status) "
        "VALUES(?,?,?,?, 'pending')", (d.isoformat(), desc, payee, src))
    return cur.lastrowid


def _split(con, eid, account, *, dr=0.0, cr=0.0):
    con.execute("INSERT INTO splits(entry_id, account, dr, cr) VALUES(?,?,?,?)",
                (eid, account, round(dr, 2), round(cr, 2)))


def _post(con, eid):
    con.execute("UPDATE entries SET status='posted', posted_at=datetime('now') WHERE id=?", (eid,))


def _booked(con, d, desc, payee, dr_acct, cr_acct, amt, src="synthetic"):
    """One balanced 2-split posted entry. Returns entry id."""
    eid = _entry(con, d, desc, payee, src)
    _split(con, eid, dr_acct, dr=amt)
    _split(con, eid, cr_acct, cr=amt)
    _post(con, eid)
    return eid


def _months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def main() -> int:
    if not DB.exists():
        print(f"ERROR: {DB} missing — run init_ledger.py first")
        return 1
    rng = random.Random(SEED)
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")

    cash = _prefer(_codes(con, "asset"), ["102", "101", "103"])
    cards = _prefer(_codes(con, "liability"), ["210", "211"])
    income = _prefer(_codes(con, "income"), ["430", "431", "400"])
    expense = _codes(con, "expense")
    AR = "110" if ("110",) in [(c,) for c in _codes(con, "asset")] else cash[0]
    AP = "200" if "200" in _codes(con, "liability") else cards[0]

    n_exp = n_inv = n_bill = 0
    for (y, m) in _months(START, END):
        first = date(y, m, 1)
        # ── expenses (direct, paid from cash or card) ──
        for _ in range(rng.randint(6, 11)):
            d = first + timedelta(days=rng.randint(0, 27))
            if d > END:
                continue
            amt = round(rng.uniform(8, 780), 2)
            _booked(con, d, rng.choice(VENDORS), rng.choice(VENDORS),
                    rng.choice(expense), rng.choice(cash + cards), amt)
            n_exp += 1

        # ── client invoices (A/R) ──
        for _ in range(rng.randint(2, 4)):
            issue = first + timedelta(days=rng.randint(0, 25))
            if issue > END:
                continue
            amt = round(rng.uniform(800, 7500), 2)
            due = issue + timedelta(days=30)
            client = rng.choice(CLIENTS)
            inv_eid = _booked(con, issue, f"Invoice — {client}", client, AR, rng.choice(income), amt, "invoice")
            # ~72% get paid (clear A/R); rest stay open → aging. Recent ones likelier open.
            days_old = (END - issue).days
            paid = rng.random() < (0.72 if days_old > 40 else 0.45)
            paid_date = None
            status = "open"
            if paid:
                pd = issue + timedelta(days=rng.randint(8, 55))
                if pd <= END:
                    _booked(con, pd, f"Payment received — {client}", client, rng.choice(cash), AR, amt, "ar_payment")
                    status, paid_date = "paid", pd.isoformat()
            con.execute("INSERT INTO invoices(party, issue_date, due_date, amount, status, paid_date, entry_id) "
                        "VALUES(?,?,?,?,?,?,?)", (client, issue.isoformat(), due.isoformat(), amt, status, paid_date, inv_eid))
            n_inv += 1

        # ── vendor bills (A/P) ──
        for _ in range(rng.randint(2, 5)):
            issue = first + timedelta(days=rng.randint(0, 25))
            if issue > END:
                continue
            amt = round(rng.uniform(120, 3200), 2)
            due = issue + timedelta(days=30)
            vendor = rng.choice(VENDORS)
            bill_eid = _booked(con, issue, f"Bill — {vendor}", vendor, rng.choice(expense), AP, amt, "bill")
            days_old = (END - issue).days
            paid = rng.random() < (0.78 if days_old > 40 else 0.5)
            paid_date = None
            status = "unpaid"
            if paid:
                pd = issue + timedelta(days=rng.randint(5, 50))
                if pd <= END:
                    _booked(con, pd, f"Bill paid — {vendor}", vendor, AP, rng.choice(cash), amt, "ap_payment")
                    status, paid_date = "paid", pd.isoformat()
            con.execute("INSERT INTO bills(party, issue_date, due_date, amount, status, paid_date, entry_id) "
                        "VALUES(?,?,?,?,?,?,?)", (vendor, issue.isoformat(), due.isoformat(), amt, status, paid_date, bill_eid))
            n_bill += 1

    # a couple of auto-categorization rules
    for pat, acct in [("Corner Cafe", "582"), ("Cloudhost", "550"), ("Lumen Utilities", "550")]:
        if acct in expense:
            con.execute("INSERT INTO payee_rules(pattern, account_code) VALUES(?,?)", (pat, acct))

    con.commit()
    dr, cr = con.execute("SELECT ROUND(SUM(dr),2), ROUND(SUM(cr),2) FROM splits").fetchone()
    n_entries = con.execute("SELECT COUNT(*) FROM entries WHERE status='posted'").fetchone()[0]
    open_ar = con.execute("SELECT COUNT(*), ROUND(COALESCE(SUM(amount),0),2) FROM invoices WHERE status='open'").fetchone()
    open_ap = con.execute("SELECT COUNT(*), ROUND(COALESCE(SUM(amount),0),2) FROM bills WHERE status='unpaid'").fetchone()
    con.close()
    print(f"seeded {n_entries} posted entries ({n_exp} expenses, {n_inv} invoices, {n_bill} bills)")
    print(f"  splits dr={dr} cr={cr} (must be equal)")
    print(f"  open A/R: {open_ar[0]} invoices, ${open_ar[1]}")
    print(f"  unpaid A/P: {open_ap[0]} bills, ${open_ap[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
