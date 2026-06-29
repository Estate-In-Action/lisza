# Multi-Tenant Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn single-tenant LISZA into a multi-client bookkeeping foundation: per-client SQLite books, a shared `lisza.db` registry/cache, an entity dimension with inter-company eliminations, and three realistic synthetic clients.

**Architecture:** DB-per-client isolation (each client = its own `clients/<slug>/ledger.db`) plus a thin shared `lisza.db` that indexes and caches but never owns truth. Every client book is entity-aware (single-business clients have one default entity); multi-location clients model each location as an entity, with a flagged inter-company entry type that nets out of consolidated reports. All access routes through one `tenancy.py` module.

**Tech Stack:** Python 3, stdlib `sqlite3`, `pytest`. No new dependencies.

**Spec:** `docs/specs/2026-06-29-multi-tenant-foundation-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `scripts/tenancy.py` | NEW. Registry schema, `ClientRow`, `list_clients`, `resolve_db`, `register_client`, `refresh_summary`, `refresh_all`, `resolve_db_path`. The one seam all client resolution flows through. |
| `scripts/book_schema.py` | NEW. The additive per-book schema (`client_profile`, `entities`, `entries.entity_id`, `entries.is_intercompany`) + idempotent `ensure_book_schema(con)` migration. Imported by `init_ledger.py` and `tenancy.py`. |
| `scripts/reports_entity.py` | NEW. Entity-aware balances: `account_balances(con, entity_id=None, consolidated=False)`. Consolidated excludes `is_intercompany=1`. |
| `scripts/client_profiles.py` | NEW. Three concrete `ClientProfile` configs (guitar-works, harborside-group, jb-design) consumed by the seeder. |
| `scripts/seed_client.py` | NEW. Generalized seeder driven by a `ClientProfile`; supersedes the hardcoded single-business `seed_synthetic.py` for multi-client seeding. |
| `scripts/init_ledger.py` | MODIFY. Call `ensure_book_schema(con)`; resolve DB path via `resolve_db_path`. |
| `scripts/test_reports.py` | MODIFY. Resolve DB via `LISZA_DB` env instead of hardcoded `ledger.db`. |
| `scripts/test_tenancy.py` | NEW. Registry, resolution, isolation, summary-refresh tests. |
| `scripts/test_reports_entity.py` | NEW. Per-entity + consolidated (eliminations-netted) report tests. |

---

## Task 1: Book schema module (additive, idempotent)

**Files:**
- Create: `scripts/book_schema.py`
- Test: `scripts/test_tenancy.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/test_tenancy.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'book_schema'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/book_schema.py`:

```python
#!/usr/bin/env python3
"""Additive per-client-book schema: entity dimension + client profile.

Idempotent. Safe to run on a fresh book or an existing single-tenant book.
"""
from __future__ import annotations

import sqlite3


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def ensure_book_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS client_profile (
            client_id      TEXT PRIMARY KEY,
            slug           TEXT NOT NULL,
            legal_name     TEXT,
            display_name   TEXT,
            ein            TEXT,
            entity_type    TEXT,
            fiscal_year_end TEXT,
            filing_cadence TEXT,
            active_window  TEXT DEFAULT '1y',
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS entities (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            type       TEXT,
            is_default INTEGER NOT NULL DEFAULT 0,
            active     INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    # exactly one default entity per book
    has_default = con.execute(
        "SELECT COUNT(*) FROM entities WHERE is_default=1").fetchone()[0]
    if has_default == 0:
        con.execute(
            "INSERT INTO entities(name, type, is_default) VALUES('Main','default',1)")
    default_id = con.execute(
        "SELECT id FROM entities WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()[0]

    if not _has_column(con, "entries", "entity_id"):
        con.execute("ALTER TABLE entries ADD COLUMN entity_id INTEGER")
        con.execute("UPDATE entries SET entity_id=? WHERE entity_id IS NULL",
                    (default_id,))
    else:
        con.execute("UPDATE entries SET entity_id=? WHERE entity_id IS NULL",
                    (default_id,))

    if not _has_column(con, "entries", "is_intercompany"):
        con.execute("ALTER TABLE entries ADD COLUMN is_intercompany INTEGER NOT NULL DEFAULT 0")

    con.commit()


def default_entity_id(con: sqlite3.Connection) -> int:
    return con.execute(
        "SELECT id FROM entities WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/book_schema.py scripts/test_tenancy.py
git commit -m "feat(lisza): additive per-book entity schema (book_schema.py)"
```

---

## Task 2: Registry schema + client resolution in tenancy.py

**Files:**
- Create: `scripts/tenancy.py`
- Test: `scripts/test_tenancy.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_tenancy.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tenancy'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/tenancy.py`:

```python
#!/usr/bin/env python3
"""LISZA multi-tenant seam: client registry, DB resolution, summary cache.

Principle: isolation is physical (one SQLite file per client); the shared
registry indexes and caches but never owns truth.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


def lisza_home() -> Path:
    return Path(os.environ.get("LISZA_HOME", str(Path(__file__).resolve().parent.parent)))


def registry_path() -> Path:
    return lisza_home() / "lisza.db"


REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id    TEXT PRIMARY KEY,
    slug         TEXT UNIQUE NOT NULL,
    db_path      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    display_name TEXT,
    entity_type  TEXT,
    last_close_date TEXT,
    next_filing_due TEXT
);
CREATE TABLE IF NOT EXISTS client_summary (
    client_id   TEXT PRIMARY KEY REFERENCES clients(client_id),
    as_of       TEXT,
    cash        REAL, open_ar REAL, open_ap REAL,
    ar_count    INTEGER, ap_count INTEGER, last_entry_date TEXT
);
CREATE TABLE IF NOT EXISTS bookkeeper_prefs (
    bookkeeper_id  TEXT PRIMARY KEY,
    layout         TEXT DEFAULT 'tile',
    card_fields_json TEXT,
    default_client TEXT,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def registry_db() -> Path:
    path = registry_path()
    con = sqlite3.connect(path)
    con.executescript(REGISTRY_SCHEMA)
    con.commit()
    con.close()
    return path


def resolve_db(slug: str) -> Path:
    return lisza_home() / "clients" / slug / "ledger.db"


def resolve_db_path(client: str | None = None) -> Path:
    if client:
        return resolve_db(client)
    env = os.environ.get("LISZA_DB")
    if env:
        return Path(env)
    return lisza_home() / "ledger.db"


@dataclass(frozen=True)
class ClientRow:
    client_id: str
    slug: str
    db_path: str
    status: str
    display_name: str | None
    entity_type: str | None


def list_clients(status: str = "active") -> list[ClientRow]:
    registry_db()
    con = sqlite3.connect(registry_path())
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM clients WHERE status=? ORDER BY slug", (status,)).fetchall()
    con.close()
    return [ClientRow(r["client_id"], r["slug"], r["db_path"], r["status"],
                      r["display_name"], r["entity_type"]) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/tenancy.py scripts/test_tenancy.py
git commit -m "feat(lisza): client registry + DB-path resolution (tenancy.py)"
```

---

## Task 3: register_client (create book + profile + default entity + registry row)

**Files:**
- Modify: `scripts/tenancy.py`
- Test: `scripts/test_tenancy.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_tenancy.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -k register_client -q`
Expected: FAIL — `AttributeError: module 'tenancy' has no attribute 'register_client'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/tenancy.py` (imports at top, function at bottom):

```python
import csv
import uuid

import book_schema

COA_PATH = Path(__file__).resolve().parent.parent / "coa.csv"


def _load_coa(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS accounts (
            code TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL,
            sign_normal TEXT NOT NULL, grp TEXT, active INTEGER NOT NULL DEFAULT 1)""")
    with COA_PATH.open() as f:
        rows = [(r["code"], r["name"], r["type"], r["sign_normal"], r.get("group"))
                for r in csv.DictReader(f)]
    con.executemany(
        """INSERT INTO accounts(code,name,type,sign_normal,grp) VALUES(?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET name=excluded.name""", rows)


def _ledger_core_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date TEXT NOT NULL, description TEXT NOT NULL, payee TEXT,
            source TEXT NOT NULL, source_ref TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','posted','void')),
            posted_at TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')), notes TEXT);
        CREATE TABLE IF NOT EXISTS splits(id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            account TEXT NOT NULL REFERENCES accounts(code),
            dr REAL NOT NULL DEFAULT 0, cr REAL NOT NULL DEFAULT 0, memo TEXT,
            CHECK ((dr=0) OR (cr=0)), CHECK (dr>=0 AND cr>=0));
        CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY AUTOINCREMENT,
            party TEXT NOT NULL, issue_date TEXT NOT NULL, due_date TEXT NOT NULL,
            amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open','paid')), paid_date TEXT,
            entry_id INTEGER REFERENCES entries(id), memo TEXT);
        CREATE TABLE IF NOT EXISTS bills(id INTEGER PRIMARY KEY AUTOINCREMENT,
            party TEXT NOT NULL, issue_date TEXT NOT NULL, due_date TEXT NOT NULL,
            amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'unpaid'
                CHECK(status IN ('unpaid','paid')), paid_date TEXT,
            entry_id INTEGER REFERENCES entries(id), memo TEXT);
        CREATE TABLE IF NOT EXISTS payee_rules(id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT NOT NULL, account_code TEXT NOT NULL REFERENCES accounts(code),
            priority INTEGER NOT NULL DEFAULT 100, active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        """
    )


def register_client(*, slug: str, display_name: str, legal_name: str | None = None,
                    entity_type: str | None = None, ein: str | None = None,
                    fiscal_year_end: str = "12-31", filing_cadence: str = "quarterly",
                    active_window: str = "1y") -> str:
    db = resolve_db(slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    client_id = uuid.uuid4().hex[:12]

    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    _load_coa(con)
    _ledger_core_schema(con)
    book_schema.ensure_book_schema(con)
    con.execute(
        """INSERT OR REPLACE INTO client_profile
           (client_id, slug, legal_name, display_name, ein, entity_type,
            fiscal_year_end, filing_cadence, active_window)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (client_id, slug, legal_name, display_name, ein, entity_type,
         fiscal_year_end, filing_cadence, active_window))
    con.commit()
    con.close()

    registry_db()
    reg = sqlite3.connect(registry_path())
    reg.execute(
        """INSERT OR REPLACE INTO clients
           (client_id, slug, db_path, status, display_name, entity_type)
           VALUES (?,?,?,?,?,?)""",
        (client_id, slug, str(db), "active", display_name, entity_type))
    reg.commit()
    reg.close()
    return client_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/tenancy.py scripts/test_tenancy.py
git commit -m "feat(lisza): register_client creates isolated entity-aware book"
```

---

## Task 4: Summary refresh into the registry cache

**Files:**
- Modify: `scripts/tenancy.py`
- Test: `scripts/test_tenancy.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_tenancy.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -k refresh_summary -q`
Expected: FAIL — `AttributeError: module 'tenancy' has no attribute 'refresh_summary'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/tenancy.py`:

```python
# cash = sum over asset accounts 101,102,103,106 (debit-normal balances)
CASH_ACCOUNTS = ("101", "102", "103", "106")


def refresh_summary(slug: str) -> dict:
    db = resolve_db(slug)
    con = sqlite3.connect(db)
    qmarks = ",".join("?" * len(CASH_ACCOUNTS))
    cash = con.execute(
        f"""SELECT ROUND(COALESCE(SUM(s.dr-s.cr),0),2) FROM splits s
            JOIN entries e ON e.id=s.entry_id AND e.status='posted'
            WHERE s.account IN ({qmarks})""", CASH_ACCOUNTS).fetchone()[0]
    open_ar, ar_count = con.execute(
        "SELECT ROUND(COALESCE(SUM(amount),0),2), COUNT(*) "
        "FROM invoices WHERE status='open'").fetchone()
    open_ap, ap_count = con.execute(
        "SELECT ROUND(COALESCE(SUM(amount),0),2), COUNT(*) "
        "FROM bills WHERE status='unpaid'").fetchone()
    last_entry = con.execute(
        "SELECT MAX(entry_date) FROM entries WHERE status='posted'").fetchone()[0]
    cid = con.execute("SELECT client_id FROM client_profile").fetchone()[0]
    con.close()

    registry_db()
    reg = sqlite3.connect(registry_path())
    reg.execute(
        """INSERT OR REPLACE INTO client_summary
           (client_id, as_of, cash, open_ar, open_ap, ar_count, ap_count, last_entry_date)
           VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?)""",
        (cid, cash, open_ar, open_ap, ar_count, ap_count, last_entry))
    reg.execute("UPDATE clients SET last_close_date=? WHERE client_id=?",
                (last_entry, cid))
    reg.commit()
    reg.close()
    return {"cash": cash, "open_ar": open_ar, "open_ap": open_ap}


def refresh_all() -> int:
    n = 0
    for row in list_clients():
        refresh_summary(row.slug)
        n += 1
    return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/tenancy.py scripts/test_tenancy.py
git commit -m "feat(lisza): refresh_summary/refresh_all cache cash+AR+AP in registry"
```

---

## Task 5: Entity-aware reports + eliminations netting

**Files:**
- Create: `scripts/reports_entity.py`
- Test: `scripts/test_reports_entity.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/test_reports_entity.py`:

```python
import sqlite3
from pathlib import Path

import book_schema
import reports_entity


def _book_with_two_entities(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "ledger.db")
    con.executescript(
        """
        CREATE TABLE accounts(code TEXT PRIMARY KEY, name TEXT, type TEXT,
            sign_normal TEXT, grp TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE entries(id INTEGER PRIMARY KEY AUTOINCREMENT, entry_date TEXT,
            description TEXT, payee TEXT, source TEXT, status TEXT DEFAULT 'pending');
        CREATE TABLE splits(id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id INTEGER,
            account TEXT, dr REAL DEFAULT 0, cr REAL DEFAULT 0);
        INSERT INTO accounts VALUES('102','Checking','asset','debit','x',1);
        INSERT INTO accounts VALUES('400','Revenue','income','credit','x',1);
        """
    )
    book_schema.ensure_book_schema(con)
    # second (non-default) entity
    con.execute("INSERT INTO entities(name,type,is_default) VALUES('Loc B','location',0)")
    return con


def _post(con, entity_id, dr_acct, cr_acct, amt, intercompany=0):
    con.execute("INSERT INTO entries(entry_date,description,source,status,entity_id,is_intercompany) "
                "VALUES('2026-02-01','t','synthetic','posted',?,?)", (entity_id, intercompany))
    eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,?,0)", (eid, dr_acct, amt))
    con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,0,?)", (eid, cr_acct, amt))


def test_per_entity_balance_isolated(tmp_path):
    con = _book_with_two_entities(tmp_path)
    a = con.execute("SELECT id FROM entities WHERE is_default=1").fetchone()[0]
    b = con.execute("SELECT id FROM entities WHERE is_default=0").fetchone()[0]
    _post(con, a, "102", "400", 100)
    _post(con, b, "102", "400", 250)
    con.commit()
    bal_a = reports_entity.account_balance(con, "400", entity_id=a)
    bal_b = reports_entity.account_balance(con, "400", entity_id=b)
    assert bal_a == 100.0 and bal_b == 250.0


def test_consolidated_nets_out_intercompany(tmp_path):
    con = _book_with_two_entities(tmp_path)
    a = con.execute("SELECT id FROM entities WHERE is_default=1").fetchone()[0]
    b = con.execute("SELECT id FROM entities WHERE is_default=0").fetchone()[0]
    _post(con, a, "102", "400", 100)
    _post(con, b, "102", "400", 250)
    _post(con, a, "102", "400", 999, intercompany=1)  # must be excluded
    con.commit()
    consolidated = reports_entity.account_balance(con, "400", consolidated=True)
    assert consolidated == 350.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_reports_entity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'reports_entity'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/reports_entity.py`:

```python
#!/usr/bin/env python3
"""Entity-aware balances.

Per-entity = filter entity_id. Consolidated = all entities, excluding
inter-company entries (is_intercompany=1) so cross-location flows don't
double-count.
"""
from __future__ import annotations

import sqlite3


def account_balance(con: sqlite3.Connection, account: str, *,
                    entity_id: int | None = None, consolidated: bool = False,
                    as_of: str | None = None) -> float:
    sql = [
        "SELECT ROUND(COALESCE(SUM(s.dr-s.cr),0),2)",
        "FROM splits s JOIN entries e ON e.id=s.entry_id",
        "WHERE e.status='posted' AND s.account=?",
    ]
    params: list = [account]
    if entity_id is not None:
        sql.append("AND e.entity_id=?")
        params.append(entity_id)
    if consolidated:
        sql.append("AND e.is_intercompany=0")
    if as_of:
        sql.append("AND e.entry_date<=?")
        params.append(as_of)
    return con.execute(" ".join(sql), params).fetchone()[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_reports_entity.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/reports_entity.py scripts/test_reports_entity.py
git commit -m "feat(lisza): entity-aware balances with inter-company netting"
```

---

## Task 6: Wire init_ledger.py + test_reports.py to path resolution

**Files:**
- Modify: `scripts/init_ledger.py:9-11`, `scripts/init_ledger.py:142-146`
- Modify: `scripts/test_reports.py:22-23`

- [ ] **Step 1: Modify init_ledger.py to resolve path + ensure entity schema**

In `scripts/init_ledger.py`, replace the path block (lines 9-11):

```python
ROOT = Path(__file__).resolve().parent.parent
import os
from tenancy import resolve_db_path
DB = resolve_db_path(client=os.environ.get("LISZA_CLIENT"))
COA = ROOT / "coa.csv"
import book_schema
```

In `main()`, after `con.executescript(SCHEMA)` (line 146), add:

```python
    book_schema.ensure_book_schema(con)
```

- [ ] **Step 2: Modify test_reports.py to resolve via env**

In `scripts/test_reports.py`, replace line 22:

```python
import os
DB = Path(os.environ.get("LISZA_DB",
          str(Path(__file__).resolve().parent.parent / "clients" / "guitar-works" / "ledger.db")))
```

- [ ] **Step 3: Verify tenancy tests still pass**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py test_reports_entity.py -q`
Expected: PASS (10 passed)

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/init_ledger.py scripts/test_reports.py
git commit -m "feat(lisza): route init_ledger + report tests through path resolution"
```

---

## Task 7: Migrate the current book to clients/guitar-works

**Files:**
- Create: `scripts/migrate_to_guitar_works.py`

- [ ] **Step 1: Write the migration script**

Create `scripts/migrate_to_guitar_works.py`:

```python
#!/usr/bin/env python3
"""One-shot: copy the legacy single-tenant ledger.db into clients/guitar-works,
attach an entity-aware schema + client profile, and register it. Idempotent-ish:
re-running overwrites the guitar-works book from the legacy copy.

The legacy LISZA/ledger.db is left untouched (Principle #2).
"""
from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path

import book_schema
import tenancy

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT / "ledger.db"


def main() -> int:
    assert LEGACY.exists(), f"legacy {LEGACY} missing"
    dest = tenancy.resolve_db("guitar-works")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.cop2 = shutil.copy2  # noqa
    shutil.copy2(LEGACY, dest)

    con = sqlite3.connect(dest)
    con.execute("PRAGMA foreign_keys=ON")
    book_schema.ensure_book_schema(con)
    cid = uuid.uuid4().hex[:12]
    con.execute(
        """INSERT OR REPLACE INTO client_profile
           (client_id, slug, legal_name, display_name, ein, entity_type,
            fiscal_year_end, filing_cadence, active_window)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (cid, "guitar-works", "Guitar Works LLC", "Guitar Works",
         "47-2201234", "llc", "12-31", "quarterly", "1y"))
    con.execute("UPDATE entities SET name='Guitar Works' WHERE is_default=1")
    con.commit()
    con.close()

    tenancy.registry_db()
    reg = sqlite3.connect(tenancy.registry_path())
    reg.execute(
        """INSERT OR REPLACE INTO clients
           (client_id, slug, db_path, status, display_name, entity_type)
           VALUES (?,?,?,?,?,?)""",
        (cid, "guitar-works", str(dest), "active", "Guitar Works", "llc"))
    reg.commit()
    reg.close()
    print(f"migrated legacy book -> {dest} (client_id={cid})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

> Note: the `shutil.copy2(LEGACY, dest)` line copies the file; the stray
> `shutil.cop2` alias line above it is a typo — delete it when implementing.

- [ ] **Step 2: Run the migration**

Run: `cd /home/workspace/LISZA/scripts && python3 migrate_to_guitar_works.py`
Expected: `migrated legacy book -> .../clients/guitar-works/ledger.db (client_id=...)`

- [ ] **Step 3: Verify existing report invariants still hold on the migrated book**

Run: `cd /home/workspace/LISZA/scripts && LISZA_DB="$(python3 -c 'import tenancy; print(tenancy.resolve_db("guitar-works"))')" python3 -m pytest test_reports.py -q`
Expected: PASS (6 passed) — balance sheet balances, AR/AP tie out, multi-year span intact.

- [ ] **Step 4: Refresh + verify summary cache**

Run: `cd /home/workspace/LISZA/scripts && python3 -c "import tenancy; print(tenancy.refresh_summary('guitar-works'))"`
Expected: a dict with non-zero `cash`, `open_ar`, `open_ap`.

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/migrate_to_guitar_works.py
git commit -m "feat(lisza): migrate legacy book to clients/guitar-works (Client 1)"
```

---

## Task 8: Client profile configs + generalized seeder

**Files:**
- Create: `scripts/client_profiles.py`
- Create: `scripts/seed_client.py`
- Test: `scripts/test_seed_client.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/test_seed_client.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_seed_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'client_profiles'`

- [ ] **Step 3: Write the profile configs**

Create `scripts/client_profiles.py`:

```python
#!/usr/bin/env python3
"""Per-client synthetic-data profiles consumed by seed_client.seed().

Each profile tunes the universe (parties), the per-entity revenue scale,
the seasonal curve, and a small set of business-type flags. ZERO real data.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClientProfile:
    slug: str
    entities: tuple[str, ...]            # ("Main",) for single-entity
    customers: tuple[str, ...]
    vendors: tuple[str, ...]
    base_monthly_rev: float              # per entity, year-1 average
    yoy: float
    season: dict                          # month -> multiplier
    revenue_accounts: tuple[str, ...]
    cogs_account: str                     # primary variable-cost account
    cogs_frac: float                      # COGS as fraction of revenue
    has_payroll: bool
    owner_draw_monthly: float
    intercompany_sweeps: bool = False    # parent cash sweeps between entities


_FLAT_SEASON = {m: 1.0 for m in range(1, 13)}
_AGENCY_SEASON = {1: 0.90, 2: 0.93, 3: 1.02, 4: 1.06, 5: 1.05, 6: 0.96,
                  7: 0.84, 8: 0.88, 9: 1.07, 10: 1.13, 11: 1.16, 12: 1.10}
_RESTAURANT_SEASON = {1: 0.88, 2: 0.90, 3: 1.0, 4: 1.05, 5: 1.10, 6: 1.12,
                      7: 1.14, 8: 1.12, 9: 1.0, 10: 0.98, 11: 1.02, 12: 1.07}

GUITAR_WORKS = ClientProfile(
    slug="guitar-works",
    entities=("Guitar Works",),
    customers=("Fretboard Retail", "Sixstring Distributors", "Harmony Music Co",
               "Cadence Instruments", "Allegro Stores"),
    vendors=("Tonewood Supply", "Hardware & Tuners Inc", "Lacquer & Finish Co",
             "Case & Gigbag Mfg", "Maple Lumber Yard", "String Source"),
    base_monthly_rev=58000.0, yoy=1.10, season=_AGENCY_SEASON,
    revenue_accounts=("400", "410"), cogs_account="500", cogs_frac=0.42,
    has_payroll=True, owner_draw_monthly=2200.0)

HARBORSIDE_GROUP = ClientProfile(
    slug="harborside-group",
    entities=("Harborside Pier", "Harborside Downtown", "Harborside Express"),
    customers=("Walk-in", "Catering Client", "Event Booking", "Delivery Apps"),
    vendors=("Fresh Produce Co", "Seafood Direct", "Beverage Distributor",
             "Linen & Laundry", "Restaurant Supply Co", "Utilities Group"),
    base_monthly_rev=42000.0, yoy=1.08, season=_RESTAURANT_SEASON,
    revenue_accounts=("400",), cogs_account="500", cogs_frac=0.34,
    has_payroll=True, owner_draw_monthly=0.0, intercompany_sweeps=True)

JB_DESIGN = ClientProfile(
    slug="jb-design",
    entities=("J.B. Design",),
    customers=("Riverside Studios", "Northgate Retail", "Bluepeak Ventures",
               "Summit Yoga", "Lantern Logistics"),
    vendors=("Cloudhost Inc", "Adobe Tools", "Freelance Collective",
             "Citywide Internet", "Apex Insurance"),
    base_monthly_rev=9500.0, yoy=1.12, season=_AGENCY_SEASON,
    revenue_accounts=("400", "410"), cogs_account="500", cogs_frac=0.18,
    has_payroll=False, owner_draw_monthly=2600.0)
```

- [ ] **Step 4: Write the generalized seeder**

Create `scripts/seed_client.py`:

```python
#!/usr/bin/env python3
"""Generalized synthetic seeder driven by a ClientProfile.

Posts a balanced multi-year double-entry book per entity:
opening capital, fixed assets + depreciation, recurring opex, revenue
(retainers/project invoices -> AR), variable COGS (vendor bills -> AP),
optional payroll, owner draws, quarterly tax. For multi-entity profiles
with intercompany_sweeps, posts a flagged inter-company cash sweep that
nets out of consolidated reports. ZERO real data; deterministic (seeded).
"""
from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, timedelta

import tenancy
import client_profiles
from client_profiles import ClientProfile

SEED = 42
START = date(2022, 1, 1)
END = date(2026, 6, 9)


def _months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m = 1 if m == 12 else m + 1
        y = y + 1 if m == 1 else y


def _day(first, rng, lo=0, hi=27):
    return first + timedelta(days=rng.randint(lo, hi))


def _entity_ids(con) -> list[int]:
    return [r[0] for r in con.execute(
        "SELECT id FROM entities WHERE active=1 ORDER BY id")]


def _booked(con, entity_id, d, desc, payee, dr_acct, cr_acct, amt,
            src="synthetic", intercompany=0):
    con.execute(
        "INSERT INTO entries(entry_date,description,payee,source,status,entity_id,is_intercompany) "
        "VALUES(?,?,?,?, 'posted', ?, ?)",
        (d.isoformat(), desc, payee, src, entity_id, intercompany))
    eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,?,0)",
                (eid, dr_acct, round(amt, 2)))
    con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,0,?)",
                (eid, cr_acct, round(amt, 2)))
    return eid


def seed(profile: ClientProfile, *, slug: str) -> dict:
    db = tenancy.resolve_db(slug)
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")

    # ensure the profile's entities exist (default already present from register)
    existing = {r[0] for r in con.execute("SELECT name FROM entities")}
    con.execute("UPDATE entities SET name=? WHERE is_default=1", (profile.entities[0],))
    for name in profile.entities[1:]:
        if name not in existing:
            con.execute("INSERT INTO entities(name,type,is_default) VALUES(?, 'location', 0)",
                        (name,))
    con.commit()

    rng = random.Random(SEED)
    n_inv = n_bill = 0
    months = list(_months(START, END))
    y0 = START.year
    eids = _entity_ids(con)

    for idx, (y, m) in enumerate(months):
        first = date(y, m, 1)
        years_in = (y - y0) + (m - 1) / 12.0
        growth = profile.yoy ** years_in
        for entity_id in eids:
            rev_target = profile.base_monthly_rev * growth * profile.season[m]
            if idx == 0:
                _booked(con, entity_id, first, "Owner opening capital", "Owner",
                        "102", "300", 60000.0, "owner")
            # recurring opex
            _booked(con, entity_id, _day(first, rng, 1, 8), "Office rent",
                    rng.choice(profile.vendors), "520", "102", 1800.0)
            if profile.has_payroll:
                _booked(con, entity_id, _day(first, rng, 1, 8), "Salaries & wages",
                        "Payroll", "555", "102", round(rev_target * 0.22, 2))
            # revenue -> AR
            for _ in range(rng.randint(2, 4)):
                issue = _day(first, rng, 0, 25)
                if issue > END:
                    continue
                amt = round(rev_target / 3 * rng.uniform(0.7, 1.3), 2)
                cust = rng.choice(profile.customers)
                rev_acct = rng.choice(profile.revenue_accounts)
                inv_eid = _booked(con, entity_id, issue, f"Invoice — {cust}", cust,
                                  "110", rev_acct, amt, "invoice")
                paid = rng.random() < 0.7
                status, paid_date = "open", None
                if paid:
                    pd = issue + timedelta(days=rng.randint(8, 40))
                    if pd <= END:
                        _booked(con, entity_id, pd, f"Payment — {cust}", cust,
                                "102", "110", amt, "ar_payment")
                        status, paid_date = "paid", pd.isoformat()
                con.execute("INSERT INTO invoices(party,issue_date,due_date,amount,status,paid_date,entry_id) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (cust, issue.isoformat(),
                             (issue + timedelta(days=30)).isoformat(), amt, status, paid_date, inv_eid))
                n_inv += 1
            # variable COGS -> AP
            vend = rng.choice(profile.vendors)
            bamt = round(rev_target * profile.cogs_frac * rng.uniform(0.8, 1.2), 2)
            issue = _day(first, rng, 0, 25)
            if issue <= END and bamt >= 1:
                bill_eid = _booked(con, entity_id, issue, f"Materials — {vend}", vend,
                                   profile.cogs_account, "200", bamt, "bill")
                paid = rng.random() < 0.6
                status, paid_date = "unpaid", None
                if paid:
                    pd = issue + timedelta(days=rng.randint(8, 40))
                    if pd <= END:
                        _booked(con, entity_id, pd, f"Bill paid — {vend}", vend,
                                "200", "102", bamt, "ap_payment")
                        status, paid_date = "paid", pd.isoformat()
                con.execute("INSERT INTO bills(party,issue_date,due_date,amount,status,paid_date,entry_id) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (vend, issue.isoformat(),
                             (issue + timedelta(days=30)).isoformat(), bamt, status, paid_date, bill_eid))
                n_bill += 1
            # owner draw
            if profile.owner_draw_monthly > 0:
                dd = _day(first, rng, 18, 27)
                if dd <= END:
                    _booked(con, entity_id, dd, "Owner draw", "Owner",
                            "300", "102", profile.owner_draw_monthly, "owner")
            # quarterly estimated tax
            if m in (3, 6, 9, 12):
                td = _day(first, rng, 10, 15)
                if td <= END:
                    _booked(con, entity_id, td, "Estimated income tax", "Tax Authority",
                            "592", "102", round(rev_target * 0.15, 2), "tax")

        # parent inter-company cash sweep (multi-entity only): move cash from
        # entity[1] to entity[0], flagged so consolidated reports net it out.
        if profile.intercompany_sweeps and len(eids) > 1:
            sd = _day(first, rng, 25, 27)
            if sd <= END:
                amt = round(rng.uniform(2000, 6000), 2)
                _booked(con, eids[0], sd, "Inter-company cash sweep (in)", "Internal",
                        "102", "300", amt, "intercompany", intercompany=1)
                _booked(con, eids[1], sd, "Inter-company cash sweep (out)", "Internal",
                        "300", "102", amt, "intercompany", intercompany=1)

    con.commit()
    dr, cr = con.execute(
        "SELECT ROUND(SUM(dr),2),ROUND(SUM(cr),2) FROM splits").fetchone()
    con.close()
    return {"slug": slug, "invoices": n_inv, "bills": n_bill, "dr": dr, "cr": cr}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug", choices=["guitar-works", "harborside-group", "jb-design"])
    args = parser.parse_args()
    profile = {"guitar-works": client_profiles.GUITAR_WORKS,
               "harborside-group": client_profiles.HARBORSIDE_GROUP,
               "jb-design": client_profiles.JB_DESIGN}[args.slug]
    print(seed(profile, slug=args.slug))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_seed_client.py -q`
Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/client_profiles.py scripts/seed_client.py scripts/test_seed_client.py
git commit -m "feat(lisza): per-client synthetic profiles + generalized seeder"
```

---

## Task 9: Seed Harborside (multi-entity) + consolidation test

**Files:**
- Test: `scripts/test_seed_client.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_seed_client.py`:

```python
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
    # consolidated cash (nets intercompany) differs from naive all-entry sum
    naive = reports_entity.account_balance(con, "102")
    consolidated = reports_entity.account_balance(con, "102", consolidated=True)
    assert consolidated != naive
    con.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_seed_client.py -k harborside -q`
Expected: FAIL initially only if seeding regressed; if it passes immediately that is acceptable (the seeder already supports multi-entity). If it fails, fix `seed_client.py` per Task 8 Step 4.

- [ ] **Step 3: Confirm pass**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_seed_client.py -q`
Expected: PASS (2 passed)

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/test_seed_client.py
git commit -m "test(lisza): harborside multi-entity + inter-company elimination"
```

---

## Task 10: Seed all three clients for real + refresh registry

**Files:**
- Create: `scripts/build_demo_clients.py`

- [ ] **Step 1: Write the orchestration script**

Create `scripts/build_demo_clients.py`:

```python
#!/usr/bin/env python3
"""Build the three demo clients end-to-end into the real LISZA_HOME:
register (if absent), seed Harborside + J.B. Design from profiles, then
refresh all summaries. Guitar Works comes from migrate_to_guitar_works.py
(its book is the migrated legacy data), so it is only refreshed here.
"""
from __future__ import annotations

import sqlite3

import tenancy
import client_profiles
import seed_client

PLAN = [
    ("harborside-group", "Harborside Restaurant Group", "llc",
     client_profiles.HARBORSIDE_GROUP),
    ("jb-design", "J.B. Design", "sole_prop", client_profiles.JB_DESIGN),
]


def _registered(slug: str) -> bool:
    return any(r.slug == slug for r in tenancy.list_clients())


def main() -> int:
    for slug, name, etype, profile in PLAN:
        if not _registered(slug):
            tenancy.register_client(slug=slug, display_name=name, entity_type=etype)
        seed_client.seed(profile, slug=slug)
    n = tenancy.refresh_all()
    print(f"built {len(PLAN)} seeded clients; refreshed {n} summaries")
    for r in tenancy.list_clients():
        s = tenancy.refresh_summary(r.slug)
        print(f"  {r.slug:18} cash={s['cash']:>12,.2f} AR={s['open_ar']:>10,.2f} AP={s['open_ap']:>10,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the full build (after migration from Task 7 has run)**

Run: `cd /home/workspace/LISZA/scripts && python3 build_demo_clients.py`
Expected: three client lines printed with non-zero cash/AR/AP; "refreshed 3 summaries".

- [ ] **Step 3: Verify each book balances**

Run:
```bash
cd /home/workspace/LISZA/scripts
for s in guitar-works harborside-group jb-design; do
  LISZA_DB="$(python3 -c "import tenancy; print(tenancy.resolve_db('$s'))")" \
  python3 -c "import os,sqlite3; c=sqlite3.connect(os.environ['LISZA_DB']); \
  dr,cr=c.execute('SELECT ROUND(SUM(dr),2),ROUND(SUM(cr),2) FROM splits s JOIN entries e ON e.id=s.entry_id AND e.status=\"posted\"').fetchone(); \
  print('$s', dr, cr, 'OK' if abs(dr-cr)<0.01 else 'IMBALANCED')"
done
```
Expected: each line ends `OK`.

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/build_demo_clients.py
git commit -m "feat(lisza): build all three demo clients + refresh registry cache"
```

---

## Task 11: Full-suite green + docs

**Files:**
- Modify: `TODO.md` (check off Step 2 items + foundation)

- [ ] **Step 1: Run the entire test suite**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest -q`
Expected: all tests pass (tenancy 8, reports_entity 2, seed_client 2, ledger_tools existing, reports 6 against guitar-works via env).

> If `test_reports.py` fails because `LISZA_DB` is unset, run it with the env:
> `LISZA_DB="$(python3 -c 'import tenancy; print(tenancy.resolve_db("guitar-works"))')" python3 -m pytest test_reports.py -q`

- [ ] **Step 2: Mark the Step-2 client items done in TODO.md**

In `TODO.md`, change the three Step-2 client checkboxes (`Client 1`, `Client 2`, `Client 3`) and the seeder line from `- [ ]` to `- [x]`, and add a one-line note under the section header:
`> Foundation (Spec 1) implemented 2026-06-29 — DB-per-client + lisza.db registry + entity dimension. Dashboard/tiles/cron remain.`

- [ ] **Step 3: Commit**

```bash
cd /home/workspace/LISZA
git add TODO.md
git commit -m "docs(lisza): mark multi-tenant foundation (Spec 1) complete"
```

---

## Self-Review notes (addressed)

- **Spec coverage:** tenancy model (Tasks 2-4), entity dimension + eliminations (Tasks 1, 5, 9), profile-in-book (Tasks 1, 3), registry cache (Task 4), existing-script resolution (Task 6), migrate Client 1 (Task 7), seed Clients 2 & 3 (Tasks 8-10), isolation test (Task 3), entity report test (Task 5), suite green (Task 11). All spec sections map to a task.
- **Naming consistency:** `resolve_db`, `resolve_db_path`, `register_client`, `refresh_summary`, `refresh_all`, `account_balance`, `ensure_book_schema`, `seed` used identically across tasks.
- **Known typo flagged inline:** the stray `shutil.cop2` alias line in Task 7 Step 1 is called out to delete.
- **Scope:** foundation only; dashboard/tiles/cron explicitly deferred to Specs 2-4.
