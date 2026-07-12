#!/usr/bin/env python3
"""Cost centers / accounting dimensions for LISZA client books.

Tag posted ledger lines with a *dimension* — a cost center, project,
department, or class — then report P&L grouped by that tag. Dimensions are a
pure overlay: the core `splits`/`entries` tables are never touched. A
`cost_centers` registry holds the taggable values; a `split_dimensions`
sidecar maps `split_id -> cost_center`. Untagged posted lines fall into an
"(unassigned)" bucket, so a book is never forced to tag everything at once.

Tagging is post-hoc (assign after posting) so no posting path changes and
every existing poster — payments, tax, credit notes, closing entries — keeps
working untouched. Deactivating a center is a soft-delete (add-don't-subtract):
the row and its historical tags are preserved.
"""
from __future__ import annotations

import argparse
import json
import sqlite3

import tenancy

_KINDS = ("cost_center", "project", "department", "class")
_CENT = 0.005

SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_centers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'cost_center'
        CHECK(kind IN ('cost_center','project','department','class')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS split_dimensions (
    split_id INTEGER PRIMARY KEY REFERENCES splits(id),
    cost_center TEXT NOT NULL REFERENCES cost_centers(code),
    assigned_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    ensure_schema(con)
    return con


# ---- registry -----------------------------------------------------------

def set_cost_center(slug: str, *, code: str, name: str,
                    kind: str = "cost_center") -> None:
    code = (code or "").strip()
    name = (name or "").strip()
    if not code:
        raise ValueError("cost-center code is required")
    if not name:
        raise ValueError("cost-center name is required")
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {_KINDS}")
    con = _book(slug)
    try:
        con.execute(
            """INSERT INTO cost_centers(code, name, kind, active)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(code) DO UPDATE SET
                   name=excluded.name, kind=excluded.kind, active=1""",
            (code, name, kind),
        )
        con.commit()
    finally:
        con.close()


def list_cost_centers(slug: str, *, kind: str | None = None,
                      active_only: bool = True) -> list[dict]:
    con = _book(slug)
    try:
        q = "SELECT code, name, kind, active FROM cost_centers"
        conds, params = [], []
        if active_only:
            conds.append("active=1")
        if kind:
            conds.append("kind=?")
            params.append(kind)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY kind, code"
        return [dict(r) for r in con.execute(q, params)]
    finally:
        con.close()


def deactivate_cost_center(slug: str, code: str) -> None:
    con = _book(slug)
    try:
        cur = con.execute("UPDATE cost_centers SET active=0 WHERE code=?", (code,))
        if cur.rowcount == 0:
            raise ValueError(f"cost center '{code}' not found")
        con.commit()
    finally:
        con.close()


# ---- tagging ------------------------------------------------------------

def _require_active_center(con: sqlite3.Connection, code: str) -> None:
    row = con.execute(
        "SELECT active FROM cost_centers WHERE code=?", (code,)
    ).fetchone()
    if not row:
        raise ValueError(f"cost center '{code}' not found")
    if not row["active"]:
        raise ValueError(f"cost center '{code}' is inactive")


def tag_split(slug: str, split_id: int, cost_center: str) -> None:
    """Assign a dimension to one posted split (idempotent reassign)."""
    con = _book(slug)
    try:
        if not con.execute("SELECT 1 FROM splits WHERE id=?", (split_id,)).fetchone():
            raise ValueError(f"split #{split_id} not found")
        _require_active_center(con, cost_center)
        con.execute(
            """INSERT INTO split_dimensions(split_id, cost_center)
               VALUES (?, ?)
               ON CONFLICT(split_id) DO UPDATE SET
                   cost_center=excluded.cost_center,
                   assigned_at=datetime('now')""",
            (split_id, cost_center),
        )
        con.commit()
    finally:
        con.close()


def untag_split(slug: str, split_id: int) -> None:
    con = _book(slug)
    try:
        con.execute("DELETE FROM split_dimensions WHERE split_id=?", (split_id,))
        con.commit()
    finally:
        con.close()


def tag_entry(slug: str, entry_id: int, cost_center: str) -> int:
    """Tag every split of an entry with one dimension; returns count tagged."""
    con = _book(slug)
    try:
        _require_active_center(con, cost_center)
        sids = [r["id"] for r in con.execute(
            "SELECT id FROM splits WHERE entry_id=?", (entry_id,))]
        if not sids:
            raise ValueError(f"entry #{entry_id} has no splits")
        for sid in sids:
            con.execute(
                """INSERT INTO split_dimensions(split_id, cost_center)
                   VALUES (?, ?)
                   ON CONFLICT(split_id) DO UPDATE SET
                       cost_center=excluded.cost_center,
                       assigned_at=datetime('now')""",
                (sid, cost_center),
            )
        con.commit()
        return len(sids)
    finally:
        con.close()


# ---- reporting ----------------------------------------------------------

def _pnl_rows(con: sqlite3.Connection, start: str, end: str):
    """Per (cost_center, account) posted P&L contribution over [start, end].

    cost_center is NULL for untagged lines. income contributes cr-dr,
    expense contributes dr-cr (both natural-positive on a profit).
    """
    return con.execute(
        """SELECT d.cost_center AS cc, a.code AS account, a.name AS acct_name,
                  a.type AS acct_type,
                  COALESCE(SUM(s.dr),0) AS dr, COALESCE(SUM(s.cr),0) AS cr
             FROM splits s
             JOIN entries e ON e.id = s.entry_id
             JOIN accounts a ON a.code = s.account
             LEFT JOIN split_dimensions d ON d.split_id = s.id
            WHERE e.status='posted'
              AND e.entry_date BETWEEN ? AND ?
              AND a.type IN ('income','expense')
            GROUP BY d.cost_center, a.code, a.name, a.type""",
        (start, end),
    ).fetchall()


def _income_expense(acct_type: str, dr: float, cr: float) -> tuple[float, float]:
    if acct_type == "income":
        return round(cr - dr, 2), 0.0
    return 0.0, round(dr - cr, 2)


def dimension_report(slug: str, *, start: str, end: str) -> dict:
    """P&L grouped by cost center, plus an (unassigned) bucket."""
    con = _book(slug)
    try:
        names = {r["code"]: (r["name"], r["kind"]) for r in con.execute(
            "SELECT code, name, kind FROM cost_centers")}
        buckets: dict = {}
        unassigned = {"income": 0.0, "expense": 0.0}
        for r in _pnl_rows(con, start, end):
            inc, exp = _income_expense(r["acct_type"], r["dr"], r["cr"])
            if r["cc"] is None:
                unassigned["income"] += inc
                unassigned["expense"] += exp
            else:
                b = buckets.setdefault(r["cc"], {"income": 0.0, "expense": 0.0})
                b["income"] += inc
                b["expense"] += exp
        centers = []
        for code, b in buckets.items():
            inc, exp = round(b["income"], 2), round(b["expense"], 2)
            if abs(inc) < _CENT and abs(exp) < _CENT:
                continue
            name, kind = names.get(code, (code, "cost_center"))
            centers.append({"code": code, "name": name, "kind": kind,
                            "income": inc, "expense": exp,
                            "net": round(inc - exp, 2)})
        centers.sort(key=lambda c: (c["kind"], c["code"]))
        return {
            "start": start, "end": end,
            "centers": centers,
            "unassigned": {"income": round(unassigned["income"], 2),
                           "expense": round(unassigned["expense"], 2),
                           "net": round(unassigned["income"] - unassigned["expense"], 2)},
        }
    finally:
        con.close()


def cost_center_pnl(slug: str, code: str, *, start: str, end: str) -> dict:
    """Single cost-center P&L with a per-account line breakdown."""
    con = _book(slug)
    try:
        row = con.execute(
            "SELECT name, kind FROM cost_centers WHERE code=?", (code,)).fetchone()
        name, kind = (row["name"], row["kind"]) if row else (code, "cost_center")
        lines, income, expense = [], 0.0, 0.0
        for r in _pnl_rows(con, start, end):
            if r["cc"] != code:
                continue
            inc, exp = _income_expense(r["acct_type"], r["dr"], r["cr"])
            income += inc
            expense += exp
            lines.append({"account": r["account"], "name": r["acct_name"],
                          "type": r["acct_type"],
                          "amount": round(inc if r["acct_type"] == "income" else exp, 2)})
        lines.sort(key=lambda x: x["account"])
        return {"code": code, "name": name, "kind": kind,
                "start": start, "end": end,
                "income": round(income, 2), "expense": round(expense, 2),
                "net": round(income - expense, 2), "lines": lines}
    finally:
        con.close()


# ---- CLI ----------------------------------------------------------------

def _cli_cc_json(argv) -> None:
    """`cc-json --client <slug> --payload <json>` → JSON result.

    payload = {"action": add|list|deactivate|tag|untag|tag_entry|report|pnl, ...}
    """
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="cost_centers.py cc-json")
    ap.add_argument("--client", required=True)
    ap.add_argument("--payload")
    a = ap.parse_args(argv)
    try:
        raw = a.payload if a.payload is not None else __import__("sys").stdin.read()
        p = json.loads(raw)
        if not Path(tenancy.resolve_db(a.client)).exists():
            print(json.dumps({"error": f"client '{a.client}' not found"}))
            return
        action = p.get("action") or "list"
        if action == "add":
            set_cost_center(a.client, code=p["code"], name=p["name"],
                            kind=p.get("kind", "cost_center"))
            print(json.dumps({"ok": True}))
        elif action == "list":
            print(json.dumps({"ok": True, "cost_centers": list_cost_centers(
                a.client, kind=p.get("kind"),
                active_only=p.get("active_only", True))}))
        elif action == "deactivate":
            deactivate_cost_center(a.client, p["code"])
            print(json.dumps({"ok": True}))
        elif action == "tag":
            tag_split(a.client, int(p["split_id"]), p["cost_center"])
            print(json.dumps({"ok": True}))
        elif action == "untag":
            untag_split(a.client, int(p["split_id"]))
            print(json.dumps({"ok": True}))
        elif action == "tag_entry":
            n = tag_entry(a.client, int(p["entry_id"]), p["cost_center"])
            print(json.dumps({"ok": True, "tagged": n}))
        elif action == "report":
            print(json.dumps({"ok": True, **dimension_report(
                a.client, start=p["start"], end=p["end"])}))
        elif action == "pnl":
            print(json.dumps({"ok": True, **cost_center_pnl(
                a.client, p["code"], start=p["start"], end=p["end"])}))
        else:
            print(json.dumps({"error": f"unknown action: {action}"}))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))


def main(argv=None) -> None:
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "cc-json":
        _cli_cc_json(argv[1:])
        return
    print("usage: cost_centers.py cc-json --client <slug> --payload <json>")


if __name__ == "__main__":
    main()
