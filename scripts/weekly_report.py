#!/usr/bin/env python3
"""
Weekly finance report — runs Sundays via Zo agent.

Aggregates the last 7 days of POSTED entries from ledger.db, plus pending
queue health, plus rolling month-to-date budget consumption.  Renders both
a plain-text body (for Telegram) and a Markdown body (for email), then
delivers to the LISZA Books Telegram group + emails the owner.

Usage:
    weekly_report.py                   # send to telegram + email
    weekly_report.py --print           # print to stdout, no delivery
    weekly_report.py --no-telegram
    weekly_report.py --no-email
    weekly_report.py --weeks 4         # cover last N weeks
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/home/workspace/LISZA")
DB = ROOT / "ledger.db"
CONFIG = ROOT / "config.json"
BUDGET = ROOT / "budget.json"
LOG = Path("/dev/shm/fin_weekly_report.log")

BOT_TOKEN = os.environ.get("FIN_T_B4ME") or os.environ.get("fin_t_b4me")


def log(msg: str) -> None:
    line = f"{datetime.utcnow().isoformat()}Z  {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_cfg() -> dict:
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {}


def load_budget() -> dict:
    """budget.json optional. Shape: {"month": {"515":1200, "525":800}, ...}"""
    if BUDGET.exists():
        return json.loads(BUDGET.read_text())
    return {}


def fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def query_period(conn: sqlite3.Connection, start: str, end: str) -> dict:
    """Return income/expense totals + per-account expense rollup for [start, end]."""
    rows = conn.execute(
        """
        SELECT a.code, a.name, a.type, a.grp,
               COALESCE(SUM(s.dr), 0)  AS dr,
               COALESCE(SUM(s.cr), 0)  AS cr
          FROM splits s
          JOIN entries  e ON e.id = s.entry_id
          JOIN accounts a ON a.code = s.account
         WHERE e.status = 'posted'
           AND e.entry_date >= ?
           AND e.entry_date <  ?
         GROUP BY a.code
        """,
        (start, end),
    ).fetchall()

    income_total = 0.0
    expense_total = 0.0
    by_expense: list[tuple] = []
    by_income: list[tuple] = []
    for code, name, typ, grp, dr, cr in rows:
        if typ == "expense":
            net = (dr or 0) - (cr or 0)
            expense_total += net
            if net != 0:
                by_expense.append((code, name, grp, net))
        elif typ == "income":
            net = (cr or 0) - (dr or 0)
            income_total += net
            if net != 0:
                by_income.append((code, name, grp, net))
    by_expense.sort(key=lambda r: -r[3])
    by_income.sort(key=lambda r: -r[3])
    return {
        "start": start,
        "end": end,
        "income_total": income_total,
        "expense_total": expense_total,
        "net": income_total - expense_total,
        "by_expense": by_expense,
        "by_income": by_income,
    }


def query_balances(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """SELECT code, name, type, balance FROM v_account_balances
            WHERE balance != 0 ORDER BY type, code"""
    ).fetchall()
    out = {"asset": [], "liability": [], "equity": [], "income": [], "expense": []}
    for code, name, typ, bal in rows:
        if typ in out:
            out[typ].append((code, name, bal))
    return out


def query_inbox_health(conn: sqlite3.Connection) -> dict:
    counts = {}
    for status, n in conn.execute(
        "SELECT status, COUNT(*) FROM pending_inbox GROUP BY status"
    ):
        counts[status] = n
    last = conn.execute(
        "SELECT received_at, source FROM pending_inbox ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {"counts": counts, "last": last}


def build_text_report(week: dict, mtd: dict, inbox: dict, budget: dict) -> str:
    L = []
    L.append(f"Weekly LISZA Report  {week['start']} -> {week['end']}")
    L.append("=" * 56)
    L.append(f"Income  : {fmt_money(week['income_total'])}")
    L.append(f"Expense : {fmt_money(week['expense_total'])}")
    L.append(f"Net     : {fmt_money(week['net'])}")
    L.append("")
    L.append("Top expenses (this week):")
    for code, name, grp, amt in week["by_expense"][:5]:
        L.append(f"  {code}  {name:<32} {fmt_money(amt):>12}")
    if not week["by_expense"]:
        L.append("  (no posted expenses this week)")
    L.append("")
    L.append(f"Month-to-date  ({mtd['start']} -> {mtd['end']}):")
    L.append(f"  Income  : {fmt_money(mtd['income_total'])}")
    L.append(f"  Expense : {fmt_money(mtd['expense_total'])}")
    L.append(f"  Net     : {fmt_money(mtd['net'])}")
    if budget:
        bm = budget.get("month", {})
        if bm:
            L.append("")
            L.append("Budget (MTD):")
            spent_by_code = {c: amt for c, _, _, amt in mtd["by_expense"]}
            for code, cap in sorted(bm.items()):
                spent = spent_by_code.get(code, 0.0)
                pct = (spent / cap * 100.0) if cap else 0.0
                bar = "#" * min(20, int(pct / 5))
                L.append(f"  {code}  {bar:<20}  {fmt_money(spent)} / {fmt_money(cap)}  ({pct:0.0f}%)")
    L.append("")
    L.append("Pending inbox:")
    if inbox["counts"]:
        for k, v in sorted(inbox["counts"].items()):
            L.append(f"  {k:<10} {v}")
    else:
        L.append("  (empty)")
    if inbox["last"]:
        L.append(f"  last received: {inbox['last'][0]}  via {inbox['last'][1]}")
    L.append("")
    L.append("Reply with /status anytime; send a receipt photo to queue it.")
    return "\n".join(L)


def build_md_report(week: dict, mtd: dict, inbox: dict, budget: dict, balances: dict) -> str:
    L = []
    L.append(f"# Weekly LISZA Report")
    L.append(f"_{week['start']} → {week['end']}_\n")
    L.append("## Week summary")
    L.append(f"- **Income:** {fmt_money(week['income_total'])}")
    L.append(f"- **Expense:** {fmt_money(week['expense_total'])}")
    L.append(f"- **Net:** {fmt_money(week['net'])}\n")
    L.append("## Top expenses (week)")
    L.append("| Code | Name | Group | Amount |")
    L.append("|------|------|-------|-------:|")
    for code, name, grp, amt in week["by_expense"][:10]:
        L.append(f"| {code} | {name} | {grp or ''} | {fmt_money(amt)} |")
    if not week["by_expense"]:
        L.append("| — | (no posted expenses this week) | | |")
    L.append("")
    L.append(f"## Month-to-date ({mtd['start']} → {mtd['end']})")
    L.append(f"- Income: {fmt_money(mtd['income_total'])}")
    L.append(f"- Expense: {fmt_money(mtd['expense_total'])}")
    L.append(f"- Net: {fmt_money(mtd['net'])}\n")
    if budget and budget.get("month"):
        L.append("## Budget consumption (MTD)")
        L.append("| Code | Cap | Spent | % |")
        L.append("|------|----:|------:|--:|")
        spent_by_code = {c: amt for c, _, _, amt in mtd["by_expense"]}
        for code, cap in sorted(budget["month"].items()):
            spent = spent_by_code.get(code, 0.0)
            pct = (spent / cap * 100.0) if cap else 0.0
            L.append(f"| {code} | {fmt_money(cap)} | {fmt_money(spent)} | {pct:0.0f}% |")
        L.append("")
    L.append("## Account balances")
    for typ in ("asset", "liability", "equity"):
        rows = balances.get(typ, [])
        if not rows:
            continue
        L.append(f"### {typ.title()}s")
        L.append("| Code | Name | Balance |")
        L.append("|------|------|--------:|")
        for code, name, bal in rows:
            L.append(f"| {code} | {name} | {fmt_money(bal)} |")
        L.append("")
    L.append("## Pending inbox")
    if inbox["counts"]:
        for k, v in sorted(inbox["counts"].items()):
            L.append(f"- {k}: {v}")
    else:
        L.append("- (empty)")
    if inbox["last"]:
        L.append(f"- last received: {inbox['last'][0]} via {inbox['last'][1]}")
    return "\n".join(L)


def send_telegram(chat_id: int, text: str) -> bool:
    if not BOT_TOKEN:
        log("FIN_T_B4ME not set, skipping telegram")
        return False
    if len(text) > 4000:
        text = text[:3990] + "\n…(truncated)"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=data
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        return True
    except Exception as e:
        log(f"telegram send failed: {e}")
        return False


def send_email_via_zo(subject: str, md_body: str) -> bool:
    """Use the zo MCP CLI bridge if available; else fall back to a no-op
    that just writes the report to disk."""
    out_dir = ROOT / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M")
    fp = out_dir / f"weekly_{stamp}.md"
    fp.write_text(md_body)
    log(f"report saved: {fp}")
    # Email delivery is performed by the parent agent (Zo) when the
    # weekly_report skill / agent invokes this script — see scripts/README.
    return True


def date_range(weeks_back: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=7 * weeks_back)
    return start.isoformat(), end.isoformat()


def month_range() -> tuple[str, str]:
    end = datetime.now(timezone.utc).date()
    start = end.replace(day=1)
    return start.isoformat(), end.isoformat()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=1)
    ap.add_argument("--print", dest="print_only", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--no-email", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    budget = load_budget()
    conn = sqlite3.connect(str(DB))
    try:
        ws, we = date_range(args.weeks)
        ms, me = month_range()
        week = query_period(conn, ws, we)
        mtd = query_period(conn, ms, me)
        inbox = query_inbox_health(conn)
        balances = query_balances(conn)
    finally:
        conn.close()

    text = build_text_report(week, mtd, inbox, budget)
    md = build_md_report(week, mtd, inbox, budget, balances)

    if args.print_only:
        print(text)
        print("\n--- markdown ---\n")
        print(md)
        return

    if not args.no_telegram and cfg.get("telegram_chat_id"):
        send_telegram(cfg["telegram_chat_id"], text)
    elif not args.no_telegram:
        log("no telegram_chat_id in config; skipping telegram delivery")

    if not args.no_email:
        send_email_via_zo(
            subject=f"Weekly LISZA Report — {week['end']}",
            md_body=md,
        )

    log("weekly report complete")


if __name__ == "__main__":
    main()
