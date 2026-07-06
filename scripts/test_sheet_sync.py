import json
import sqlite3

import pytest

import post_pending
import sheet_sync
import tenancy


class FakeWorksheet:
    def __init__(self, values):
        self.values = [list(r) for r in values]

    def get_all_values(self):
        return [list(r) for r in self.values]

    def update_cell(self, row, col, value):
        while len(self.values) < row:
            self.values.append([])
        while len(self.values[row - 1]) < col:
            self.values[row - 1].append("")
        self.values[row - 1][col - 1] = value


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(
        slug="jb-design", display_name="J.B. Design", entity_type="sole_prop"
    )
    return tenancy.resolve_db("jb-design")


def _queue_reviewed(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    post_pending.ensure_pending_inbox(con)
    payload = {
        "coded": {
            "vendor": "Staples",
            "date": "2026-06-30",
            "category_hint": "office supplies",
            "suggested_payment_account_code": "102",
        }
    }
    cur = con.execute(
        """INSERT INTO pending_inbox
           (source, raw_path, parsed_json, suggested_account, suggested_amount, status)
           VALUES ('receipt_scanner', 'receipt.pdf', ?, '510', 42.5, 'reviewed')""",
        (json.dumps(payload),),
    )
    con.commit()
    return con, cur.lastrowid


def test_parse_action_supports_overrides_and_reject_reason():
    assert sheet_sync.parse_action("approve: account=525, amount=50.25") == (
        "approve",
        {"account": "525", "amount": "50.25"},
    )
    assert sheet_sync.parse_action("reject: duplicate receipt") == (
        "reject",
        {"reason": "duplicate receipt"},
    )
    with pytest.raises(ValueError):
        sheet_sync.parse_action("approve: account")


def test_fetch_pending_adds_action_columns(client_db):
    con, _ = _queue_reviewed(client_db)
    data = sheet_sync.fetch_pending(con)
    con.close()

    assert data[0][-2:] == ["Action", "Action Result"]
    assert data[1][-2:] == ["", ""]


def test_process_sheet_actions_approves_row(client_db):
    con, row_id = _queue_reviewed(client_db)
    ws = FakeWorksheet([
        ["ID", "Received At", "Source", "Status", "Suggested Account",
         "Suggested Amount", "Notes", "Raw Path", "Action", "Action Result"],
        [str(row_id), "", "", "reviewed", "510", "42.5", "", "", "approve", ""],
    ])

    counts = sheet_sync.process_sheet_actions(ws, con)

    status, entry_id = con.execute(
        "SELECT status, entry_id FROM pending_inbox WHERE id=?", (row_id,)
    ).fetchone()
    entry_count = con.execute(
        "SELECT COUNT(*) FROM entries WHERE source_ref=?", (f"inbox:{row_id}",)
    ).fetchone()[0]
    con.close()

    assert counts["approved"] == 1
    assert status == "posted"
    assert isinstance(entry_id, int)
    assert entry_count == 1
    assert ws.values[1][8] == ""
    assert ws.values[1][9].startswith("POSTED as #")


def test_process_sheet_actions_keeps_error_action_for_fixing(client_db):
    con, row_id = _queue_reviewed(client_db)
    ws = FakeWorksheet([
        ["ID", "Action", "Action Result"],
        [str(row_id), "approve: acount=525", ""],
    ])

    counts = sheet_sync.process_sheet_actions(ws, con)
    status = con.execute("SELECT status FROM pending_inbox WHERE id=?", (row_id,)).fetchone()[0]
    con.close()

    assert counts["errors"] == 1
    assert status == "reviewed"
    assert ws.values[1][1] == "approve: acount=525"
    assert ws.values[1][2].startswith("ERROR:")


def test_process_sheet_actions_edits_and_rejects(client_db):
    con, row_id = _queue_reviewed(client_db)
    ws = FakeWorksheet([
        ["ID", "Action", "Action Result"],
        [str(row_id), "edit: amount=55.00", ""],
    ])

    counts = sheet_sync.process_sheet_actions(ws, con)
    amount = con.execute(
        "SELECT suggested_amount FROM pending_inbox WHERE id=?", (row_id,)
    ).fetchone()[0]

    ws = FakeWorksheet([
        ["ID", "Action", "Action Result"],
        [str(row_id), "reject: duplicate", ""],
    ])
    reject_counts = sheet_sync.process_sheet_actions(ws, con)
    status = con.execute("SELECT status FROM pending_inbox WHERE id=?", (row_id,)).fetchone()[0]
    con.close()

    assert counts["edited"] == 1
    assert amount == 55.0
    assert reject_counts["rejected"] == 1
    assert status == "rejected"
