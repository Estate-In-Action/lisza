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


def test_register_client_creates_isolated_book(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    cid = tenancy.register_client(
        slug="acme-co", display_name="Acme Co", entity_type="llc",
        legal_name="Acme Co LLC", ein="11-1111111", filing_cadence="quarterly")
    assert cid
    # registry row exists with cached projection
    rows = tenancy.list_clients()
    assert [r.slug for r in rows] == ["acme-co"]
    assert rows[0].display_name == "Acme Co"
    # the book exists, is entity-aware, and carries its own profile
    book = sqlite3.connect(tenancy.resolve_db("acme-co"))
    prof = book.execute("SELECT display_name, slug FROM client_profile").fetchone()
    assert prof == ("Acme Co", "acme-co")
    assert book.execute("SELECT COUNT(*) FROM entities WHERE is_default=1").fetchone()[0] == 1
    book.close()


def test_isolation_write_to_one_book_not_seen_in_other(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="a-co", display_name="A")
    tenancy.register_client(slug="b-co", display_name="B")
    a = sqlite3.connect(tenancy.resolve_db("a-co"))
    a.execute("INSERT INTO accounts(code,name,type,sign_normal) "
              "VALUES('999','Test','asset','debit')")
    a.commit(); a.close()
    b = sqlite3.connect(tenancy.resolve_db("b-co"))
    leaked = b.execute("SELECT COUNT(*) FROM accounts WHERE code='999'").fetchone()[0]
    b.close()
    assert leaked == 0


def test_refresh_summary_caches_cash_ar_ap(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="sum-co", display_name="Sum Co")
    book = sqlite3.connect(tenancy.resolve_db("sum-co"))
    # one posted entry: debit checking 500 / credit revenue 500
    book.execute("INSERT INTO entries(entry_date,description,source,status,entity_id) "
                 "VALUES('2026-01-05','sale','synthetic','posted',"
                 "(SELECT id FROM entities WHERE is_default=1))")
    eid = book.execute("SELECT last_insert_rowid()").fetchone()[0]
    book.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,?,?)",
                 (eid, "102", 500, 0))
    book.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,?,?)",
                 (eid, "400", 0, 500))
    # one open invoice
    book.execute("INSERT INTO invoices(party,issue_date,due_date,amount,status) "
                 "VALUES('X','2026-01-01','2026-01-31',300,'open')")
    book.commit(); book.close()

    tenancy.refresh_summary("sum-co")
    reg = sqlite3.connect(tenancy.registry_path())
    row = reg.execute("SELECT cash, open_ar, open_ap FROM client_summary "
                      "WHERE client_id=(SELECT client_id FROM clients WHERE slug='sum-co')").fetchone()
    reg.close()
    assert row == (500.0, 300.0, 0.0)


from datetime import date


def test_next_filing_due_quarterly_monthly_annual():
    # quarterly: next statutory quarter-end filing strictly after the reference
    assert tenancy.compute_next_filing_due("quarterly", date(2026, 6, 9)) == date(2026, 7, 31)
    assert tenancy.compute_next_filing_due("quarterly", date(2026, 11, 2)) == date(2027, 1, 31)
    # monthly: last day of the month following the reference month
    assert tenancy.compute_next_filing_due("monthly", date(2026, 6, 9)) == date(2026, 7, 31)
    assert tenancy.compute_next_filing_due("monthly", date(2026, 12, 3)) == date(2027, 1, 31)
    # annual: Apr 15 of the year after the next fiscal-year-end on/after reference
    assert tenancy.compute_next_filing_due("annual", date(2026, 6, 9), "12-31") == date(2027, 4, 15)


import client_profiles
import seed_client


def test_refresh_summary_caches_entity_count_and_filing_due(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="jb-design", display_name="J.B. Design",
                            entity_type="sole_prop", filing_cadence="annual")
    seed_client.seed(client_profiles.JB_DESIGN, slug="jb-design")
    tenancy.register_client(slug="harborside-group", display_name="Harborside Group",
                            entity_type="llc", filing_cadence="quarterly")
    seed_client.seed(client_profiles.HARBORSIDE_GROUP, slug="harborside-group")
    tenancy.refresh_all()
    reg = sqlite3.connect(tenancy.registry_path())
    reg.row_factory = sqlite3.Row
    rows = {r["slug"]: r for r in reg.execute(
        "SELECT c.slug, c.next_filing_due, s.entity_count "
        "FROM clients c JOIN client_summary s ON s.client_id=c.client_id").fetchall()}
    reg.close()
    assert rows["jb-design"]["entity_count"] == 1
    assert rows["harborside-group"]["entity_count"] == 3
    assert rows["jb-design"]["next_filing_due"] is not None
    assert rows["harborside-group"]["next_filing_due"] is not None


def test_kind_column_migration_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.registry_db()
    tenancy.registry_db()
    con = sqlite3.connect(tenancy.registry_path())
    kind_cols = [r for r in con.execute("PRAGMA table_info(clients)") if r[1] == "kind"]
    con.close()
    assert len(kind_cols) == 1


def test_existing_client_rows_default_to_kind_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="defaults-co", display_name="Defaults Co")
    con = sqlite3.connect(tenancy.registry_path())
    kind = con.execute("SELECT kind FROM clients WHERE slug='defaults-co'").fetchone()[0]
    con.close()
    assert kind == "client"


def test_register_client_writes_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="house-ish", display_name="Houseish", kind="house")
    tenancy.register_client(slug="plain-co", display_name="Plain Co")  # default
    con = sqlite3.connect(tenancy.registry_path())
    kinds = dict(con.execute("SELECT slug, kind FROM clients").fetchall())
    con.close()
    assert kinds["house-ish"] == "house"
    assert kinds["plain-co"] == "client"


def test_ensure_house_registers_hidden_full_schema_book(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    cid1 = tenancy.ensure_house()
    cid2 = tenancy.ensure_house()  # idempotent: same id, no duplicate row
    assert cid1 == cid2

    con = sqlite3.connect(tenancy.registry_path())
    rows = con.execute("SELECT slug, kind FROM clients WHERE slug='_house'").fetchall()
    con.close()
    assert rows == [("_house", "house")]

    p = tenancy.resolve_db("_house")
    assert p == tmp_path / "clients" / "_house" / "ledger.db"
    assert p.exists()

    book = sqlite3.connect(p)
    tables = {r[0] for r in book.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    book.close()
    assert {"accounts", "entries", "splits", "invoices", "bills",
            "entities", "client_profile", "employees",
            "payroll_runs", "payroll_lines"} <= tables


def test_list_clients_excludes_house(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="real-co", display_name="Real Co")
    tenancy.ensure_house()
    slugs = [r.slug for r in tenancy.list_clients()]
    assert "real-co" in slugs
    assert "_house" not in slugs


import json as _json


def test_house_config_default_then_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.ensure_house()
    cfg = tenancy.get_house_config()
    assert isinstance(cfg["tiles"], list) and len(cfg["tiles"]) >= 1
    assert all("key" in t and "label" in t for t in cfg["tiles"])
    new_cfg = {"tiles": cfg["tiles"] + [{"key": "custom_x", "label": "Custom X", "hint": "added"}]}
    tenancy.set_house_config(new_cfg)
    again = tenancy.get_house_config()
    assert any(t["key"] == "custom_x" for t in again["tiles"])
