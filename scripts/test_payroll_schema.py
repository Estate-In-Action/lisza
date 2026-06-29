import sqlite3
import book_schema

def _tables(con):
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

def test_payroll_tables_created_and_idempotent():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE entries(id INTEGER PRIMARY KEY, entry_date TEXT)")
    book_schema.ensure_book_schema(con)
    book_schema.ensure_book_schema(con)  # idempotent second call
    t = _tables(con)
    assert {"employees", "payroll_runs", "payroll_lines"} <= t
    cols = {r[1] for r in con.execute("PRAGMA table_info(employees)")}
    assert {"entity_id", "filing_status", "work_state", "residence_state",
            "annual_salary", "w4_dependents_amt", "w4_extra_withholding"} <= cols
    lcols = {r[1] for r in con.execute("PRAGMA table_info(payroll_lines)")}
    assert {"run_id", "employee_id", "entry_id", "gross", "fed_wh", "ss_ee",
            "ss_er", "medi_ee", "medi_er", "addl_medi", "state_wh", "futa",
            "suta", "net"} <= lcols
