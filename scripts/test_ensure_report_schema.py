import sqlite3

from ensure_report_schema import ensure_report_schema


def _base_ledger(con: sqlite3.Connection) -> None:
    """Minimal accounts/entries/splits so the view is valid."""
    con.executescript(
        """
        CREATE TABLE accounts (code TEXT PRIMARY KEY, name TEXT, type TEXT,
                               sign_normal TEXT, grp TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE entries (id INTEGER PRIMARY KEY, entry_date TEXT, payee TEXT,
                              description TEXT, status TEXT, entity_id INTEGER,
                              is_intercompany INTEGER DEFAULT 0);
        CREATE TABLE splits (id INTEGER PRIMARY KEY, entry_id INTEGER, account TEXT,
                             dr REAL DEFAULT 0, cr REAL DEFAULT 0);
        INSERT INTO accounts VALUES ('1000','Cash','asset','debit','',1);
        INSERT INTO entries VALUES (1,'2026-01-01','X','d','posted',1,0);
        INSERT INTO splits  VALUES (1,1,'1000',100,0);
        """
    )


def test_creates_missing_objects():
    con = sqlite3.connect(":memory:")
    _base_ledger(con)
    created = ensure_report_schema(con)
    assert set(created) == {"category_overrides", "v_account_balances"}
    # Both objects now usable.
    assert con.execute("SELECT balance FROM v_account_balances WHERE code='1000'").fetchone()[0] == 100
    con.execute("INSERT INTO category_overrides(entry_id, account_code) VALUES (1,'1000')")


def test_idempotent():
    con = sqlite3.connect(":memory:")
    _base_ledger(con)
    ensure_report_schema(con)
    # Second run creates nothing.
    assert ensure_report_schema(con) == []


def test_preserves_existing_overrides():
    con = sqlite3.connect(":memory:")
    _base_ledger(con)
    ensure_report_schema(con)
    con.execute("INSERT INTO category_overrides(entry_id, account_code) VALUES (1,'1000')")
    # Re-running must not wipe the row.
    ensure_report_schema(con)
    assert con.execute("SELECT COUNT(*) FROM category_overrides").fetchone()[0] == 1
