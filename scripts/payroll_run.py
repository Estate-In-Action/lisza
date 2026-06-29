#!/usr/bin/env python3
"""Execute a pay run: compute each employee, post one balanced entry, record it.

Opens only the requested client's ledger.db. YTD is computed from prior posted
payroll_lines in the same calendar year, so statutory caps reset on Jan 1.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta

import tenancy
from payroll_engine import Employee, compute_paycheck

_EMP_COLS = ("id", "name", "filing_status", "work_state", "residence_state",
             "pay_frequency", "annual_salary", "w4_multiple_jobs",
             "w4_dependents_amt", "w4_other_income", "w4_deductions",
             "w4_extra_withholding")

_SUM_FIELDS = ("gross", "fed_wh", "ss_ee", "ss_er", "medi_ee", "medi_er",
               "addl_medi", "state_wh", "futa", "suta", "net")


def _as_date(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


def _employee_from_row(row) -> Employee:
    m = dict(zip(_EMP_COLS, row))
    return Employee(
        name=m["name"], filing_status=m["filing_status"],
        work_state=m["work_state"], residence_state=m["residence_state"],
        annual_salary=m["annual_salary"], pay_frequency=m["pay_frequency"],
        w4_multiple_jobs=m["w4_multiple_jobs"],
        w4_dependents_amt=m["w4_dependents_amt"],
        w4_other_income=m["w4_other_income"], w4_deductions=m["w4_deductions"],
        w4_extra_withholding=m["w4_extra_withholding"])


def _ytd_gross(con, employee_id: int, year: int, before: str) -> float:
    return con.execute(
        "SELECT COALESCE(SUM(l.gross),0) FROM payroll_lines l "
        "JOIN payroll_runs r ON r.id=l.run_id "
        "WHERE l.employee_id=? AND substr(r.period_end,1,4)=? "
        "AND r.period_end < ?",
        (employee_id, str(year), before)).fetchone()[0]


def _post_entry(con, entity_id: int, pay_date: str, tot: dict) -> int:
    con.execute(
        "INSERT INTO entries(entry_date,description,payee,source,status,"
        "entity_id,is_intercompany) VALUES(?,?,?,?, 'posted', ?, 0)",
        (pay_date, "Payroll", "Payroll", "payroll", entity_id))
    eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    fica = round(tot["ss_ee"] + tot["ss_er"] + tot["medi_ee"]
                 + tot["medi_er"] + tot["addl_medi"], 2)
    employer_tax = round(tot["ss_er"] + tot["medi_er"] + tot["futa"]
                         + tot["suta"], 2)
    debits = [("555", round(tot["gross"], 2)), ("556", employer_tax)]
    credits = [("245", tot["fed_wh"]), ("246", fica), ("247", tot["state_wh"]),
               ("248", tot["futa"]), ("249", tot["suta"]), ("102", tot["net"])]
    for acct, amt in debits:
        if amt:
            con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,?,0)",
                        (eid, acct, round(amt, 2)))
    for acct, amt in credits:
        if amt:
            con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,0,?)",
                        (eid, acct, round(amt, 2)))
    return eid


def run_payroll(slug: str, entity_id: int, period_end,
                pay_date=None, period_start=None):
    """Compute + post one pay run for one entity. Returns run_id, or None if no
    active employees."""
    pe = _as_date(period_end)
    pd_ = _as_date(pay_date) if pay_date else pe
    ps = _as_date(period_start) if period_start else (pe - timedelta(days=13))

    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.execute("PRAGMA foreign_keys=ON")
    try:
        rows = con.execute(
            f"SELECT {','.join(_EMP_COLS)} FROM employees "
            "WHERE entity_id=? AND active=1 ORDER BY id", (entity_id,)).fetchall()
        if not rows:
            return None

        con.execute(
            "INSERT INTO payroll_runs(entity_id,period_start,period_end,pay_date) "
            "VALUES(?,?,?,?)",
            (entity_id, ps.isoformat(), pe.isoformat(), pd_.isoformat()))
        run_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

        year = pe.year
        tot = defaultdict(float)
        computed = []
        for row in rows:
            emp = _employee_from_row(row)
            gross = round(emp.annual_salary / 26, 2)
            ytd = _ytd_gross(con, row[0], year, pe.isoformat())
            res = compute_paycheck(emp, gross, ytd, year)
            computed.append((row[0], res))
            for f in _SUM_FIELDS:
                tot[f] += getattr(res, f)
        tot = {k: round(v, 2) for k, v in tot.items()}

        eid = _post_entry(con, entity_id, pd_.isoformat(), tot)
        for emp_id, res in computed:
            con.execute(
                "INSERT INTO payroll_lines(run_id,employee_id,entry_id,gross,"
                "fed_wh,ss_ee,ss_er,medi_ee,medi_er,addl_medi,state_wh,futa,"
                "suta,net) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, emp_id, eid, res.gross, res.fed_wh, res.ss_ee,
                 res.ss_er, res.medi_ee, res.medi_er, res.addl_medi,
                 res.state_wh, res.futa, res.suta, res.net))
        con.commit()
        return run_id
    finally:
        con.close()
