#!/usr/bin/env python3
"""Seed biweekly payroll history for clients with employees. Synthetic only.

Rosters are keyed by client slug. Clients absent from ROSTERS (e.g. jb-design,
owner-draws only) are a no-op. Biweekly runs span the ledger data window.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import tenancy
import payroll_run

START = date(2022, 1, 1)
END = date(2026, 6, 9)

# Each roster entry: entity name + Employee fields. All synthetic.
ROSTERS = {
    "guitar-works": [
        {"entity": "Guitar Works", "name": "Dana Whitlock",
         "filing_status": "married_jointly", "work_state": "CA",
         "residence_state": "CA", "annual_salary": 72000.0,
         "w4_dependents_amt": 2000.0},
        {"entity": "Guitar Works", "name": "Marco Ferreira",
         "filing_status": "single", "work_state": "CA",
         "residence_state": "CA", "annual_salary": 58000.0},
        {"entity": "Guitar Works", "name": "Priya Anand",
         "filing_status": "single", "work_state": "CA",
         "residence_state": "CA", "annual_salary": 245000.0},
    ],
    "harborside-group": [
        {"entity": "Harborside Pier", "name": "Tom Vasquez",
         "filing_status": "married_jointly", "work_state": "PA",
         "residence_state": "PA", "annual_salary": 95000.0},
        {"entity": "Harborside Pier", "name": "Lena Kowalski",
         "filing_status": "single", "work_state": "PA",
         "residence_state": "PA", "annual_salary": 42000.0},
        {"entity": "Harborside Downtown", "name": "Sam Okafor",
         "filing_status": "head_of_household", "work_state": "PA",
         "residence_state": "PA", "annual_salary": 51000.0,
         "w4_dependents_amt": 2000.0},
        {"entity": "Harborside Downtown", "name": "Grace Lim",
         "filing_status": "single", "work_state": "PA",
         "residence_state": "FL", "annual_salary": 38000.0},
        {"entity": "Harborside Express", "name": "Hector Ruiz",
         "filing_status": "single", "work_state": "PA",
         "residence_state": "PA", "annual_salary": 47000.0},
        {"entity": "Harborside Express", "name": "Aisha Bello",
         "filing_status": "married_jointly", "work_state": "PA",
         "residence_state": "PA", "annual_salary": 63000.0},
    ],
}

_EMP_INSERT_FIELDS = ("entity_id", "name", "filing_status", "work_state",
                      "residence_state", "annual_salary", "w4_multiple_jobs",
                      "w4_dependents_amt", "w4_other_income", "w4_deductions",
                      "w4_extra_withholding")


def _entity_id(con, name: str) -> int:
    row = con.execute("SELECT id FROM entities WHERE name=?", (name,)).fetchone()
    return row[0] if row else con.execute(
        "SELECT id FROM entities WHERE is_default=1").fetchone()[0]


def _insert_roster(con, roster) -> set[int]:
    entity_ids = set()
    for r in roster:
        ent = _entity_id(con, r["entity"])
        entity_ids.add(ent)
        vals = {
            "entity_id": ent, "name": r["name"],
            "filing_status": r["filing_status"], "work_state": r["work_state"],
            "residence_state": r["residence_state"],
            "annual_salary": r["annual_salary"], "w4_multiple_jobs": 0,
            "w4_dependents_amt": r.get("w4_dependents_amt", 0.0),
            "w4_other_income": 0.0, "w4_deductions": 0.0,
            "w4_extra_withholding": 0.0,
        }
        cols = ",".join(_EMP_INSERT_FIELDS)
        ph = ",".join("?" * len(_EMP_INSERT_FIELDS))
        con.execute(f"INSERT INTO employees({cols}) VALUES({ph})",
                    tuple(vals[c] for c in _EMP_INSERT_FIELDS))
    con.commit()
    return entity_ids


def seed_payroll(slug: str) -> int:
    """Insert the client's roster and book biweekly runs. Returns run count."""
    roster = ROSTERS.get(slug)
    if not roster:
        return 0
    con = sqlite3.connect(tenancy.resolve_db(slug))
    try:
        entity_ids = sorted(_insert_roster(con, roster))
    finally:
        con.close()

    runs = 0
    d = START
    while d + timedelta(days=13) <= END:
        period_end = d + timedelta(days=13)
        for ent in entity_ids:
            if payroll_run.run_payroll(slug, ent, period_end) is not None:
                runs += 1
        d = d + timedelta(days=14)
    return runs
