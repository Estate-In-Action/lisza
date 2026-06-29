#!/usr/bin/env python3
"""Year-keyed statutory payroll tax tables + lookup helpers.

Figures are real published rates/wage bases (demo fidelity). The CA state
schedule is a SIMPLIFIED demo schedule (genuine marginal rates, rounded
thresholds, no CA standard deduction / exemption credits). SUTA rates are
representative new-employer rates. Pure data + pure functions; no DB, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- FICA / FUTA constants ---
SS_RATE = 0.062
MEDICARE_RATE = 0.0145
ADDL_MEDICARE_RATE = 0.009
ADDL_MEDICARE_THRESHOLD = 200000
FUTA_RATE = 0.006            # 6.0% gross - 5.4% credit
FUTA_WAGE_BASE = 7000

_SS_WAGE_BASE = {2022: 147000, 2023: 160200, 2024: 168600,
                 2025: 176100, 2026: 183600}


def ss_wage_base(year: int) -> int:
    return _SS_WAGE_BASE.get(year, 176100)


# --- Federal Standard withholding annual schedules (Pub 15-T Worksheet 1A) ---
# Each schedule: ascending tuples of (floor, base_tax, marginal_rate).
_FED_2024 = {
    "single": [(0, 0.0, 0.0), (6000, 0.0, 0.10), (17600, 1160.0, 0.12),
               (53150, 5426.0, 0.22), (106525, 17168.50, 0.24),
               (197950, 39110.50, 0.32), (249725, 55678.50, 0.35),
               (615350, 183647.25, 0.37)],
    "married_jointly": [(0, 0.0, 0.0), (16300, 0.0, 0.10), (39500, 2320.0, 0.12),
                        (110800, 10876.0, 0.22), (217550, 34361.0, 0.24),
                        (400200, 78197.0, 0.32), (503750, 111357.0, 0.35),
                        (747500, 196669.50, 0.37)],
    "head_of_household": [(0, 0.0, 0.0), (13300, 0.0, 0.10), (34900, 2160.0, 0.12),
                          (77375, 7257.0, 0.22), (114650, 15457.50, 0.24),
                          (204600, 37045.50, 0.32), (257825, 54077.50, 0.35),
                          (633425, 185537.50, 0.37)],
}
_FED_2025 = {
    "single": [(0, 0.0, 0.0), (6400, 0.0, 0.10), (18325, 1192.50, 0.12),
               (54875, 5578.50, 0.22), (109750, 17651.0, 0.24),
               (203700, 40199.0, 0.32), (256925, 57231.0, 0.35),
               (632750, 188769.75, 0.37)],
    "married_jointly": [(0, 0.0, 0.0), (17100, 0.0, 0.10), (40950, 2385.0, 0.12),
                        (114050, 11157.0, 0.22), (223800, 35302.0, 0.24),
                        (411700, 80398.0, 0.32), (518150, 114462.0, 0.35),
                        (768700, 202154.50, 0.37)],
    "head_of_household": [(0, 0.0, 0.0), (13900, 0.0, 0.10), (36350, 2245.0, 0.12),
                          (80650, 7561.0, 0.22), (119300, 16064.0, 0.24),
                          (213250, 38612.0, 0.32), (266475, 55640.0, 0.35),
                          (642300, 187178.75, 0.37)],
}
_FED = {2024: _FED_2024, 2025: _FED_2025}


def fed_schedule(year: int, filing_status: str):
    y = 2024 if year < 2024 else (2025 if year > 2025 else year)
    table = _FED[y]
    return table.get(filing_status, table["single"])


def bracket_tax(schedule, amount: float) -> float:
    """Tentative tax from an ascending (floor, base, rate) schedule."""
    floor, base, rate = 0.0, 0.0, 0.0
    for f, b, r in schedule:
        if amount >= f:
            floor, base, rate = f, b, r
        else:
            break
    return base + rate * (amount - floor)


# --- State rules ---
# CA simplified demo brackets (annual, ascending (floor, base, rate)).
_CA_SINGLE = [(0, 0.0, 0.01), (10500, 105.0, 0.02), (24900, 393.0, 0.04),
              (39200, 965.0, 0.06), (54400, 1877.0, 0.08), (68700, 3021.0, 0.093)]
_CA_MFJ = [(0, 0.0, 0.01), (21000, 210.0, 0.02), (49800, 786.0, 0.04),
           (78400, 1930.0, 0.06), (108800, 3754.0, 0.08), (137400, 6042.0, 0.093)]


@dataclass(frozen=True)
class StateRule:
    code: str
    kind: str                       # 'progressive' | 'flat' | 'none'
    flat_rate: float = 0.0
    brackets: dict = field(default_factory=dict)
    suta_rate: float = 0.0
    suta_wage_base: float = 0.0

    def income_wh(self, period_gross: float, adjusted_annual: float,
                  filing_status: str, pay_periods: int) -> float:
        if self.kind == "none":
            return 0.0
        if self.kind == "flat":
            return round(period_gross * self.flat_rate, 2)
        sched = self.brackets.get(filing_status) or self.brackets["single"]
        return round(bracket_tax(sched, adjusted_annual) / pay_periods, 2)


STATE_RULES = {
    "CA": StateRule("CA", "progressive",
                    brackets={"single": _CA_SINGLE, "married_jointly": _CA_MFJ},
                    suta_rate=0.034, suta_wage_base=7000),
    "PA": StateRule("PA", "flat", flat_rate=0.0307,
                    suta_rate=0.03689, suta_wage_base=10000),
    "TX": StateRule("TX", "none", suta_rate=0.027, suta_wage_base=9000),
    "FL": StateRule("FL", "none", suta_rate=0.027, suta_wage_base=7000),
}
