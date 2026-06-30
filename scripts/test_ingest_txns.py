import sqlite3

import pytest

import tenancy
import ingest_txns


CC_CSV = """Date,Description,Debit,Credit,Category,Name,Card
06/01/2026,COFFEE SHOP,12.50,,Dining,Jamie,Visa 1234
06/02/2026,CLIENT PAYMENT,,500.00,Deposit,Jamie,Visa 1234
"""

CHECKING_CSV = """Posting Date,Transaction Date,Amount,Credit Debit Indicator,Description,Category,Card Ending
06/03/2026,06/03/2026,250.00,Debit,OFFICE SUPPLIES,General Merchandise,9999
"""


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(
        slug="jb-design", display_name="J.B. Design", entity_type="sole_prop"
    )
    return tenancy.resolve_db("jb-design")


def test_parse_csv_content_bank_cc():
    rows = ingest_txns.parse_csv_content(CC_CSV, source_file="cc.csv")
    assert len(rows) == 2
    debit = next(r for r in rows if r["is_debit"])
    assert debit["amount"] == 12.50
    assert debit["description"] == "COFFEE SHOP"
    credit = next(r for r in rows if not r["is_debit"])
    assert credit["amount"] == 500.00


def test_parse_csv_content_checking():
    rows = ingest_txns.parse_csv_content(CHECKING_CSV, source_file="chk.csv")
    assert len(rows) == 1
    assert rows[0]["is_debit"] is True
    assert rows[0]["amount"] == 250.00


def test_parse_csv_content_unknown_returns_empty():
    rows = ingest_txns.parse_csv_content("foo,bar\n1,2\n", source_file="x.csv")
    assert rows == []


def test_ingest_text_self_heals_inbox(client_db):
    # register_client does not create pending_inbox; ingest_text must create it.
    con = sqlite3.connect(client_db)
    pre = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_inbox'"
    ).fetchone()
    con.close()
    assert pre is None

    ingest_txns.ingest_text(CC_CSV, db_path=client_db, source_file="cc.csv")

    con = sqlite3.connect(client_db)
    post = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_inbox'"
    ).fetchone()
    con.close()
    assert post is not None


def test_ingest_text_lands_new_rows(client_db):
    result = ingest_txns.ingest_text(CC_CSV, db_path=client_db, source_file="cc.csv")
    assert result["inserted"] == 2
    con = sqlite3.connect(client_db)
    rows = con.execute("SELECT status, source FROM pending_inbox").fetchall()
    con.close()
    assert len(rows) == 2
    assert all(r[0] == "new" for r in rows)
    assert all(r[1] == ingest_txns.SOURCE_NAME for r in rows)


def test_ingest_text_posts_nothing(client_db):
    # Option A: import never touches the posted ledger; entries stays empty.
    ingest_txns.ingest_text(CC_CSV, db_path=client_db, source_file="cc.csv")
    con = sqlite3.connect(client_db)
    n = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    con.close()
    assert n == 0


def test_ingest_text_is_idempotent(client_db):
    ingest_txns.ingest_text(CC_CSV, db_path=client_db, source_file="cc.csv")
    second = ingest_txns.ingest_text(CC_CSV, db_path=client_db, source_file="cc.csv")
    assert second["inserted"] == 0
    assert second["skipped"] == 2
    con = sqlite3.connect(client_db)
    n = con.execute("SELECT COUNT(*) FROM pending_inbox").fetchone()[0]
    con.close()
    assert n == 2
