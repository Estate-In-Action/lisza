#!/usr/bin/env python3
"""Non-interactive LISZA ledger tools.

Adds three review-first capabilities without mutating posted history in place:
manual journal posting, adjusting entries that reverse/correct a prior entry,
and bank-statement reconciliation tables/matching.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

DB_PATH = Path(os.environ.get("LISZA_DB", "LISZA/ledger.db"))


@dataclass(frozen=True)
class Split:
    account: str
    dr: float
    cr: float
    memo: str | None = None


def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    ensure_reconciliation_schema(con)
    return con


def iso_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def ensure_reconciliation_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS statement_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_account TEXT NOT NULL,
            statement_date TEXT NOT NULL,
            description TEXT,
            amount REAL NOT NULL,
            external_ref TEXT,
            status TEXT NOT NULL DEFAULT 'unmatched'
                CHECK(status IN ('unmatched','matched','ignored')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(statement_account, statement_date, amount, external_ref)
        );

        CREATE TABLE IF NOT EXISTS reconciliation_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_line_id INTEGER NOT NULL REFERENCES statement_lines(id),
            entry_id INTEGER NOT NULL REFERENCES entries(id),
            matched_at TEXT NOT NULL DEFAULT (datetime('now')),
            method TEXT NOT NULL DEFAULT 'manual'
                CHECK(method IN ('manual','auto_exact','auto_date_window')),
            notes TEXT,
            UNIQUE(statement_line_id, entry_id)
        );
        """
    )
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='reconciliation_matches'"
    ).fetchone()
    if row and "auto_date_window" not in (row[0] or ""):
        con.executescript(
            """
            ALTER TABLE reconciliation_matches RENAME TO reconciliation_matches_old;
            CREATE TABLE reconciliation_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                statement_line_id INTEGER NOT NULL REFERENCES statement_lines(id),
                entry_id INTEGER NOT NULL REFERENCES entries(id),
                matched_at TEXT NOT NULL DEFAULT (datetime('now')),
                method TEXT NOT NULL DEFAULT 'manual'
                    CHECK(method IN ('manual','auto_exact','auto_date_window')),
                notes TEXT,
                UNIQUE(statement_line_id, entry_id)
            );
            INSERT INTO reconciliation_matches(id, statement_line_id, entry_id, matched_at, method, notes)
                SELECT id, statement_line_id, entry_id, matched_at, method, notes
                FROM reconciliation_matches_old;
            DROP TABLE reconciliation_matches_old;
            """
        )
    con.commit()


def parse_split(text: str) -> Split:
    # account:dr:100.25:memo or account:cr:100.25
    parts = text.split(":", 3)
    if len(parts) < 3:
        raise ValueError("split must be account:dr|cr:amount[:memo]")
    account, side, amount_text = parts[:3]
    memo = parts[3] if len(parts) == 4 else None
    amount = round(float(amount_text), 2)
    if amount <= 0:
        raise ValueError("split amount must be positive")
    side = side.lower()
    if side not in {"dr", "cr"}:
        raise ValueError("split side must be dr or cr")
    return Split(account=account, dr=amount if side == "dr" else 0.0, cr=amount if side == "cr" else 0.0, memo=memo)


def validate_splits(con: sqlite3.Connection, splits: Iterable[Split]) -> list[Split]:
    rows = list(splits)
    if len(rows) < 2:
        raise ValueError("journal entry needs at least two splits")
    for split in rows:
        exists = con.execute("SELECT 1 FROM accounts WHERE code=? AND active=1", (split.account,)).fetchone()
        if not exists:
            raise ValueError(f"unknown or inactive account: {split.account}")
    total_dr = round(sum(s.dr for s in rows), 2)
    total_cr = round(sum(s.cr for s in rows), 2)
    if abs(total_dr - total_cr) > 0.005:
        raise ValueError(f"unbalanced entry: debits {total_dr:.2f} != credits {total_cr:.2f}")
    return rows


def post_journal(
    con: sqlite3.Connection,
    *,
    entry_date: str,
    description: str,
    splits: list[Split],
    payee: str | None = None,
    source: str = "manual",
    source_ref: str | None = None,
    notes: str | None = None,
    posted: bool = True,
) -> int:
    rows = validate_splits(con, splits)
    now = iso_now()
    status = "posted" if posted else "pending"
    posted_at = now if posted else None
    cur = con.execute(
        """INSERT INTO entries(entry_date, description, payee, source, source_ref, status, posted_at, created_at, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (entry_date, description, payee, source, source_ref, status, posted_at, now, notes),
    )
    entry_id = int(cur.lastrowid)
    for split in rows:
        con.execute(
            "INSERT INTO splits(entry_id, account, dr, cr, memo) VALUES (?, ?, ?, ?, ?)",
            (entry_id, split.account, split.dr, split.cr, split.memo),
        )
    con.commit()
    return entry_id


def adjusting_splits_for_entry(con: sqlite3.Connection, entry_id: int) -> list[Split]:
    rows = con.execute("SELECT account, dr, cr, memo FROM splits WHERE entry_id=? ORDER BY id", (entry_id,)).fetchall()
    if not rows:
        raise ValueError(f"entry {entry_id} has no splits")
    return [
        Split(
            account=row["account"],
            dr=float(row["cr"] or 0),
            cr=float(row["dr"] or 0),
            memo=f"Reverse entry {entry_id}" if not row["memo"] else f"Reverse entry {entry_id}: {row['memo']}",
        )
        for row in rows
    ]


def post_adjustment(
    con: sqlite3.Connection,
    *,
    original_entry_id: int,
    entry_date: str,
    description: str,
    replacement_splits: list[Split] | None = None,
    notes: str | None = None,
) -> list[int]:
    original = con.execute("SELECT id, status FROM entries WHERE id=?", (original_entry_id,)).fetchone()
    if not original:
        raise ValueError(f"entry {original_entry_id} not found")
    if original["status"] != "posted":
        raise ValueError("only posted entries need reversing adjustments; edit pending entries before posting")
    reversal_id = post_journal(
        con,
        entry_date=entry_date,
        description=f"Reverse entry {original_entry_id}: {description}",
        splits=adjusting_splits_for_entry(con, original_entry_id),
        source="manual",
        source_ref=f"adjustment:{original_entry_id}:reversal",
        notes=notes,
        posted=True,
    )
    ids = [reversal_id]
    if replacement_splits:
        ids.append(
            post_journal(
                con,
                entry_date=entry_date,
                description=description,
                splits=replacement_splits,
                source="manual",
                source_ref=f"adjustment:{original_entry_id}:replacement",
                notes=notes,
                posted=True,
            )
        )
    return ids


def import_statement(con: sqlite3.Connection, csv_path: Path, *, statement_account: str) -> int:
    inserted = 0
    with csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            date_value = row.get("date") or row.get("Date") or row.get("posted_date")
            amount_value = row.get("amount") or row.get("Amount")
            if not date_value or amount_value is None:
                continue
            desc = row.get("description") or row.get("Description") or row.get("payee") or row.get("Payee")
            ref = row.get("id") or row.get("ref") or row.get("Reference") or f"{date_value}:{desc}:{amount_value}"
            before = con.total_changes
            try:
                con.execute(
                    """INSERT OR IGNORE INTO statement_lines(statement_account, statement_date, description, amount, external_ref)
                       VALUES (?, ?, ?, ?, ?)""",
                    (statement_account, date_value, desc, round(float(amount_value), 2), ref),
                )
                inserted += int(con.total_changes > before)
            except sqlite3.IntegrityError:
                continue
    con.commit()
    return inserted


def _entry_is_already_matched(con: sqlite3.Connection, entry_id: int, statement_account: str) -> bool:
    row = con.execute(
        """SELECT 1
           FROM reconciliation_matches rm
           JOIN statement_lines sl ON sl.id=rm.statement_line_id
           WHERE rm.entry_id=? AND sl.statement_account=?
           LIMIT 1""",
        (entry_id, statement_account),
    ).fetchone()
    return row is not None


def _candidate_entries(
    con: sqlite3.Connection,
    *,
    statement_account: str,
    amount: float,
    start_date: str,
    end_date: str,
) -> list[int]:
    rows = con.execute(
        """SELECT DISTINCT e.id
           FROM entries e JOIN splits s ON s.entry_id=e.id
           WHERE e.status='posted'
             AND e.entry_date >= ? AND e.entry_date <= ?
             AND s.account=?
             AND ROUND(s.dr - s.cr, 2)=?
           ORDER BY e.id""",
        (start_date, end_date, statement_account, round(float(amount), 2)),
    ).fetchall()
    return [
        int(row["id"])
        for row in rows
        if not _entry_is_already_matched(con, int(row["id"]), statement_account)
    ]


def _match_line(con: sqlite3.Connection, *, line_id: int, entry_id: int, method: str) -> None:
    con.execute(
        "INSERT OR IGNORE INTO reconciliation_matches(statement_line_id, entry_id, method) VALUES (?, ?, ?)",
        (line_id, entry_id, method),
    )
    con.execute("UPDATE statement_lines SET status='matched' WHERE id=?", (line_id,))


def auto_match_exact(con: sqlite3.Connection, *, statement_account: str, date_tolerance_days: int = 3) -> int:
    matched = 0
    statement_rows = con.execute(
        """SELECT * FROM statement_lines
           WHERE statement_account=? AND status='unmatched'
           ORDER BY statement_date, id""",
        (statement_account,),
    ).fetchall()
    for line in statement_rows:
        candidates = _candidate_entries(
            con,
            statement_account=statement_account,
            amount=float(line["amount"]),
            start_date=line["statement_date"],
            end_date=line["statement_date"],
        )
        if len(candidates) != 1:
            if date_tolerance_days <= 0:
                continue
            statement_date = datetime.strptime(line["statement_date"], "%Y-%m-%d").date()
            candidates = _candidate_entries(
                con,
                statement_account=statement_account,
                amount=float(line["amount"]),
                start_date=(statement_date - timedelta(days=date_tolerance_days)).isoformat(),
                end_date=(statement_date + timedelta(days=date_tolerance_days)).isoformat(),
            )
            if len(candidates) != 1:
                continue
            _match_line(con, line_id=int(line["id"]), entry_id=candidates[0], method="auto_date_window")
            matched += 1
            continue
        _match_line(con, line_id=int(line["id"]), entry_id=candidates[0], method="auto_exact")
        matched += 1
    con.commit()
    return matched


def reconciliation_summary(con: sqlite3.Connection, *, statement_account: str) -> dict:
    rows = con.execute(
        "SELECT status, COUNT(*), ROUND(COALESCE(SUM(amount),0),2) FROM statement_lines WHERE statement_account=? GROUP BY status",
        (statement_account,),
    ).fetchall()
    return {
        "statement_account": statement_account,
        "status_counts": {row[0]: row[1] for row in rows},
        "status_amounts": {row[0]: row[2] for row in rows},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    journal = sub.add_parser("journal")
    journal.add_argument("--date", required=True)
    journal.add_argument("--description", required=True)
    journal.add_argument("--split", action="append", required=True, help="account:dr|cr:amount[:memo]")
    journal.add_argument("--payee")
    journal.add_argument("--notes")
    journal.add_argument("--pending", action="store_true")

    adjust = sub.add_parser("adjust")
    adjust.add_argument("entry_id", type=int)
    adjust.add_argument("--date", required=True)
    adjust.add_argument("--description", required=True)
    adjust.add_argument("--split", action="append", help="optional replacement account:dr|cr:amount[:memo]")
    adjust.add_argument("--notes")

    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--account", required=True)
    reconcile.add_argument("--csv", type=Path)
    reconcile.add_argument("--auto-match", action="store_true")
    reconcile.add_argument("--date-tolerance-days", type=int, default=3)

    args = parser.parse_args()
    con = get_db()
    if args.cmd == "journal":
        entry_id = post_journal(
            con,
            entry_date=args.date,
            description=args.description,
            payee=args.payee,
            notes=args.notes,
            splits=[parse_split(s) for s in args.split],
            posted=not args.pending,
        )
        print(json.dumps({"entry_id": entry_id, "status": "pending" if args.pending else "posted"}, indent=2))
    elif args.cmd == "adjust":
        replacement = [parse_split(s) for s in args.split] if args.split else None
        ids = post_adjustment(
            con,
            original_entry_id=args.entry_id,
            entry_date=args.date,
            description=args.description,
            replacement_splits=replacement,
            notes=args.notes,
        )
        print(json.dumps({"created_entry_ids": ids}, indent=2))
    elif args.cmd == "reconcile":
        imported = import_statement(con, args.csv, statement_account=args.account) if args.csv else 0
        matched = auto_match_exact(
            con,
            statement_account=args.account,
            date_tolerance_days=max(0, args.date_tolerance_days),
        ) if args.auto_match else 0
        print(json.dumps({"imported": imported, "matched": matched, "summary": reconciliation_summary(con, statement_account=args.account)}, indent=2, sort_keys=True))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
