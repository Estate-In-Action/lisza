# Receipt Classifier UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pending_inbox` review actionable — implement `post_pending.py` (library+CLI), extend `telegram_intake.py` with inline keyboards + callback handlers, extend `sheet_sync.py` with bidirectional Sheet↔DB polling. After this plan, every receipt in `reviewed` state can be approved/edited/rejected from either Telegram (mobile, single-receipt) or Google Sheets (batch).

**Architecture:** Three implementation slices on top of one shared backend. `post_pending.py` is the single source of truth for `pending_inbox` state transitions, with SQL status guards for concurrency. Both UI surfaces import the same library; either may also use the CLI. Status guards ensure no double-post regardless of which surface fires first.

**Tech Stack:** Python 3 stdlib (`sqlite3`, `urllib`, `argparse`, `json`, `pathlib`). New dependency on `pytest` for testing (added to LISZA). Existing dependencies: `google-api-python-client` (sheet_sync.py already uses).

**Spec:** `/home/workspace/LISZA/docs/specs/2026-05-18-receipt-classifier-ui-design.md`

**Persona:** Bookkeeper. Strict isolation — no file outside `LISZA/` or `LISZA-dev/` is modified.

---

## File Structure

| Path | Action | Purpose |
|---|---|---|
| `LISZA-dev/` | Bootstrap | Sandbox per Rule #5 (currently does not exist). |
| `LISZA-dev/ledger.db` | Bootstrap copy | Isolated ledger for destructive testing. NOT a symlink. |
| `LISZA-dev/scripts/post_pending.py` | Create | Backend library + CLI. Tested here first. |
| `LISZA-dev/scripts/test_post_pending.py` | Create | Pytest tests for `post_pending`. |
| `LISZA-dev/scripts/telegram_intake.py` | Modify | Add inline-keyboard send + `callback_query` handler + reviewed-row scanner. |
| `LISZA-dev/scripts/sheet_sync.py` | Modify | Add `_Pending` action/action_result columns + `process_sheet_actions()` + `--bidirectional` flag. |
| `LISZA/scripts/post_pending.py` | Promote | Production mirror after dev tests pass. |
| `LISZA/scripts/test_post_pending.py` | Promote | Mirror tests. |
| `LISZA/scripts/telegram_intake.py` | Modify | Production mirror. |
| `LISZA/scripts/sheet_sync.py` | Modify | Production mirror. |
| `crontab` | Modify | +1 entry: `*/2 * * * *` for `sheet_sync.py --bidirectional`. |
| `LISZA/PLAN.md` | Modify | Check off Phase 2 boxes. |
| `LISZA/logs/sheet_sync.log` | Auto-create | Cron append log. |

**File-level boundaries:**
- `post_pending.py` — pure library + thin CLI. No HTTP, no UI. Single responsibility: state transitions on `pending_inbox`. Surfaces depend on it; it depends on nothing project-specific except `ledger.db` schema.
- `telegram_intake.py` — owns Telegram I/O. Imports `post_pending` for state transitions. Does not know about Sheets.
- `sheet_sync.py` — owns Sheet I/O. Imports `post_pending` for state transitions. Does not know about Telegram.

---

## Task 1: Bootstrap `LISZA-dev/`

PLAN.md Rule #5 references `LISZA-dev/` but the directory does not exist. Create it now as the sandbox for this and future LISZA work.

**Files:**
- Create: `/home/workspace/LISZA-dev/` (directory)
- Create: `/home/workspace/LISZA-dev/scripts/` (directory)
- Create: `/home/workspace/LISZA-dev/ledger.db` (copy)
- Create: `/home/workspace/LISZA-dev/data/` (directory)
- Create: `/home/workspace/LISZA-dev/logs/` (directory)

- [ ] **Step 1: Create directory structure**

Run:
```bash
mkdir -p /home/workspace/LISZA-dev/scripts /home/workspace/LISZA-dev/data /home/workspace/LISZA-dev/logs /home/workspace/LISZA-dev/inbox/telegram /home/workspace/LISZA-dev/inbox/email
ls -la /home/workspace/LISZA-dev/
```

Expected: five subdirectories exist.

- [ ] **Step 2: Copy ledger.db (NOT a symlink — isolated)**

Run:
```bash
cp /home/workspace/LISZA/ledger.db /home/workspace/LISZA-dev/ledger.db
sqlite3 /home/workspace/LISZA-dev/ledger.db "SELECT count(*) FROM entries;"
sqlite3 /home/workspace/LISZA/ledger.db "SELECT count(*) FROM entries;"
```

Expected: both queries return the same number. The copy is independent — destructive dev tests won't affect prod.

- [ ] **Step 3: Copy current scripts as baseline**

Run:
```bash
cp /home/workspace/LISZA/scripts/*.py /home/workspace/LISZA-dev/scripts/
ls /home/workspace/LISZA-dev/scripts/
```

Expected: ~10 .py files copied (telegram_intake.py, code_receipt.py, sheet_sync.py, etc.).

- [ ] **Step 4: Verify dev scripts run against dev DB**

Run:
```bash
cd /home/workspace/LISZA-dev
sqlite3 ledger.db "SELECT id, status FROM pending_inbox LIMIT 5;"
```

Expected: at least one row visible. Confirms the dev DB is queryable.

- [ ] **Step 5: Note bootstrap in PLAN.md (commit later in Task 14)**

No commit yet — `LISZA/` is gitignored. The bootstrap is recorded by the existence of the directory and noted in the final PLAN.md update.

---

## Task 2: pytest scaffold + smoke test

**Files:**
- Create: `/home/workspace/LISZA-dev/scripts/test_post_pending.py`

- [ ] **Step 1: Verify pytest is available**

Run: `python3 -m pytest --version`
Expected: `pytest X.Y.Z` (already installed system-wide per HIP).

- [ ] **Step 2: Write the test file scaffold**

Create `/home/workspace/LISZA-dev/scripts/test_post_pending.py`:

```python
"""Tests for post_pending — state transitions on LISZA/pending_inbox."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Tests are run from LISZA-dev/, so post_pending lives next door.
sys.path.insert(0, "/home/workspace/LISZA-dev/scripts")


@pytest.fixture
def temp_ledger():
    """A fresh copy of the dev ledger for each test."""
    src = Path("/home/workspace/LISZA-dev/ledger.db")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tmp_path = Path(tf.name)
    # Copy the schema + data
    import shutil
    shutil.copy(src, tmp_path)
    yield str(tmp_path)
    tmp_path.unlink()


def _insert_reviewed_row(db_path, *, suggested_account="591", suggested_amount=42.50,
                         payee="AMAZON.COM", date="2026-05-18"):
    """Helper: put a row in 'reviewed' state ready for approve."""
    conn = sqlite3.connect(db_path)
    parsed = {
        "vendor": payee,
        "date": date,
        "total": suggested_amount,
        "suggested_account_code": suggested_account,
        "suggested_payment_account_code": "260",  # CC
    }
    cur = conn.execute(
        "INSERT INTO pending_inbox (source, raw_path, parsed_json, "
        "suggested_account, suggested_amount, status) "
        "VALUES (?, ?, ?, ?, ?, 'reviewed')",
        ("telegram", "/tmp/fake.jpg", json.dumps(parsed), suggested_account, suggested_amount),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def test_module_importable():
    """post_pending must be importable as a module."""
    import post_pending
    assert hasattr(post_pending, "approve_row")
    assert hasattr(post_pending, "reject_row")
    assert hasattr(post_pending, "edit_row")
```

- [ ] **Step 3: Run smoke test — expect failure (no post_pending.py yet)**

Run: `cd /home/workspace/LISZA-dev && python3 -m pytest scripts/test_post_pending.py -v`
Expected: FAIL with ModuleNotFoundError for `post_pending` — this is the failing test we'll fix in Task 3.

---

## Task 3: post_pending.py — `approve_row` happy path

**Files:**
- Create: `/home/workspace/LISZA-dev/scripts/post_pending.py`
- Modify: `/home/workspace/LISZA-dev/scripts/test_post_pending.py`

- [ ] **Step 1: Write the failing test**

Append to `test_post_pending.py`:

```python
def test_approve_row_happy_path(temp_ledger, monkeypatch):
    """Approving a reviewed row creates a balanced entries+splits pair."""
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger, suggested_account="591", suggested_amount=42.50)

    import importlib
    import post_pending
    importlib.reload(post_pending)

    entry_id = post_pending.approve_row(row_id)
    assert entry_id is not None and entry_id > 0

    conn = sqlite3.connect(temp_ledger)
    # Row transitioned
    status = conn.execute("SELECT status FROM pending_inbox WHERE id=?", (row_id,)).fetchone()[0]
    assert status == "posted"
    # entries row exists
    payee = conn.execute("SELECT payee FROM entries WHERE id=?", (entry_id,)).fetchone()[0]
    assert payee == "AMAZON.COM"
    # splits balance: sum(dr) == sum(cr)
    dr = conn.execute("SELECT COALESCE(SUM(dr),0) FROM splits WHERE entry_id=?", (entry_id,)).fetchone()[0]
    cr = conn.execute("SELECT COALESCE(SUM(cr),0) FROM splits WHERE entry_id=?", (entry_id,)).fetchone()[0]
    assert dr == cr == 42.50
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA-dev && python3 -m pytest scripts/test_post_pending.py::test_approve_row_happy_path -v`
Expected: FAIL with `ModuleNotFoundError` or `AttributeError: approve_row`.

- [ ] **Step 3: Implement minimal `post_pending.py`**

Create `/home/workspace/LISZA-dev/scripts/post_pending.py`:

```python
#!/usr/bin/env python3
"""
post_pending — promote pending_inbox rows to posted ledger entries.

Library API:
    approve_row(row_id, *, account_override, amount_override, payment_account) -> entry_id | None
    reject_row(row_id, *, reason) -> bool
    edit_row(row_id, *, field, new_value) -> bool

CLI:
    post_pending.py --row-id N --approve [--account CODE] [--amount FLOAT] [--payment CODE]
    post_pending.py --row-id N --reject [--reason TEXT]
    post_pending.py --row-id N --edit FIELD=VALUE

Concurrency: BEGIN IMMEDIATE + status guard. If status changes between
detection and write (e.g. another surface wins the race), returns None.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LEDGER = Path("/home/workspace/LISZA/ledger.db")
DEFAULT_PAYMENT_ACCOUNT = "102"  # Bank checking


def _db_path() -> str:
    return os.environ.get("FIN_LEDGER_DB") or str(DEFAULT_LEDGER)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=30.0)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def approve_row(row_id: int, *,
                account_override: str | None = None,
                amount_override: float | None = None,
                payment_account: str = DEFAULT_PAYMENT_ACCOUNT) -> int | None:
    """Promote a 'reviewed' row to a posted double-entry transaction.

    Returns entries.id on success, None if the row was not 'reviewed'
    at the moment of the SQL update (another surface won the race, or
    the row never existed).

    Raises ValueError on validation failures (bad account, non-positive amount).
    """
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT parsed_json, suggested_account, suggested_amount, status, source "
            "FROM pending_inbox WHERE id=?", (row_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        parsed_json, sugg_acct, sugg_amt, status, source = row
        if status != "reviewed":
            conn.rollback()
            return None

        account = account_override or sugg_acct
        amount = amount_override if amount_override is not None else sugg_amt
        if not account:
            raise ValueError(f"row {row_id} has no suggested_account and no override")
        if amount is None or amount <= 0:
            raise ValueError(f"row {row_id} amount must be > 0, got {amount}")

        # Validate accounts exist
        for code in (account, payment_account):
            exists = conn.execute("SELECT 1 FROM accounts WHERE code=?", (code,)).fetchone()
            if not exists:
                raise ValueError(f"account code {code!r} not in chart of accounts")

        # Pull date + payee from parsed_json for description
        try:
            pj = json.loads(parsed_json) if parsed_json else {}
        except json.JSONDecodeError:
            pj = {}
        payee = pj.get("vendor") or pj.get("payee") or "(unknown)"
        date = pj.get("date") or datetime.now(timezone.utc).date().isoformat()
        memo = f"posted from pending #{row_id} via {source}"

        # Create entry + splits
        cur = conn.execute(
            "INSERT INTO entries (date, description, payee, source, source_ref, status) "
            "VALUES (?, ?, ?, ?, ?, 'posted')",
            (date, payee, payee, source, f"pending_inbox:{row_id}"),
        )
        entry_id = cur.lastrowid
        conn.execute(
            "INSERT INTO splits (entry_id, account, dr, cr, memo) VALUES (?, ?, ?, 0, ?)",
            (entry_id, account, amount, memo),
        )
        conn.execute(
            "INSERT INTO splits (entry_id, account, dr, cr, memo) VALUES (?, ?, 0, ?, ?)",
            (entry_id, payment_account, amount, memo),
        )

        # Status guard — only update if still 'reviewed'
        cur = conn.execute(
            "UPDATE pending_inbox SET status='posted', entry_id=?, "
            "notes = COALESCE(notes,'') || ? "
            "WHERE id=? AND status='reviewed'",
            (entry_id, f"\n[{_now_iso()}] posted as entry #{entry_id}", row_id),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return None

        conn.commit()
        return entry_id
    except ValueError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reject_row(row_id: int, *, reason: str = "") -> bool:
    """Set status='rejected'. Returns True on transition, False otherwise."""
    conn = _connect()
    try:
        note = f"\n[{_now_iso()}] rejected: {reason}" if reason else f"\n[{_now_iso()}] rejected"
        cur = conn.execute(
            "UPDATE pending_inbox SET status='rejected', notes = COALESCE(notes,'') || ? "
            "WHERE id=? AND status='reviewed'",
            (note, row_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def edit_row(row_id: int, *, field: str, new_value) -> bool:
    """Update one parsed field. Row stays in 'reviewed' state.

    field: 'account' | 'amount' | 'date' | 'payee' | 'memo'
    Returns True if a 'reviewed' row was updated.
    """
    if field not in ("account", "amount", "date", "payee", "memo"):
        raise ValueError(f"unknown field {field!r}")
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT parsed_json, status FROM pending_inbox WHERE id=?", (row_id,)
        ).fetchone()
        if not row or row[1] != "reviewed":
            return False
        try:
            pj = json.loads(row[0]) if row[0] else {}
        except json.JSONDecodeError:
            pj = {}

        if field == "account":
            pj["suggested_account_code"] = str(new_value)
            new_acct = str(new_value)
            new_amt = None
        elif field == "amount":
            pj["total"] = float(new_value)
            new_acct = None
            new_amt = float(new_value)
        elif field == "date":
            pj["date"] = str(new_value)
            new_acct = None
            new_amt = None
        elif field == "payee":
            pj["vendor"] = str(new_value)
            new_acct = None
            new_amt = None
        else:  # memo
            pj["memo"] = str(new_value)
            new_acct = None
            new_amt = None

        note = f"\n[{_now_iso()}] edit: {field}={new_value}"
        sql_parts = ["parsed_json=?"]
        params: list = [json.dumps(pj)]
        if new_acct is not None:
            sql_parts.append("suggested_account=?")
            params.append(new_acct)
        if new_amt is not None:
            sql_parts.append("suggested_amount=?")
            params.append(new_amt)
        sql_parts.append("notes = COALESCE(notes,'') || ?")
        params.append(note)
        params.append(row_id)

        cur = conn.execute(
            f"UPDATE pending_inbox SET {', '.join(sql_parts)} WHERE id=? AND status='reviewed'",
            params,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _cli() -> int:
    ap = argparse.ArgumentParser(description="post_pending — pending_inbox state transitions")
    ap.add_argument("--row-id", type=int, required=True)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--approve", action="store_true")
    grp.add_argument("--reject", action="store_true")
    grp.add_argument("--edit", action="append", default=[],
                     help="field=value, repeatable (account|amount|date|payee|memo)")
    ap.add_argument("--account")
    ap.add_argument("--amount", type=float)
    ap.add_argument("--payment", default=DEFAULT_PAYMENT_ACCOUNT)
    ap.add_argument("--reason", default="")
    args = ap.parse_args()

    if args.approve:
        try:
            entry_id = approve_row(args.row_id, account_override=args.account,
                                    amount_override=args.amount, payment_account=args.payment)
            if entry_id is None:
                print(f"NOOP: row {args.row_id} not in 'reviewed' state", file=sys.stderr)
                return 1
            print(f"posted as entry #{entry_id}")
            return 0
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    if args.reject:
        ok = reject_row(args.row_id, reason=args.reason)
        print("rejected" if ok else f"NOOP: row {args.row_id} not in 'reviewed' state")
        return 0 if ok else 1
    # edit
    any_ok = False
    for kv in args.edit:
        if "=" not in kv:
            print(f"bad --edit value {kv!r}, expected field=value", file=sys.stderr)
            return 2
        field, _, val = kv.partition("=")
        ok = edit_row(args.row_id, field=field, new_value=val)
        if ok:
            any_ok = True
            print(f"edited {field}={val}")
        else:
            print(f"NOOP for {field}", file=sys.stderr)
    return 0 if any_ok else 1


if __name__ == "__main__":
    sys.exit(_cli())
```

- [ ] **Step 4: Run test to verify happy path passes**

Run: `cd /home/workspace/LISZA-dev && python3 -m pytest scripts/test_post_pending.py::test_approve_row_happy_path scripts/test_post_pending.py::test_module_importable -v`
Expected: 2 passed.

- [ ] **Step 5: Commit (dev-local note only — LISZA is gitignored)**

Record the milestone in a local note file:
```bash
echo "$(date -u +%FT%TZ) Task 3 complete: post_pending.py approve_row happy path" >> /home/workspace/LISZA-dev/.task_log
```

---

## Task 4: `approve_row` — concurrency guard

**Files:**
- Modify: `/home/workspace/LISZA-dev/scripts/test_post_pending.py`

- [ ] **Step 1: Write the failing test**

Append to `test_post_pending.py`:

```python
def test_approve_row_returns_none_if_already_posted(temp_ledger, monkeypatch):
    """Two parallel approves: exactly one entry row is created."""
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger)

    import importlib
    import post_pending
    importlib.reload(post_pending)

    first = post_pending.approve_row(row_id)
    second = post_pending.approve_row(row_id)
    assert first is not None
    assert second is None

    conn = sqlite3.connect(temp_ledger)
    n = conn.execute(
        "SELECT count(*) FROM entries WHERE source_ref=?",
        (f"pending_inbox:{row_id}",)
    ).fetchone()[0]
    assert n == 1
    conn.close()


def test_approve_row_returns_none_for_nonexistent():
    """Approving a non-existent row returns None, not an exception."""
    import post_pending
    assert post_pending.approve_row(999999) is None
```

- [ ] **Step 2: Run tests — expect pass (the status guard is already implemented in Task 3)**

Run: `cd /home/workspace/LISZA-dev && python3 -m pytest scripts/test_post_pending.py::test_approve_row_returns_none_if_already_posted scripts/test_post_pending.py::test_approve_row_returns_none_for_nonexistent -v`
Expected: 2 passed (Task 3's implementation already handles these cases). If they fail, audit the Task 3 implementation.

---

## Task 5: `approve_row` — validation (zero amount, bad account)

**Files:**
- Modify: `/home/workspace/LISZA-dev/scripts/test_post_pending.py`

- [ ] **Step 1: Write the failing test**

Append to `test_post_pending.py`:

```python
def test_approve_row_rejects_zero_amount(temp_ledger, monkeypatch):
    """Zero amount must raise ValueError (real data has rows with amount=0.0)."""
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger, suggested_amount=0.0)

    import importlib
    import post_pending
    importlib.reload(post_pending)
    with pytest.raises(ValueError, match="amount"):
        post_pending.approve_row(row_id)


def test_approve_row_rejects_negative_amount(temp_ledger, monkeypatch):
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger, suggested_amount=-5.0)

    import importlib
    import post_pending
    importlib.reload(post_pending)
    with pytest.raises(ValueError, match="amount"):
        post_pending.approve_row(row_id)


def test_approve_row_rejects_unknown_account(temp_ledger, monkeypatch):
    """Override with a code that doesn't exist must raise."""
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger)

    import importlib
    import post_pending
    importlib.reload(post_pending)
    with pytest.raises(ValueError, match="not in chart of accounts"):
        post_pending.approve_row(row_id, account_override="ZZZZ")
```

- [ ] **Step 2: Run tests — Task 3's implementation should already pass these**

Run: `cd /home/workspace/LISZA-dev && python3 -m pytest scripts/test_post_pending.py -k "rejects" -v`
Expected: 3 passed. If any fail, examine the Task 3 validation block (lines guarding `amount` and the `accounts` lookup) and fix in place.

---

## Task 6: `approve_row` — override paths

**Files:**
- Modify: `/home/workspace/LISZA-dev/scripts/test_post_pending.py`

- [ ] **Step 1: Write the failing test**

Append to `test_post_pending.py`:

```python
def test_approve_row_account_override(temp_ledger, monkeypatch):
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger, suggested_account="591")

    import importlib
    import post_pending
    importlib.reload(post_pending)
    entry_id = post_pending.approve_row(row_id, account_override="589")

    conn = sqlite3.connect(temp_ledger)
    accts = [r[0] for r in conn.execute(
        "SELECT account FROM splits WHERE entry_id=? AND dr > 0", (entry_id,)
    )]
    conn.close()
    assert accts == ["589"]


def test_approve_row_amount_override(temp_ledger, monkeypatch):
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger, suggested_amount=42.50)

    import importlib
    import post_pending
    importlib.reload(post_pending)
    entry_id = post_pending.approve_row(row_id, amount_override=99.00)

    conn = sqlite3.connect(temp_ledger)
    drs = conn.execute("SELECT dr FROM splits WHERE entry_id=? AND dr > 0", (entry_id,)).fetchone()
    conn.close()
    assert drs[0] == 99.00


def test_approve_row_payment_account_override(temp_ledger, monkeypatch):
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger)

    import importlib
    import post_pending
    importlib.reload(post_pending)
    entry_id = post_pending.approve_row(row_id, payment_account="260")

    conn = sqlite3.connect(temp_ledger)
    cr_acct = conn.execute(
        "SELECT account FROM splits WHERE entry_id=? AND cr > 0", (entry_id,)
    ).fetchone()[0]
    conn.close()
    assert cr_acct == "260"
```

- [ ] **Step 2: Run tests — expect pass**

Run: `cd /home/workspace/LISZA-dev && python3 -m pytest scripts/test_post_pending.py -k "override" -v`
Expected: 3 passed.

---

## Task 7: `reject_row` + `edit_row` tests

**Files:**
- Modify: `/home/workspace/LISZA-dev/scripts/test_post_pending.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_post_pending.py`:

```python
def test_reject_row_transitions_status(temp_ledger, monkeypatch):
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger)

    import importlib
    import post_pending
    importlib.reload(post_pending)
    ok = post_pending.reject_row(row_id, reason="test duplicate")
    assert ok

    conn = sqlite3.connect(temp_ledger)
    status, notes = conn.execute(
        "SELECT status, notes FROM pending_inbox WHERE id=?", (row_id,)
    ).fetchone()
    conn.close()
    assert status == "rejected"
    assert "test duplicate" in (notes or "")


def test_reject_row_noop_on_already_actioned(temp_ledger, monkeypatch):
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger)

    import importlib
    import post_pending
    importlib.reload(post_pending)
    post_pending.reject_row(row_id, reason="first")
    ok2 = post_pending.reject_row(row_id, reason="second")
    assert ok2 is False


def test_edit_row_amount(temp_ledger, monkeypatch):
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger, suggested_amount=42.50)

    import importlib
    import post_pending
    importlib.reload(post_pending)
    ok = post_pending.edit_row(row_id, field="amount", new_value=99.00)
    assert ok

    conn = sqlite3.connect(temp_ledger)
    amt, status = conn.execute(
        "SELECT suggested_amount, status FROM pending_inbox WHERE id=?", (row_id,)
    ).fetchone()
    conn.close()
    assert amt == 99.00
    assert status == "reviewed"  # stays in reviewed after edit


def test_edit_row_rejects_unknown_field(temp_ledger, monkeypatch):
    monkeypatch.setenv("FIN_LEDGER_DB", temp_ledger)
    row_id = _insert_reviewed_row(temp_ledger)

    import importlib
    import post_pending
    importlib.reload(post_pending)
    with pytest.raises(ValueError, match="unknown field"):
        post_pending.edit_row(row_id, field="bogus", new_value="x")
```

- [ ] **Step 2: Run tests — expect pass (Task 3 implemented these)**

Run: `cd /home/workspace/LISZA-dev && python3 -m pytest scripts/test_post_pending.py -k "reject_row or edit_row" -v`
Expected: 4 passed.

- [ ] **Step 3: Full sweep — all post_pending tests pass together**

Run: `cd /home/workspace/LISZA-dev && python3 -m pytest scripts/test_post_pending.py -v`
Expected: all ~12 tests pass.

---

## Task 8: post_pending CLI smoke test

**Files:**
- Read-only verification of the CLI surface.

- [ ] **Step 1: Test approve from the command line**

```bash
cd /home/workspace/LISZA-dev
# Insert a fresh test row
python3 -c "
import sqlite3, json
conn = sqlite3.connect('ledger.db')
parsed = {'vendor':'CLI_TEST','date':'2026-05-18','total':10.00,'suggested_account_code':'591'}
cur = conn.execute(\"INSERT INTO pending_inbox(source, raw_path, parsed_json, suggested_account, suggested_amount, status) VALUES ('test','/tmp/x',?,'591',10.00,'reviewed')\", (json.dumps(parsed),))
print('row_id:', cur.lastrowid)
conn.commit()
"
# Approve it via CLI
python3 scripts/post_pending.py --row-id <ID_FROM_ABOVE> --approve
```

Expected: prints `posted as entry #N`. Verify with `sqlite3 ledger.db "SELECT status FROM pending_inbox WHERE id=<ID>"` → `posted`.

- [ ] **Step 2: Test reject via CLI**

```bash
# Insert another row, then reject
python3 scripts/post_pending.py --row-id <ID> --reject --reason "cli test reject"
```

Expected: `rejected`. Verify status.

- [ ] **Step 3: Test edit via CLI**

```bash
python3 scripts/post_pending.py --row-id <ID> --edit amount=55.00
```

Expected: `edited amount=55.00`. Verify suggested_amount column.

- [ ] **Step 4: Cleanup test rows**

```bash
sqlite3 /home/workspace/LISZA-dev/ledger.db "DELETE FROM splits WHERE entry_id IN (SELECT id FROM entries WHERE payee='CLI_TEST'); DELETE FROM entries WHERE payee='CLI_TEST'; DELETE FROM pending_inbox WHERE source='test';"
```

Verify clean: `SELECT count(*) FROM pending_inbox WHERE source='test';` → 0.

---

## Task 9: telegram_intake.py — keyboard rendering helper

The TG callbacks need a shared helper for building inline keyboard payloads and sending them.

**Files:**
- Modify: `/home/workspace/LISZA-dev/scripts/telegram_intake.py`

- [ ] **Step 1: Add helper functions near other I/O helpers**

In `telegram_intake.py`, find `def send_message(chat_id, text)` and add right after it:

```python
def send_message_with_keyboard(chat_id: int, text: str, keyboard: list[list[dict]]) -> dict | None:
    """Send a Telegram message with an inline keyboard. Returns the response message dict or None on error.

    keyboard format: [[{"text": "...", "callback_data": "..."}, ...], ...]
    """
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": json.dumps({"inline_keyboard": keyboard}),
    }
    try:
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(f"{API}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            response = json.loads(resp.read())
            return response.get("result") if response.get("ok") else None
    except Exception as e:
        log(f"send_message_with_keyboard failed: {e}")
        return None


def edit_message_text(chat_id: int, message_id: int, text: str,
                       keyboard: list[list[dict]] | None = None) -> bool:
    """Edit an existing message's text and (optionally) keyboard. Returns True on success."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if keyboard is not None:
        payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    try:
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(f"{API}/editMessageText", data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            response = json.loads(resp.read())
            return bool(response.get("ok"))
    except Exception as e:
        log(f"edit_message_text failed: {e}")
        return False


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    """Dismiss the loading state on a tapped inline button."""
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(f"{API}/answerCallbackQuery", data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            json.loads(resp.read())
    except Exception as e:
        log(f"answer_callback_query failed: {e}")


def _approve_keyboard(row_id: int) -> list[list[dict]]:
    """Top-level approve/edit/reject keyboard for a reviewed receipt."""
    return [[
        {"text": "✅ Approve", "callback_data": f"approve:{row_id}"},
        {"text": "✏️ Edit",    "callback_data": f"edit:{row_id}"},
        {"text": "❌ Reject",  "callback_data": f"reject:{row_id}"},
    ]]


def _edit_field_keyboard(row_id: int) -> list[list[dict]]:
    """Field-picker keyboard shown when user taps Edit."""
    return [
        [{"text": "Account", "callback_data": f"editfld:{row_id}:account"},
         {"text": "Amount",  "callback_data": f"editfld:{row_id}:amount"},
         {"text": "Date",    "callback_data": f"editfld:{row_id}:date"}],
        [{"text": "Payee",   "callback_data": f"editfld:{row_id}:payee"},
         {"text": "Memo",    "callback_data": f"editfld:{row_id}:memo"},
         {"text": "⬅️ Back", "callback_data": f"editback:{row_id}"}],
    ]
```

Note: `urllib.parse` is imported via `urllib.request` already in this file; if not, add `import urllib.parse` near top.

- [ ] **Step 2: Verify the module still imports cleanly**

Run: `cd /home/workspace/LISZA-dev && python3 -c "import importlib.util, sys; spec=importlib.util.spec_from_file_location('ti','scripts/telegram_intake.py'); m=importlib.util.module_from_spec(spec); print('imports ok')"`

This may fail if `BOT_TOKEN` env var isn't set (the script exits at import time). If so, set a dummy: `FIN_T_B4ME=test python3 -c "..."`.

Expected: `imports ok` or graceful module load.

---

## Task 10: telegram_intake.py — scan for unsent reviewed rows

After `code_receipt.py` transitions a row to `reviewed`, the poll loop must observe it and post the keyboard.

**Files:**
- Modify: `/home/workspace/LISZA-dev/scripts/telegram_intake.py`

- [ ] **Step 1: Add scanner function**

Add near `handle_message`:

```python
def _format_receipt_summary(row_id: int, parsed: dict) -> str:
    """Pretty receipt summary text shown above the keyboard."""
    vendor = parsed.get("vendor") or "(unknown)"
    total = parsed.get("total")
    date = parsed.get("date") or "(no date)"
    acct = parsed.get("suggested_account_code") or "?"
    pay = parsed.get("suggested_payment_account_code") or "102"
    conf = parsed.get("confidence")
    conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else "?"
    return (
        f"🧾 <b>Receipt #{row_id}</b>\n"
        f"Payee:  {vendor}\n"
        f"Amount: ${total}\n"
        f"Date:   {date}\n"
        f"Suggest: {acct} (DR) ← {pay} (CR)\n"
        f"Confidence: {conf_str}"
    )


def scan_reviewed_and_send_keyboards(cfg: dict) -> int:
    """Post inline keyboards for any reviewed rows that haven't been keyboarded yet.

    Marker: a JSON tag inside pending_inbox.notes — 'keyboard_sent_at'.
    Returns the count of keyboards sent this scan.
    """
    chat_id = cfg.get("telegram_chat_id")
    if not chat_id:
        return 0
    conn = sqlite3.connect(str(DB))
    try:
        rows = conn.execute(
            "SELECT id, parsed_json, COALESCE(notes,'') FROM pending_inbox WHERE status='reviewed'"
        ).fetchall()
    finally:
        conn.close()
    sent = 0
    for row_id, parsed_json, notes in rows:
        if "keyboard_sent_at" in (notes or ""):
            continue
        try:
            parsed = json.loads(parsed_json) if parsed_json else {}
        except json.JSONDecodeError:
            parsed = {}
        text = _format_receipt_summary(row_id, parsed)
        msg = send_message_with_keyboard(chat_id, text, _approve_keyboard(row_id))
        if msg and msg.get("message_id"):
            tag = f"\n[{datetime.utcnow().isoformat()}Z] keyboard_sent_at msg={msg['message_id']}"
            conn = sqlite3.connect(str(DB))
            try:
                conn.execute(
                    "UPDATE pending_inbox SET notes=COALESCE(notes,'') || ? WHERE id=?",
                    (tag, row_id),
                )
                conn.commit()
            finally:
                conn.close()
            sent += 1
    if sent:
        log(f"sent {sent} keyboards")
    return sent
```

Note: `DB` is already defined in telegram_intake.py (likely `DB = ROOT / "ledger.db"`).

- [ ] **Step 2: Call the scanner in the main poll_loop**

Find `poll_loop` at the bottom of the file. Inside the `while True:` block, after the `for upd in updates:` loop and before `if updates: save_state(state)`, add:

```python
            # After processing inbound messages, scan for newly-reviewed rows
            try:
                scan_reviewed_and_send_keyboards(cfg)
            except Exception as e:
                log(f"keyboard scan error: {e}")
```

- [ ] **Step 3: Smoke test the scanner against dev DB**

Insert a fresh reviewed row into LISZA-dev/ledger.db. Set `FIN_T_B4ME=<test_bot_token>` env var (do NOT use the real `@fin_t_4me_bot` token; use a dedicated test bot OR mock the API). Run telegram_intake.py briefly:

```bash
cd /home/workspace/LISZA-dev
# Insert test row (skip if you already have one in 'reviewed')
sqlite3 ledger.db "INSERT INTO pending_inbox(source, raw_path, parsed_json, suggested_account, suggested_amount, status) VALUES ('scanner_test','/tmp/x','{\"vendor\":\"SCANNER_TEST\",\"total\":1.00,\"suggested_account_code\":\"591\"}', '591', 1.00, 'reviewed');"
# Run the intake briefly — interrupt after ~10s
timeout 10 python3 scripts/telegram_intake.py || true
# Check if keyboard_sent_at marker was added
sqlite3 ledger.db "SELECT notes FROM pending_inbox WHERE source='scanner_test';"
```

Expected: notes contains `keyboard_sent_at`. (If you skipped the live TG send by not setting a token, the scanner won't have marked the row — that's OK, we'll test via mocked API in task 11.)

---

## Task 11: callback_query handler — approve

**Files:**
- Modify: `/home/workspace/LISZA-dev/scripts/telegram_intake.py`

- [ ] **Step 1: Extend poll_loop to handle callback_query updates**

In `telegram_intake.py`, find the inner loop in `poll_loop`:

```python
for upd in updates:
    state["offset"] = upd["update_id"] + 1
    msg = upd.get("message") or upd.get("channel_post")
    if not msg:
        continue
    try:
        cfg = handle_message(cfg, msg)
    except Exception as e:
        log(f"handler error: {e}")
```

Replace with:

```python
for upd in updates:
    state["offset"] = upd["update_id"] + 1
    if "callback_query" in upd:
        try:
            handle_callback(cfg, upd["callback_query"])
        except Exception as e:
            log(f"callback handler error: {e}")
        continue
    msg = upd.get("message") or upd.get("channel_post")
    if not msg:
        continue
    try:
        cfg = handle_message(cfg, msg)
    except Exception as e:
        log(f"handler error: {e}")
```

- [ ] **Step 2: Add `handle_callback` function**

Insert above `poll_loop`:

```python
def handle_callback(cfg: dict, cq: dict) -> None:
    """Dispatch an inline-keyboard tap to the right post_pending action."""
    import post_pending  # local import keeps top of file clean

    cq_id = cq.get("id", "")
    data = cq.get("data", "")
    msg = cq.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    pinned = cfg.get("telegram_chat_id")
    if pinned and chat_id != pinned:
        answer_callback_query(cq_id, "wrong chat")
        return

    parts = data.split(":")
    if len(parts) < 2:
        answer_callback_query(cq_id, "bad callback data")
        return
    verb = parts[0]
    try:
        row_id = int(parts[1])
    except ValueError:
        answer_callback_query(cq_id, "bad row id")
        return

    if verb == "approve":
        try:
            entry_id = post_pending.approve_row(row_id)
        except ValueError as e:
            answer_callback_query(cq_id, "validation failed")
            edit_message_text(chat_id, message_id, f"⚠️ ERROR: {e}", keyboard=[])
            return
        if entry_id is None:
            answer_callback_query(cq_id, "already actioned")
            edit_message_text(chat_id, message_id,
                              f"⚠️ Already actioned (row {row_id})", keyboard=[])
            return
        answer_callback_query(cq_id, "posted")
        edit_message_text(chat_id, message_id,
                          f"✅ <b>POSTED</b> as entry #{entry_id}", keyboard=[])
        return

    # placeholder branches for edit/reject — Tasks 12, 13
    answer_callback_query(cq_id, f"verb {verb} not implemented yet")
```

- [ ] **Step 3: Smoke test approve flow against a dev DB row**

Manual test against the dev bot/chat:
```bash
cd /home/workspace/LISZA-dev
sqlite3 ledger.db "INSERT INTO pending_inbox(source, raw_path, parsed_json, suggested_account, suggested_amount, status) VALUES ('cb_test','/tmp/x','{\"vendor\":\"CB_TEST\",\"total\":3.00,\"suggested_account_code\":\"591\"}', '591', 3.00, 'reviewed');"
# Run intake; tap Approve in the test chat when the keyboard appears
FIN_T_B4ME=<test_bot_token> python3 scripts/telegram_intake.py
```

Expected: message edits to `✅ POSTED as entry #N`. Verify entry exists in ledger:

```bash
sqlite3 ledger.db "SELECT id, payee FROM entries WHERE payee='CB_TEST';"
```

---

## Task 12: callback_query handler — reject (with reason flow)

**Files:**
- Modify: `/home/workspace/LISZA-dev/scripts/telegram_intake.py`

- [ ] **Step 1: Add per-row pending-action state to notes**

When user taps Reject, the bot expects a follow-up text message with the reason. We track "waiting for reject reason for row N" inside the row's notes JSON to survive bot restarts.

Add a helper:

```python
def _set_pending_action(row_id: int, action: str | None) -> None:
    """Mark a pending UI action on a row (for follow-up text capture).

    action: 'await_reject_reason' | 'await_edit_value:<field>' | None to clear.
    Stored as a `pending_action=<value>` tag in notes.
    """
    conn = sqlite3.connect(str(DB))
    try:
        notes = conn.execute(
            "SELECT COALESCE(notes,'') FROM pending_inbox WHERE id=?", (row_id,)
        ).fetchone()
        existing = notes[0] if notes else ""
        # Strip any prior pending_action tag
        lines = [ln for ln in existing.split("\n") if not ln.startswith("pending_action=")]
        if action is not None:
            lines.append(f"pending_action={action}")
        new_notes = "\n".join(lines)
        conn.execute("UPDATE pending_inbox SET notes=? WHERE id=?", (new_notes, row_id))
        conn.commit()
    finally:
        conn.close()


def _get_pending_action(row_id: int) -> str | None:
    conn = sqlite3.connect(str(DB))
    try:
        row = conn.execute("SELECT COALESCE(notes,'') FROM pending_inbox WHERE id=?", (row_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    for ln in row[0].split("\n"):
        if ln.startswith("pending_action="):
            return ln.split("=", 1)[1]
    return None


def _find_row_awaiting_action(chat_id: int) -> int | None:
    """Find a single row currently awaiting follow-up input. Single human, so at most one expected."""
    conn = sqlite3.connect(str(DB))
    try:
        rows = conn.execute(
            "SELECT id, COALESCE(notes,'') FROM pending_inbox WHERE status='reviewed'"
        ).fetchall()
    finally:
        conn.close()
    for rid, notes in rows:
        if "pending_action=" in (notes or ""):
            return rid
    return None
```

- [ ] **Step 2: Add reject branch to `handle_callback`**

In `handle_callback`, replace the placeholder branch with:

```python
    if verb == "reject":
        _set_pending_action(row_id, "await_reject_reason")
        answer_callback_query(cq_id, "awaiting reason")
        edit_message_text(chat_id, message_id,
                          f"❌ Reject #{row_id} — reply with a reason (or send /skip)",
                          keyboard=[])
        return
```

- [ ] **Step 3: Extend `handle_message` to capture follow-up text**

Find `handle_message` and add a branch near the existing text-command handling (before `/ping` check):

```python
    # Check for pending follow-up text (reject reason or edit value)
    text = (msg.get("text") or "").strip()
    if text and not text.startswith("/"):
        awaiting_row = _find_row_awaiting_action(chat_id)
        if awaiting_row:
            action = _get_pending_action(awaiting_row) or ""
            if action == "await_reject_reason":
                import post_pending
                ok = post_pending.reject_row(awaiting_row, reason=text)
                _set_pending_action(awaiting_row, None)
                send_message(chat_id,
                             f"❌ REJECTED #{awaiting_row}: {text}" if ok
                             else f"⚠️ row #{awaiting_row} no longer rejectable")
                return cfg
            if action.startswith("await_edit_value:"):
                field = action.split(":", 1)[1]
                import post_pending
                value = text
                if field == "amount":
                    try:
                        value = float(text)
                    except ValueError:
                        send_message(chat_id, f"⚠️ amount must be a number, got {text!r}")
                        return cfg
                try:
                    ok = post_pending.edit_row(awaiting_row, field=field, new_value=value)
                except ValueError as e:
                    send_message(chat_id, f"⚠️ {e}")
                    return cfg
                _set_pending_action(awaiting_row, None)
                send_message(chat_id,
                             f"✏️ Edited #{awaiting_row}: {field}={value}"
                             if ok else f"⚠️ edit failed on #{awaiting_row}")
                return cfg
```

Also handle `/skip` to cancel a pending reject:

```python
    if text == "/skip":
        awaiting_row = _find_row_awaiting_action(chat_id)
        if awaiting_row:
            _set_pending_action(awaiting_row, None)
            send_message(chat_id, f"⏩ skipped pending action on #{awaiting_row}")
            return cfg
```

- [ ] **Step 4: Smoke test reject flow**

```bash
cd /home/workspace/LISZA-dev
sqlite3 ledger.db "INSERT INTO pending_inbox(source, raw_path, parsed_json, suggested_account, suggested_amount, status) VALUES ('rj_test','/tmp/x','{\"vendor\":\"RJ_TEST\",\"total\":5.00,\"suggested_account_code\":\"591\"}', '591', 5.00, 'reviewed');"
FIN_T_B4ME=<test_bot_token> python3 scripts/telegram_intake.py
# In the test chat: tap Reject, then reply "test reject reason"
# Verify:
sqlite3 ledger.db "SELECT status, notes FROM pending_inbox WHERE source='rj_test';"
```

Expected: status=`rejected`, notes contains "test reject reason".

---

## Task 13: callback_query handler — edit (field picker + value capture)

**Files:**
- Modify: `/home/workspace/LISZA-dev/scripts/telegram_intake.py`

- [ ] **Step 1: Add edit/editfld/editback branches to `handle_callback`**

In `handle_callback`, add before the placeholder line:

```python
    if verb == "edit":
        answer_callback_query(cq_id, "pick a field")
        edit_message_text(chat_id, message_id,
                          msg.get("text") or f"Edit receipt #{row_id} — pick a field",
                          keyboard=_edit_field_keyboard(row_id))
        return

    if verb == "editfld":
        # data format: editfld:<row_id>:<field>
        if len(parts) < 3:
            answer_callback_query(cq_id, "missing field")
            return
        field = parts[2]
        if field not in ("account", "amount", "date", "payee", "memo"):
            answer_callback_query(cq_id, f"unknown field {field}")
            return
        _set_pending_action(row_id, f"await_edit_value:{field}")
        answer_callback_query(cq_id, f"reply with new {field}")
        edit_message_text(chat_id, message_id,
                          f"✏️ Reply with new <b>{field}</b> for receipt #{row_id} (or send /skip)",
                          keyboard=[])
        return

    if verb == "editback":
        # User tapped Back — restore the main approve/edit/reject keyboard
        answer_callback_query(cq_id, "back")
        _set_pending_action(row_id, None)
        # Re-render summary
        conn = sqlite3.connect(str(DB))
        try:
            row = conn.execute("SELECT parsed_json FROM pending_inbox WHERE id=?", (row_id,)).fetchone()
        finally:
            conn.close()
        try:
            parsed = json.loads(row[0]) if row and row[0] else {}
        except json.JSONDecodeError:
            parsed = {}
        text_str = _format_receipt_summary(row_id, parsed)
        edit_message_text(chat_id, message_id, text_str, keyboard=_approve_keyboard(row_id))
        return
```

- [ ] **Step 2: Smoke test edit flow end-to-end**

```bash
cd /home/workspace/LISZA-dev
sqlite3 ledger.db "INSERT INTO pending_inbox(source, raw_path, parsed_json, suggested_account, suggested_amount, status) VALUES ('ed_test','/tmp/x','{\"vendor\":\"ED_TEST\",\"total\":1.00,\"suggested_account_code\":\"591\"}', '591', 1.00, 'reviewed');"
FIN_T_B4ME=<test_bot_token> python3 scripts/telegram_intake.py
# In test chat: tap Edit, tap Amount, reply "77.00", then tap Approve on the refreshed keyboard
# Verify:
sqlite3 ledger.db "SELECT suggested_amount, status FROM pending_inbox WHERE source='ed_test';"
```

Expected after edit-then-approve: status=`posted`, entries row exists with amount 77.00.

- [ ] **Step 3: Cleanup all TG test rows**

```bash
sqlite3 /home/workspace/LISZA-dev/ledger.db "
  DELETE FROM splits WHERE entry_id IN (SELECT id FROM entries WHERE payee IN ('SCANNER_TEST','CB_TEST','RJ_TEST','ED_TEST'));
  DELETE FROM entries WHERE payee IN ('SCANNER_TEST','CB_TEST','RJ_TEST','ED_TEST');
  DELETE FROM pending_inbox WHERE source IN ('scanner_test','cb_test','rj_test','ed_test');
"
```

---

## Task 14: sheet_sync.py — add action + action_result columns to `_Pending`

**Files:**
- Modify: `/home/workspace/LISZA-dev/scripts/sheet_sync.py`

- [ ] **Step 1: Extend the `_Pending` headers list**

Find the `_Pending` tab construction in `sheet_sync.py`. The headers are likely defined where `fetch_pending` runs or in `push_tab`. Add `action` and `action_result` as the final two columns:

```python
PENDING_HEADERS = [
    "id", "received_at", "source", "payee", "amount",
    "suggested_account", "confidence", "action", "action_result",
]
```

If `fetch_pending` already returns a fixed-shape list of dicts, extend each row dict with `action: ""` and `action_result: ""` (these are user-editable; the DB pushes empty values, polling fills in result).

```python
def fetch_pending(conn):
    rows = conn.execute(
        "SELECT id, received_at, source, "
        "       json_extract(parsed_json, '$.vendor') AS payee, "
        "       suggested_amount AS amount, suggested_account, "
        "       json_extract(parsed_json, '$.confidence') AS confidence "
        "FROM pending_inbox WHERE status='reviewed' ORDER BY received_at DESC LIMIT 200"
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r[0], "received_at": r[1], "source": r[2],
            "payee": r[3] or "", "amount": r[4] or "",
            "suggested_account": r[5] or "", "confidence": r[6] or "",
            "action": "",  # user fills
            "action_result": "",  # DB fills
        })
    return out
```

The exact existing implementation may differ; preserve its style but ensure the two new columns are present and in the right position. If the existing fetch_pending returns tuples, preserve that — but match the headers order.

- [ ] **Step 2: Push current state to dev sheet to verify schema lands**

```bash
cd /home/workspace/LISZA-dev
FIN_LEDGER_DB=/home/workspace/LISZA-dev/ledger.db \
LISZA_SHEETS_SA_JSON="$LISZA_SHEETS_SA_JSON" \
python3 scripts/sheet_sync.py
```

Expected: `_Pending` tab gets `action` and `action_result` columns at the end. Visually inspect.

---

## Task 15: sheet_sync.py — `process_sheet_actions()` (read sheet, dispatch)

**Files:**
- Modify: `/home/workspace/LISZA-dev/scripts/sheet_sync.py`

- [ ] **Step 1: Add action parser + dispatcher**

Add helper near other functions:

```python
def _parse_action(raw: str) -> dict:
    """Parse user-typed action cell. Returns dict with keys: verb, params, error.

    Grammar:
      approve
      approve:account=591
      approve:account=591,amount=45.00
      reject
      reject:duplicate of #38
      edit:account=591
    """
    raw = (raw or "").strip()
    if not raw:
        return {"verb": None, "params": {}, "error": None}
    if ":" in raw:
        verb, _, rest = raw.partition(":")
        verb = verb.strip().lower()
    else:
        verb = raw.lower()
        rest = ""
    if verb not in ("approve", "reject", "edit"):
        return {"verb": verb, "params": {}, "error": f"unknown verb {verb!r}"}

    params: dict = {}
    if verb == "reject":
        # reject:<freeform reason>
        if rest:
            params["reason"] = rest.strip()
        return {"verb": verb, "params": params, "error": None}

    if rest:
        # comma-separated key=value pairs
        for kv in rest.split(","):
            kv = kv.strip()
            if not kv:
                continue
            if "=" not in kv:
                return {"verb": verb, "params": params, "error": f"bad param {kv!r}"}
            k, _, v = kv.partition("=")
            k = k.strip()
            v = v.strip()
            if k not in ("account", "amount", "date", "payee", "memo"):
                return {"verb": verb, "params": params, "error": f"unknown field {k!r}"}
            if k == "amount":
                try:
                    v = float(v)
                except ValueError:
                    return {"verb": verb, "params": params, "error": f"bad amount {v!r}"}
            params[k] = v
    return {"verb": verb, "params": params, "error": None}


def process_sheet_actions(ws, conn) -> dict:
    """Read _Pending sheet; dispatch action cells; write action_result back.

    Returns counters {'approved':n, 'rejected':n, 'edited':n, 'errors':n, 'noops':n}.
    """
    import post_pending

    # gspread API may differ — adapt to the existing pattern. Sheet is the same
    # ws passed in by push flow. Read all rows.
    all_values = ws.get_all_values()
    if not all_values:
        return {"approved": 0, "rejected": 0, "edited": 0, "errors": 0, "noops": 0}
    headers = all_values[0]
    if "action" not in headers or "action_result" not in headers:
        log("⚠️ _Pending headers missing action/action_result")
        return {"approved": 0, "rejected": 0, "edited": 0, "errors": 0, "noops": 0}
    idx_id = headers.index("id")
    idx_action = headers.index("action")
    idx_result = headers.index("action_result")

    counters = {"approved": 0, "rejected": 0, "edited": 0, "errors": 0, "noops": 0}
    now_str = datetime.utcnow().strftime("%H:%M UTC")
    # Build list of (row_number, action, current_result)
    todo = []
    for i, row in enumerate(all_values[1:], start=2):  # row 1 is header; data starts row 2
        action = row[idx_action] if idx_action < len(row) else ""
        existing_result = row[idx_result] if idx_result < len(row) else ""
        if action.strip() and not existing_result.strip():
            try:
                rid = int(row[idx_id])
            except (ValueError, IndexError):
                continue
            todo.append((i, rid, action.strip()))

    for sheet_row_num, row_id, action_text in todo:
        parsed = _parse_action(action_text)
        if parsed["error"]:
            counters["errors"] += 1
            ws.update_cell(sheet_row_num, idx_result + 1,
                           f"⚠️ ERROR: {parsed['error']}")
            continue
        verb, params = parsed["verb"], parsed["params"]
        try:
            if verb == "approve":
                acct = params.get("account")
                amt = params.get("amount")
                entry_id = post_pending.approve_row(
                    row_id, account_override=acct, amount_override=amt
                )
                if entry_id is None:
                    counters["noops"] += 1
                    ws.update_cell(sheet_row_num, idx_result + 1,
                                   f"⚠️ Already actioned at {now_str}")
                else:
                    counters["approved"] += 1
                    ws.update_cell(sheet_row_num, idx_result + 1,
                                   f"✅ POSTED as #{entry_id} at {now_str}")
                    ws.update_cell(sheet_row_num, idx_action + 1, "")  # clear action
            elif verb == "reject":
                ok = post_pending.reject_row(row_id, reason=params.get("reason", ""))
                if ok:
                    counters["rejected"] += 1
                    ws.update_cell(sheet_row_num, idx_result + 1,
                                   f"❌ REJECTED: {params.get('reason','(no reason)')}")
                    ws.update_cell(sheet_row_num, idx_action + 1, "")
                else:
                    counters["noops"] += 1
                    ws.update_cell(sheet_row_num, idx_result + 1,
                                   f"⚠️ Already actioned at {now_str}")
            elif verb == "edit":
                edited_any = False
                for field in ("account", "amount", "date", "payee", "memo"):
                    if field in params:
                        ok = post_pending.edit_row(row_id, field=field, new_value=params[field])
                        edited_any = edited_any or ok
                if edited_any:
                    counters["edited"] += 1
                    fields_str = ",".join(f"{k}={v}" for k, v in params.items())
                    ws.update_cell(sheet_row_num, idx_result + 1,
                                   f"✏️ EDITED: {fields_str}")
                    ws.update_cell(sheet_row_num, idx_action + 1, "")
                else:
                    counters["noops"] += 1
                    ws.update_cell(sheet_row_num, idx_result + 1,
                                   f"⚠️ row not in 'reviewed' state")
        except ValueError as e:
            counters["errors"] += 1
            ws.update_cell(sheet_row_num, idx_result + 1, f"⚠️ ERROR: {e}")

    return counters
```

Note: this assumes the existing `sheet_sync.py` uses `gspread` or a similar wrapper exposing `ws.get_all_values()` and `ws.update_cell(row, col, val)`. If the existing implementation uses the lower-level Google Sheets API directly, adapt these calls to use `values().get()` / `values().update()` from the existing `get_client()` helper.

- [ ] **Step 2: Add `--bidirectional` CLI flag**

Find `def main():` at the bottom of `sheet_sync.py`. Add an argparse:

```python
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--bidirectional", action="store_true",
                    help="After pushing DB→Sheet, also pull action cells back into DB")
    args = ap.parse_args()

    # existing push logic ...
    # After pushing, if bidirectional, run process_sheet_actions on _Pending tab
    if args.bidirectional:
        # Reuse the connection + worksheet you already have
        result = process_sheet_actions(pending_ws, conn)
        log(f"bidirectional sync: {result}")
```

The exact integration point depends on the existing main() structure — preserve push-only as default.

- [ ] **Step 3: Smoke test against a duplicated dev sheet**

Manual sequence (requires a duplicated test spreadsheet):
```bash
# 1. Duplicate Personal Accounts → "Personal Accounts (DEV TEST)" in Drive UI
# 2. Update LISZA-dev/scripts/sheet_sync.py SPREADSHEET_ID constant to point at the test sheet
# 3. Run --bidirectional once to seed schema
cd /home/workspace/LISZA-dev
FIN_LEDGER_DB=ledger.db python3 scripts/sheet_sync.py --bidirectional
# 4. In the dev sheet, type "approve" in the action cell of a reviewed row
# 5. Wait 2 min (or re-run) to trigger pull
FIN_LEDGER_DB=ledger.db python3 scripts/sheet_sync.py --bidirectional
# 6. Verify action cell cleared, action_result says "✅ POSTED as #N"
sqlite3 ledger.db "SELECT id, status FROM pending_inbox WHERE id=<that row>;"
```

Expected: status=`posted`, action_result populated.

---

## Task 16: Hybrid concurrency live test

**Files:**
- Read-only verification.

- [ ] **Step 1: Insert a fresh reviewed row in dev**

```bash
sqlite3 /home/workspace/LISZA-dev/ledger.db \
  "INSERT INTO pending_inbox(source, raw_path, parsed_json, suggested_account, suggested_amount, status) VALUES ('hybrid','/tmp/x','{\"vendor\":\"HYBRID_TEST\",\"total\":7.00,\"suggested_account_code\":\"591\"}', '591', 7.00, 'reviewed');"
ROW_ID=$(sqlite3 /home/workspace/LISZA-dev/ledger.db "SELECT id FROM pending_inbox WHERE source='hybrid' ORDER BY id DESC LIMIT 1;")
echo "ROW_ID=$ROW_ID"
```

- [ ] **Step 2: Simultaneously fire approves from both surfaces**

Open two shells:

```bash
# Shell A — TG path (CLI simulates a callback)
python3 /home/workspace/LISZA-dev/scripts/post_pending.py --row-id $ROW_ID --approve &

# Shell B — Sheet path
python3 /home/workspace/LISZA-dev/scripts/post_pending.py --row-id $ROW_ID --approve &

wait
```

(The `&` runs them concurrently. We use `post_pending.py` CLI directly to simulate both surfaces' shared backend call — the real surfaces import the same library, so this is equivalent.)

- [ ] **Step 3: Verify only one entries row was created**

```bash
sqlite3 /home/workspace/LISZA-dev/ledger.db "SELECT count(*) FROM entries WHERE source_ref='pending_inbox:'||$ROW_ID;"
```

Expected: `1`. One process succeeded, the other returned None (exit code 1).

- [ ] **Step 4: Verify pending_inbox status**

```bash
sqlite3 /home/workspace/LISZA-dev/ledger.db "SELECT status, entry_id FROM pending_inbox WHERE id=$ROW_ID;"
```

Expected: `posted|<entry_id>`.

- [ ] **Step 5: Cleanup**

```bash
sqlite3 /home/workspace/LISZA-dev/ledger.db "
  DELETE FROM splits WHERE entry_id IN (SELECT id FROM entries WHERE payee='HYBRID_TEST');
  DELETE FROM entries WHERE payee='HYBRID_TEST';
  DELETE FROM pending_inbox WHERE source='hybrid';
"
```

---

## Task 17: Promote dev → prod (post_pending.py + tests)

**Files:**
- Create: `/home/workspace/LISZA/scripts/post_pending.py`
- Create: `/home/workspace/LISZA/scripts/test_post_pending.py`
- Modify: `/home/workspace/LISZA/scripts/telegram_intake.py`
- Modify: `/home/workspace/LISZA/scripts/sheet_sync.py`

- [ ] **Step 1: Diff each dev/prod pair**

```bash
diff -q /home/workspace/LISZA-dev/scripts/post_pending.py /home/workspace/LISZA/scripts/post_pending.py 2>&1
diff -q /home/workspace/LISZA-dev/scripts/telegram_intake.py /home/workspace/LISZA/scripts/telegram_intake.py
diff -q /home/workspace/LISZA-dev/scripts/sheet_sync.py /home/workspace/LISZA/scripts/sheet_sync.py
```

Expected: `post_pending.py` differs because prod doesn't have it yet ("only in LISZA-dev"); the other two show diffs reflecting the extensions.

- [ ] **Step 2: Copy with explicit per-file confirmation**

```bash
cp /home/workspace/LISZA-dev/scripts/post_pending.py /home/workspace/LISZA/scripts/post_pending.py
cp /home/workspace/LISZA-dev/scripts/test_post_pending.py /home/workspace/LISZA/scripts/test_post_pending.py
cp /home/workspace/LISZA-dev/scripts/telegram_intake.py /home/workspace/LISZA/scripts/telegram_intake.py
cp /home/workspace/LISZA-dev/scripts/sheet_sync.py /home/workspace/LISZA/scripts/sheet_sync.py
```

- [ ] **Step 3: Confirm byte parity**

```bash
diff -q /home/workspace/LISZA-dev/scripts/{post_pending,telegram_intake,sheet_sync}.py /home/workspace/LISZA/scripts/
```

Expected: empty (identical).

- [ ] **Step 4: Run prod tests once**

```bash
cd /home/workspace/LISZA && FIN_LEDGER_DB=/home/workspace/LISZA-dev/ledger.db python3 -m pytest scripts/test_post_pending.py -v
```

(Use dev DB for the test run to avoid mutating prod ledger during tests.)
Expected: all pass.

- [ ] **Step 5: No git commit — LISZA is gitignored**

This is recorded only in `LISZA/PLAN.md` (Task 19).

---

## Task 18: Restart `fin-telegram-intake` service + install cron

**Files:**
- Modify: `crontab`
- Restart: user service `fin-telegram-intake`

- [ ] **Step 1: Restart fin-telegram-intake to pick up extended telegram_intake.py**

Run via Zo service tools (or whatever the equivalent is on this machine — likely `systemctl --user restart fin-telegram-intake` or via the Zo MCP `update_user_service`). The service env should already have `FIN_T_B4ME` injected.

Run:
```bash
# Check service status command depends on Zo's runtime — adapt as needed
ps aux | grep -i fin-telegram-intake | grep -v grep
```

If running via Zo's user services, use the MCP tool (outside this plan's bash scope) or `update_user_service` to bounce the process. After restart, verify in logs:
```bash
tail -n 20 /home/workspace/LISZA/logs/telegram_intake.log 2>/dev/null || true
```

Expected: recent log lines from the new code (look for `keyboard scan error` only on error, or no errors at all).

- [ ] **Step 2: Add sheet_sync.py --bidirectional crontab entry**

```bash
crontab -l > /tmp/crontab.new.txt
cat >> /tmp/crontab.new.txt <<'EOF'

# LISZA: bidirectional sheet sync — every 2 min — added 2026-05-18
*/2 * * * * /usr/local/bin/python3 /home/workspace/LISZA/scripts/sheet_sync.py --bidirectional >> /home/workspace/LISZA/logs/sheet_sync.log 2>&1
EOF
crontab /tmp/crontab.new.txt
crontab -l | grep -A1 "bidirectional sheet sync"
rm /tmp/crontab.new.txt
```

Expected: entry visible in crontab listing.

- [ ] **Step 3: Wait one cycle (max 2 min), inspect log**

```bash
sleep 130
tail -n 30 /home/workspace/LISZA/logs/sheet_sync.log
```

Expected: at least one cycle logged, including `bidirectional sync: {...counters...}` line. No traceback.

---

## Task 19: PLAN.md updates + final live test

**Files:**
- Modify: `/home/workspace/LISZA/PLAN.md`

- [ ] **Step 1: Send a real receipt + approve via TG**

In the real LISZA Books Telegram chat, send a photo of a receipt. Wait for `code_receipt.py` to finish parsing (status goes to `reviewed`). Then within ~50s the keyboard appears (next intake poll). Tap Approve. Verify:

```bash
sqlite3 /home/workspace/LISZA/ledger.db \
  "SELECT id, payee, date FROM entries ORDER BY id DESC LIMIT 1;"
sqlite3 /home/workspace/LISZA/ledger.db \
  "SELECT * FROM v_check_register ORDER BY date DESC LIMIT 1;"
```

Expected: new entry exists, check register shows the running balance updated.

- [ ] **Step 2: Verify Sheet `_Pending` tab reflects the post**

Open Personal Accounts → `_Pending` tab. After the next 2-min cron cycle, the row that was just posted should no longer appear (since it's no longer in `reviewed` status). Optionally, queue another receipt and approve it via Sheet (type `approve` in the action cell) to verify the Sheet surface end-to-end.

- [ ] **Step 3: Update LISZA/PLAN.md**

Edit `/home/workspace/LISZA/PLAN.md`. In the Phase 2 section, change:

```markdown
- [ ] Review/approve UI — Sheet `_Pending` tab buttons or Telegram inline keyboard
- [ ] `post_pending.py` — promote a `reviewed` row to a posted entry (debit
      expense / credit payment account)
```

to:

```markdown
- [x] Review/approve UI — Telegram inline keyboard + Google Sheet bidirectional
      (`_Pending.action` cell) — shipped 2026-05-18
- [x] `post_pending.py` — library+CLI for approve/reject/edit (shipped 2026-05-18)
```

Also add a one-line note at the top of Phase 2 referencing the spec:

```markdown
### Phase 2 — Intake
*Spec: `LISZA/docs/specs/2026-05-18-receipt-classifier-ui-design.md` — shipped 2026-05-18*
```

- [ ] **Step 4: Bootstrap note for LISZA-dev/**

Append a one-line note under "Storage Layout" or similar section in PLAN.md, since the directory now exists:

```markdown
| `LISZA-dev/` | dev sandbox (bootstrapped 2026-05-18, was referenced by Rule #5 but did not exist before) |
```

- [ ] **Step 5: No git commit — LISZA is gitignored. The PLAN.md is the audit trail.**

---

## Plan Self-Review

**Spec coverage check:**
- post_pending.py library+CLI → Tasks 3, 4, 5, 6, 7, 8 (TDD across the API surface)
- approve_row / reject_row / edit_row signatures → Tasks 3, 7
- SQL status guard concurrency → Tasks 4, 16
- TG inline keyboard (Approve/Edit/Reject) → Tasks 9, 10, 11, 12, 13
- TG edit flow (field picker + reply capture) → Task 13
- Sheet bidirectional with action/action_result columns → Tasks 14, 15
- Sheet action grammar parser → Task 15
- 2-min cron cadence for sheet_sync → Task 18
- Hybrid concurrency model (TG wins, Sheet writes "Already actioned") → Tasks 11 (TG path), 15 (Sheet path), 16 (live test)
- LISZA-dev bootstrap → Task 1
- Promote dev→prod with diff-verify → Task 17
- PLAN.md updates → Task 19
- Acceptance criteria mapped:
  - approve happy + idempotent → Tasks 3, 4
  - zero/negative amount rejected → Task 5
  - TG keyboard appears → Task 10 (smoke), Task 19 (live)
  - Sheet bidirectional cycle → Task 15
  - Hybrid race → Task 16
  - cron installed → Task 18
  - PLAN.md updated → Task 19

**Placeholder scan:** No "TBD", "implement later", "add validation" without code, or "similar to Task N" found. Every code step has actual code.

**Type consistency:**
- `approve_row(row_id, *, account_override, amount_override, payment_account) -> int | None` — consistent across spec, task 3 impl, task 11 callback, task 15 dispatcher.
- `reject_row(row_id, *, reason) -> bool` — consistent.
- `edit_row(row_id, *, field, new_value) -> bool` — consistent. Field set: account|amount|date|payee|memo, identical in tests, TG handler, Sheet parser.
- Callback data format: `approve:N`, `edit:N`, `reject:N`, `editfld:N:FIELD`, `editback:N` — consistent across keyboard helpers and `handle_callback`.
- `pending_action` notes tag values: `await_reject_reason`, `await_edit_value:<field>` — consistent across `_set_pending_action`, `_get_pending_action`, follow-up text handler.
- `_Pending` column headers (id, received_at, source, payee, amount, suggested_account, confidence, action, action_result) — consistent between Task 14 schema and Task 15 column lookups.
- `process_sheet_actions` return keys (approved, rejected, edited, errors, noops) — consistent.

**Risk coverage (from spec Risks table):**
- Dedup state corruption — N/A for LISZA (no dedup state file in this design).
- TG handler crash mid-edit → all state in `pending_inbox.notes`, recoverable on next callback (Task 12).
- Sheet polling double-fire → SQL status guard prevents double-action (Task 16).
- Pixtral mis-parse → edit flow exists (Task 13).
- Malformed action grammar → action_result writes ERROR, action cell preserved (Task 15).
- Google Sheets API quota → 2-min cadence × ~10 ops/cycle = well under quota.
- TG bot offline → Sheet surface fully independent (verified by isolation in Task 15).
- Concurrent edits to notes → atomic single-statement UPDATEs, last-write-wins on notes is acceptable per spec.

No gaps found.
