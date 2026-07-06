import json
import sqlite3

import receipt_scanner


RECEIPT_TEXT = """Blue Bottle Coffee
2026-06-10
Coffee Beans 18.00
Oat Latte 6.50
Subtotal 24.50
Tax 1.96
Total: $26.46
Visa
"""


def test_scan_receipt_extracts_line_items():
    scanned = receipt_scanner.scan_receipt(RECEIPT_TEXT)
    assert scanned["vendor"] == "Blue Bottle Coffee"
    assert scanned["amount"] == 26.46
    assert scanned["category"] == "Meals"
    assert scanned["line_items"] == [
        {"description": "Coffee Beans", "amount": 18.0},
        {"description": "Oat Latte", "amount": 6.5},
    ]
    assert scanned["line_item_total"] == 24.5


def test_insert_into_ledger_payload_includes_line_items():
    con = sqlite3.connect(":memory:")
    expense = receipt_scanner.scan_receipt(RECEIPT_TEXT)
    row_id = receipt_scanner.insert_into_ledger(expense, con)
    row = con.execute("SELECT source, parsed_json, suggested_account FROM pending_inbox WHERE id=?", (row_id,)).fetchone()
    con.close()

    payload = json.loads(row[1])
    assert row[0] == "receipt_scanner"
    assert row[2] == "525"
    assert payload["line_items"][0] == {"description": "Coffee Beans", "amount": 18.0}
    assert payload["line_item_total"] == 24.5
