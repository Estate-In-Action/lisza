#!/usr/bin/env python3
"""Read-only IRS-form rollups (W-2, 941) over payroll_lines. No tax math —
every figure is recovered from withholding already in the table."""
from __future__ import annotations

import sqlite3


def latest_payroll_year(con: sqlite3.Connection) -> str | None:
    row = con.execute(
        "SELECT MAX(substr(pay_date, 1, 4)) FROM payroll_runs").fetchone()
    return row[0] if row and row[0] else None


def build_payroll(con: sqlite3.Connection) -> dict:
    year = latest_payroll_year(con)
    if year is None:
        return {"status": "none", "message": "No payroll runs on file"}
    raise NotImplementedError  # filled in Task 4


def w2_rollup(con: sqlite3.Connection, year: str) -> list[dict]:
    rows = con.execute(
        """SELECT e.name AS employee, ent.name AS entity,
                  SUM(pl.gross)     AS gross,
                  SUM(pl.fed_wh)    AS fed_wh,
                  SUM(pl.ss_ee)     AS ss_ee,
                  SUM(pl.medi_ee)   AS medi_ee,
                  SUM(pl.addl_medi) AS addl_medi,
                  SUM(pl.state_wh)  AS state_wh
           FROM payroll_lines pl
           JOIN payroll_runs pr ON pr.id = pl.run_id
           JOIN employees e ON e.id = pl.employee_id
           JOIN entities ent ON ent.id = e.entity_id
           WHERE substr(pr.pay_date, 1, 4) = ?
           GROUP BY pl.employee_id
           ORDER BY e.name""", (year,)).fetchall()
    out = []
    for r in rows:
        gross = round(r["gross"], 2)
        out.append({
            "employee": r["employee"],
            "entity": r["entity"],
            "box1_wages": gross,
            "box2_fed_wh": round(r["fed_wh"], 2),
            "box3_ss_wages": round(r["ss_ee"] / 0.062, 2),
            "box4_ss_tax": round(r["ss_ee"], 2),
            "box5_medi_wages": gross,
            "box6_medi_tax": round(r["medi_ee"] + r["addl_medi"], 2),
            "box16_state_wages": gross,
            "box17_state_tax": round(r["state_wh"], 2),
        })
    return out
