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
