#!/usr/bin/env python3
"""Generate a 4-year fully-synthetic SMALL-BUSINESS demo book for LISZA.

Models a small creative / marketing services studio. Fake parties, amounts,
dates — ZERO real financial data. Deterministic (seeded). Posts balanced
double-entry from 2022-01 → 2026-06:

  - opening owner capital + fixed assets (computers, furniture)
  - straight-line monthly depreciation
  - recurring fixed opex (rent, payroll, software, insurance, internet ...)
  - revenue: monthly retainers + project invoices (A/R), ~18%/yr growth with
    seasonal swing (soft summer, strong Q4)
  - variable cost of services (subcontractors, media, printing) as vendor
    bills (A/P), scaled to revenue
  - A/R + A/P aging (recent items likelier still open)
  - quarterly estimated income tax + monthly owner draws + interest income

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
START = date(2022, 1, 1)
END = date(2026, 6, 9)

# ── the studio's book of business ──
CLIENTS = ["Maple & Co Consulting", "Riverside Studios LLC", "Northgate Retail",
           "Bluepeak Ventures", "Harborline Design", "Summit Yoga Collective",
           "Greenfield Landscaping", "Tidewater Marketing", "Oakhaus Interiors",
           "Lantern Logistics", "Cedar & Pine Realty", "Brightwave Media"]
# anchor clients carry a steady monthly retainer
RETAINER_CLIENTS = ["Maple & Co Consulting", "Tidewater Marketing", "Cedar & Pine Realty"]

VENDORS = ["Acme Software Co", "Metro Print Shop", "Cloudhost Inc", "Skyline Coworking",
           "Bluebird Office Supplies", "Citywide Internet", "Rapid Courier",
           "Lumen Utilities", "Apex Insurance", "Pinnacle Accounting Tools",
           "Northstar Subcontractors", "Vivid Print House", "Adwell Media Buying",
           "Freelance Collective"]

# seasonal revenue multiplier by calendar month (agency: soft summer, strong Q4)
SEASON = {1: 0.90, 2: 0.93, 3: 1.02, 4: 1.06, 5: 1.05, 6: 0.96,
          7: 0.84, 8: 0.88, 9: 1.07, 10: 1.13, 11: 1.16, 12: 1.10}
YOY = 1.18              # ~18% annual revenue growth
BASE_MONTHLY_REV = 30000.0   # first-year average monthly revenue


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


def _day(first, rng, lo=0, hi=27):
    return first + timedelta(days=rng.randint(lo, hi))


def _paid_prob(days_old, recent, mid, old):
    """Older receivables/payables are far likelier already settled."""
    if days_old > 60:
        return old
    if days_old > 30:
        return mid
    return recent


def main() -> int:
    if not DB.exists():
        print(f"ERROR: {DB} missing — run init_ledger.py first")
        return 1
    rng = random.Random(SEED)
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")

    n_exp = n_inv = n_bill = 0
    dep_per_month = 0.0  # straight-line depreciation, set after asset purchase

    months = list(_months(START, END))
    y0 = START.year
    card_bal = {"210": 0.0, "211": 0.0}  # revolving balances, paid down monthly
    online_bal = 0.0                      # online-payments float, swept to checking

    for idx, (y, m) in enumerate(months):
        first = date(y, m, 1)
        years_in = (y - y0) + (m - 1) / 12.0
        growth = YOY ** years_in
        rev_target = BASE_MONTHLY_REV * growth * SEASON[m]

        # ───────────────────────── opening month ─────────────────────────
        if idx == 0:
            _booked(con, first, "Owner opening capital contribution", "Owner",
                    "102", "300", 100000.0, "owner")
            # buy fixed assets out of checking
            _booked(con, _day(first, rng, 1, 4), "Computers & software — startup",
                    "Acme Software Co", "161", "102", 16800.0, "asset")
            _booked(con, _day(first, rng, 1, 6), "Office furniture & equipment",
                    "Bluebird Office Supplies", "160", "102", 7200.0, "asset")
            # straight-line over 36 months on the $24,000 of fixed assets
            dep_per_month = round(24000.0 / 36.0, 2)
            # seed a little starting cash buffer in savings
            _booked(con, _day(first, rng, 5, 9), "Transfer to business savings",
                    "Internal", "103", "102", 8000.0, "transfer")

        # ──────────────────── monthly depreciation ───────────────────────
        # straight-line: 36 months only, so accumulated dep == asset cost
        if dep_per_month > 0 and idx < 36:
            dd = date(y, m, 28)
            if dd <= END:
                _booked(con, dd, "Monthly depreciation",
                        "Depreciation", "595", "165", dep_per_month, "depreciation")
                n_exp += 1

        # ──────────────────── recurring fixed opex ───────────────────────
        # (amounts drift up slowly with the business)
        opex = [
            ("520", "Skyline Coworking", "Office rent", 2200 + 70 * idx / 6),
            ("555", "Payroll", "Salaries & wages", 8600 * (1 + 0.04 * years_in)),
            ("556", "Payroll Taxes", "Payroll taxes", 1180 * (1 + 0.04 * years_in)),
            ("540", "Acme Software Co", "Software subscriptions", 430 + 12 * idx / 6),
            ("545", "Citywide Internet", "Internet & phone", 175),
            ("570", "Apex Insurance", "Business insurance", 245),
            ("575", "Business Checking", "Bank & merchant fees", 38 + rng.uniform(0, 22)),
            ("580", "Pinnacle Accounting Tools", "Dues & memberships", 65),
        ]
        for acct, payee, desc, amt in opex:
            d = _day(first, rng, 1, 10)
            if d > END:
                continue
            pay_from = "210" if acct in ("540", "545") and rng.random() < 0.6 else "102"
            amt = round(amt, 2)
            _booked(con, d, desc, payee, acct, pay_from, amt)
            if pay_from in card_bal:
                card_bal[pay_from] += amt
            n_exp += 1

        # utilities every other month-ish
        ud = _day(first, rng, 2, 22)
        if rng.random() < 0.7 and ud <= END:
            _booked(con, ud, "Utilities", "Lumen Utilities",
                    "535", "102", round(rng.uniform(140, 320), 2))
            n_exp += 1

        # ── variable, smaller direct expenses (card or checking) ──
        small = [
            ("525", "Meals & client entertainment", 40, 240),
            ("530", "Travel & lodging", 120, 900),
            ("510", "Office supplies", 25, 220),
            ("565", "Advertising & marketing", 200, 1400),
            ("560", "Equipment & tools", 60, 650),
            ("585", "Continuing education", 0, 380),
            ("590", "Miscellaneous expense", 15, 160),
        ]
        for acct, desc, lo, hi in small:
            for _ in range(rng.randint(0, 2)):
                d = _day(first, rng)
                if d > END or hi == 0:
                    continue
                amt = round(rng.uniform(max(lo, 1), max(hi, lo + 1)), 2)
                pay_from = rng.choice(["210", "211", "102"])
                _booked(con, d, desc, rng.choice(VENDORS), acct, pay_from, amt)
                if pay_from in card_bal:
                    card_bal[pay_from] += amt
                n_exp += 1

        # ─────────────────── revenue: monthly retainers ──────────────────
        retainer_each = rev_target * 0.28 / max(1, len(RETAINER_CLIENTS))
        for client in RETAINER_CLIENTS:
            issue = _day(first, rng, 1, 5)
            if issue > END:
                continue
            amt = round(retainer_each * rng.uniform(0.92, 1.08), 2)
            due = issue + timedelta(days=15)
            inv_eid = _booked(con, issue, f"Monthly retainer — {client}", client,
                              "110", "420", amt, "invoice")
            # retainers almost always paid promptly; only the newest stay open
            paid = rng.random() < _paid_prob((END - issue).days, 0.70, 0.90, 0.98)
            status, paid_date = "open", None
            if paid:
                pd = issue + timedelta(days=rng.randint(3, 18))
                if pd <= END:
                    into = rng.choice(["102", "106"])
                    _booked(con, pd, f"Retainer payment — {client}", client,
                            into, "110", amt, "ar_payment")
                    if into == "106":
                        online_bal += amt
                    status, paid_date = "paid", pd.isoformat()
            con.execute("INSERT INTO invoices(party, issue_date, due_date, amount, status, paid_date, entry_id) "
                        "VALUES(?,?,?,?,?,?,?)", (client, issue.isoformat(), due.isoformat(), amt, status, paid_date, inv_eid))
            n_inv += 1

        # ─────────────────── revenue: project invoices ───────────────────
        proj_pool = rev_target * 0.72
        n_projects = rng.randint(2, 4)
        for _ in range(n_projects):
            issue = _day(first, rng, 0, 25)
            if issue > END:
                continue
            amt = round(proj_pool / n_projects * rng.uniform(0.7, 1.3), 2)
            due = issue + timedelta(days=30)
            client = rng.choice(CLIENTS)
            rev_acct = rng.choice(["400", "410", "430"])
            inv_eid = _booked(con, issue, f"Project invoice — {client}", client,
                              "110", rev_acct, amt, "invoice")
            paid = rng.random() < _paid_prob((END - issue).days, 0.40, 0.75, 0.96)
            status, paid_date = "open", None
            if paid:
                pd = issue + timedelta(days=rng.randint(12, 55))
                if pd <= END:
                    into = rng.choice(["102", "106"])
                    _booked(con, pd, f"Payment received — {client}", client,
                            into, "110", amt, "ar_payment")
                    if into == "106":
                        online_bal += amt
                    status, paid_date = "paid", pd.isoformat()
            con.execute("INSERT INTO invoices(party, issue_date, due_date, amount, status, paid_date, entry_id) "
                        "VALUES(?,?,?,?,?,?,?)", (client, issue.isoformat(), due.isoformat(), amt, status, paid_date, inv_eid))
            n_inv += 1

        # ───────────── variable cost of services (vendor bills, A/P) ──────
        # subcontractors + media + printing scale with revenue
        cos = [
            ("500", "Northstar Subcontractors", "Subcontractor services", rev_target * 0.20),
            ("505", "Adwell Media Buying", "Client media & ad spend", rev_target * 0.07),
            ("515", "Vivid Print House", "Printing & production", rev_target * 0.04),
            ("550", "Pinnacle Accounting Tools", "Professional services", 600.0),
        ]
        for acct, vendor, desc, base in cos:
            issue = _day(first, rng, 0, 25)
            if issue > END or base < 1:
                continue
            amt = round(base * rng.uniform(0.7, 1.25), 2)
            due = issue + timedelta(days=30)
            bill_eid = _booked(con, issue, f"{desc} — {vendor}", vendor, acct, "200", amt, "bill")
            paid = rng.random() < _paid_prob((END - issue).days, 0.50, 0.80, 0.95)
            status, paid_date = "unpaid", None
            if paid:
                pd = issue + timedelta(days=rng.randint(8, 45))
                if pd <= END:
                    pay_from = rng.choice(["102", "210"])
                    _booked(con, pd, f"Bill paid — {vendor}", vendor, "200",
                            pay_from, amt, "ap_payment")
                    if pay_from in card_bal:
                        card_bal[pay_from] += amt
                    status, paid_date = "paid", pd.isoformat()
            con.execute("INSERT INTO bills(party, issue_date, due_date, amount, status, paid_date, entry_id) "
                        "VALUES(?,?,?,?,?,?,?)", (vendor, issue.isoformat(), due.isoformat(), amt, status, paid_date, bill_eid))
            n_bill += 1

        # ───────────────── sweep online-payments float to checking ───────
        if online_bal > 50:
            swd = _day(first, rng, 25, 27)
            if swd <= END:
                amt = round(online_bal * rng.uniform(0.90, 1.0), 2)
                _booked(con, swd, "Payout from online payments", "Stripe",
                        "102", "106", amt, "transfer")
                online_bal -= amt

        # ───────────────── pay down credit cards (revolving) ─────────────
        for card in ("210", "211"):
            if card_bal[card] > 50:
                pdd = _day(first, rng, 20, 27)
                if pdd <= END:
                    paydown = round(card_bal[card] * rng.uniform(0.82, 0.97), 2)
                    _booked(con, pdd, "Credit card payment", "Card Issuer",
                            card, "102", paydown, "cc_payment")
                    card_bal[card] -= paydown

        # ───────────────── interest income on savings ────────────────────
        intd = date(y, m, 27)
        if rng.random() < 0.9 and intd <= END:
            _booked(con, intd, "Interest earned — business savings",
                    "Business Savings", "103", "440", round(rng.uniform(6, 70), 2), "interest")

        # ───────────────── owner draw (monthly) ──────────────────────────
        draw = 1400 * (1 + 0.03 * years_in)
        dd = _day(first, rng, 18, 27)
        if dd <= END:
            _booked(con, dd, "Owner draw", "Owner", "300", "102", round(draw, 2), "owner")

        # ───────────────── quarterly estimated income tax ────────────────
        if m in (3, 6, 9, 12):
            tax = round(rev_target * 0.18, 2)  # ~a quarter's tax on profit
            td = _day(first, rng, 10, 15)
            if td <= END:
                _booked(con, td, "Estimated income tax payment", "Tax Authority",
                        "592", "102", tax, "tax")
                n_exp += 1

        # ───────────────── occasional savings sweep ──────────────────────
        if rng.random() < 0.4:
            sd = _day(first, rng, 20, 27)
            if sd <= END:
                _booked(con, sd, "Transfer to business savings", "Internal",
                        "103", "102", round(rng.uniform(1500, 5000), 2), "transfer")

    # ── auto-categorization rules (payee → account) ──
    for pat, acct in [("Skyline Coworking", "520"), ("Cloudhost", "540"),
                      ("Lumen Utilities", "535"), ("Apex Insurance", "570"),
                      ("Metro Print Shop", "515"), ("Citywide Internet", "545")]:
        con.execute("INSERT INTO payee_rules(pattern, account_code) VALUES(?,?)", (pat, acct))

    con.commit()
    dr, cr = con.execute("SELECT ROUND(SUM(dr),2), ROUND(SUM(cr),2) FROM splits").fetchone()
    n_entries = con.execute("SELECT COUNT(*) FROM entries WHERE status='posted'").fetchone()[0]
    open_ar = con.execute("SELECT COUNT(*), ROUND(COALESCE(SUM(amount),0),2) FROM invoices WHERE status='open'").fetchone()
    open_ap = con.execute("SELECT COUNT(*), ROUND(COALESCE(SUM(amount),0),2) FROM bills WHERE status='unpaid'").fetchone()
    span = con.execute("SELECT MIN(entry_date), MAX(entry_date) FROM entries").fetchone()
    con.close()
    print(f"seeded {n_entries} posted entries ({n_exp} expenses, {n_inv} invoices, {n_bill} bills)")
    print(f"  span {span[0]} → {span[1]}")
    print(f"  splits dr={dr} cr={cr} (must be equal)")
    print(f"  open A/R: {open_ar[0]} invoices, ${open_ar[1]}")
    print(f"  unpaid A/P: {open_ap[0]} bills, ${open_ap[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
