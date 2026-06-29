#!/usr/bin/env python3
"""Generalized synthetic seeder driven by a ClientProfile.

Posts a balanced multi-year double-entry book per entity:
opening capital, fixed assets + depreciation, recurring opex, revenue
(retainers/project invoices -> AR), variable COGS (vendor bills -> AP),
optional payroll, owner draws, quarterly tax. For multi-entity profiles
with intercompany_sweeps, posts a flagged inter-company cash sweep that
nets out of consolidated reports. ZERO real data; deterministic (seeded).
"""
from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, timedelta

import tenancy
import client_profiles
from client_profiles import ClientProfile

SEED = 42
START = date(2022, 1, 1)
END = date(2026, 6, 9)


def _months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m = 1 if m == 12 else m + 1
        y = y + 1 if m == 1 else y


def _day(first, rng, lo=0, hi=27):
    return first + timedelta(days=rng.randint(lo, hi))


def _entity_ids(con) -> list[int]:
    return [r[0] for r in con.execute(
        "SELECT id FROM entities WHERE active=1 ORDER BY id")]


def _booked(con, entity_id, d, desc, payee, dr_acct, cr_acct, amt,
            src="synthetic", intercompany=0):
    con.execute(
        "INSERT INTO entries(entry_date,description,payee,source,status,entity_id,is_intercompany) "
        "VALUES(?,?,?,?, 'posted', ?, ?)",
        (d.isoformat(), desc, payee, src, entity_id, intercompany))
    eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,?,0)",
                (eid, dr_acct, round(amt, 2)))
    con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,0,?)",
                (eid, cr_acct, round(amt, 2)))
    return eid


def seed(profile: ClientProfile, *, slug: str) -> dict:
    db = tenancy.resolve_db(slug)
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")

    # ensure the profile's entities exist (default already present from register)
    existing = {r[0] for r in con.execute("SELECT name FROM entities")}
    con.execute("UPDATE entities SET name=? WHERE is_default=1", (profile.entities[0],))
    for name in profile.entities[1:]:
        if name not in existing:
            con.execute("INSERT INTO entities(name,type,is_default) VALUES(?, 'location', 0)",
                        (name,))
    con.commit()

    rng = random.Random(SEED)
    n_inv = n_bill = 0
    months = list(_months(START, END))
    y0 = START.year
    eids = _entity_ids(con)

    for idx, (y, m) in enumerate(months):
        first = date(y, m, 1)
        years_in = (y - y0) + (m - 1) / 12.0
        growth = profile.yoy ** years_in
        for entity_id in eids:
            rev_target = profile.base_monthly_rev * growth * profile.season[m]
            if idx == 0:
                _booked(con, entity_id, first, "Owner opening capital", "Owner",
                        "102", "300", 60000.0, "owner")
            # recurring opex
            _booked(con, entity_id, _day(first, rng, 1, 8), "Office rent",
                    rng.choice(profile.vendors), "520", "102", 1800.0)
            # revenue -> AR
            for _ in range(rng.randint(2, 4)):
                issue = _day(first, rng, 0, 25)
                if issue > END:
                    continue
                amt = round(rev_target / 3 * rng.uniform(0.7, 1.3), 2)
                cust = rng.choice(profile.customers)
                rev_acct = rng.choice(profile.revenue_accounts)
                inv_eid = _booked(con, entity_id, issue, f"Invoice — {cust}", cust,
                                  "110", rev_acct, amt, "invoice")
                paid = rng.random() < 0.7
                status, paid_date = "open", None
                if paid:
                    pd = issue + timedelta(days=rng.randint(8, 40))
                    if pd <= END:
                        _booked(con, entity_id, pd, f"Payment — {cust}", cust,
                                "102", "110", amt, "ar_payment")
                        status, paid_date = "paid", pd.isoformat()
                con.execute("INSERT INTO invoices(party,issue_date,due_date,amount,status,paid_date,entry_id) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (cust, issue.isoformat(),
                             (issue + timedelta(days=30)).isoformat(), amt, status, paid_date, inv_eid))
                n_inv += 1
            # variable COGS -> AP
            vend = rng.choice(profile.vendors)
            bamt = round(rev_target * profile.cogs_frac * rng.uniform(0.8, 1.2), 2)
            issue = _day(first, rng, 0, 25)
            if issue <= END and bamt >= 1:
                bill_eid = _booked(con, entity_id, issue, f"Materials — {vend}", vend,
                                   profile.cogs_account, "200", bamt, "bill")
                paid = rng.random() < 0.6
                status, paid_date = "unpaid", None
                if paid:
                    pd = issue + timedelta(days=rng.randint(8, 40))
                    if pd <= END:
                        _booked(con, entity_id, pd, f"Bill paid — {vend}", vend,
                                "200", "102", bamt, "ap_payment")
                        status, paid_date = "paid", pd.isoformat()
                con.execute("INSERT INTO bills(party,issue_date,due_date,amount,status,paid_date,entry_id) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (vend, issue.isoformat(),
                             (issue + timedelta(days=30)).isoformat(), bamt, status, paid_date, bill_eid))
                n_bill += 1
            # owner draw
            if profile.owner_draw_monthly > 0:
                dd = _day(first, rng, 18, 27)
                if dd <= END:
                    _booked(con, entity_id, dd, "Owner draw", "Owner",
                            "300", "102", profile.owner_draw_monthly, "owner")
            # quarterly estimated tax
            if m in (3, 6, 9, 12):
                td = _day(first, rng, 10, 15)
                if td <= END:
                    _booked(con, entity_id, td, "Estimated income tax", "Tax Authority",
                            "592", "102", round(rev_target * 0.15, 2), "tax")

        # parent inter-company management fee (multi-entity only): the parent
        # (eids[0]) charges each location for shared back-office. Parent books
        # inter-company revenue; the location books an inter-company expense.
        # Both legs are flagged so consolidated reports net the fee out — a pure
        # cash transfer would be invisible to consolidation, but a fee inflates
        # gross revenue/expense and must be eliminated.
        if profile.intercompany_sweeps and len(eids) > 1:
            sd = _day(first, rng, 25, 27)
            if sd <= END:
                for loc in eids[1:]:
                    fee = round(rng.uniform(2000, 6000), 2)
                    _booked(con, eids[0], sd, "Inter-company management fee (revenue)",
                            "Internal", "102", "410", fee, "intercompany", intercompany=1)
                    _booked(con, loc, sd, "Inter-company management fee (expense)",
                            "Internal", "500", "102", fee, "intercompany", intercompany=1)

    con.commit()
    dr, cr = con.execute(
        "SELECT ROUND(SUM(dr),2),ROUND(SUM(cr),2) FROM splits").fetchone()
    con.close()

    # real payroll history replaces the lump-sum wage placeholder
    import seed_payroll
    seed_payroll.seed_payroll(slug=slug)

    return {"slug": slug, "invoices": n_inv, "bills": n_bill, "dr": dr, "cr": cr}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug", choices=["guitar-works", "harborside-group", "jb-design"])
    args = parser.parse_args()
    profile = {"guitar-works": client_profiles.GUITAR_WORKS,
               "harborside-group": client_profiles.HARBORSIDE_GROUP,
               "jb-design": client_profiles.JB_DESIGN}[args.slug]
    print(seed(profile, slug=args.slug))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
