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
from pathlib import Path

import book_schema

COA_PATH = Path(__file__).resolve().parent.parent / "coa.csv"


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
