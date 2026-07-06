import sqlite3

import pytest

import statement_automation
import tenancy


CSV_TEXT = """Date,Description,Debit,Credit,Category,Name,Card
06/01/2026,COFFEE SHOP,12.50,,Dining,Jamie,Visa 1234
"""


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(
        slug="jb-design", display_name="J.B. Design", entity_type="sole_prop"
    )
    db = tenancy.resolve_db("jb-design")
    monkeypatch.setattr(statement_automation.ingest_txns, "DB_PATH", db)
    return db


def test_discover_files_only_supported_suffixes(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.csv").write_text(CSV_TEXT)
    (inbox / "b.PDF").write_bytes(b"%PDF")
    (inbox / "ignore.txt").write_text("x")

    names = [p.name for p in statement_automation.discover_files(inbox)]

    assert names == ["a.csv", "b.PDF"]


def test_run_once_imports_csv_and_records_manifest(tmp_path, client_db):
    inbox = tmp_path / "inbox"
    manifest = tmp_path / "manifest.json"
    inbox.mkdir()
    (inbox / "statement.csv").write_text(CSV_TEXT)

    first = statement_automation.run_once(inbox=inbox, manifest_path=manifest)
    second = statement_automation.run_once(inbox=inbox, manifest_path=manifest)

    con = sqlite3.connect(client_db)
    pending = con.execute("SELECT COUNT(*) FROM pending_inbox").fetchone()[0]
    entries = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    con.close()

    assert first["seen"] == 1
    assert first["processed"] == 1
    assert second["skipped"] == 1
    assert pending == 1
    assert entries == 0
    assert "statement.csv" in manifest.read_text()


def test_run_once_dry_run_does_not_write_manifest_or_ledger(tmp_path, client_db):
    inbox = tmp_path / "inbox"
    manifest = tmp_path / "manifest.json"
    inbox.mkdir()
    (inbox / "statement.csv").write_text(CSV_TEXT)

    result = statement_automation.run_once(
        inbox=inbox, manifest_path=manifest, dry_run=True
    )

    con = sqlite3.connect(client_db)
    has_inbox = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_inbox'"
    ).fetchone()
    con.close()

    assert result["processed"] == 1
    assert result["dry_run"] is True
    assert not manifest.exists()
    assert has_inbox is not None


def test_changed_file_reprocesses_but_row_dedupe_still_protects_ledger(tmp_path, client_db):
    inbox = tmp_path / "inbox"
    manifest = tmp_path / "manifest.json"
    inbox.mkdir()
    statement = inbox / "statement.csv"
    statement.write_text(CSV_TEXT)

    first = statement_automation.run_once(inbox=inbox, manifest_path=manifest)
    statement.write_text(CSV_TEXT + "06/02/2026,OFFICE SHOP,5.00,,Office,Jamie,Visa 1234\n")
    second = statement_automation.run_once(inbox=inbox, manifest_path=manifest)

    con = sqlite3.connect(client_db)
    pending = con.execute("SELECT COUNT(*) FROM pending_inbox").fetchone()[0]
    con.close()

    assert first["processed"] == 1
    assert second["processed"] == 1
    assert pending == 2
