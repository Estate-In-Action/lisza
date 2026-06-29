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
    return {
        "status": "active",
        "year": int(year),
        "summary": payroll_summary(con, year),
        "w2": w2_rollup(con, year),
        "form941": form941_rollup(con, year),
    }


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


def form941_rollup(con: sqlite3.Connection, year: str) -> list[dict]:
    rows = con.execute(
        """SELECT ent.name AS entity,
                  ((CAST(substr(pr.pay_date, 6, 2) AS INTEGER) - 1) / 3) + 1 AS quarter,
                  SUM(pl.gross)     AS wages,
                  SUM(pl.fed_wh)    AS fed_wh,
                  SUM(pl.ss_ee + pl.ss_er)                       AS ss_tax,
                  SUM(pl.medi_ee + pl.medi_er + pl.addl_medi)    AS medi_tax,
                  SUM(pl.fed_wh + pl.ss_ee + pl.ss_er
                      + pl.medi_ee + pl.medi_er + pl.addl_medi)  AS total_liability,
                  COUNT(DISTINCT pr.id) AS run_count
           FROM payroll_lines pl
           JOIN payroll_runs pr ON pr.id = pl.run_id
           JOIN entities ent ON ent.id = pr.entity_id
           WHERE substr(pr.pay_date, 1, 4) = ?
           GROUP BY pr.entity_id, quarter
           ORDER BY ent.name, quarter""", (year,)).fetchall()
    return [{
        "entity": r["entity"],
        "quarter": int(r["quarter"]),
        "year": int(year),
        "wages": round(r["wages"], 2),
        "fed_wh": round(r["fed_wh"], 2),
        "ss_tax": round(r["ss_tax"], 2),
        "medi_tax": round(r["medi_tax"], 2),
        "total_liability": round(r["total_liability"], 2),
        "run_count": r["run_count"],
    } for r in rows]


def payroll_summary(con: sqlite3.Connection, year: str) -> dict:
    r = con.execute(
        """SELECT COUNT(DISTINCT pl.employee_id) AS employees,
                  COUNT(DISTINCT pl.run_id)       AS run_count,
                  SUM(pl.gross)  AS gross,
                  SUM(pl.net)    AS net,
                  SUM(pl.fed_wh) AS fed_wh,
                  SUM(pl.ss_ee + pl.medi_ee + pl.addl_medi) AS employee_fica,
                  SUM(pl.ss_er + pl.medi_er)                AS employer_fica,
                  SUM(pl.ss_er + pl.medi_er + pl.futa + pl.suta) AS employer_tax_total
           FROM payroll_lines pl
           JOIN payroll_runs pr ON pr.id = pl.run_id
           WHERE substr(pr.pay_date, 1, 4) = ?""", (year,)).fetchone()
    return {
        "employees": r["employees"] or 0,
        "run_count": r["run_count"] or 0,
        "gross": round(r["gross"] or 0.0, 2),
        "net": round(r["net"] or 0.0, 2),
        "fed_wh": round(r["fed_wh"] or 0.0, 2),
        "employee_fica": round(r["employee_fica"] or 0.0, 2),
        "employer_fica": round(r["employer_fica"] or 0.0, 2),
        "employer_tax_total": round(r["employer_tax_total"] or 0.0, 2),
    }
