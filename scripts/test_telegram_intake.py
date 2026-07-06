import json
import sqlite3
from pathlib import Path

import pytest

import post_pending
import telegram_intake
import tenancy


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(
        slug="jb-design", display_name="J.B. Design", entity_type="sole_prop"
    )
    db = tenancy.resolve_db("jb-design")
    monkeypatch.setattr(telegram_intake, "DB", Path(db))
    return db


def _queue_reviewed(db_path):
    con = sqlite3.connect(db_path)
    post_pending.ensure_pending_inbox(con)
    payload = {
        "coded": {
            "vendor": "Staples",
            "date": "2026-06-30",
            "confidence": 0.91,
            "category_hint": "office supplies",
            "suggested_payment_account_code": "102",
        }
    }
    cur = con.execute(
        """INSERT INTO pending_inbox
           (source, raw_path, parsed_json, suggested_account, suggested_amount, status)
           VALUES ('telegram', 'receipt.jpg', ?, '510', 42.5, 'reviewed')""",
        (json.dumps(payload),),
    )
    con.commit()
    con.close()
    return cur.lastrowid


def test_notify_reviewed_rows_sends_keyboard_once(client_db, monkeypatch):
    row_id = _queue_reviewed(client_db)
    sent = []

    def fake_send(chat_id, text, keyboard):
        sent.append((chat_id, text, keyboard))
        return {"message_id": 900 + len(sent)}

    monkeypatch.setattr(telegram_intake, "send_message_with_keyboard", fake_send)

    first = telegram_intake.notify_reviewed_rows({"telegram_chat_id": 123})
    second = telegram_intake.notify_reviewed_rows({"telegram_chat_id": 123})

    con = sqlite3.connect(client_db)
    notes = con.execute("SELECT notes FROM pending_inbox WHERE id=?", (row_id,)).fetchone()[0]
    con.close()

    assert first == 1
    assert second == 0
    assert len(sent) == 1
    assert f"approve:{row_id}" == sent[0][2][0][0]["callback_data"]
    assert f"reject:{row_id}" == sent[0][2][0][1]["callback_data"]
    assert "tg_review_message_id=901" in notes


def test_handle_callback_approve_posts_entry(client_db, monkeypatch):
    row_id = _queue_reviewed(client_db)
    answers = []
    edits = []
    monkeypatch.setattr(telegram_intake, "answer_callback_query", lambda cq_id, text="": answers.append((cq_id, text)))
    monkeypatch.setattr(telegram_intake, "edit_message_text", lambda *args, **kwargs: edits.append((args, kwargs)))

    telegram_intake.handle_callback(
        {"telegram_chat_id": 123},
        {
            "id": "cb1",
            "data": f"approve:{row_id}",
            "message": {"message_id": 55, "chat": {"id": 123}},
        },
    )

    con = sqlite3.connect(client_db)
    status, entry_id = con.execute(
        "SELECT status, entry_id FROM pending_inbox WHERE id=?", (row_id,)
    ).fetchone()
    entry_count = con.execute(
        "SELECT COUNT(*) FROM entries WHERE source_ref=?", (f"inbox:{row_id}",)
    ).fetchone()[0]
    con.close()

    assert status == "posted"
    assert isinstance(entry_id, int)
    assert entry_count == 1
    assert answers == [("cb1", "posted")]
    assert "posted as entry" in edits[0][0][2]


def test_handle_callback_reject_marks_rejected(client_db, monkeypatch):
    row_id = _queue_reviewed(client_db)
    answers = []
    monkeypatch.setattr(telegram_intake, "answer_callback_query", lambda cq_id, text="": answers.append((cq_id, text)))
    monkeypatch.setattr(telegram_intake, "edit_message_text", lambda *args, **kwargs: None)

    telegram_intake.handle_callback(
        {"telegram_chat_id": 123},
        {
            "id": "cb2",
            "data": f"reject:{row_id}",
            "message": {"message_id": 55, "chat": {"id": 123}},
        },
    )

    con = sqlite3.connect(client_db)
    status, notes = con.execute(
        "SELECT status, notes FROM pending_inbox WHERE id=?", (row_id,)
    ).fetchone()
    con.close()

    assert status == "rejected"
    assert "telegram reject button" in notes
    assert answers == [("cb2", "rejected")]
