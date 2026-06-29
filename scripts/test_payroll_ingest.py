import os, sqlite3
import pytest

import tenancy
import payroll_ingest


@pytest.fixture
def book(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LISZA_HOME", str(home))
    repo_coa = os.path.join(os.path.dirname(__file__), "..", "coa.csv")
    monkeypatch.setattr(tenancy, "COA_PATH",
                        __import__("pathlib").Path(repo_coa))
    tenancy.register_client(slug="acme", display_name="Acme", entity_type="llc")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    ent = con.execute("SELECT id FROM entities WHERE is_default=1").fetchone()[0]
    con.executemany(
        "INSERT INTO employees(entity_id,name,filing_status,work_state,"
        "residence_state,annual_salary) VALUES(?,?,?,?,?,?)",
        [(ent, "Worker A", "single", "PA", "PA", 52000.0),
         (ent, "Worker B", "single", "PA", "PA", 60000.0)])
    con.commit()
    ids = {r[1]: r[0] for r in con.execute("SELECT id,name FROM employees")}
    con.close()
    return "acme", ent, ids


def _line(name, gross, fed_wh, ss, medi, state):
    """A self-consistent register line (employer side mirrors employee side)."""
    net = round(gross - fed_wh - ss - medi - state, 2)
    return {"name": name, "gross": gross, "fed_wh": fed_wh,
            "ss_ee": ss, "ss_er": ss, "medi_ee": medi, "medi_er": medi,
            "addl_medi": 0.0, "state_wh": state, "futa": round(gross * 0.006, 2),
            "suta": 0.0, "net": net}


def _balance(con):
    return con.execute(
        "SELECT ROUND(SUM(dr),2), ROUND(SUM(cr),2) FROM splits").fetchone()


def test_import_books_register_verbatim(book):
    slug, ent, _ = book
    reg = [_line("Worker A", 2000.0, 180.0, 124.0, 29.0, 60.0),
           _line("Worker B", 2307.69, 240.0, 143.08, 33.46, 70.0)]
    rid = payroll_ingest.import_register(slug, ent, "2025-01-17", reg)
    con = sqlite3.connect(tenancy.resolve_db(slug))
    assert con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM payroll_lines WHERE run_id=?",
                       (rid,)).fetchone()[0] == 2
    assert con.execute(
        "SELECT COUNT(*) FROM entries WHERE source='payroll'").fetchone()[0] == 1
    # figures stored verbatim, not recomputed
    a_gross, a_fed, a_net = con.execute(
        "SELECT gross,fed_wh,net FROM payroll_lines l JOIN employees e "
        "ON e.id=l.employee_id WHERE e.name='Worker A'").fetchone()
    assert (a_gross, a_fed, a_net) == (2000.0, 180.0, 1607.0)
    con.close()


def test_posted_entry_balances_and_hits_accounts(book):
    slug, ent, _ = book
    reg = [_line("Worker A", 2000.0, 180.0, 124.0, 29.0, 60.0)]
    payroll_ingest.import_register(slug, ent, "2025-01-17", reg)
    con = sqlite3.connect(tenancy.resolve_db(slug))
    dr, cr = _balance(con)
    assert dr == cr and dr > 0
    accts = {r[0] for r in con.execute("SELECT DISTINCT account FROM splits")}
    assert {"555", "556", "245", "246", "247", "102"} <= accts
    eid = con.execute(
        "SELECT entry_id FROM payroll_lines").fetchone()[0]
    assert eid is not None
    con.close()


def test_rejects_inconsistent_net(book):
    slug, ent, _ = book
    bad = _line("Worker A", 2000.0, 180.0, 124.0, 29.0, 60.0)
    bad["net"] = 1900.0  # should be 1607.0
    with pytest.raises(ValueError, match="net"):
        payroll_ingest.import_register(slug, ent, "2025-01-17", [bad])
    con = sqlite3.connect(tenancy.resolve_db(slug))
    assert con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0] == 0
    con.close()


def test_rejects_negative_figure(book):
    slug, ent, _ = book
    bad = _line("Worker A", 2000.0, 180.0, 124.0, 29.0, 60.0)
    bad["fed_wh"] = -10.0
    with pytest.raises(ValueError, match="negative"):
        payroll_ingest.import_register(slug, ent, "2025-01-17", [bad])


def test_rejects_missing_required(book):
    slug, ent, _ = book
    with pytest.raises(ValueError, match="gross|net"):
        payroll_ingest.import_register(
            slug, ent, "2025-01-17", [{"name": "Worker A", "net": 100.0}])


def test_resolves_by_employee_id(book):
    slug, ent, ids = book
    line = _line("Worker A", 2000.0, 180.0, 124.0, 29.0, 60.0)
    del line["name"]
    line["employee_id"] = ids["Worker A"]
    rid = payroll_ingest.import_register(slug, ent, "2025-01-17", [line])
    con = sqlite3.connect(tenancy.resolve_db(slug))
    assert con.execute("SELECT employee_id FROM payroll_lines WHERE run_id=?",
                       (rid,)).fetchone()[0] == ids["Worker A"]
    con.close()


def test_rejects_unknown_employee(book):
    slug, ent, _ = book
    with pytest.raises(ValueError, match="unknown employee"):
        payroll_ingest.import_register(
            slug, ent, "2025-01-17",
            [_line("Nobody", 2000.0, 180.0, 124.0, 29.0, 60.0)])


def test_rejects_employee_id_from_other_entity(book):
    slug, ent, ids = book
    line = _line("Worker A", 2000.0, 180.0, 124.0, 29.0, 60.0)
    del line["name"]
    line["employee_id"] = ids["Worker A"]
    with pytest.raises(ValueError, match="employee_id"):
        payroll_ingest.import_register(slug, ent + 999, "2025-01-17", [line])


def test_rejects_empty_register(book):
    slug, ent, _ = book
    with pytest.raises(ValueError, match="no lines"):
        payroll_ingest.import_register(slug, ent, "2025-01-17", [])


def test_isolation_other_book_untouched(book):
    slug, ent, _ = book
    tenancy.register_client(slug="other", display_name="Other", entity_type="llc")
    payroll_ingest.import_register(
        slug, ent, "2025-01-17",
        [_line("Worker A", 2000.0, 180.0, 124.0, 29.0, 60.0)])
    con = sqlite3.connect(tenancy.resolve_db("other"))
    assert con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0] == 0
    con.close()
