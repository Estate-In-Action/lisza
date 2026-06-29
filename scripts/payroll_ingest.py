#!/usr/bin/env python3
"""Ingest a finished payroll register: book externally-calculated figures.

The compute path (payroll_run) runs gross-to-net itself. Ingestion is the other
door: a register already calculated by a payroll provider (Gusto/ADP) or a prior
bookkeeper arrives as per-employee figures, and we book it verbatim — the same
payroll_runs/payroll_lines rows and the same balanced GL entry as a computed run,
but we never recompute. Garbage-in is the risk, so each line is checked for
internal consistency (net == gross − employee deductions) before anything writes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta

import book_schema
import tenancy
from payroll_run import _SUM_FIELDS, _post_entry

_LINE_FIELDS = _SUM_FIELDS  # the 11 money fields on a payroll_lines row
_REQUIRED = ("gross", "net")
_EE_DEDUCTIONS = ("fed_wh", "ss_ee", "medi_ee", "addl_medi", "state_wh")
_CENT = 0.01


def _as_date(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


def _resolve_employee(con: sqlite3.Connection, entity_id: int, line: dict) -> int:
    eid = line.get("employee_id")
    if eid is not None:
        eid = int(eid)
        if not con.execute("SELECT 1 FROM employees WHERE id=? AND entity_id=?",
                           (eid, entity_id)).fetchone():
            raise ValueError(
                f"unknown employee_id {eid} for entity {entity_id}")
        return eid
    name = (line.get("name") or "").strip()
    if not name:
        raise ValueError("each register line needs employee_id or name")
    rows = con.execute("SELECT id FROM employees WHERE entity_id=? AND name=?",
                       (entity_id, name)).fetchall()
    if not rows:
        raise ValueError(f"unknown employee: {name}")
    if len(rows) > 1:
        raise ValueError(f"ambiguous employee name: {name}")
    return rows[0][0]


def _clean_line(line: dict) -> dict:
    for f in _REQUIRED:
        if line.get(f) is None:
            raise ValueError(f"register line missing required field: {f}")
    out = {f: round(float(line.get(f, 0.0) or 0.0), 2) for f in _LINE_FIELDS}
    for f in _LINE_FIELDS:
        if out[f] < 0:
            raise ValueError(f"negative {f} in register line")
    expected_net = round(out["gross"] - sum(out[f] for f in _EE_DEDUCTIONS), 2)
    if abs(expected_net - out["net"]) > _CENT:
        raise ValueError(
            f"net {out['net']} != gross - deductions {expected_net}")
    return out


def import_register(slug: str, entity_id: int, period_end, lines: list, *,
                    pay_date=None, period_start=None) -> int:
    """Validate a finished register and book it as one pay run. Returns run_id."""
    if not lines:
        raise ValueError("register has no lines")
    db = tenancy.resolve_db(slug)
    if not db.exists():
        raise ValueError(f"unknown client: {slug}")
    pe = _as_date(period_end)
    pd_ = _as_date(pay_date) if pay_date else pe
    ps = _as_date(period_start) if period_start else (pe - timedelta(days=13))

    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        prepared = []
        tot = defaultdict(float)
        for line in lines:
            emp_id = _resolve_employee(con, entity_id, line)
            vals = _clean_line(line)
            prepared.append((emp_id, vals))
            for f in _LINE_FIELDS:
                tot[f] += vals[f]
        tot = {k: round(v, 2) for k, v in tot.items()}

        con.execute(
            "INSERT INTO payroll_runs(entity_id,period_start,period_end,pay_date) "
            "VALUES(?,?,?,?)",
            (entity_id, ps.isoformat(), pe.isoformat(), pd_.isoformat()))
        run_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

        eid = _post_entry(con, entity_id, pd_.isoformat(), tot)
        for emp_id, v in prepared:
            con.execute(
                "INSERT INTO payroll_lines(run_id,employee_id,entry_id,gross,"
                "fed_wh,ss_ee,ss_er,medi_ee,medi_er,addl_medi,state_wh,futa,"
                "suta,net) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, emp_id, eid, v["gross"], v["fed_wh"], v["ss_ee"],
                 v["ss_er"], v["medi_ee"], v["medi_er"], v["addl_medi"],
                 v["state_wh"], v["futa"], v["suta"], v["net"]))
        con.commit()
        return run_id
    finally:
        con.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    im = sub.add_parser("import")
    im.add_argument("slug")
    im.add_argument("--period-end", required=True)
    im.add_argument("--pay-date")
    im.add_argument("--period-start")
    im.add_argument("--entity", type=int)
    im.add_argument("--register", required=True,
                    help="JSON array of register lines")
    args = p.parse_args()
    try:
        return _dispatch(args)
    except ValueError as e:  # JSONDecodeError is a ValueError subclass
        print(json.dumps({"error": str(e)}))
        return 0


def _dispatch(args) -> int:
    if args.cmd == "import":
        lines = json.loads(args.register)
        if not isinstance(lines, list):
            raise ValueError("register must be a JSON array")
        entity_id = args.entity
        if entity_id is None:
            db = tenancy.resolve_db(args.slug)
            if not db.exists():
                raise ValueError(f"unknown client: {args.slug}")
            con = sqlite3.connect(db)
            entity_id = book_schema.default_entity_id(con)
            con.close()
        run_id = import_register(
            args.slug, entity_id, args.period_end, lines,
            pay_date=args.pay_date, period_start=args.period_start)
        print(json.dumps({"ok": True, "run_id": run_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
