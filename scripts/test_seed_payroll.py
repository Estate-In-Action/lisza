import os, sqlite3
import pytest
from pathlib import Path

import tenancy
import seed_payroll


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("LISZA_HOME", str(h))
    repo_coa = os.path.join(os.path.dirname(__file__), "..", "coa.csv")
    monkeypatch.setattr(tenancy, "COA_PATH", Path(repo_coa))
    return h


def test_seed_payroll_creates_roster_and_runs(home):
    tenancy.register_client(slug="guitar-works", display_name="Guitar Works",
                            entity_type="llc")
    n = seed_payroll.seed_payroll(slug="guitar-works")
    con = sqlite3.connect(tenancy.resolve_db("guitar-works"))
    assert con.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0] > 0
    assert n > 0
    dr, cr = con.execute("SELECT ROUND(SUM(dr),2), ROUND(SUM(cr),2) FROM splits").fetchone()
    assert dr == cr
    con.close()


def test_seed_payroll_noop_for_non_payroll_client(home):
    tenancy.register_client(slug="jb-design", display_name="J.B. Design",
                            entity_type="llc")
    n = seed_payroll.seed_payroll(slug="jb-design")
    assert n == 0
    con = sqlite3.connect(tenancy.resolve_db("jb-design"))
    assert con.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0
    con.close()


def test_seed_payroll_is_idempotent(home):
    tenancy.register_client(slug="guitar-works", display_name="Guitar Works",
                            entity_type="llc")
    first_runs = seed_payroll.seed_payroll(slug="guitar-works")
    con = sqlite3.connect(tenancy.resolve_db("guitar-works"))
    first_counts = {
        "employees": con.execute("SELECT COUNT(*) FROM employees").fetchone()[0],
        "payroll_runs": con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0],
        "payroll_lines": con.execute("SELECT COUNT(*) FROM payroll_lines").fetchone()[0],
        "payroll_entries": con.execute("SELECT COUNT(*) FROM entries WHERE source=\x27payroll\x27").fetchone()[0],
    }
    con.close()

    second_runs = seed_payroll.seed_payroll(slug="guitar-works")
    con = sqlite3.connect(tenancy.resolve_db("guitar-works"))
    second_counts = {
        "employees": con.execute("SELECT COUNT(*) FROM employees").fetchone()[0],
        "payroll_runs": con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0],
        "payroll_lines": con.execute("SELECT COUNT(*) FROM payroll_lines").fetchone()[0],
        "payroll_entries": con.execute("SELECT COUNT(*) FROM entries WHERE source=\x27payroll\x27").fetchone()[0],
    }
    dr, cr = con.execute("SELECT ROUND(SUM(dr),2), ROUND(SUM(cr),2) FROM splits").fetchone()
    con.close()

    assert first_runs == second_runs
    assert first_counts == second_counts
    assert dr == cr
