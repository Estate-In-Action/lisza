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


def _seed_cash_book(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    con.executescript(
        """
        INSERT INTO entries(id, entry_date, description, source, status)
          VALUES(1, '2026-07-01', 'owner capital', 'manual', 'posted'),
                (2, '2026-07-02', 'sale', 'manual', 'posted'),
                (3, '2026-07-03', 'expense', 'manual', 'posted'),
                (4, '2026-07-04', 'buy equipment', 'manual', 'posted'),
                (5, '2026-07-05', 'transfer to savings', 'manual', 'posted');
        INSERT INTO splits(entry_id, account, dr, cr)
          VALUES(1, '102', 500, 0), (1, '300', 0, 500),
                (2, '102', 1000, 0), (2, '400', 0, 1000),
                (3, '500', 250, 0), (3, '102', 0, 250),
                (4, '160', 400, 0), (4, '102', 0, 400),
                (5, '103', 200, 0), (5, '102', 0, 200);
        """
    )
    con.commit()
    con.close()


def test_cash_flow_classifies_and_reconciles(tmp_path, monkeypatch):
    _seed_cash_book(tmp_path, monkeypatch)
    cf = statement_suite.cash_flow("acme", start="2026-07-01", end="2026-07-31")
    # operating: +1000 sale - 250 expense; investing: -400 equipment (asset 150);
    # financing: +500 owner capital. Cash-to-cash transfer (entry 5) nets to zero.
    assert cf["operating"] == 750.0
    assert cf["investing"] == -400.0
    assert cf["financing"] == 500.0
    assert cf["net_change"] == 850.0
    assert cf["opening_cash"] == 0.0
    assert cf["closing_cash"] == 850.0
    assert cf["reconciled"] == 0.0


def test_cash_flow_opening_balance_excluded_from_period(tmp_path, monkeypatch):
    _seed_cash_book(tmp_path, monkeypatch)
    # A period that starts after all activity: opening carries the full prior cash,
    # net change is zero, and it still reconciles.
    cf = statement_suite.cash_flow("acme", start="2026-08-01", end="2026-08-31")
    assert cf["opening_cash"] == 850.0
    assert cf["net_change"] == 0.0
    assert cf["closing_cash"] == 850.0
    assert cf["reconciled"] == 0.0


def test_statement_suite_includes_cash_flow(tmp_path, monkeypatch):
    _seed_cash_book(tmp_path, monkeypatch)
    suite = statement_suite.statement_suite("acme", start="2026-07-01", end="2026-07-31")
    assert "cash_flow" in suite
    assert suite["cash_flow"]["closing_cash"] == 850.0
