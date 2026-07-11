import io
import sqlite3
from datetime import datetime

from openpyxl import Workbook

import ingest_txns
import tenancy


def _make_xlsx_bytes(*, date_as_datetime=False):
    wb = Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "Debit", "Credit", "Category", "Name", "Card"])
    d1 = datetime(2026, 7, 1) if date_as_datetime else "07/01/2026"
    ws.append([d1, "COFFEE SHOP", 12.50, "", "Restaurants/Dining", "Jane", "Business Credit Card"])
    ws.append(["07/02/2026", "CLIENT DEPOSIT", "", 1500.00, "Deposit", "Jane", "Business Checking"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_to_csv_text_emits_header_and_rows():
    text = ingest_txns.xlsx_to_csv_text(_make_xlsx_bytes())
    lines = [l for l in text.splitlines() if l.strip()]
    assert lines[0].lower().startswith("date,")
    assert any("COFFEE SHOP" in l for l in lines)
    assert any("CLIENT DEPOSIT" in l for l in lines)


def test_xlsx_to_csv_text_normalizes_datetime_cells():
    text = ingest_txns.xlsx_to_csv_text(_make_xlsx_bytes(date_as_datetime=True))
    # a real datetime cell must serialize to an ISO date, not "2026-07-01 00:00:00"
    assert "2026-07-01" in text
    assert "00:00:00" not in text


def test_ingest_xlsx_parks_rows_in_pending_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    db_path = tenancy.resolve_db("acme")
    result = ingest_txns.ingest_xlsx(_make_xlsx_bytes(), db_path=db_path, source_file="bank.xlsx")
    assert result["parsed"] == 2
    assert result["inserted"] == 2
    con = sqlite3.connect(db_path)
    n = con.execute(
        "SELECT COUNT(*) FROM pending_inbox WHERE source=?", (ingest_txns.SOURCE_NAME,)
    ).fetchone()[0]
    con.close()
    assert n == 2


def test_ingest_xlsx_dedupes_on_reimport(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    db_path = tenancy.resolve_db("acme")
    data = _make_xlsx_bytes()
    ingest_txns.ingest_xlsx(data, db_path=db_path, source_file="bank.xlsx")
    second = ingest_txns.ingest_xlsx(data, db_path=db_path, source_file="bank.xlsx")
    assert second["inserted"] == 0
    assert second["skipped"] == 2


def test_import_xlsx_cli_base64_payload(tmp_path, monkeypatch):
    import base64
    import json
    import os
    import subprocess

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="acme", display_name="Acme Co")
    payload = json.dumps({
        "xlsx_b64": base64.b64encode(_make_xlsx_bytes()).decode(),
        "source_file": "bank.xlsx",
    })
    proc = subprocess.run(
        ["python3", "ingest_txns.py", "import-xlsx", "--client", "acme", "--payload", payload],
        cwd=scripts_dir, capture_output=True, text=True,
        env={**os.environ, "LISZA_HOME": str(tmp_path)},
    )
    out = json.loads(proc.stdout.strip())
    assert out.get("ok") is True
    assert out["inserted"] == 2
