#!/usr/bin/env python3
"""Per-client automation profile CLI and due-job planner.

The planner is advisory only: it reports what is due or upcoming, and never
posts entries, sends reports, files taxes, or changes client books.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

import tenancy


def _parse_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _last_day_of_month(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


def _job(key: str, label: str, due: date, as_of: date, source: str) -> dict:
    if due <= as_of:
        status = "due_now"
    elif due <= as_of + timedelta(days=14):
        status = "upcoming"
    else:
        status = "scheduled"
    return {
        "key": key,
        "label": label,
        "due_date": due.isoformat(),
        "status": status,
        "source": source,
    }


def _client_context(slug: str) -> dict:
    tenancy.registry_db()
    import sqlite3

    con = sqlite3.connect(tenancy.registry_path())
    con.row_factory = sqlite3.Row
    row = con.execute(
        """SELECT c.slug, c.next_filing_due, s.last_entry_date
           FROM clients c
           LEFT JOIN client_summary s ON s.client_id=c.client_id
           WHERE c.slug=?""",
        (slug,),
    ).fetchone()
    con.close()
    if not row:
        raise ValueError(f"unknown client: {slug}")
    return dict(row)


def plan_due_jobs(slug: str, *, as_of: date | None = None,
                  include_scheduled: bool = False) -> list[dict]:
    ref = as_of or date.today()
    profile = tenancy.get_automation_profile(slug)
    ctx = _client_context(slug)
    reports = profile.get("reports", {})
    jobs: list[dict] = []

    last_entry = date.fromisoformat(ctx["last_entry_date"]) if ctx.get("last_entry_date") else None
    if reports.get("weekly_digest", True):
        jobs.append(_job("weekly_digest", "Weekly digest", ref, ref, "profile.reports"))

    if reports.get("monthly_close", True) and last_entry:
        month_end = _last_day_of_month(last_entry)
        jobs.append(_job("monthly_close", "Monthly close review",
                         month_end + timedelta(days=5), ref, "last_entry_date"))

    if reports.get("quarterly_packet", True) and ctx.get("next_filing_due"):
        next_due = date.fromisoformat(ctx["next_filing_due"])
        jobs.append(_job("quarterly_packet", "Quarterly packet",
                         next_due - timedelta(days=14), ref, "next_filing_due"))
        jobs.append(_job("tax_packet", "Tax packet prep",
                         next_due - timedelta(days=7), ref, "next_filing_due"))

    if profile.get("payroll_schedule") not in (None, "", "none"):
        jobs.append(_job("payroll_review", "Payroll liability review",
                         ref, ref, "profile.payroll_schedule"))

    if profile.get("sales_tax_jurisdictions"):
        jobs.append(_job("sales_tax_review", "Sales-tax liability review",
                         ref, ref, "profile.sales_tax_jurisdictions"))

    if not include_scheduled:
        jobs = [j for j in jobs if j["status"] in ("due_now", "upcoming")]
    return sorted(jobs, key=lambda j: (j["due_date"], j["key"]))


def plan_all(*, as_of: date | None = None, include_scheduled: bool = False) -> dict:
    ref = as_of or date.today()
    clients = []
    for row in tenancy.list_clients():
        clients.append({
            "slug": row.slug,
            "display_name": row.display_name,
            "jobs": plan_due_jobs(row.slug, as_of=ref, include_scheduled=include_scheduled),
        })
    return {"as_of": ref.isoformat(), "clients": clients}


def _bool(value: str) -> bool:
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def _set(args: argparse.Namespace) -> dict:
    profile = tenancy.get_automation_profile(args.slug)
    if args.filing_cadence:
        profile["filing_cadence"] = args.filing_cadence
    if args.active_window:
        profile["active_window"] = args.active_window
    if args.payroll_schedule:
        profile["payroll_schedule"] = args.payroll_schedule
    if args.delivery:
        profile["delivery"] = args.delivery
    if args.sales_tax_jurisdictions is not None:
        profile["sales_tax_jurisdictions"] = [
            x.strip() for x in args.sales_tax_jurisdictions.split(",") if x.strip()
        ]
    for key in ("weekly_digest", "monthly_close", "quarterly_packet"):
        value = getattr(args, key)
        if value is not None:
            profile.setdefault("reports", {})[key] = value
    tenancy.set_automation_profile(args.slug, profile)
    return profile


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    getp = sub.add_parser("get", help="print one client's automation profile")
    getp.add_argument("slug")

    setp = sub.add_parser("set", help="update one client's automation profile")
    setp.add_argument("slug")
    setp.add_argument("--filing-cadence", choices=["monthly", "quarterly", "annual"])
    setp.add_argument("--active-window")
    setp.add_argument("--payroll-schedule")
    setp.add_argument("--delivery", choices=["dashboard", "email", "telegram"])
    setp.add_argument("--sales-tax-jurisdictions")
    setp.add_argument("--weekly-digest", type=_bool)
    setp.add_argument("--monthly-close", type=_bool)
    setp.add_argument("--quarterly-packet", type=_bool)

    planp = sub.add_parser("plan", help="print advisory due jobs")
    planp.add_argument("--client")
    planp.add_argument("--as-of")
    planp.add_argument("--include-scheduled", action="store_true")

    args = ap.parse_args()
    if args.cmd == "get":
        print(json.dumps(tenancy.get_automation_profile(args.slug), indent=2))
    elif args.cmd == "set":
        print(json.dumps(_set(args), indent=2))
    elif args.cmd == "plan":
        ref = _parse_date(args.as_of)
        if args.client:
            data = {"as_of": ref.isoformat(), "clients": [{
                "slug": args.client,
                "jobs": plan_due_jobs(args.client, as_of=ref,
                                      include_scheduled=args.include_scheduled),
            }]}
        else:
            data = plan_all(as_of=ref, include_scheduled=args.include_scheduled)
        print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
