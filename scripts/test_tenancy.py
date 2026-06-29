import sqlite3
from pathlib import Path

import book_schema


def _fresh_book(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "ledger.db")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(
        """
        CREATE TABLE accounts(code TEXT PRIMARY KEY, name TEXT, type TEXT,
            sign_normal TEXT, grp TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE entries(id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date TEXT, description TEXT, payee TEXT, source TEXT,
            status TEXT DEFAULT 'pending');
        CREATE TABLE splits(id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER, account TEXT, dr REAL DEFAULT 0, cr REAL DEFAULT 0);
        """
    )
    return con


def test_ensure_book_schema_adds_entities_and_default(tmp_path):
    con = _fresh_book(tmp_path)
    book_schema.ensure_book_schema(con)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"client_profile", "entities"} <= tables
    default = con.execute(
        "SELECT name, is_default FROM entities WHERE is_default=1").fetchone()
    assert default is not None and default[1] == 1


def test_ensure_book_schema_backfills_entity_id(tmp_path):
    con = _fresh_book(tmp_path)
    con.execute("INSERT INTO entries(entry_date, description, source) "
                "VALUES('2025-01-01','x','synthetic')")
    book_schema.ensure_book_schema(con)
    cols = {r[1] for r in con.execute("PRAGMA table_info(entries)")}
    assert {"entity_id", "is_intercompany"} <= cols
    eid_entity = con.execute("SELECT entity_id FROM entries").fetchone()[0]
    default_id = con.execute(
        "SELECT id FROM entities WHERE is_default=1").fetchone()[0]
    assert eid_entity == default_id


def test_ensure_book_schema_is_idempotent(tmp_path):
    con = _fresh_book(tmp_path)
    book_schema.ensure_book_schema(con)
    book_schema.ensure_book_schema(con)  # must not raise
    n_default = con.execute(
        "SELECT COUNT(*) FROM entities WHERE is_default=1").fetchone()[0]
    assert n_default == 1


import tenancy


def test_registry_init_and_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    reg = tenancy.registry_db()
    assert reg.exists()
    # resolve_db builds the conventional per-client path
    p = tenancy.resolve_db("guitar-works")
    assert p == tmp_path / "clients" / "guitar-works" / "ledger.db"


def test_resolve_db_path_prefers_client_then_env_then_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    monkeypatch.delenv("LISZA_DB", raising=False)
    # explicit client wins
    assert tenancy.resolve_db_path(client="acme") == \
        tmp_path / "clients" / "acme" / "ledger.db"
    # env fallback
    monkeypatch.setenv("LISZA_DB", str(tmp_path / "custom.db"))
    assert tenancy.resolve_db_path() == tmp_path / "custom.db"
