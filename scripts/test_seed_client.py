import sqlite3

import tenancy
import client_profiles
import seed_client
import reports_entity


def test_seed_solopreneur_balances_and_single_entity(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="jb-design", display_name="J.B. Design",
                            entity_type="sole_prop")
    seed_client.seed(client_profiles.JB_DESIGN, slug="jb-design")
    con = sqlite3.connect(tenancy.resolve_db("jb-design"))
    # one entity (solopreneur)
    assert con.execute("SELECT COUNT(*) FROM entities WHERE active=1").fetchone()[0] == 1
    # books balance
    dr, cr = con.execute(
        "SELECT ROUND(SUM(s.dr),2),ROUND(SUM(s.cr),2) FROM splits s "
        "JOIN entries e ON e.id=s.entry_id AND e.status='posted'").fetchone()
    assert abs(dr - cr) < 0.01
    con.close()


def test_harborside_multi_entity_and_eliminations(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="harborside-group", display_name="Harborside Group",
                            entity_type="llc")
    seed_client.seed(client_profiles.HARBORSIDE_GROUP, slug="harborside-group")
    con = sqlite3.connect(tenancy.resolve_db("harborside-group"))
    # three active entities
    assert con.execute("SELECT COUNT(*) FROM entities WHERE active=1").fetchone()[0] == 3
    # at least one flagged inter-company entry exists
    ic = con.execute("SELECT COUNT(*) FROM entries WHERE is_intercompany=1").fetchone()[0]
    assert ic > 0
    # consolidated revenue nets out the inter-company management fee, so it is
    # strictly less than the naive all-entry sum (which double-counts the fee).
    naive = reports_entity.account_balance(con, "410")
    consolidated = reports_entity.account_balance(con, "410", consolidated=True)
    assert naive > 0 and consolidated == 0 and consolidated < naive
    con.close()


def _seed_counts(con):
    return {
        "entries": con.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
        "splits": con.execute("SELECT COUNT(*) FROM splits").fetchone()[0],
        "invoices": con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0],
        "bills": con.execute("SELECT COUNT(*) FROM bills").fetchone()[0],
        "employees": con.execute("SELECT COUNT(*) FROM employees").fetchone()[0],
        "payroll_runs": con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0],
        "payroll_lines": con.execute("SELECT COUNT(*) FROM payroll_lines").fetchone()[0],
    }


def test_seed_client_is_idempotent_for_demo_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="harborside-group", display_name="Harborside Group",
                            entity_type="llc")

    first = seed_client.seed(client_profiles.HARBORSIDE_GROUP, slug="harborside-group")
    con = sqlite3.connect(tenancy.resolve_db("harborside-group"))
    first_counts = _seed_counts(con)
    con.close()

    second = seed_client.seed(client_profiles.HARBORSIDE_GROUP, slug="harborside-group")
    con = sqlite3.connect(tenancy.resolve_db("harborside-group"))
    second_counts = _seed_counts(con)
    dr, cr = con.execute(
        "SELECT ROUND(SUM(s.dr),2),ROUND(SUM(s.cr),2) FROM splits s "
        "JOIN entries e ON e.id=s.entry_id AND e.status=\x27posted\x27").fetchone()
    con.close()

    assert second["reset"]["entries"] > 0
    assert second["reset"]["payroll_runs"] > 0
    assert first["invoices"] == second["invoices"]
    assert first_counts == second_counts
    assert abs(dr - cr) < 0.01
