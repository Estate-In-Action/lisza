#!/usr/bin/env python3
"""Project the lisza.db registry cache into public/dashboard.json.

Pure registry read — opens no client books. Run after tenancy.refresh_all()
so client_summary is current.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import tenancy

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
DEFAULT_CARD_FIELDS = ["cash", "open_ar", "open_ap", "last_entry"]


def _prefs(con: sqlite3.Connection) -> dict:
    row = con.execute(
        "SELECT layout, card_fields_json FROM bookkeeper_prefs LIMIT 1").fetchone()
    layout = row["layout"] if row and row["layout"] else "tile"
    if row and row["card_fields_json"]:
        card_fields = json.loads(row["card_fields_json"])
    else:
        card_fields = list(DEFAULT_CARD_FIELDS)
    return {"layout": layout, "card_fields": card_fields}


def build_dashboard() -> dict:
    tenancy.registry_db()
    con = sqlite3.connect(tenancy.registry_path())
    con.row_factory = sqlite3.Row
    prefs = _prefs(con)
    rows = con.execute(
        """SELECT c.slug, c.display_name, c.entity_type, c.status, c.next_filing_due,
                  s.cash, s.open_ar, s.open_ap, s.ar_count, s.ap_count,
                  s.entity_count, s.last_entry_date
           FROM clients c
           LEFT JOIN client_summary s ON s.client_id = c.client_id
           WHERE c.status = 'active'
           ORDER BY c.display_name""").fetchall()
    con.close()

    clients = []
    for r in rows:
        ec = r["entity_count"] or 1
        clients.append({
            "slug": r["slug"],
            "display_name": r["display_name"],
            "entity_type": r["entity_type"],
            "status": r["status"],
            "cash": r["cash"],
            "open_ar": r["open_ar"],
            "open_ap": r["open_ap"],
            "ar_count": r["ar_count"],
            "ap_count": r["ap_count"],
            "entity_count": ec,
            "consolidates": ec > 1,
            "last_entry": r["last_entry_date"],
            "next_filing_due": r["next_filing_due"],
        })

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prefs": prefs,
        "clients": clients,
    }


def write_dashboard(path: str | Path | None = None) -> Path:
    out = Path(path) if path else PUBLIC_DIR / "dashboard.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_dashboard(), indent=2))
    return out


if __name__ == "__main__":
    p = write_dashboard()
    print(f"wrote {p}")
