# Payroll Engine (Spec 3B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real gross-to-net payroll subsystem that computes statutory withholdings, posts balanced double-entry pay runs to each client's `ledger.db`, and seeds biweekly history for the payroll clients.

**Architecture:** A pure calculator (`payroll_engine.compute_paycheck`) reads year-keyed statutory tables (`payroll_tax_tables.py`) and returns a `PaycheckResult`. A DB executor (`payroll_run.run_payroll`) loads employees, computes YTD, posts one balanced multi-split entry per run, and records `payroll_runs`/`payroll_lines`. A seeder (`seed_payroll.py`) books biweekly runs across the 2022-01-01…2026-06-09 data window, replacing the old lump-sum wage placeholder.

**Tech Stack:** Python 3 stdlib (`sqlite3`, `dataclasses`, `datetime`), pytest. No third-party deps.

---

## Conventions for the implementer

- Work in the worktree the controller created; run all commands from `scripts/`.
- Run a single test file: `python3 -m pytest test_payroll_engine.py -v`.
- Posting mirrors `seed_client._booked`: `entries.status='posted'`, splits have exactly one of `dr`/`cr` nonzero.
- Money is rounded to 2 decimals at every boundary with `round(x, 2)`.
- All employee data is synthetic. No real PII.
- **Federal year clamp:** the data window starts 2022 but only 2024/2025 federal
  Standard schedules are embedded; `fed_schedule()` clamps `year<2024→2024` and
  `year>2025→2025`. SS wage bases are accurate per year (2022-2026). This is a
  documented demo simplification (penny-accuracy for synthetic past years is not
  a goal — the engine math is the deliverable).
- **CA state schedule is a labeled simplified demo schedule** (genuine marginal
  rates, rounded thresholds, no CA standard deduction / exemption credits). PA
  (flat 3.07%) and TX/FL (no income tax) are exact. SUTA rates are
  representative new-employer rates.

---

## Task 1: COA payroll-liability accounts (245–249)

**Files:**
- Modify: `coa.csv` (append 5 rows)
- Test: `scripts/test_payroll_coa.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_payroll_coa.py
import csv, os

COA = os.path.join(os.path.dirname(__file__), "..", "coa.csv")

def test_payroll_liability_accounts_present():
    rows = {r["code"]: r for r in csv.DictReader(open(COA))}
    for code, name in [
        ("245", "Federal Income Tax Withheld Payable"),
        ("246", "FICA Payable"),
        ("247", "State Income Tax Withheld Payable"),
        ("248", "FUTA Payable"),
        ("249", "SUTA Payable"),
    ]:
        assert code in rows, f"missing COA code {code}"
        assert rows[code]["name"] == name
        assert rows[code]["type"] == "liability"
        assert rows[code]["sign_normal"] == "credit"
        assert rows[code]["group"] == "current_liabilities"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_payroll_coa.py -v`
Expected: FAIL (`missing COA code 245`).

- [ ] **Step 3: Append the 5 rows to `coa.csv`**

Add these lines after the existing `230,Sales Tax Payable,...` / `250,Income Tax Payable,...` block (order within the file does not matter; append at the end of the liability section or end of file):

```csv
245,Federal Income Tax Withheld Payable,liability,credit,current_liabilities
246,FICA Payable,liability,credit,current_liabilities
247,State Income Tax Withheld Payable,liability,credit,current_liabilities
248,FUTA Payable,liability,credit,current_liabilities
249,SUTA Payable,liability,credit,current_liabilities
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_payroll_coa.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coa.csv scripts/test_payroll_coa.py
git commit -m "feat(lisza): payroll-liability COA accounts (245-249)"
```

---

## Task 2: Per-book schema — employees, payroll_runs, payroll_lines

**Files:**
- Modify: `scripts/book_schema.py` (add table DDL inside `ensure_book_schema`)
- Test: `scripts/test_payroll_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_payroll_schema.py
import sqlite3
import book_schema

def _tables(con):
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

def test_payroll_tables_created_and_idempotent():
    con = sqlite3.connect(":memory:")
    # ensure_book_schema expects an entries table to alter
    con.execute("CREATE TABLE entries(id INTEGER PRIMARY KEY, entry_date TEXT)")
    book_schema.ensure_book_schema(con)
    book_schema.ensure_book_schema(con)  # idempotent second call
    t = _tables(con)
    assert {"employees", "payroll_runs", "payroll_lines"} <= t
    cols = {r[1] for r in con.execute("PRAGMA table_info(employees)")}
    assert {"entity_id", "filing_status", "work_state", "residence_state",
            "annual_salary", "w4_dependents_amt", "w4_extra_withholding"} <= cols
    lcols = {r[1] for r in con.execute("PRAGMA table_info(payroll_lines)")}
    assert {"run_id", "employee_id", "entry_id", "gross", "fed_wh", "ss_ee",
            "ss_er", "medi_ee", "medi_er", "addl_medi", "state_wh", "futa",
            "suta", "net"} <= lcols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_payroll_schema.py -v`
Expected: FAIL (`employees` not in tables).

- [ ] **Step 3: Add the DDL to `ensure_book_schema`**

In `scripts/book_schema.py`, inside `ensure_book_schema`, **before** the final
`con.commit()` (after the existing `entities`/`entries` logic), add:

```python
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id     INTEGER NOT NULL,
            name          TEXT NOT NULL,
            filing_status TEXT NOT NULL,
            work_state    TEXT NOT NULL,
            residence_state TEXT NOT NULL,
            pay_frequency TEXT NOT NULL DEFAULT 'biweekly',
            annual_salary REAL NOT NULL,
            w4_multiple_jobs INTEGER NOT NULL DEFAULT 0,
            w4_dependents_amt REAL NOT NULL DEFAULT 0,
            w4_other_income   REAL NOT NULL DEFAULT 0,
            w4_deductions     REAL NOT NULL DEFAULT 0,
            w4_extra_withholding REAL NOT NULL DEFAULT 0,
            active        INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS payroll_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id    INTEGER NOT NULL,
            period_start TEXT NOT NULL,
            period_end   TEXT NOT NULL,
            pay_date     TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS payroll_lines (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      INTEGER NOT NULL REFERENCES payroll_runs(id),
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            entry_id    INTEGER REFERENCES entries(id),
            gross       REAL NOT NULL,
            fed_wh      REAL NOT NULL,
            ss_ee       REAL NOT NULL, ss_er  REAL NOT NULL,
            medi_ee     REAL NOT NULL, medi_er REAL NOT NULL,
            addl_medi   REAL NOT NULL,
            state_wh    REAL NOT NULL,
            futa        REAL NOT NULL, suta   REAL NOT NULL,
            net         REAL NOT NULL
        )
        """
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_payroll_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/book_schema.py scripts/test_payroll_schema.py
git commit -m "feat(lisza): employees + payroll_runs + payroll_lines schema"
```

---

## Task 3: Statutory tax tables (`payroll_tax_tables.py`)

**Files:**
- Create: `scripts/payroll_tax_tables.py`
- Test: `scripts/test_payroll_tax_tables.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_payroll_tax_tables.py
import payroll_tax_tables as t

def test_bracket_tax_single_2025_known_point():
    sched = t.fed_schedule(2025, "single")
    # annual adjusted 52,000 -> 1192.50 + 12% of (52000-18325)
    assert round(t.bracket_tax(sched, 52000.0), 2) == 5233.50

def test_fed_year_clamp():
    # 2022/2023 clamp to 2024; 2026 clamps to 2025
    assert t.fed_schedule(2022, "single") == t.fed_schedule(2024, "single")
    assert t.fed_schedule(2026, "married_jointly") == t.fed_schedule(2025, "married_jointly")

def test_ss_wage_base_per_year():
    assert t.ss_wage_base(2024) == 168600
    assert t.ss_wage_base(2025) == 176100
    assert t.ss_wage_base(2026) == 183600

def test_fica_futa_constants():
    assert t.SS_RATE == 0.062
    assert t.MEDICARE_RATE == 0.0145
    assert t.ADDL_MEDICARE_RATE == 0.009
    assert t.ADDL_MEDICARE_THRESHOLD == 200000
    assert t.FUTA_RATE == 0.006
    assert t.FUTA_WAGE_BASE == 7000

def test_state_rules_archetypes():
    assert t.STATE_RULES["PA"].kind == "flat"
    assert round(t.STATE_RULES["PA"].flat_rate, 4) == 0.0307
    assert t.STATE_RULES["CA"].kind == "progressive"
    assert t.STATE_RULES["TX"].kind == "none"
    assert t.STATE_RULES["FL"].kind == "none"
    # TX/FL still levy SUTA even with no income tax
    assert t.STATE_RULES["TX"].suta_rate > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_payroll_tax_tables.py -v`
Expected: FAIL (`No module named 'payroll_tax_tables'`).

- [ ] **Step 3: Create `scripts/payroll_tax_tables.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_payroll_tax_tables.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/payroll_tax_tables.py scripts/test_payroll_tax_tables.py
git commit -m "feat(lisza): statutory payroll tax tables (federal/FICA/FUTA/state)"
```

---

## Task 4: Paycheck calculator (`payroll_engine.py`)

**Files:**
- Create: `scripts/payroll_engine.py`
- Test: `scripts/test_payroll_engine.py`

- [ ] **Step 1: Write the failing tests (federal + FICA + state + FUTA/SUTA + balance)**

```python
# scripts/test_payroll_engine.py
from payroll_engine import Employee, compute_paycheck

def _emp(**kw):
    base = dict(name="X", filing_status="single", work_state="CA",
                residence_state="CA", annual_salary=52000.0)
    base.update(kw)
    return Employee(**base)

def test_federal_single_2025_known_point():
    r = compute_paycheck(_emp(), gross=2000.0, ytd=0.0, year=2025)
    assert r.fed_wh == 201.29   # 5233.50 annual / 26

def test_federal_dependent_credit_and_extra():
    r = compute_paycheck(_emp(w4_dependents_amt=2600.0, w4_extra_withholding=15.0),
                         gross=2000.0, ytd=0.0, year=2025)
    # 201.288.. - 2600/26(=100.0) + 15.0 = 116.29
    assert r.fed_wh == 116.29

def test_fica_basic():
    r = compute_paycheck(_emp(), gross=2000.0, ytd=0.0, year=2025)
    assert r.ss_ee == 124.0 and r.ss_er == 124.0
    assert r.medi_ee == 29.0 and r.medi_er == 29.0
    assert r.addl_medi == 0.0

def test_ss_caps_at_wage_base():
    r = compute_paycheck(_emp(), gross=2000.0, ytd=175000.0, year=2025)
    assert r.ss_ee == round(1100.0 * 0.062, 2)   # only 1,100 under 176,100 cap
    over = compute_paycheck(_emp(), gross=2000.0, ytd=176100.0, year=2025)
    assert over.ss_ee == 0.0 and over.ss_er == 0.0

def test_additional_medicare_over_200k_ee_only():
    r = compute_paycheck(_emp(), gross=2000.0, ytd=199000.0, year=2025)
    assert r.addl_medi == round(1000.0 * 0.009, 2)   # only 1,000 over 200k
    assert compute_paycheck(_emp(), gross=2000.0, ytd=100000.0, year=2025).addl_medi == 0.0

def test_state_pa_flat():
    r = compute_paycheck(_emp(work_state="PA", residence_state="PA"),
                         gross=2000.0, ytd=0.0, year=2025)
    assert r.state_wh == round(2000.0 * 0.0307, 2)   # 61.40

def test_state_ca_progressive():
    r = compute_paycheck(_emp(), gross=2000.0, ytd=0.0, year=2025)
    assert r.state_wh == 66.65   # (965 + 6% of (52000-39200)) / 26

def test_state_none_tx_fl_zero():
    assert compute_paycheck(_emp(work_state="TX", residence_state="CA"),
                            gross=2000.0, ytd=0.0, year=2025).state_wh == 0.0
    assert compute_paycheck(_emp(work_state="FL", residence_state="FL"),
                            gross=2000.0, ytd=0.0, year=2025).state_wh == 0.0

def test_multistate_work_state_governs():
    # works PA, lives FL -> PA withheld
    r = compute_paycheck(_emp(work_state="PA", residence_state="FL"),
                         gross=2000.0, ytd=0.0, year=2025)
    assert r.state_wh == round(2000.0 * 0.0307, 2)

def test_futa_caps_at_7000():
    assert compute_paycheck(_emp(), gross=2000.0, ytd=0.0, year=2025).futa == 12.0
    assert compute_paycheck(_emp(), gross=2000.0, ytd=6000.0, year=2025).futa == 6.0
    assert compute_paycheck(_emp(), gross=2000.0, ytd=7000.0, year=2025).futa == 0.0

def test_suta_pa_rate_and_base():
    r = compute_paycheck(_emp(work_state="PA", residence_state="PA"),
                         gross=2000.0, ytd=0.0, year=2025)
    assert r.suta == round(2000.0 * 0.03689, 2)

def test_balance_identity_holds_to_the_cent():
    configs = [
        _emp(),
        _emp(work_state="PA", residence_state="FL"),
        _emp(filing_status="married_jointly", annual_salary=95000.0),
        _emp(annual_salary=245000.0),       # exercises SS cap + addl medicare
        _emp(work_state="TX", residence_state="TX"),
    ]
    for e in configs:
        for ytd in (0.0, 6500.0, 175000.0, 205000.0):
            r = compute_paycheck(e, gross=round(e.annual_salary / 26, 2),
                                 ytd=ytd, year=2025)
            lhs = round(r.gross + r.ss_er + r.medi_er + r.futa + r.suta, 2)
            fica = r.ss_ee + r.ss_er + r.medi_ee + r.medi_er + r.addl_medi
            rhs = round(r.fed_wh + fica + r.state_wh + r.futa + r.suta + r.net, 2)
            assert lhs == rhs, (e.work_state, ytd, lhs, rhs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_payroll_engine.py -v`
Expected: FAIL (`No module named 'payroll_engine'`).

- [ ] **Step 3: Create `scripts/payroll_engine.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_payroll_engine.py -v`
Expected: PASS (all tests, including `test_balance_identity_holds_to_the_cent`).

- [ ] **Step 5: Commit**

```bash
git add scripts/payroll_engine.py scripts/test_payroll_engine.py
git commit -m "feat(lisza): gross-to-net paycheck calculator (compute_paycheck)"
```

---

## Task 5: Pay-run executor (`payroll_run.py`)

**Files:**
- Create: `scripts/payroll_run.py`
- Test: `scripts/test_payroll_run.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_payroll_run.py
import os, sqlite3
import pytest

import tenancy
import payroll_run


@pytest.fixture
def book(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LISZA_HOME", str(home))
    # COA loader reads tenancy.COA_PATH; point it at the repo coa.csv
    repo_coa = os.path.join(os.path.dirname(__file__), "..", "coa.csv")
    monkeypatch.setattr(tenancy, "COA_PATH", __import__("pathlib").Path(repo_coa))
    tenancy.register_client(slug="acme", display_name="Acme", entity_type="llc")
    con = sqlite3.connect(tenancy.resolve_db("acme"))
    ent = con.execute("SELECT id FROM entities WHERE is_default=1").fetchone()[0]
    con.execute(
        "INSERT INTO employees(entity_id,name,filing_status,work_state,"
        "residence_state,annual_salary) VALUES(?,?,?,?,?,?)",
        (ent, "Worker A", "single", "PA", "PA", 52000.0))
    con.commit()
    con.close()
    return "acme", ent


def _splits_balance(con):
    dr, cr = con.execute(
        "SELECT ROUND(SUM(dr),2), ROUND(SUM(cr),2) FROM splits").fetchone()
    return dr, cr


def test_run_posts_one_balanced_entry_with_lines(book):
    slug, ent = book
    rid = payroll_run.run_payroll(slug, ent, "2025-01-17")
    con = sqlite3.connect(tenancy.resolve_db(slug))
    assert con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM payroll_lines WHERE run_id=?",
                       (rid,)).fetchone()[0] == 1
    # exactly one payroll entry, and it balances
    assert con.execute(
        "SELECT COUNT(*) FROM entries WHERE source='payroll'").fetchone()[0] == 1
    dr, cr = _splits_balance(con)
    assert dr == cr and dr > 0
    accts = {r[0] for r in con.execute("SELECT DISTINCT account FROM splits")}
    assert {"555", "556", "245", "246", "247"} <= accts   # PA has state tax
    # line links to the entry
    eid = con.execute("SELECT entry_id FROM payroll_lines WHERE run_id=?",
                      (rid,)).fetchone()[0]
    assert eid is not None
    con.close()


def test_ytd_accumulates_across_runs(book):
    slug, ent = book
    payroll_run.run_payroll(slug, ent, "2025-01-17")
    payroll_run.run_payroll(slug, ent, "2025-01-31")
    con = sqlite3.connect(tenancy.resolve_db(slug))
    # two runs, two lines, FUTA on run 2 still > 0 (under 7k after one biweekly)
    assert con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0] == 2
    futa_vals = [r[0] for r in con.execute(
        "SELECT futa FROM payroll_lines ORDER BY id")]
    assert futa_vals[0] > 0 and futa_vals[1] > 0
    con.close()


def test_run_opens_only_requested_client(book, tmp_path):
    slug, ent = book
    # a second client must remain untouched
    tenancy.register_client(slug="other", display_name="Other", entity_type="llc")
    payroll_run.run_payroll(slug, ent, "2025-01-17")
    con = sqlite3.connect(tenancy.resolve_db("other"))
    assert con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0] == 0
    con.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_payroll_run.py -v`
Expected: FAIL (`No module named 'payroll_run'`).

- [ ] **Step 3: Create `scripts/payroll_run.py`**

```python
#!/usr/bin/env python3
"""Execute a pay run: compute each employee, post one balanced entry, record it.

Opens only the requested client's ledger.db. YTD is computed from prior posted
payroll_lines in the same calendar year, so statutory caps reset on Jan 1.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta

import tenancy
from payroll_engine import Employee, compute_paycheck

_EMP_COLS = ("id", "name", "filing_status", "work_state", "residence_state",
             "pay_frequency", "annual_salary", "w4_multiple_jobs",
             "w4_dependents_amt", "w4_other_income", "w4_deductions",
             "w4_extra_withholding")

_SUM_FIELDS = ("gross", "fed_wh", "ss_ee", "ss_er", "medi_ee", "medi_er",
               "addl_medi", "state_wh", "futa", "suta", "net")


def _as_date(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


def _employee_from_row(row) -> Employee:
    m = dict(zip(_EMP_COLS, row))
    return Employee(
        name=m["name"], filing_status=m["filing_status"],
        work_state=m["work_state"], residence_state=m["residence_state"],
        annual_salary=m["annual_salary"], pay_frequency=m["pay_frequency"],
        w4_multiple_jobs=m["w4_multiple_jobs"],
        w4_dependents_amt=m["w4_dependents_amt"],
        w4_other_income=m["w4_other_income"], w4_deductions=m["w4_deductions"],
        w4_extra_withholding=m["w4_extra_withholding"])


def _ytd_gross(con, employee_id: int, year: int, before: str) -> float:
    return con.execute(
        "SELECT COALESCE(SUM(l.gross),0) FROM payroll_lines l "
        "JOIN payroll_runs r ON r.id=l.run_id "
        "WHERE l.employee_id=? AND substr(r.period_end,1,4)=? "
        "AND r.period_end < ?",
        (employee_id, str(year), before)).fetchone()[0]


def _post_entry(con, entity_id: int, pay_date: str, tot: dict) -> int:
    con.execute(
        "INSERT INTO entries(entry_date,description,payee,source,status,"
        "entity_id,is_intercompany) VALUES(?,?,?,?, 'posted', ?, 0)",
        (pay_date, "Payroll", "Payroll", "payroll", entity_id))
    eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    fica = round(tot["ss_ee"] + tot["ss_er"] + tot["medi_ee"]
                 + tot["medi_er"] + tot["addl_medi"], 2)
    employer_tax = round(tot["ss_er"] + tot["medi_er"] + tot["futa"]
                         + tot["suta"], 2)
    debits = [("555", round(tot["gross"], 2)), ("556", employer_tax)]
    credits = [("245", tot["fed_wh"]), ("246", fica), ("247", tot["state_wh"]),
               ("248", tot["futa"]), ("249", tot["suta"]), ("102", tot["net"])]
    for acct, amt in debits:
        if amt:
            con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,?,0)",
                        (eid, acct, round(amt, 2)))
    for acct, amt in credits:
        if amt:
            con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,0,?)",
                        (eid, acct, round(amt, 2)))
    return eid


def run_payroll(slug: str, entity_id: int, period_end,
                pay_date=None, period_start=None):
    """Compute + post one pay run for one entity. Returns run_id, or None if no
    active employees."""
    pe = _as_date(period_end)
    pd_ = _as_date(pay_date) if pay_date else pe
    ps = _as_date(period_start) if period_start else (pe - timedelta(days=13))

    con = sqlite3.connect(tenancy.resolve_db(slug))
    con.execute("PRAGMA foreign_keys=ON")
    try:
        rows = con.execute(
            f"SELECT {','.join(_EMP_COLS)} FROM employees "
            "WHERE entity_id=? AND active=1 ORDER BY id", (entity_id,)).fetchall()
        if not rows:
            return None

        con.execute(
            "INSERT INTO payroll_runs(entity_id,period_start,period_end,pay_date) "
            "VALUES(?,?,?,?)",
            (entity_id, ps.isoformat(), pe.isoformat(), pd_.isoformat()))
        run_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

        year = pe.year
        tot = defaultdict(float)
        computed = []
        for row in rows:
            emp = _employee_from_row(row)
            gross = round(emp.annual_salary / 26, 2)
            ytd = _ytd_gross(con, row[0], year, pe.isoformat())
            res = compute_paycheck(emp, gross, ytd, year)
            computed.append((row[0], res))
            for f in _SUM_FIELDS:
                tot[f] += getattr(res, f)
        tot = {k: round(v, 2) for k, v in tot.items()}

        eid = _post_entry(con, entity_id, pd_.isoformat(), tot)
        for emp_id, res in computed:
            con.execute(
                "INSERT INTO payroll_lines(run_id,employee_id,entry_id,gross,"
                "fed_wh,ss_ee,ss_er,medi_ee,medi_er,addl_medi,state_wh,futa,"
                "suta,net) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, emp_id, eid, res.gross, res.fed_wh, res.ss_ee,
                 res.ss_er, res.medi_ee, res.medi_er, res.addl_medi,
                 res.state_wh, res.futa, res.suta, res.net))
        con.commit()
        return run_id
    finally:
        con.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_payroll_run.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/payroll_run.py scripts/test_payroll_run.py
git commit -m "feat(lisza): run_payroll posts balanced pay-run entries + lines"
```

---

## Task 6: Seed payroll history + wire into `seed_client`

**Files:**
- Create: `scripts/seed_payroll.py`
- Modify: `scripts/seed_client.py` (drop lump-sum wage line for payroll clients; call `seed_payroll`)
- Test: `scripts/test_seed_payroll.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_seed_payroll.py
import os, sqlite3
import pytest
from pathlib import Path

import tenancy
import seed_payroll


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("LISZA_HOME", str(h))
    repo_coa = os.path.join(os.path.dirname(__file__), "..", "coa.csv")
    monkeypatch.setattr(tenancy, "COA_PATH", Path(repo_coa))
    return h


def test_seed_payroll_creates_roster_and_runs(home):
    tenancy.register_client(slug="guitar-works", display_name="Guitar Works",
                            entity_type="llc")
    n = seed_payroll.seed_payroll(slug="guitar-works")
    con = sqlite3.connect(tenancy.resolve_db("guitar-works"))
    assert con.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM payroll_runs").fetchone()[0] > 0
    assert n > 0
    # whole book balances after payroll seeding
    dr, cr = con.execute("SELECT ROUND(SUM(dr),2), ROUND(SUM(cr),2) FROM splits").fetchone()
    assert dr == cr
    con.close()


def test_seed_payroll_noop_for_non_payroll_client(home):
    tenancy.register_client(slug="jb-design", display_name="J.B. Design",
                            entity_type="llc")
    n = seed_payroll.seed_payroll(slug="jb-design")
    assert n == 0
    con = sqlite3.connect(tenancy.resolve_db("jb-design"))
    assert con.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0
    con.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_seed_payroll.py -v`
Expected: FAIL (`No module named 'seed_payroll'`).

- [ ] **Step 3: Create `scripts/seed_payroll.py`**

```python
#!/usr/bin/env python3
"""Seed biweekly payroll history for clients with employees. Synthetic only.

Rosters are keyed by client slug. Clients absent from ROSTERS (e.g. jb-design,
owner-draws only) are a no-op. Biweekly runs span the ledger data window.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import tenancy
import payroll_run

START = date(2022, 1, 1)
END = date(2026, 6, 9)

# Each roster entry: entity name + Employee fields. All synthetic.
ROSTERS = {
    "guitar-works": [
        {"entity": "Guitar Works", "name": "Dana Whitlock",
         "filing_status": "married_jointly", "work_state": "CA",
         "residence_state": "CA", "annual_salary": 72000.0,
         "w4_dependents_amt": 2000.0},
        {"entity": "Guitar Works", "name": "Marco Ferreira",
         "filing_status": "single", "work_state": "CA",
         "residence_state": "CA", "annual_salary": 58000.0},
        {"entity": "Guitar Works", "name": "Priya Anand",
         "filing_status": "single", "work_state": "CA",
         "residence_state": "CA", "annual_salary": 245000.0},
    ],
    "harborside-group": [
        {"entity": "Harborside Pier", "name": "Tom Vasquez",
         "filing_status": "married_jointly", "work_state": "PA",
         "residence_state": "PA", "annual_salary": 95000.0},
        {"entity": "Harborside Pier", "name": "Lena Kowalski",
         "filing_status": "single", "work_state": "PA",
         "residence_state": "PA", "annual_salary": 42000.0},
        {"entity": "Harborside Downtown", "name": "Sam Okafor",
         "filing_status": "head_of_household", "work_state": "PA",
         "residence_state": "PA", "annual_salary": 51000.0,
         "w4_dependents_amt": 2000.0},
        {"entity": "Harborside Downtown", "name": "Grace Lim",
         "filing_status": "single", "work_state": "PA",
         "residence_state": "FL", "annual_salary": 38000.0},
        {"entity": "Harborside Express", "name": "Hector Ruiz",
         "filing_status": "single", "work_state": "PA",
         "residence_state": "PA", "annual_salary": 47000.0},
        {"entity": "Harborside Express", "name": "Aisha Bello",
         "filing_status": "married_jointly", "work_state": "PA",
         "residence_state": "PA", "annual_salary": 63000.0},
    ],
}

_EMP_INSERT_FIELDS = ("entity_id", "name", "filing_status", "work_state",
                      "residence_state", "annual_salary", "w4_multiple_jobs",
                      "w4_dependents_amt", "w4_other_income", "w4_deductions",
                      "w4_extra_withholding")


def _entity_id(con, name: str) -> int:
    row = con.execute("SELECT id FROM entities WHERE name=?", (name,)).fetchone()
    return row[0] if row else con.execute(
        "SELECT id FROM entities WHERE is_default=1").fetchone()[0]


def _insert_roster(con, roster) -> set[int]:
    entity_ids = set()
    for r in roster:
        ent = _entity_id(con, r["entity"])
        entity_ids.add(ent)
        vals = {
            "entity_id": ent, "name": r["name"],
            "filing_status": r["filing_status"], "work_state": r["work_state"],
            "residence_state": r["residence_state"],
            "annual_salary": r["annual_salary"], "w4_multiple_jobs": 0,
            "w4_dependents_amt": r.get("w4_dependents_amt", 0.0),
            "w4_other_income": 0.0, "w4_deductions": 0.0,
            "w4_extra_withholding": 0.0,
        }
        cols = ",".join(_EMP_INSERT_FIELDS)
        ph = ",".join("?" * len(_EMP_INSERT_FIELDS))
        con.execute(f"INSERT INTO employees({cols}) VALUES({ph})",
                    tuple(vals[c] for c in _EMP_INSERT_FIELDS))
    con.commit()
    return entity_ids


def seed_payroll(slug: str) -> int:
    """Insert the client's roster and book biweekly runs. Returns run count."""
    roster = ROSTERS.get(slug)
    if not roster:
        return 0
    con = sqlite3.connect(tenancy.resolve_db(slug))
    try:
        entity_ids = sorted(_insert_roster(con, roster))
    finally:
        con.close()

    runs = 0
    d = START
    while d + timedelta(days=13) <= END:
        period_end = d + timedelta(days=13)
        for ent in entity_ids:
            if payroll_run.run_payroll(slug, ent, period_end) is not None:
                runs += 1
        d = d + timedelta(days=14)
    return runs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_seed_payroll.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire into `seed_client.py` and drop the lump-sum placeholder**

In `scripts/seed_client.py`, remove the lump-sum wage line inside the monthly
loop:

```python
            if profile.has_payroll:
                _booked(con, entity_id, _day(first, rng, 1, 8), "Salaries & wages",
                        "Payroll", "555", "102", round(rev_target * 0.22, 2))
```

Then, near the end of `seed()` — after the monthly loop commits and **before**
the final `return` — add a payroll seeding call. Locate the end of `seed()`
(after `con.commit()` / `con.close()` if present) and insert:

```python
    # real payroll history replaces the lump-sum wage placeholder
    import seed_payroll
    seed_payroll.seed_payroll(slug=slug)
```

If `seed()` closes `con` before returning, place the `seed_payroll` call after
`con.close()` (it opens its own connection). Verify by reading the tail of
`seed()` before editing.

- [ ] **Step 6: Run the seed-related suite to verify nothing broke**

Run: `python3 -m pytest test_seed_payroll.py test_seed_client.py -v`
Expected: PASS. If `test_seed_client.py` asserted on the old lump-sum wage line,
update that assertion to reflect real payroll entries (`source='payroll'`)
instead — show the diff in your report.

- [ ] **Step 7: Commit**

```bash
git add scripts/seed_payroll.py scripts/seed_client.py scripts/test_seed_payroll.py
git commit -m "feat(lisza): seed biweekly payroll history; drop lump-sum placeholder"
```

---

## Task 7: Regenerate demo books + 3A artifacts; full suite green

**Files:**
- Modify (regenerated): `public/clients/*.json`, `public/dashboard.json`,
  `clients/*/ledger.db`

- [ ] **Step 1: Run the full Python suite**

Run: `python3 -m pytest -q`
Expected: PASS (all tests, including the prior 3A generator tests and the new
payroll tests).

- [ ] **Step 2: Rebuild the demo books from scratch**

The demo books are regenerated by the demo builder. Confirm the entrypoint first:

Run: `python3 -c "import build_demo_clients, inspect; print(build_demo_clients.__file__)"`
Then rebuild:
Run: `python3 build_demo_clients.py`
Expected: rebuilds each client's `ledger.db` with real payroll history.

- [ ] **Step 3: Regenerate the published JSON artifacts**

Run: `python3 build_client_detail.py && python3 build_dashboard.py`
Expected: `wrote 3 client detail files` and `wrote .../public/dashboard.json`.

- [ ] **Step 4: Verify the detail view still renders for all three clients**

Run:
```bash
node render_detail_check.js guitar-works
node render_detail_check.js harborside-group
node render_detail_check.js jb-design
```
Expected: `OK <slug>: all 5 tiles render` for each.

- [ ] **Step 5: Sanity-check that payroll landed in the books**

Run:
```bash
python3 -c "import sqlite3, tenancy; con=sqlite3.connect(tenancy.resolve_db('guitar-works')); print('runs', con.execute('SELECT COUNT(*) FROM payroll_runs').fetchone()[0]); dr,cr=con.execute('SELECT ROUND(SUM(dr),2),ROUND(SUM(cr),2) FROM splits').fetchone(); print('balance', dr, cr); assert dr==cr"
```
Expected: `runs` > 0 and `balance` equal on both sides.

- [ ] **Step 6: Commit the regenerated artifacts**

```bash
git add public/clients public/dashboard.json clients
git commit -m "chore(lisza): regenerate demo books + artifacts with real payroll"
```

---

## Self-review notes (for the controller)

- **Spec coverage:** Task 1 = COA accounts; Task 2 = 3 tables; Task 3 = tax
  tables (federal/FICA/FUTA/state archetypes); Task 4 = compute_paycheck
  (federal, FICA cap, addl Medicare, state, multi-state, FUTA/SUTA, balance
  identity); Task 5 = run_payroll posting + YTD + isolation; Task 6 = seeding +
  wiring; Task 7 = regenerate + full green.
- **Deferred (documented in spec):** Step-2-checkbox federal schedule; the W-2/941
  surfacing (Spec 3C).
- **Type consistency:** `Employee`/`PaycheckResult` fields, `_SUM_FIELDS`, the
  `payroll_lines` columns, and the `_post_entry` account codes all align across
  Tasks 2/4/5. `income_wh(period_gross, adjusted_annual, filing_status, pp)` in
  Task 3 matches the call in Task 4.
