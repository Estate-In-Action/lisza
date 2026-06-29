#!/usr/bin/env python3
"""Entity-aware balances.

Per-entity = filter entity_id. Consolidated = all entities, excluding
inter-company entries (is_intercompany=1) so cross-location flows don't
double-count.
"""
from __future__ import annotations

import sqlite3


def account_balance(con: sqlite3.Connection, account: str, *,
                    entity_id: int | None = None, consolidated: bool = False,
                    as_of: str | None = None) -> float:
    sql = [
        "SELECT ROUND(COALESCE(SUM(s.dr-s.cr),0),2)",
        "FROM splits s JOIN entries e ON e.id=s.entry_id",
        "WHERE e.status='posted' AND s.account=?",
    ]
    params: list = [account]
    if entity_id is not None:
        sql.append("AND e.entity_id=?")
        params.append(entity_id)
    if consolidated:
        sql.append("AND e.is_intercompany=0")
    if as_of:
        sql.append("AND e.entry_date<=?")
        params.append(as_of)
    return con.execute(" ".join(sql), params).fetchone()[0]
