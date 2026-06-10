#!/usr/bin/env python3
"""Initialize LISZA/ledger.db with double-entry schema and load COA."""
from __future__ import annotations
import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "ledger.db"
COA = ROOT / "coa.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL CHECK(type IN ('asset','liability','equity','income','expense')),
    sign_normal TEXT NOT NULL CHECK(sign_normal IN ('debit','credit')),
    grp         TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date  TEXT NOT NULL,
    description TEXT NOT NULL,
    payee       TEXT,
    source      TEXT NOT NULL,         -- manual | telegram | email | bank_import | sheet
    source_ref  TEXT,                   -- bot msg id, email msg id, file path
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','posted','void')),
    posted_at   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS splits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id   INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    account    TEXT NOT NULL REFERENCES accounts(code),
    dr         REAL NOT NULL DEFAULT 0,
    cr         REAL NOT NULL DEFAULT 0,
    memo       TEXT,
    CHECK ((dr = 0) OR (cr = 0)),
    CHECK (dr >= 0 AND cr >= 0)
);

CREATE INDEX IF NOT EXISTS idx_splits_entry   ON splits(entry_id);
CREATE INDEX IF NOT EXISTS idx_splits_account ON splits(account);
CREATE INDEX IF NOT EXISTS idx_entries_date   ON entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_entries_status ON entries(status);

CREATE TABLE IF NOT EXISTS pending_inbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    source      TEXT NOT NULL,         -- telegram | email
    raw_path    TEXT,                   -- path to attachment / raw payload
    parsed_json TEXT,                   -- vision/parser output
    suggested_account TEXT,
    suggested_amount  REAL,
    status      TEXT NOT NULL DEFAULT 'new'
                CHECK(status IN ('new','reviewed','posted','rejected')),
    entry_id    INTEGER REFERENCES entries(id),
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS payee_rules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern      TEXT NOT NULL,
    account_code TEXT NOT NULL REFERENCES accounts(code),
    priority     INTEGER NOT NULL DEFAULT 100,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Accounts Receivable: client invoices. status='open' drives AR aging.
CREATE TABLE IF NOT EXISTS invoices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    party      TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    due_date   TEXT NOT NULL,
    amount     REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','paid')),
    paid_date  TEXT,
    entry_id   INTEGER REFERENCES entries(id),
    memo       TEXT
);

-- Accounts Payable: vendor bills. status='unpaid' drives AP aging.
CREATE TABLE IF NOT EXISTS bills (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    party      TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    due_date   TEXT NOT NULL,
    amount     REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'unpaid' CHECK(status IN ('unpaid','paid')),
    paid_date  TEXT,
    entry_id   INTEGER REFERENCES entries(id),
    memo       TEXT
);

-- Manual recategorization: maps an entry to a user-chosen account, overriding
-- the originally-posted expense/income split account in reports.
CREATE TABLE IF NOT EXISTS category_overrides (
    entry_id     INTEGER PRIMARY KEY REFERENCES entries(id),
    account_code TEXT NOT NULL REFERENCES accounts(code),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_bills_status    ON bills(status);

CREATE VIEW IF NOT EXISTS v_account_balances AS
SELECT a.code, a.name, a.type, a.sign_normal,
       COALESCE(SUM(s.dr), 0) AS total_dr,
       COALESCE(SUM(s.cr), 0) AS total_cr,
       CASE
         WHEN a.sign_normal = 'debit'  THEN COALESCE(SUM(s.dr), 0) - COALESCE(SUM(s.cr), 0)
         WHEN a.sign_normal = 'credit' THEN COALESCE(SUM(s.cr), 0) - COALESCE(SUM(s.dr), 0)
       END AS balance
FROM accounts a
LEFT JOIN splits s ON s.account = a.code
LEFT JOIN entries e ON e.id = s.entry_id AND e.status = 'posted'
GROUP BY a.code;

CREATE TRIGGER IF NOT EXISTS trg_post_balance_check
BEFORE UPDATE OF status ON entries
WHEN NEW.status = 'posted' AND OLD.status != 'posted'
BEGIN
    SELECT CASE
        WHEN (SELECT ABS(SUM(dr) - SUM(cr)) FROM splits WHERE entry_id = NEW.id) > 0.005
        THEN RAISE(ABORT, 'unbalanced entry: debits != credits')
    END;
END;
"""

def main() -> int:
    if not COA.exists():
        print(f"ERROR: missing {COA}", file=sys.stderr)
        return 1

    fresh = not DB.exists()
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)

    with COA.open() as f:
        reader = csv.DictReader(f)
        rows = [
            (r["code"], r["name"], r["type"], r["sign_normal"], r.get("group"))
            for r in reader
        ]

    con.executemany(
        """INSERT INTO accounts(code, name, type, sign_normal, grp)
           VALUES(?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET
             name=excluded.name, type=excluded.type,
             sign_normal=excluded.sign_normal, grp=excluded.grp""",
        rows,
    )
    con.commit()

    n = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    by_type = dict(con.execute(
        "SELECT type, COUNT(*) FROM accounts GROUP BY type"
    ).fetchall())
    con.close()

    print(f"{'created' if fresh else 'updated'} {DB}")
    print(f"accounts: {n} total — {by_type}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
