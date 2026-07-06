import sqlite3

import budgeting
import tenancy


def test_budget_variance_for_income_and_expense(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    con.executescript(
        """
        INSERT INTO entries(id, entry_date, description, source, status)
          VALUES(1, '2026-07-01', 'sale', 'manual', 'posted'),
                (2, '2026-07-02', 'expense', 'manual', 'posted');
        INSERT INTO splits(entry_id, account, dr, cr)
          VALUES(1, '102', 1100, 0), (1, '400', 0, 1100),
                (2, '500', 250, 0), (2, '102', 0, 250);
        """
    )
    con.commit()
    con.close()
    budgeting.set_budget_line("acme", period_start="2026-07-01", period_end="2026-07-31", account="400", amount=1000)
    budgeting.set_budget_line("acme", period_start="2026-07-01", period_end="2026-07-31", account="500", amount=300)

    report = budgeting.budget_variance("acme", period_start="2026-07-01", period_end="2026-07-31")
    rows = {r["account"]: r for r in report["lines"]}
    assert rows["400"]["actual"] == 1100.0
    assert rows["400"]["delta"] == 100.0
    assert rows["400"]["delta_pct"] == 10.0
    assert rows["500"]["actual"] == 250.0
    assert rows["500"]["delta"] == -50.0


def test_budget_line_requires_known_account(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    try:
        budgeting.set_budget_line("acme", period_start="2026-07-01", period_end="2026-07-31", account="999", amount=10)
    except ValueError as exc:
        assert "unknown account" in str(exc)
    else:
        raise AssertionError("expected unknown account guard")
