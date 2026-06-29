#!/usr/bin/env python3
"""LISZA multi-tenant seam: client registry, DB resolution, summary cache.

Principle: isolation is physical (one SQLite file per client); the shared
registry indexes and caches but never owns truth.
"""
from __future__ import annotations

import csv
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import book_schema

COA_PATH = Path(__file__).resolve().parent.parent / "coa.csv"
HOUSE_SLUG = "_house"

# Default housekeeping config for the house tenant. The seam: add tiles here
# (or via set_house_config) and the Admin -> Housekeeping surface renders them.
HOUSE_CONFIG_DEFAULT = {
    "tiles": [
        {"key": "my_pnl", "label": "My P&L", "hint": "Overview tab"},
        {"key": "my_ar", "label": "Fees receivable", "hint": "what clients owe me"},
        {"key": "my_payroll", "label": "Staff payroll", "hint": "Payroll tab"},
    ]
}


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
    kind         TEXT NOT NULL DEFAULT 'client',
    last_close_date TEXT,
    next_filing_due TEXT
);
CREATE TABLE IF NOT EXISTS client_summary (
    client_id   TEXT PRIMARY KEY REFERENCES clients(client_id),
    as_of       TEXT,
    cash        REAL, open_ar REAL, open_ap REAL,
    ar_count    INTEGER, ap_count INTEGER, last_entry_date TEXT,
    entity_count INTEGER
);
CREATE TABLE IF NOT EXISTS bookkeeper_prefs (
    bookkeeper_id  TEXT PRIMARY KEY,
    layout         TEXT DEFAULT 'tile',
    card_fields_json TEXT,
    default_client TEXT,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _ensure_registry_columns(con: sqlite3.Connection) -> None:
    if not book_schema._has_column(con, "client_summary", "entity_count"):
        con.execute("ALTER TABLE client_summary ADD COLUMN entity_count INTEGER")
    if not book_schema._has_column(con, "clients", "kind"):
        con.execute("ALTER TABLE clients ADD COLUMN kind TEXT NOT NULL DEFAULT 'client'")


def registry_db() -> Path:
    path = registry_path()
    con = sqlite3.connect(path)
    con.executescript(REGISTRY_SCHEMA)
    _ensure_registry_columns(con)
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
        "SELECT * FROM clients WHERE status=? AND kind != 'house' ORDER BY slug",
        (status,)).fetchall()
    con.close()
    return [ClientRow(r["client_id"], r["slug"], r["db_path"], r["status"],
                      r["display_name"], r["entity_type"]) for r in rows]


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
                    active_window: str = "1y", kind: str = "client") -> str:
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
           (client_id, slug, db_path, status, display_name, entity_type, kind)
           VALUES (?,?,?,?,?,?,?)""",
        (client_id, slug, str(db), "active", display_name, entity_type, kind))
    reg.commit()
    reg.close()
    return client_id


def ensure_house() -> str:
    """Register the bookkeeper's own 'house' book idempotently.

    Returns the existing client_id if `_house` is already registered, otherwise
    registers it (kind='house', hidden from the roster) and returns the new id.
    """
    registry_db()
    reg = sqlite3.connect(registry_path())
    row = reg.execute(
        "SELECT client_id FROM clients WHERE slug=?", (HOUSE_SLUG,)).fetchone()
    reg.close()
    if row:
        client_id = row[0]
    else:
        client_id = register_client(
            slug=HOUSE_SLUG, display_name="House (My Books)", kind="house")
    # register_client builds the core/entity/payroll schema but not the report
    # objects (v_account_balances view, category_overrides) the console Overview
    # reads — real client books get those from ensure_report_schema. Apply them
    # here so the house book is console-ready and self-heal older house books.
    import ensure_report_schema
    ensure_report_schema.apply(str(resolve_db(HOUSE_SLUG)))
    return client_id


def get_house_config() -> dict:
    """Read the house housekeeping config, falling back to the default."""
    registry_db()
    reg = sqlite3.connect(registry_path())
    row = reg.execute(
        "SELECT card_fields_json FROM bookkeeper_prefs WHERE bookkeeper_id=?",
        (HOUSE_SLUG,)).fetchone()
    reg.close()
    if row and row[0]:
        import json
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            pass
    return dict(HOUSE_CONFIG_DEFAULT)


def set_house_config(config: dict) -> None:
    """Persist a housekeeping config for the house tenant (extension seam)."""
    import json
    registry_db()
    reg = sqlite3.connect(registry_path())
    reg.execute(
        """INSERT OR REPLACE INTO bookkeeper_prefs (bookkeeper_id, card_fields_json, updated_at)
           VALUES (?, ?, datetime('now'))""",
        (HOUSE_SLUG, json.dumps(config)))
    reg.commit()
    reg.close()


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def compute_next_filing_due(cadence: str, reference: date,
                            fiscal_year_end: str = "12-31") -> date:
    """Next filing deadline strictly after `reference`, per cadence.

    monthly   -> last day of the month following the reference month.
    quarterly -> next of Apr 30 / Jul 31 / Oct 31 / Jan 31 after reference.
    annual    -> Apr 15 of the year after the next fiscal-year-end on/after ref.
    """
    cad = (cadence or "quarterly").lower()
    if cad == "monthly":
        ny, nm = (reference.year + 1, 1) if reference.month == 12 \
            else (reference.year, reference.month + 1)
        return _last_day_of_month(ny, nm)
    if cad == "annual":
        mm, dd = (int(x) for x in fiscal_year_end.split("-"))
        fye = date(reference.year, mm, dd)
        if fye < reference:
            fye = date(reference.year + 1, mm, dd)
        return date(fye.year + 1, 4, 15)
    # quarterly (default): sweep two years so we always find a date after ref
    candidates = []
    for y in (reference.year, reference.year + 1):
        candidates += [date(y, 4, 30), date(y, 7, 31), date(y, 10, 31),
                       date(y + 1, 1, 31)]
    for c in sorted(candidates):
        if c > reference:
            return c
    raise AssertionError("unreachable: two-year sweep always yields a date")


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
    entity_count = con.execute(
        "SELECT COUNT(*) FROM entities WHERE active=1").fetchone()[0]
    cid, cadence, fye = con.execute(
        "SELECT client_id, filing_cadence, fiscal_year_end FROM client_profile"
    ).fetchone()
    con.close()

    next_due = None
    if last_entry:
        next_due = compute_next_filing_due(
            cadence, date.fromisoformat(last_entry), fye or "12-31").isoformat()

    registry_db()
    reg = sqlite3.connect(registry_path())
    reg.execute(
        """INSERT OR REPLACE INTO client_summary
           (client_id, as_of, cash, open_ar, open_ap, ar_count, ap_count,
            last_entry_date, entity_count)
           VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)""",
        (cid, cash, open_ar, open_ap, ar_count, ap_count, last_entry, entity_count))
    reg.execute("UPDATE clients SET last_close_date=?, next_filing_due=? WHERE client_id=?",
                (last_entry, next_due, cid))
    reg.commit()
    reg.close()
    return {"cash": cash, "open_ar": open_ar, "open_ap": open_ap,
            "entity_count": entity_count, "next_filing_due": next_due}


def refresh_all() -> int:
    n = 0
    for row in list_clients():
        refresh_summary(row.slug)
        n += 1
    return n
