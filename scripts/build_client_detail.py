#!/usr/bin/env python3
"""Project each client ledger.db into public/clients/<slug>.json (read-only)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import tenancy

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
TOP_N = 8


def aging_buckets(items, as_of: str) -> dict:
    """Bucket AR/AP items by days-past-due vs as_of. items: dicts with
    party, due_date (ISO), amount."""
    ref = date.fromisoformat(as_of)
    buckets = {"current": 0.0, "d1_30": 0.0, "d31_60": 0.0,
               "d61_90": 0.0, "d90_plus": 0.0}
    total = 0.0
    count = 0
    enriched = []
    for it in items:
        amt = float(it["amount"])
        dpd = (ref - date.fromisoformat(it["due_date"])).days
        if dpd <= 0:
            buckets["current"] += amt
        elif dpd <= 30:
            buckets["d1_30"] += amt
        elif dpd <= 60:
            buckets["d31_60"] += amt
        elif dpd <= 90:
            buckets["d61_90"] += amt
        else:
            buckets["d90_plus"] += amt
        total += amt
        count += 1
        enriched.append({"party": it["party"], "due_date": it["due_date"],
                         "amount": round(amt, 2), "days_past_due": dpd})
    buckets = {k: round(v, 2) for k, v in buckets.items()}
    top = sorted(enriched, key=lambda r: r["amount"], reverse=True)[:TOP_N]
    return {"open_total": round(total, 2), "open_count": count,
            "aging": buckets, "top_open": top}
