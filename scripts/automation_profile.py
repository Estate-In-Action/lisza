#!/usr/bin/env python3
"""Per-client automation profile CLI and due-job planner.

The planner is advisory only: it reports what is due or upcoming, and never
posts entries, sends reports, files taxes, or changes client books.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

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
        "approval_required": True,
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
    elif not reports:
        jobs.append({
            "key": "profile_config",
            "label": "Configure automation profile",
            "due_date": ref.isoformat(),
            "status": "blocked",
            "source": "missing profile reports",
            "approval_required": False,
        })

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


def workflow_queue(*, as_of: date | None = None, include_scheduled: bool = False) -> dict:
    plan = plan_all(as_of=as_of, include_scheduled=include_scheduled)
    queue = []
    for client in plan["clients"]:
        for job in client["jobs"]:
            queue.append({
                "client_slug": client["slug"],
                "client_name": client.get("display_name"),
                **job,
            })
    queue.sort(key=lambda j: (j["status"] != "due_now", j["due_date"], j["client_slug"], j["key"]))
    return {
        "generated_at": date.today().isoformat(),
        "as_of": plan["as_of"],
        "mode": "dry_run",
        "execution": "disabled",
        "queue": queue,
        "summary": {
            "due_now": sum(1 for j in queue if j["status"] == "due_now"),
            "upcoming": sum(1 for j in queue if j["status"] == "upcoming"),
            "scheduled": sum(1 for j in queue if j["status"] == "scheduled"),
            "blocked": sum(1 for j in queue if j["status"] == "blocked"),
            "approval_required": sum(1 for j in queue if j.get("approval_required")),
        },
    }


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


def set_profile_from_json(slug: str, payload: dict) -> dict:
    profile = tenancy.get_automation_profile(slug)
    allowed = {
        "reports", "filing_cadence", "sales_tax_jurisdictions",
        "active_window", "payroll_schedule", "delivery",
    }
    for key, value in payload.items():
        if key in allowed:
            profile[key] = value
    profile["reports"] = {
        "weekly_digest": bool(profile.get("reports", {}).get("weekly_digest", True)),
        "monthly_close": bool(profile.get("reports", {}).get("monthly_close", True)),
        "quarterly_packet": bool(profile.get("reports", {}).get("quarterly_packet", True)),
    }
    if not isinstance(profile.get("sales_tax_jurisdictions"), list):
        profile["sales_tax_jurisdictions"] = []
    tenancy.set_automation_profile(slug, profile)
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

    setjson = sub.add_parser("set-json", help="update one profile from JSON")
    setjson.add_argument("slug")
    setjson.add_argument("--payload", required=True)

    planp = sub.add_parser("plan", help="print advisory due jobs")
    planp.add_argument("--client")
    planp.add_argument("--as-of")
    planp.add_argument("--include-scheduled", action="store_true")

    queuep = sub.add_parser("queue", help="print or write the dry-run workflow queue")
    queuep.add_argument("--as-of")
    queuep.add_argument("--include-scheduled", action="store_true")
    queuep.add_argument("--write", help="optional output JSON path")

    args = ap.parse_args()
    if args.cmd == "get":
        print(json.dumps(tenancy.get_automation_profile(args.slug), indent=2))
    elif args.cmd == "set":
        print(json.dumps(_set(args), indent=2))
    elif args.cmd == "set-json":
        payload = json.loads(args.payload)
        print(json.dumps(set_profile_from_json(args.slug, payload), indent=2))
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
    elif args.cmd == "queue":
        ref = _parse_date(args.as_of)
        data = workflow_queue(as_of=ref, include_scheduled=args.include_scheduled)
        if args.write:
            out = Path(args.write)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, indent=2))
        print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
