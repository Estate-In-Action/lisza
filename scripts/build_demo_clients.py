#!/usr/bin/env python3
"""Build the three demo clients end-to-end into the real LISZA_HOME:
register (if absent), seed Harborside + J.B. Design from profiles, then
refresh all summaries. Guitar Works comes from migrate_to_guitar_works.py
(its book is the migrated legacy data), so it is only refreshed here.
"""
from __future__ import annotations

import tenancy
import client_profiles
import seed_client

PLAN = [
    ("harborside-group", "Harborside Restaurant Group", "llc",
     client_profiles.HARBORSIDE_GROUP),
    ("jb-design", "J.B. Design", "sole_prop", client_profiles.JB_DESIGN),
]


def _registered(slug: str) -> bool:
    return any(r.slug == slug for r in tenancy.list_clients())


def main() -> int:
    for slug, name, etype, profile in PLAN:
        if not _registered(slug):
            tenancy.register_client(slug=slug, display_name=name, entity_type=etype)
        seed_client.seed(profile, slug=slug)
    n = tenancy.refresh_all()
    print(f"built {len(PLAN)} seeded clients; refreshed {n} summaries")
    for r in tenancy.list_clients():
        s = tenancy.refresh_summary(r.slug)
        print(f"  {r.slug:18} cash={s['cash']:>12,.2f} AR={s['open_ar']:>10,.2f} AP={s['open_ap']:>10,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
