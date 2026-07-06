import sqlite3

import cash_management
import tenancy


def test_cash_position_rolls_cash_ar_ap_and_reconciliation(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    con.executescript(
        """
        INSERT INTO entries(id, entry_date, description, source, status)
          VALUES(1, '2026-07-01', 'sale', 'manual', 'posted'),
                (2, '2026-07-02', 'expense', 'manual', 'posted');
        INSERT INTO splits(entry_id, account, dr, cr)
          VALUES(1, '102', 500, 0), (1, '400', 0, 500),
                (2, '500', 125, 0), (2, '102', 0, 125);
        INSERT INTO invoices(party, issue_date, due_date, amount, status)
          VALUES('Customer A', '2026-07-01', '2026-07-31', 200, 'open');
        INSERT INTO bills(party, issue_date, due_date, amount, status)
          VALUES('Vendor B', '2026-07-01', '2026-07-20', 75, 'unpaid');
        CREATE TABLE statement_lines(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          statement_account TEXT, statement_date TEXT, description TEXT,
          amount REAL, external_ref TEXT, status TEXT
        );
        INSERT INTO statement_lines(statement_account, statement_date, amount, status)
          VALUES('102', '2026-07-02', -125, 'unmatched');
        """
    )
    con.commit()
    con.close()

    position = cash_management.cash_position("acme", as_of="2026-07-31")

    assert position["cash_total"] == 375.0
    assert position["open_ar"] == 200.0
    assert position["open_ap"] == 75.0
    assert position["net_near_term_cash"] == 500.0
    assert position["unreconciled_statement_count"] == 1
    assert position["unreconciled_statement_amount"] == -125.0
