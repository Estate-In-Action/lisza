# Spec 7 — Payroll Ingestion (Finished-Register Import)

**Date:** 2026-06-29
**Status:** Approved (operator: "then payroll ingestion")
**Predecessor:** Client Management (Spec 6)
**Relatives:** payroll_engine (gross-to-net calc), payroll_run (compute + post a run),
payroll_rollup (W-2/941 reads)

## Purpose

The existing payroll path *computes*: `payroll_run.run_payroll` derives each
employee's gross from `annual_salary`, runs `compute_paycheck`, and posts a
balanced GL entry. That path owns the math.

Ingestion is the **other door**. In the real bookkeeping workflow the numbers
are usually calculated *somewhere else* — a payroll provider (Gusto, ADP,
QuickBooks Payroll) or a prior bookkeeper — and arrive as a finished **register**:
per-employee figures already withheld and netted. The bookkeeper's job is to
*book* that register, not re-run it. Ingestion stores those external figures
verbatim and posts the same GL entry a computed run would, **without
recomputing**.

This keeps the two doors symmetric: a computed run and an ingested run produce
identical `payroll_runs` / `payroll_lines` rows and the same balanced entry, so
the W-2/941 rollups treat them the same.

## Where the data lives

No new tables. Ingestion writes the existing per-book payroll tables:

| Datum | Home | Why |
|-------|------|-----|
| Run header | per-book `payroll_runs` | same as a computed run |
| Per-employee figures | per-book `payroll_lines` | the register *is* these rows |
| GL entry + splits | per-book `entries`/`splits` | posted by the shared `payroll_run._post_entry` |

The account mapping is unchanged (reused, not re-implemented):
debits `555` gross / `556` employer tax; credits `245` fed WH, `246` FICA,
`247` state WH, `248` FUTA, `249` SUTA, `102` net.

## Trust model

Ingestion **trusts** the external numbers — that's the whole point — so it does
not recompute withholding. But "garbage in" is the real risk, so each line is
validated before anything is written:

- `gross` and `net` are required; the nine other money fields default to `0`.
- no negative figures.
- **internal consistency:** `net == gross − (fed_wh + ss_ee + medi_ee +
  addl_medi + state_wh)` within one cent. (Employer-side taxes `ss_er`,
  `medi_er`, `futa`, `suta` don't affect net — they're employer cost, booked to
  `556`/`246`/`248`/`249`.)
- each line resolves to exactly one active-or-inactive `employees` row in the
  target entity, by `employee_id` or by unique `name`.

Validation is all-or-nothing: if any line fails, nothing is written.

## Engine — `scripts/payroll_ingest.py`

Pure-Python, mirrors `payroll_run.py` conventions; reuses its `_post_entry` and
`_SUM_FIELDS`.

| Function | Behavior |
|----------|----------|
| `import_register(slug, entity_id, period_end, lines, *, pay_date=None, period_start=None) -> run_id` | validate + resolve every line, insert one `payroll_runs` row, post one balanced entry, insert one `payroll_lines` row per register line; returns `run_id` |
| `_resolve_employee(con, entity_id, line) -> int` | `employee_id` (must belong to entity) or unique `name`; raises on unknown/ambiguous |
| `_clean_line(line) -> dict` | coerce 11 money fields to rounded floats; enforce required/non-negative/net-consistency |

Defaults match `run_payroll`: `pay_date` → `period_end`; `period_start` →
`period_end − 13 days` (biweekly).

CLI `main()` → JSON on stdout:
`import <slug> --period-end <date> --register <json-array> [--pay-date]
[--period-start] [--entity <id>]`. `--entity` defaults to the book's default
entity. Like the other engines, `main()` catches `ValueError` (JSONDecodeError
is a subclass, so a malformed `--register` is caught too) → `{"error": msg}`
exit 0, so the route always gets JSON.

## Route wiring (`/api/lisza`, server-side only)

- **POST `mode=pr_ingest`** → body `{slug, period_end, register:[…], pay_date?,
  period_start?, entity?}` → spawn `python3 payroll_ingest.py import …` with
  `--register` carrying the JSON array; return its JSON.
- Reuse existing CORS + `slugOk`.

## Page wiring (`/lisza/console`, Payroll tab, server-side only)

Add an **Import register** panel to the Payroll section:
- client picker (slug), period-end / pay-date inputs.
- a register editor: rows of `employee + gross + fed_wh + ss_ee + ss_er +
  medi_ee + medi_er + addl_medi + state_wh + futa + suta + net`, with a live
  per-row net-check hint.
- submit → `pr_ingest`; on success show the new `run_id` and refresh the run
  list.

Styling reuses existing console tokens.

## Testing (`scripts/test_payroll_ingest.py`, TDD)

- imports a 2-employee register → one run, two lines, one balanced entry; the
  stored figures equal the register verbatim (no recompute).
- aggregate splits balance (dr == cr) and hit the expected accounts.
- rejects a line whose `net` doesn't match `gross − deductions`.
- rejects negative figures; rejects missing `gross`/`net`.
- resolves employee by name; rejects unknown and ambiguous names.
- `employee_id` from another entity is rejected.
- empty register is rejected; nothing is written on any validation failure.
- isolation: importing into one client doesn't touch another's book.

All tests isolate via a temp `LISZA_HOME` (monkeypatch) + repo `coa.csv`, like
`test_payroll_run.py`.

## Out of scope (deferred)

- **Hourly / time-punch computation** (regular + overtime → gross, then through
  `compute_paycheck`). That's a *compute* feature, a sibling of `payroll_run`,
  not ingestion — defer to its own cycle.
- CSV/spreadsheet upload parsing (register arrives as structured JSON now; file
  parsing later).
- De-duplication / idempotency keys across re-imported registers (operator
  reviews before booking; revisit if double-booking shows up).
