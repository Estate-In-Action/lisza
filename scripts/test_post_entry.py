import sqlite3

import pytest

import tenancy
import post_entry


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(
        slug="jb-design", display_name="J.B. Design", entity_type="sole_prop"
    )
    return tenancy.resolve_db("jb-design")


def _balanced_payload(post=True):
    return {
        "entry_date": "2026-06-30",
        "description": "Office supplies",
        "payee": "Staples",
        "post": post,
        "splits": [
            {"account": "510", "dr": 40.0, "cr": 0.0, "memo": "pens"},
            {"account": "102", "dr": 0.0, "cr": 40.0, "memo": ""},
        ],
    }


def test_post_json_posts_balanced_entry(client_db):
    result = post_entry.post_json(_balanced_payload(post=True), db_path=client_db)
    assert result["status"] == "posted"
    assert result["balanced"] is True
    con = sqlite3.connect(client_db)
    row = con.execute(
        "SELECT status, source FROM entries WHERE id=?", (result["entry_id"],)
    ).fetchone()
    splits = con.execute(
        "SELECT COUNT(*) FROM splits WHERE entry_id=?", (result["entry_id"],)
    ).fetchone()[0]
    con.close()
    assert row[0] == "posted"
    assert row[1] == "manual"
    assert splits == 2


def test_post_json_saves_pending_when_post_false(client_db):
    result = post_entry.post_json(_balanced_payload(post=False), db_path=client_db)
    assert result["status"] == "pending"
    con = sqlite3.connect(client_db)
    status = con.execute(
        "SELECT status FROM entries WHERE id=?", (result["entry_id"],)
    ).fetchone()[0]
    con.close()
    assert status == "pending"


def test_post_json_rejects_unbalanced_post(client_db):
    payload = _balanced_payload(post=True)
    payload["splits"][1]["cr"] = 30.0  # 40 dr vs 30 cr
    with pytest.raises(ValueError):
        post_entry.post_json(payload, db_path=client_db)


def test_post_json_unbalanced_allowed_as_pending(client_db):
    payload = _balanced_payload(post=False)
    payload["splits"][1]["cr"] = 30.0
    result = post_entry.post_json(payload, db_path=client_db)
    assert result["status"] == "pending"
    assert result["balanced"] is False


def test_post_json_requires_two_splits(client_db):
    payload = _balanced_payload()
    payload["splits"] = payload["splits"][:1]
    with pytest.raises(ValueError):
        post_entry.post_json(payload, db_path=client_db)


def test_post_json_rejects_unknown_account(client_db):
    payload = _balanced_payload()
    payload["splits"][0]["account"] = "999999"
    with pytest.raises(ValueError):
        post_entry.post_json(payload, db_path=client_db)


def test_post_json_rejects_split_with_both_sides(client_db):
    payload = _balanced_payload()
    payload["splits"][0]["cr"] = 5.0  # dr=40 and cr=5 on one split
    with pytest.raises(ValueError):
        post_entry.post_json(payload, db_path=client_db)
