# Spec 3B — Full-Fidelity Payroll Engine

**Date:** 2026-06-29
**Status:** Approved (design checkpoint passed)
**Predecessors:** Spec 1 (multi-tenant foundation), Spec 2 (dashboard), Spec 3A (client detail view)
**Successors:** Spec 3C (payroll tile + W-2/941 form data)

## Purpose

Give LISZA a real gross-to-net payroll subsystem. It models employees, computes
statutory withholdings (federal income tax, FICA, FUTA/SUTA, state income tax),
posts each pay run as a balanced double-entry transaction to the client's
`ledger.db`, and seeds biweekly history for the payroll clients. The per-run
detail it records (`payroll_lines`) is the source data Spec 3C surfaces as the
payroll tile and the W-2 / 941 forms.

This replaces the current lump-sum wage placeholder in `seed_client.py`
(`_booked(..., "555", "102", rev*0.22)`) with real per-employee pay runs.

## Scope

**In scope**
- An `employees` table per book (synthetic data only — no real PII).
- `payroll_runs` + `payroll_lines` tables capturing each pay run and its
  per-employee breakdown.
- Year-keyed statutory tax tables (federal brackets, FICA/FUTA/SUTA rates and
  wage bases, in-scope state rules).
- A pure paycheck calculator: `compute_paycheck(employee, gross, ytd)`.
- A pay-run executor: `run_payroll(slug, entity_id, period_end)` that posts a
  balanced ledger entry and records the run.
- Five new COA liability accounts (245–249) for payroll withholdings.
- A `seed_payroll` step that books biweekly runs across the data window for the
  payroll clients, wired into `seed_client.seed()`.
- pytest coverage for the calculator (bracket math, FICA cap, Additional
  Medicare, FUTA/SUTA wage bases, state archetypes, multi-state) and for the
  run executor (balanced entries, YTD accumulation across runs, isolation).

**Out of scope**
- The payroll detail tile and W-2 / 941 rendering (Spec 3C).
- Hourly/overtime, benefits/pre-tax deductions (401k, Section 125), garnishments,
  bonuses/supplemental flat-rate withholding, local (city) taxes.
- Any real-employee data; any filing or payment to tax authorities.
- All 50 states — only the demo employers' states are implemented.

## Architecture

Follows the existing library/desk module convention and the static seed pattern.

```
employees + tax tables --compute_paycheck--> PaycheckResult (pure, no DB)
                                                    |
run_payroll(slug, entity_id, period_end) --posts--> ledger.db entry + splits
                                                    +--> payroll_runs / payroll_lines
seed_payroll(profile, slug) --biweekly loop--> run_payroll(...) across data window
```

### New modules (`scripts/`)

- `payroll_tax_tables.py` — statutory constants keyed by tax year. Pure data +
  small lookup helpers. Holds: federal percentage-method annual bracket
  schedules (per filing status, Standard and Step-2-checkbox variants); FICA SS
  rate + wage base; Medicare rate; Additional Medicare rate + threshold; FUTA
  effective rate + wage base; a `STATE_RULES` map for in-scope states.
- `payroll_engine.py` — `Employee` dataclass and the pure calculator
  `compute_paycheck(emp, gross, ytd, year) -> PaycheckResult`. No DB access.
  `PaycheckResult` carries gross, fed_wh, ss_ee, ss_er, medi_ee, medi_er,
  addl_medi, state_wh, futa, suta, net, and the employer-tax total.
- `payroll_run.py` — `run_payroll(slug, entity_id, period_end, pay_date=None)`:
  loads active employees for the entity, computes YTD from prior posted
  `payroll_lines` in the same calendar year, calls `compute_paycheck`, posts one
  balanced entry, and writes `payroll_runs` + `payroll_lines`. Opens only the
  requested client's DB.
- `seed_payroll.py` — `seed_payroll(profile, slug)`: builds the synthetic roster
  for the client (from a per-client roster map), then books biweekly runs from
  the data window start through the last seeded month.

### Schema additions (`book_schema.ensure_book_schema`, additive + idempotent)

```sql
CREATE TABLE IF NOT EXISTS employees (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id     INTEGER NOT NULL,
    name          TEXT NOT NULL,
    filing_status TEXT NOT NULL,            -- single | married_jointly | head_of_household
    work_state    TEXT NOT NULL,
    residence_state TEXT NOT NULL,
    pay_frequency TEXT NOT NULL DEFAULT 'biweekly',
    annual_salary REAL NOT NULL,
    w4_multiple_jobs INTEGER NOT NULL DEFAULT 0,   -- Step 2 checkbox (2c)
    w4_dependents_amt REAL NOT NULL DEFAULT 0,     -- Step 3 annual credit
    w4_other_income   REAL NOT NULL DEFAULT 0,     -- Step 4a annual
    w4_deductions     REAL NOT NULL DEFAULT 0,     -- Step 4b annual
    w4_extra_withholding REAL NOT NULL DEFAULT 0,  -- Step 4c per-period
    active        INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS payroll_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id    INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    pay_date     TEXT NOT NULL
);
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
);
```

### COA additions (`coa.csv`)

| code | name | type | sign_normal | group |
|------|------|------|-------------|-------|
| 245 | Federal Income Tax Withheld Payable | liability | credit | current_liabilities |
| 246 | FICA Payable | liability | credit | current_liabilities |
| 247 | State Income Tax Withheld Payable | liability | credit | current_liabilities |
| 248 | FUTA Payable | liability | credit | current_liabilities |
| 249 | SUTA Payable | liability | credit | current_liabilities |

## Posting convention (one balanced entry per pay run)

A pay run aggregates all employees in the run into a single balanced entry
(per `seed_client._booked` style — but multi-split). Debits = credits.

```
DR 555 Salaries & Wages     Σ gross
DR 556 Payroll Taxes        Σ (ss_er + medi_er + futa + suta)
   CR 245 Federal WH Payable   Σ fed_wh
   CR 246 FICA Payable         Σ (ss_ee + ss_er + medi_ee + medi_er + addl_medi)
   CR 247 State WH Payable     Σ state_wh
   CR 248 FUTA Payable         Σ futa
   CR 249 SUTA Payable         Σ suta
   CR 102 Business Checking    Σ net
```

**Balance identity** (must hold to the cent, asserted in tests):
`gross + employer_tax == fed_wh + fica_total + state_wh + futa + suta + net`
where `employer_tax = ss_er + medi_er + futa + suta` and
`fica_total = ss_ee + ss_er + medi_ee + medi_er + addl_medi`.
Net = `gross − fed_wh − ss_ee − medi_ee − addl_medi − state_wh`.

## Tax computation

### Federal income tax — IRS Pub 15-T percentage method (automated systems)

Worksheet 1A logic, per pay period:
1. Annualize: `annual_wage = gross × pay_periods` (biweekly → 26).
2. `adjusted = annual_wage + w4_other_income − w4_deductions` (floor 0).
3. Select the annual Standard-withholding bracket schedule for `filing_status`.
4. `tentative_annual = base + pct × (adjusted − bracket_floor)`.
5. `per_period = tentative_annual / pay_periods`.
6. Subtract dependent credit: `per_period −= w4_dependents_amt / pay_periods` (floor 0).
7. Add `w4_extra_withholding`.
The actual annual Standard bracket schedules (2024, 2025) are embedded in
`payroll_tax_tables.py`. **2026 uses the 2025 schedule** as a documented demo
stand-in (official 2026 percentage-method tables publish late in 2025; this is
synthetic data, so penny-accuracy for a future year is not a goal — the engine
math is the deliverable).

**Step-2-checkbox schedule deferred.** The `w4_multiple_jobs` field is retained
in the employee model (W-4 completeness for 3C), but the engine uses the
Standard schedule regardless; the separate Step-2-checkbox rate schedules are
out of scope for 3B. The synthetic roster contains no checkbox employees, so the
behavior is fully exercised. Adding the checkbox schedules later is a
`payroll_tax_tables.py` data addition, not an engine change.

### FICA

- Social Security: `6.2%` EE and `6.2%` ER on wages until YTD reaches the
  year's wage base (2024 $168,600; 2025 $176,100; 2026 $183,600). Only the
  portion of this period's wages below the cap is taxed.
- Medicare: `1.45%` EE and `1.45%` ER, uncapped.
- Additional Medicare: `0.9%` EE only, on wages where YTD exceeds `$200,000`
  (single threshold used for withholding regardless of filing status — the IRS
  employer rule). No employer match.

### FUTA / SUTA (employer-only)

- FUTA: `0.6%` effective (6.0% gross − 5.4% credit) on the first `$7,000` of
  YTD wages per employee.
- SUTA: `STATE_RULES[work_state].suta_rate` on the first
  `STATE_RULES[work_state].suta_wage_base` of YTD wages. States with no SUTA in
  scope contribute 0.

### State income tax — in-scope states only

`STATE_RULES` keys are the demo employers' work states. Each rule provides a
`state_wh(adjusted_annual, filing_status, pay_periods)` strategy:
- **Progressive-bracket archetype — California (`CA`)**: annual bracket schedule
  per filing status; annualize → bracket → per period.
- **Flat archetype — Pennsylvania (`PA`)**: flat `3.07%` of period gross.
- **No-income-tax archetype — Texas (`TX`), Florida (`FL`)**: `state_wh = 0`.

**Multi-state rule:** income-tax withholding follows `work_state`. An employee
whose `residence_state` is a no-tax state but who works in a taxing state still
has `work_state` tax withheld; an employee working in a no-tax state has zero
state withholding regardless of residence. (Reciprocity agreements are out of
scope.)

## Demo rosters (synthetic)

Per-client roster map in `seed_payroll.py`. All names/salaries fabricated.

- **Guitar Works** (work_state `CA`, single entity): 3 employees —
  e.g. a shop manager (married_jointly, ~$72k), a luthier (single, ~$58k), and
  one high earner (single, ~$245k) to exercise the SS cap and Additional
  Medicare.
- **Harborside Group** (3 entities, work_state `PA`): 6 employees spread across
  the three locations; one with `residence_state = FL` (multi-state path), a mix
  of filing statuses, salaries ~$38k–$95k.
- **jb-design**: no employees (`has_payroll=False`; owner draws only). The seed
  step is a no-op for this client.

## Seeding

`seed_payroll(profile, slug)` runs after the core ledger seed:
1. Insert the client's roster into `employees` (mapped to entity ids).
2. From the data-window start, step biweekly (every 14 days) through the last
   seeded month; for each entity with employees, call `run_payroll`.
3. Remove the old lump-sum wage line from `seed_client.seed()` for payroll
   clients (jb-design keeps owner draws unchanged).

`run_payroll` computes YTD from prior posted `payroll_lines` in the same
calendar year, so caps reset on Jan 1 and accumulate correctly within a year.

## Testing

- `test_payroll_engine.py`:
  - bracket math at known points for each filing status (Standard schedule);
  - dependent credit and extra withholding applied correctly;
  - SS stops at the wage base (YTD straddling the cap taxes only the under-cap
    portion; fully-over yields 0);
  - Additional Medicare applies only above $200k YTD, EE-only;
  - FUTA/SUTA stop at their wage bases;
  - state archetypes: CA progressive, PA flat 3.07%, TX/FL zero;
  - multi-state: work_state governs;
  - **balance identity holds to the cent** for every computed paycheck.
- `test_payroll_run.py`:
  - a run posts a single balanced entry (Σdr == Σcr) with the expected accounts;
  - `payroll_runs`/`payroll_lines` rows written and linked to the entry;
  - YTD accumulates across two sequential runs (second run reflects the cap);
  - `run_payroll` opens only the requested client's DB (isolation).
- Regenerate demo books and confirm `build_client_detail` still passes and the
  historical tile's expense movement now includes real payroll.

## Risks / notes

- **Year coverage:** 2026 reuses the 2025 federal schedule (documented above).
  Wage bases are real per year.
- **Pre-existing wage placeholder:** removing `rev*0.22` changes seeded expense
  totals; 3A artifacts get regenerated, and 3A tests are data-tolerant so they
  remain green.
- **State scope is intentionally small** (CA/PA/TX/FL). Adding a state later is
  a `STATE_RULES` entry, not an engine change — the seam is the
  `state_wh` strategy.
