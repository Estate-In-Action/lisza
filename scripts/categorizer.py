#!/usr/bin/env python3
"""
categorizer.py — bank-rule management + learning + suggestion surface.

Extends the existing categorization engine in ingest_txns.py (payee_rules +
infer_categorization). This module adds the pieces that engine lacked:

  Phase 1 — rule CRUD:
    rules-list / rule-add / rule-delete (soft-delete, Principle #2)

  Phase 2 — learning + review:
    learn    — mine posted expense entries by payee, promote the modal
               account to a 'learned' payee_rule (priority 500, below the
               hand-written user rules at 100 so a human decision always wins).
    suggest  — run the live rules engine over posted expense entries and
               report what it *would* categorize each as, with a confidence
               chip and an agree/disagree flag for the review surface.

Nothing here posts ledger entries or moves money — it only manages the rule
table and reports suggestions for a human to approve.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict

import ingest_txns
import tenancy

MATCH_KINDS = ("contains", "exact", "startswith")

LEARNED_PRIORITY = 500
LEARN_MIN_COUNT = 3
LEARN_MIN_SHARE = 0.6


def _ensure_source_column(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(payee_rules)")}
    if "source" not in cols:
        con.execute(
            "ALTER TABLE payee_rules ADD COLUMN source TEXT NOT NULL DEFAULT 'user'")


def _book(slug: str) -> sqlite3.Connection:
    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    ingest_txns.ensure_categorization_schema(con)
    _ensure_source_column(con)
    return con


# --- Phase 1: rule CRUD ------------------------------------------------------

def list_rules(slug: str, *, include_inactive: bool = False) -> list[dict]:
    con = _book(slug)
    where = "" if include_inactive else "WHERE r.active = 1"
    rows = con.execute(
        f"""SELECT r.id, r.pattern, r.account_code, a.name AS account_name,
                   r.match_kind, r.tax_code, r.confidence, r.priority, r.active,
                   r.source, r.match_count, r.last_matched_at, r.created_at
            FROM payee_rules r
            LEFT JOIN accounts a ON a.code = r.account_code
            {where}
            ORDER BY r.priority ASC, r.id ASC"""
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def add_rule(slug: str, *, pattern: str, account_code: str,
             match_kind: str = "contains", priority: int = 100,
             confidence: float = 0.95, tax_code: str | None = None,
             source: str = "user") -> int:
    pattern = (pattern or "").strip()
    if not pattern:
        raise ValueError("pattern is required")
    if match_kind not in MATCH_KINDS:
        raise ValueError(f"invalid match_kind: {match_kind}")
    con = _book(slug)
    acct = con.execute(
        "SELECT code FROM accounts WHERE code = ? AND active = 1",
        (account_code,)).fetchone()
    if not acct:
        con.close()
        raise ValueError(f"unknown account code: {account_code}")
    cur = con.execute(
        """INSERT INTO payee_rules
             (pattern, account_code, match_kind, tax_code, confidence,
              priority, active, source)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
        (pattern, account_code, match_kind, tax_code, float(confidence),
         int(priority), source))
    rule_id = cur.lastrowid
    con.commit()
    con.close()
    return int(rule_id)


def delete_rule(slug: str, rule_id: int) -> None:
    # Soft-delete (LISZA Principle #2: we add, we don't subtract).
    con = _book(slug)
    con.execute("UPDATE payee_rules SET active = 0 WHERE id = ?", (int(rule_id),))
    con.commit()
    con.close()


# --- Phase 2: learning + suggestions ----------------------------------------

def learn(slug: str, *, min_count: int = LEARN_MIN_COUNT,
          min_share: float = LEARN_MIN_SHARE, apply: bool = True) -> list[dict]:
    """Mine posted expense entries: for each payee, promote the modal expense
    account to a 'learned' rule when it is frequent and dominant enough.

    Never overrides a hand-written (source != 'learned') rule for the same
    payee. Re-running refreshes existing learned rules in place (idempotent).
    """
    con = _book(slug)
    rows = con.execute(
        """SELECT LOWER(TRIM(e.payee)) AS payee, s.account AS account_code,
                  COUNT(*) AS n
           FROM entries e
           JOIN splits s ON s.entry_id = e.id
           JOIN accounts a ON a.code = s.account AND a.type = 'expense'
           WHERE e.status = 'posted'
             AND e.payee IS NOT NULL AND TRIM(e.payee) != ''
           GROUP BY LOWER(TRIM(e.payee)), s.account"""
    ).fetchall()

    by_payee: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in rows:
        by_payee[r["payee"]].append((r["account_code"], int(r["n"])))

    existing = con.execute(
        "SELECT id, LOWER(TRIM(pattern)) AS p, source "
        "FROM payee_rules WHERE active = 1").fetchall()
    user_patterns = {r["p"] for r in existing if r["source"] != "learned"}
    learned_by_pattern = {r["p"]: r["id"] for r in existing if r["source"] == "learned"}

    results: list[dict] = []
    for payee, accts in by_payee.items():
        total = sum(n for _, n in accts)
        if total < min_count:
            continue
        account_code, top = max(accts, key=lambda t: t[1])
        share = top / total
        if share < min_share:
            continue
        if payee in user_patterns:
            continue  # a hand-written rule already owns this payee

        name_row = con.execute(
            "SELECT name FROM accounts WHERE code = ?", (account_code,)).fetchone()
        rec = {
            "pattern": payee,
            "account_code": account_code,
            "account_name": name_row["name"] if name_row else None,
            "confidence": round(share, 2),
            "sample_size": total,
            "matched": top,
        }
        if apply:
            if payee in learned_by_pattern:
                rule_id = learned_by_pattern[payee]
                con.execute(
                    """UPDATE payee_rules
                       SET account_code = ?, confidence = ?, match_kind = 'contains',
                           priority = ?, active = 1
                       WHERE id = ?""",
                    (account_code, round(share, 2), LEARNED_PRIORITY, rule_id))
                rec["rule_id"] = rule_id
                rec["action"] = "updated"
            else:
                cur = con.execute(
                    """INSERT INTO payee_rules
                         (pattern, account_code, match_kind, confidence,
                          priority, active, source)
                       VALUES (?, ?, 'contains', ?, ?, 1, 'learned')""",
                    (payee, account_code, round(share, 2), LEARNED_PRIORITY))
                rec["rule_id"] = cur.lastrowid
                rec["action"] = "created"
        else:
            rec["action"] = "proposed"
        results.append(rec)

    if apply:
        con.commit()
    con.close()
    return sorted(results, key=lambda r: (-r["confidence"], r["pattern"]))


def suggest(slug: str, *, limit: int = 200) -> list[dict]:
    """Run the live rules engine over posted expense entries and report what
    it would categorize each as, vs. its current account — the review surface.
    """
    con = _book(slug)
    accounts = ingest_txns.load_accounts()
    payee_rules = ingest_txns.load_payee_rules(con)
    rows = con.execute(
        """SELECT e.id AS entry_id, e.entry_date, e.payee, e.description,
                  s.account AS current_code, a.name AS current_name
           FROM entries e
           JOIN splits s ON s.entry_id = e.id
           JOIN accounts a ON a.code = s.account AND a.type = 'expense'
           WHERE e.status = 'posted'
           ORDER BY e.entry_date DESC, e.id DESC
           LIMIT ?""",
        (int(limit),)).fetchall()

    out: list[dict] = []
    for r in rows:
        desc = r["description"] or r["payee"] or ""
        cat = ingest_txns.infer_categorization(accounts, payee_rules, desc, "", True)
        sug_code = cat.get("account_code")
        sug_name = None
        if sug_code:
            nm = con.execute(
                "SELECT name FROM accounts WHERE code = ?", (sug_code,)).fetchone()
            sug_name = nm["name"] if nm else None
        out.append({
            "entry_id": r["entry_id"],
            "entry_date": r["entry_date"],
            "payee": r["payee"],
            "description": r["description"],
            "current_code": r["current_code"],
            "current_name": r["current_name"],
            "suggested_code": sug_code,
            "suggested_name": sug_name,
            "source": cat.get("source"),
            "confidence": round(float(cat.get("confidence") or 0), 2),
            "agree": sug_code == r["current_code"],
        })
    con.close()
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rules-list")
    p.add_argument("--client", required=True)
    p.add_argument("--include-inactive", action="store_true")

    p = sub.add_parser("rule-add")
    p.add_argument("--client", required=True)
    p.add_argument("--pattern", required=True)
    p.add_argument("--account-code", required=True)
    p.add_argument("--match-kind", default="contains")
    p.add_argument("--priority", type=int, default=100)
    p.add_argument("--confidence", type=float, default=0.95)
    p.add_argument("--tax-code", default=None)

    p = sub.add_parser("rule-delete")
    p.add_argument("--client", required=True)
    p.add_argument("--rule-id", type=int, required=True)

    p = sub.add_parser("learn")
    p.add_argument("--client", required=True)
    p.add_argument("--min-count", type=int, default=LEARN_MIN_COUNT)
    p.add_argument("--min-share", type=float, default=LEARN_MIN_SHARE)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("suggest")
    p.add_argument("--client", required=True)
    p.add_argument("--limit", type=int, default=200)

    args = ap.parse_args(argv)
    try:
        if args.cmd == "rules-list":
            print(json.dumps({"ok": True, "rules": list_rules(
                args.client, include_inactive=args.include_inactive)}))
        elif args.cmd == "rule-add":
            rule_id = add_rule(
                args.client, pattern=args.pattern, account_code=args.account_code,
                match_kind=args.match_kind, priority=args.priority,
                confidence=args.confidence, tax_code=args.tax_code)
            print(json.dumps({"ok": True, "rule_id": rule_id}))
        elif args.cmd == "rule-delete":
            delete_rule(args.client, args.rule_id)
            print(json.dumps({"ok": True}))
        elif args.cmd == "learn":
            res = learn(args.client, min_count=args.min_count,
                        min_share=args.min_share, apply=not args.dry_run)
            print(json.dumps({"ok": True, "learned": res, "applied": not args.dry_run}))
        elif args.cmd == "suggest":
            print(json.dumps({"ok": True, "suggestions": suggest(
                args.client, limit=args.limit)}))
    except (ValueError, sqlite3.Error) as e:
        print(json.dumps({"error": str(e)}))
    except Exception as e:  # surface unexpected failures as JSON, never a traceback
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
