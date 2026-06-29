# Spec 3C — Payroll Tile + W-2 / 941 Form Data

**Date:** 2026-06-29
**Status:** Approved (design checkpoint passed)
**Predecessors:** Spec 3A (client detail view), Spec 3B (payroll engine)
**Successors:** none (closes the 3A→3B→3C payroll arc)

## Purpose

3B built the payroll engine and seeded biweekly history into `payroll_lines`.
3A built the per-client detail page with four live tiles and a payroll
**placeholder**. 3C closes the loop: it surfaces the payroll engine in the UI
and produces IRS-form-shaped aggregations (W-2 per employee, 941 per entity per
quarter) — all as read-only rollups over the existing `payroll_lines` data.
No new computation, no new tax math: every figure already lives in the table.

## Scope

**In scope**
- Extend `build_client_detail.py` so the `payroll` key holds a real summary
  plus `w2` and `form941` rollups for the **latest calendar year present in
  `payroll_lines`**.
- A real Payroll tile in `dashboard.js` replacing the placeholder: per-year
  totals, a W-2 list (per employee), and a 941 list (per entity per quarter).
- pytest coverage for the new rollup helpers; headless render check for the tile.

**Out of scope**
- Any write/edit/posting — strictly read-only (same discipline as 3A/3B UI).
- PDF/printable form rendering — JSON output only; the tile is screen-only.
- Multi-year history selection in the UI — the generator emits one (latest) year.
- e-file, form validation, or filing-deadline logic beyond what 3A already shows.

## The forms are recoverable from withholding

`payroll_lines` columns: `gross, fed_wh, ss_ee, ss_er, medi_ee, medi_er,
addl_medi, state_wh, futa, suta, net`. Because the engine withheld at statutory
rates, every IRS box is recoverable by aggregation:

| Form box | Source |
|---|---|
| W-2 Box 1 (wages) | `SUM(gross)` (no pretax deductions in model) |
| W-2 Box 2 (fed income tax) | `SUM(fed_wh)` |
| W-2 Box 3 (SS wages) | `ROUND(SUM(ss_ee) / 0.062, 2)` — recovers the *capped* base |
| W-2 Box 4 (SS tax) | `SUM(ss_ee)` |
| W-2 Box 5 (Medicare wages) | `SUM(gross)` (uncapped) |
| W-2 Box 6 (Medicare tax) | `SUM(medi_ee) + SUM(addl_medi)` |
| W-2 Box 16 (state wages) | `SUM(gross)` |
| W-2 Box 17 (state income tax) | `SUM(state_wh)` |
| 941 Line 2 (wages) | `SUM(gross)` per entity/quarter |
| 941 Line 3 (fed income tax) | `SUM(fed_wh)` |
| 941 Line 5a (SS, ee+er) | `SUM(ss_ee + ss_er)` (tax); base = `SUM(ss_ee)/0.062` |
| 941 Line 5c (Medicare, ee+er) | `SUM(medi_ee + medi_er + addl_medi)` |
| 941 Line 10 (total liability) | `SUM(fed_wh + ss_ee + ss_er + medi_ee + medi_er + addl_medi)` |

The quarter key is `((month - 1) // 3) + 1` over `payroll_runs.pay_date`.

## Architecture

Follows the 3A/3B pipeline unchanged:

```
payroll_lines (per client)  --build_client_detail.py-->  public/clients/<slug>.json
       (JOIN payroll_runs for pay_date/entity, employees for name)   |
dashboard.js  renderClientDetail()  --reads d.payroll-->  Payroll tile
```

`payroll_lines` has no date of its own — it joins to `payroll_runs` (`pay_date`,
`entity_id`) and `employees` (`name`, `entity_id`). The W-2 group-by is
`employee_id`; the 941 group-by is `(entity_id, quarter)`.

## Output shape (`payroll` key)

When a client has payroll for the latest year:

```json
"payroll": {
  "status": "active",
  "year": 2026,
  "summary": {
    "employees": 3,
    "run_count": 11,
    "gross": 91234.56,
    "net": 71022.10,
    "fed_wh": 9876.54,
    "employee_fica": 6543.21,
    "employer_fica": 6543.21,
    "employer_tax_total": 7012.34
  },
  "w2": [
    {"employee": "Dana Whitlock", "entity": "Guitar Works",
     "box1_wages": 30461.54, "box2_fed_wh": 1834.12,
     "box3_ss_wages": 30461.54, "box4_ss_tax": 1888.62,
     "box5_medi_wages": 30461.54, "box6_medi_tax": 441.69,
     "box16_state_wages": 30461.54, "box17_state_tax": 707.26}
  ],
  "form941": [
    {"entity": "Guitar Works", "quarter": 1, "year": 2026,
     "wages": 91234.56, "fed_wh": 5478.30,
     "ss_tax": 11313.08, "medi_tax": 2645.80,
     "total_liability": 19437.18, "run_count": 6}
  ]
}
```

When a client has **no** payroll (e.g. J.B. Design, the solopreneur with no
employees): `{"status": "none", "message": "No payroll runs on file"}`.

## Reference data

- **Guitar Works** — 1 entity, 3 employees, biweekly runs → W-2 × 3, 941 × (1 entity × N quarters).
- **Harborside Group** — 3 entities, 6 employees → W-2 × 6, 941 × (3 entities × N quarters).
- **J.B. Design** — 0 employees → `status: none`.

"Latest calendar year present" = `MAX(substr(pay_date,1,4))` across the book.

## Testing

- pytest for `payroll_rollup` helpers: W-2 box identities (Box 3 = ss_ee/0.062;
  Box 5 = gross; Box 6 = medi_ee + addl_medi), 941 quarter bucketing, the
  `status:none` path, and the latest-year selection.
- Build the three real books, regenerate artifacts, assert Guitar Works yields
  3 W-2s and J.B. Design yields `status:none`.
- Headless render check: the Payroll tile renders summary + W-2 + 941 rows
  against the real `guitar-works.json`, and shows the empty state for jb-design.

## Out-of-scope deferrals (logged, not built)

- Printable/PDF W-2 and 941 forms.
- Multi-year selector in the UI.
- State-form equivalents (DE-9, etc.) and local taxes.
- Actual e-file payloads.
