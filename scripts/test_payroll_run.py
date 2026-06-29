import os, sqlite3
import pytest

import tenancy
import payroll_run


@pytest.fixture
def book(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LISZA_HOME", str(home))
    # COA loader reads tenancy.COA_PATH; point it at the repo coa.csv
    repo_coa = os.path.join(os.path.dirname(__file__), "..", "coa.csv")
    monkeypatch.setattr(tenancy, "COA_PATH", __import__("pathlib").Path(repo_coa))
    tenancy.register_client(slug="acme", display_name="Acme", entity_type="llc")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    ent = con.execute("SELECT id FROM entities WHERE is_default=1").fetchone()[0]
    con.execute(
        "INSERT INTO employees(entity_id,name,filing_status,work_state,"
        "residence_state,annual_salary) VALUES(?,?,?,?,?,?)",
        (ent, "Worker A", "single", "PA", "PA", 52000.0))
    con.commit()
    con.close()
    return "acme", ent


def _splits_balance(con):
    dr, cr = con.execute(
        "SELECT ROUND(SUM(dr),2), ROUND(SUM(cr),2) FROM splits").fetchone()
    return dr, cr


def test_run_posts_one_balanced_entry_with_lines(book):
    slug, ent = book
    rid = payroll_run.run_payroll(slug, ent, "2025-01-17")
    con = sqlite3.connect(tenancy.resolve_db(slug))
    assert con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM payroll_lines WHERE run_id=?",
                       (rid,)).fetchone()[0] == 1
    assert con.execute(
        "SELECT COUNT(*) FROM entries WHERE source='payroll'").fetchone()[0] == 1
    dr, cr = _splits_balance(con)
    assert dr == cr and dr > 0
    accts = {r[0] for r in con.execute("SELECT DISTINCT account FROM splits")}
    assert {"555", "556", "245", "246", "247"} <= accts   # PA has state tax
    eid = con.execute("SELECT entry_id FROM payroll_lines WHERE run_id=?",
                      (rid,)).fetchone()[0]
    assert eid is not None
    con.close()


def test_ytd_accumulates_across_runs(book):
    slug, ent = book
    payroll_run.run_payroll(slug, ent, "2025-01-17")
    payroll_run.run_payroll(slug, ent, "2025-01-31")
    con = sqlite3.connect(tenancy.resolve_db(slug))
    assert con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0] == 2
    futa_vals = [r[0] for r in con.execute(
        "SELECT futa FROM payroll_lines ORDER BY id")]
    assert futa_vals[0] > 0 and futa_vals[1] > 0
    con.close()


def test_run_opens_only_requested_client(book, tmp_path):
    slug, ent = book
    tenancy.register_client(slug="other", display_name="Other", entity_type="llc")
    payroll_run.run_payroll(slug, ent, "2025-01-17")
    con = sqlite3.connect(tenancy.resolve_db("other"))
    assert con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0] == 0
    con.close()
