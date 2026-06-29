#!/usr/bin/env python3
"""Pure gross-to-net paycheck calculator. No DB, no I/O.

`compute_paycheck` reads year-keyed tables from payroll_tax_tables and returns a
PaycheckResult. YTD is the employee's year-to-date gross BEFORE this period, used
for the Social Security cap, Additional Medicare threshold, and FUTA/SUTA bases.
"""
from __future__ import annotations

from dataclasses import dataclass

import payroll_tax_tables as t

PAY_PERIODS = {"biweekly": 26, "weekly": 52, "semimonthly": 24, "monthly": 12}


@dataclass(frozen=True)
class Employee:
    name: str
    filing_status: str            # single | married_jointly | head_of_household
    work_state: str
    residence_state: str
    annual_salary: float
    pay_frequency: str = "biweekly"
    w4_multiple_jobs: int = 0
    w4_dependents_amt: float = 0.0
    w4_other_income: float = 0.0
    w4_deductions: float = 0.0
    w4_extra_withholding: float = 0.0


@dataclass(frozen=True)
class PaycheckResult:
    gross: float
    fed_wh: float
    ss_ee: float
    ss_er: float
    medi_ee: float
    medi_er: float
    addl_medi: float
    state_wh: float
    futa: float
    suta: float
    net: float

    @property
    def employer_tax(self) -> float:
        return round(self.ss_er + self.medi_er + self.futa + self.suta, 2)


def _federal_wh(emp: Employee, gross: float, year: int, pp: int) -> float:
    annual = gross * pp
    adjusted = max(0.0, annual + emp.w4_other_income - emp.w4_deductions)
    sched = t.fed_schedule(year, emp.filing_status)
    tentative = t.bracket_tax(sched, adjusted)
    per = tentative / pp - emp.w4_dependents_amt / pp
    per = max(0.0, per) + emp.w4_extra_withholding
    return round(per, 2)


def compute_paycheck(emp: Employee, gross: float, ytd: float,
                     year: int) -> PaycheckResult:
    pp = PAY_PERIODS[emp.pay_frequency]
    gross = round(gross, 2)

    fed_wh = _federal_wh(emp, gross, year, pp)

    # Social Security: only the slice of this period's gross under the wage base.
    ss_cap = t.ss_wage_base(year)
    ss_taxable = max(0.0, min(gross, ss_cap - ytd))
    ss_ee = round(ss_taxable * t.SS_RATE, 2)
    ss_er = ss_ee

    # Medicare: uncapped, both sides.
    medi_ee = round(gross * t.MEDICARE_RATE, 2)
    medi_er = medi_ee

    # Additional Medicare: EE only, on the slice over the YTD threshold.
    addl_taxable = max(0.0, min(gross, (ytd + gross) - t.ADDL_MEDICARE_THRESHOLD))
    addl_medi = round(addl_taxable * t.ADDL_MEDICARE_RATE, 2)

    # State income tax: governed by WORK state.
    annual = gross * pp
    adjusted = max(0.0, annual + emp.w4_other_income - emp.w4_deductions)
    rule = t.STATE_RULES.get(emp.work_state)
    state_wh = (rule.income_wh(gross, adjusted, emp.filing_status, pp)
                if rule else 0.0)

    # FUTA (employer-only), first 7,000 YTD.
    futa_taxable = max(0.0, min(gross, t.FUTA_WAGE_BASE - ytd))
    futa = round(futa_taxable * t.FUTA_RATE, 2)

    # SUTA (employer-only), per-state rate/base.
    suta = 0.0
    if rule and rule.suta_rate > 0:
        suta_taxable = max(0.0, min(gross, rule.suta_wage_base - ytd))
        suta = round(suta_taxable * rule.suta_rate, 2)

    net = round(gross - fed_wh - ss_ee - medi_ee - addl_medi - state_wh, 2)

    return PaycheckResult(
        gross=gross, fed_wh=fed_wh, ss_ee=ss_ee, ss_er=ss_er,
        medi_ee=medi_ee, medi_er=medi_er, addl_medi=addl_medi,
        state_wh=state_wh, futa=futa, suta=suta, net=net)
