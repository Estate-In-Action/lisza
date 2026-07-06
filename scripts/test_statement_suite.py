import sqlite3

import statement_suite
import tenancy


def test_statement_suite_ties_out(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    con.executescript(
        """
        INSERT INTO entries(id, entry_date, description, source, status)
          VALUES(1, '2026-07-01', 'owner capital', 'manual', 'posted'),
                (2, '2026-07-02', 'sale', 'manual', 'posted'),
                (3, '2026-07-03', 'expense', 'manual', 'posted');
        INSERT INTO splits(entry_id, account, dr, cr)
          VALUES(1, '102', 500, 0), (1, '300', 0, 500),
                (2, '102', 1000, 0), (2, '400', 0, 1000),
                (3, '500', 250, 0), (3, '102', 0, 250);
        """
    )
    con.commit()
    con.close()

    suite = statement_suite.statement_suite("acme", start="2026-07-01", end="2026-07-31")
    assert suite["pnl"]["income"] == 1000.0
    assert suite["pnl"]["expense"] == 250.0
    assert suite["pnl"]["net_income"] == 750.0
    assert suite["balance_sheet"]["assets"] == 1250.0
    assert suite["balance_sheet"]["liabilities"] == 0.0
    assert suite["balance_sheet"]["equity_total"] == 1250.0
    assert suite["balance_sheet"]["balanced"] == 0.0
    tb_totals = (
        round(sum(r["debit"] for r in suite["trial_balance"]), 2),
        round(sum(r["credit"] for r in suite["trial_balance"]), 2),
    )
    assert tb_totals == (1750.0, 1750.0)
