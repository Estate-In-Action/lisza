import csv
import json
import sqlite3

import ecosystem_export
import tenancy


def test_export_client_writes_manifest_and_datasets(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    con.executescript(
        """
        INSERT INTO entries(id, entry_date, description, payee, source, status)
          VALUES(1, '2026-07-01', 'sale', 'Customer A', 'manual', 'posted');
        INSERT INTO splits(entry_id, account, dr, cr)
          VALUES(1, '102', 100.00, 0), (1, '400', 0, 100.00);
        INSERT INTO invoices(id, party, issue_date, due_date, amount, status)
          VALUES(3, 'Customer A', '2026-07-01', '2026-07-31', 100.00, 'open');
        INSERT INTO bills(id, party, issue_date, due_date, amount, status)
          VALUES(4, 'Vendor B', '2026-07-01', '2026-07-15', 25.00, 'unpaid');
        """
    )
    con.commit()
    con.close()

    manifest = ecosystem_export.export_client("acme", out_dir=tmp_path / "out")

    assert manifest["mode"] == "read_only_export"
    assert manifest["external_api_calls"] == "disabled"
    assert manifest["writeback"] == "disabled"
    assert set(manifest["datasets"]) == {"accounts", "journal_lines", "invoices", "bills"}
    assert manifest["datasets"]["journal_lines"]["rows"] == 2

    manifest_file = tmp_path / "out" / "manifest.json"
    invoices_json = tmp_path / "out" / "invoices.json"
    bills_csv = tmp_path / "out" / "bills.csv"
    assert manifest_file.exists()
    assert json.loads(invoices_json.read_text())[0]["party"] == "Customer A"
    with bills_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["party"] == "Vendor B"


def test_field_map_contains_qbo_and_xero_targets():
    assert ecosystem_export.FIELD_MAP["accounts"]["qbo"]["code"] == "AcctNum"
    assert ecosystem_export.FIELD_MAP["accounts"]["xero"]["code"] == "Code"
