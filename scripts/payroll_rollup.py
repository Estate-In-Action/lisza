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
