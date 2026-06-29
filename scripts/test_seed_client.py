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
