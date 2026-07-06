import json
import sqlite3

import pytest

import tenancy
import post_pending


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(
        slug="jb-design", display_name="J.B. Design", entity_type="sole_prop"
    )
    return tenancy.resolve_db("jb-design")


def _queue_reviewed(db_path, *, amount=42.5, account="510", payment_account="102"):
    con = sqlite3.connect(db_path)
    post_pending.ensure_pending_inbox(con)
    payload = {
        "coded": {
            "vendor": "Staples",
            "date": "2026-06-30",
            "category_hint": "office supplies",
            "suggested_payment_account_code": payment_account,
        }
    }
    cur = con.execute(
        """INSERT INTO pending_inbox
           (source, raw_path, parsed_json, suggested_account, suggested_amount, status)
           VALUES ('receipt_scanner', 'receipt.pdf', ?, ?, ?, 'reviewed')""",
        (json.dumps(payload), account, amount),
    )
    con.commit()
    con.close()
    return cur.lastrowid


def test_connect_self_heals_pending_inbox(client_db):
    con = sqlite3.connect(client_db)
    pre = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_inbox'"
    ).fetchone()
    con.close()
    assert pre is None

    con = post_pending.connect(client_db)
    post = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_inbox'"
    ).fetchone()
    con.close()
    assert post is not None


def test_approve_row_posts_balanced_entry(client_db):
    row_id = _queue_reviewed(client_db)

    entry_id = post_pending.approve_row(row_id, db_path=client_db)

    assert isinstance(entry_id, int)
    con = sqlite3.connect(client_db)
    status = con.execute(
        "SELECT status, entry_id FROM pending_inbox WHERE id=?", (row_id,)
    ).fetchone()
    entry = con.execute(
        "SELECT status, source, source_ref, payee FROM entries WHERE id=?", (entry_id,)
    ).fetchone()
    totals = con.execute(
        "SELECT ROUND(SUM(dr), 2), ROUND(SUM(cr), 2) FROM splits WHERE entry_id=?",
        (entry_id,),
    ).fetchone()
    con.close()

    assert status == ("posted", entry_id)
    assert entry == ("posted", "receipt_scanner", f"inbox:{row_id}", "Staples")
    assert totals == (42.5, 42.5)


def test_approve_row_is_idempotent(client_db):
    row_id = _queue_reviewed(client_db)
    first = post_pending.approve_row(row_id, db_path=client_db)
    second = post_pending.approve_row(row_id, db_path=client_db)

    con = sqlite3.connect(client_db)
    count = con.execute(
        "SELECT COUNT(*) FROM entries WHERE source_ref=?", (f"inbox:{row_id}",)
    ).fetchone()[0]
    con.close()

    assert isinstance(first, int)
    assert second is None
    assert count == 1


def test_approve_row_validates_account_and_amount(client_db):
    bad_account = _queue_reviewed(client_db, account="999999")
    with pytest.raises(ValueError, match="account"):
        post_pending.approve_row(bad_account, db_path=client_db)

    bad_amount = _queue_reviewed(client_db, amount=0)
    with pytest.raises(ValueError, match="amount"):
        post_pending.approve_row(bad_amount, db_path=client_db)


def test_reject_row_only_actions_reviewed_rows(client_db):
    row_id = _queue_reviewed(client_db)
    assert post_pending.reject_row(row_id, db_path=client_db, reason="duplicate")
    assert not post_pending.reject_row(row_id, db_path=client_db, reason="again")

    con = sqlite3.connect(client_db)
    status, notes = con.execute(
        "SELECT status, notes FROM pending_inbox WHERE id=?", (row_id,)
    ).fetchone()
    con.close()

    assert status == "rejected"
    assert "duplicate" in notes


def test_edit_row_updates_reviewed_row(client_db):
    row_id = _queue_reviewed(client_db)

    assert post_pending.edit_row(row_id, db_path=client_db, field="account", value="525")
    assert post_pending.edit_row(row_id, db_path=client_db, field="amount", value="50.25")
    assert post_pending.edit_row(row_id, db_path=client_db, field="payee", value="New Vendor")

    con = sqlite3.connect(client_db)
    row = con.execute(
        "SELECT suggested_account, suggested_amount, parsed_json, notes FROM pending_inbox WHERE id=?",
        (row_id,),
    ).fetchone()
    con.close()

    payload = json.loads(row[2])
    assert row[0] == "525"
    assert row[1] == 50.25
    assert payload["coded"]["payee"] == "New Vendor"
    assert "edited account=525" in row[3]
