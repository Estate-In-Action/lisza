import sqlite3

import payroll_rollup as pr


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE entities(id INTEGER PRIMARY KEY, name TEXT, type TEXT,
                              is_default INT, active INT);
        CREATE TABLE employees(id INTEGER PRIMARY KEY, entity_id INT, name TEXT,
                               active INT);
        CREATE TABLE payroll_runs(id INTEGER PRIMARY KEY, entity_id INT,
                                  period_start TEXT, period_end TEXT, pay_date TEXT);
        CREATE TABLE payroll_lines(
            id INTEGER PRIMARY KEY, run_id INT, employee_id INT, entry_id INT,
            gross REAL, fed_wh REAL, ss_ee REAL, ss_er REAL, medi_ee REAL,
            medi_er REAL, addl_medi REAL, state_wh REAL, futa REAL, suta REAL,
            net REAL);
        """)
    con.commit()
    return con


def test_latest_year_none_when_no_runs():
    con = _con()
    assert pr.latest_payroll_year(con) is None


def test_latest_year_picks_max():
    con = _con()
    con.executescript(
        """INSERT INTO payroll_runs(id,entity_id,pay_date)
             VALUES(1,1,'2024-03-15'),(2,1,'2026-01-10'),(3,1,'2025-07-01');""")
    con.commit()
    assert pr.latest_payroll_year(con) == "2026"


def test_build_payroll_status_none_when_empty():
    con = _con()
    out = pr.build_payroll(con)
    assert out == {"status": "none", "message": "No payroll runs on file"}
