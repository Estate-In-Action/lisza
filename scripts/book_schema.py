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
