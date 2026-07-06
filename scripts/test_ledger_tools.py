import csv
import json
import sqlite3
from pathlib import Path

import pytest

import ledger_tools


def _db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    db = tmp_path / "ledger.db"
    monkeypatch.setattr(ledger_tools, "DB_PATH", db)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE accounts (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            sign_normal TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date TEXT NOT NULL,
            description TEXT NOT NULL,
            payee TEXT,
            source TEXT NOT NULL,
            source_ref TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            posted_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            notes TEXT
        );
        CREATE TABLE splits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            account TEXT NOT NULL REFERENCES accounts(code),
            dr REAL NOT NULL DEFAULT 0,
            cr REAL NOT NULL DEFAULT 0,
            memo TEXT
        );
        INSERT INTO accounts(code, name, type, sign_normal) VALUES
          ('102','Business Checking','asset','debit'),
          ('400','Service Revenue','income','credit'),
          ('540','Software','expense','debit');
        """
    )
    ledger_tools.ensure_reconciliation_schema(con)
    return con


def test_post_journal_requires_balanced_splits(tmp_path, monkeypatch):
    con = _db(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="unbalanced"):
        ledger_tools.post_journal(
            con,
            entry_date="2026-06-29",
            description="bad",
            splits=[ledger_tools.Split("102", 100, 0), ledger_tools.Split("400", 0, 90)],
        )
    entry_id = ledger_tools.post_journal(
        con,
        entry_date="2026-06-29",
        description="invoice paid",
        splits=[ledger_tools.Split("102", 100, 0), ledger_tools.Split("400", 0, 100)],
    )
    totals = con.execute("SELECT ROUND(SUM(dr),2), ROUND(SUM(cr),2) FROM splits WHERE entry_id=?", (entry_id,)).fetchone()
    assert tuple(totals) == (100.0, 100.0)
    audit = con.execute("SELECT event_type, payload_json FROM journal_audit WHERE entry_id=?", (entry_id,)).fetchone()
    assert audit["event_type"] == "journal_posted"
    assert json.loads(audit["payload_json"])["split_count"] == 2
    con.close()


def test_adjustment_reverses_original_and_posts_replacement(tmp_path, monkeypatch):
    con = _db(tmp_path, monkeypatch)
    original = ledger_tools.post_journal(
        con,
        entry_date="2026-06-29",
        description="misposted expense",
        splits=[ledger_tools.Split("540", 50, 0), ledger_tools.Split("102", 0, 50)],
    )
    created = ledger_tools.post_adjustment(
        con,
        original_entry_id=original,
        entry_date="2026-06-30",
        description="correct expense amount",
        replacement_splits=[ledger_tools.Split("540", 45, 0), ledger_tools.Split("102", 0, 45)],
    )
    assert len(created) == 2
    rows = con.execute("SELECT source_ref FROM entries WHERE id IN (?, ?) ORDER BY id", created).fetchall()
    assert rows[0]["source_ref"].endswith(":reversal")
    assert rows[1]["source_ref"].endswith(":replacement")
    audit = con.execute(
        "SELECT event_type, payload_json FROM journal_audit WHERE entry_id=? AND event_type='adjustment_created'",
        (original,),
    ).fetchone()
    assert audit is not None
    assert json.loads(audit["payload_json"])["created_entry_ids"] == created
    con.close()


def test_reconcile_imports_and_exact_matches_statement_line(tmp_path, monkeypatch):
    con = _db(tmp_path, monkeypatch)
    ledger_tools.post_journal(
        con,
        entry_date="2026-06-29",
        description="payment",
        splits=[ledger_tools.Split("102", 100, 0), ledger_tools.Split("400", 0, 100)],
    )
    statement = tmp_path / "statement.csv"
    with statement.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "description", "amount", "id"])
        writer.writeheader()
        writer.writerow({"date": "2026-06-29", "description": "payment", "amount": "100.00", "id": "bank-1"})
    assert ledger_tools.import_statement(con, statement, statement_account="102") == 1
    assert ledger_tools.auto_match_exact(con, statement_account="102") == 1
    summary = ledger_tools.reconciliation_summary(con, statement_account="102")
    assert summary["status_counts"] == {"matched": 1}
    method = con.execute("SELECT method FROM reconciliation_matches").fetchone()["method"]
    assert method == "auto_exact"
    con.close()


def test_reconcile_date_window_matches_unique_candidate(tmp_path, monkeypatch):
    con = _db(tmp_path, monkeypatch)
    entry_id = ledger_tools.post_journal(
        con,
        entry_date="2026-06-27",
        description="card settlement",
        splits=[ledger_tools.Split("540", 25, 0), ledger_tools.Split("102", 0, 25)],
    )
    statement = tmp_path / "statement.csv"
    with statement.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "description", "amount", "id"])
        writer.writeheader()
        writer.writerow({"date": "2026-06-30", "description": "card settlement", "amount": "-25.00", "id": "bank-2"})
    assert ledger_tools.import_statement(con, statement, statement_account="102") == 1
    assert ledger_tools.auto_match_exact(con, statement_account="102", date_tolerance_days=3) == 1
    row = con.execute("SELECT entry_id, method FROM reconciliation_matches").fetchone()
    assert row["entry_id"] == entry_id
    assert row["method"] == "auto_date_window"
    con.close()


def test_reconcile_does_not_reuse_already_matched_entry(tmp_path, monkeypatch):
    con = _db(tmp_path, monkeypatch)
    ledger_tools.post_journal(
        con,
        entry_date="2026-06-29",
        description="payment",
        splits=[ledger_tools.Split("102", 100, 0), ledger_tools.Split("400", 0, 100)],
    )
    statement = tmp_path / "statement.csv"
    with statement.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "description", "amount", "id"])
        writer.writeheader()
        writer.writerow({"date": "2026-06-29", "description": "payment", "amount": "100.00", "id": "bank-1"})
        writer.writerow({"date": "2026-06-29", "description": "payment duplicate", "amount": "100.00", "id": "bank-2"})
    assert ledger_tools.import_statement(con, statement, statement_account="102") == 2
    assert ledger_tools.auto_match_exact(con, statement_account="102") == 1
    summary = ledger_tools.reconciliation_summary(con, statement_account="102")
    assert summary["status_counts"] == {"matched": 1, "unmatched": 1}
    con.close()
