#!/usr/bin/env python3
"""Per-client synthetic-data profiles consumed by seed_client.seed().

Each profile tunes the universe (parties), the per-entity revenue scale,
the seasonal curve, and a small set of business-type flags. ZERO real data.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClientProfile:
    slug: str
    entities: tuple[str, ...]            # ("Main",) for single-entity
    customers: tuple[str, ...]
    vendors: tuple[str, ...]
    base_monthly_rev: float              # per entity, year-1 average
    yoy: float
    season: dict                          # month -> multiplier
    revenue_accounts: tuple[str, ...]
    cogs_account: str                     # primary variable-cost account
    cogs_frac: float                      # COGS as fraction of revenue
    has_payroll: bool
    owner_draw_monthly: float
    intercompany_sweeps: bool = False    # parent cash sweeps between entities


_FLAT_SEASON = {m: 1.0 for m in range(1, 13)}
_AGENCY_SEASON = {1: 0.90, 2: 0.93, 3: 1.02, 4: 1.06, 5: 1.05, 6: 0.96,
                  7: 0.84, 8: 0.88, 9: 1.07, 10: 1.13, 11: 1.16, 12: 1.10}
_RESTAURANT_SEASON = {1: 0.88, 2: 0.90, 3: 1.0, 4: 1.05, 5: 1.10, 6: 1.12,
                      7: 1.14, 8: 1.12, 9: 1.0, 10: 0.98, 11: 1.02, 12: 1.07}

GUITAR_WORKS = ClientProfile(
    slug="guitar-works",
    entities=("Guitar Works",),
    customers=("Fretboard Retail", "Sixstring Distributors", "Harmony Music Co",
               "Cadence Instruments", "Allegro Stores"),
    vendors=("Tonewood Supply", "Hardware & Tuners Inc", "Lacquer & Finish Co",
             "Case & Gigbag Mfg", "Maple Lumber Yard", "String Source"),
    base_monthly_rev=58000.0, yoy=1.10, season=_AGENCY_SEASON,
    revenue_accounts=("400", "410"), cogs_account="500", cogs_frac=0.42,
    has_payroll=True, owner_draw_monthly=2200.0)

HARBORSIDE_GROUP = ClientProfile(
    slug="harborside-group",
    entities=("Harborside Pier", "Harborside Downtown", "Harborside Express"),
    customers=("Walk-in", "Catering Client", "Event Booking", "Delivery Apps"),
    vendors=("Fresh Produce Co", "Seafood Direct", "Beverage Distributor",
             "Linen & Laundry", "Restaurant Supply Co", "Utilities Group"),
    base_monthly_rev=42000.0, yoy=1.08, season=_RESTAURANT_SEASON,
    revenue_accounts=("400",), cogs_account="500", cogs_frac=0.34,
    has_payroll=True, owner_draw_monthly=0.0, intercompany_sweeps=True)

JB_DESIGN = ClientProfile(
    slug="jb-design",
    entities=("J.B. Design",),
    customers=("Riverside Studios", "Northgate Retail", "Bluepeak Ventures",
               "Summit Yoga", "Lantern Logistics"),
    vendors=("Cloudhost Inc", "Adobe Tools", "Freelance Collective",
             "Citywide Internet", "Apex Insurance"),
    base_monthly_rev=9500.0, yoy=1.12, season=_AGENCY_SEASON,
    revenue_accounts=("400", "410"), cogs_account="500", cogs_frac=0.18,
    has_payroll=False, owner_draw_monthly=2600.0)
