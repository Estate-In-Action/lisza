# Payroll Tile + W-2/941 Form Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the 3B payroll engine in the client-detail UI by emitting real W-2 (per employee) and 941 (per entity per quarter) rollups for the latest calendar year, and replacing the payroll placeholder tile with a real one.

**Architecture:** A new pure module `scripts/payroll_rollup.py` holds read-only aggregation helpers that take an open `sqlite3.Connection` and a year string. `build_client_detail.py` calls one orchestrator (`build_payroll(con)`) to populate the `payroll` key. The vanilla-JS `renderClientDetail` gains a real Payroll tile that handles both the `active` and `none` states. No new tax math — every IRS box is recovered from the withholding columns already in `payroll_lines`.

**Tech Stack:** Python 3 + stdlib `sqlite3`, pytest, vanilla JS (CommonJS-exported for a Node headless render check), no new dependencies.

**Reference spec:** `docs/specs/2026-06-29-payroll-tile-forms-design.md`

---

## File Structure

- **Create** `scripts/payroll_rollup.py` — pure aggregation helpers + `build_payroll(con)` orchestrator. One responsibility: turn `payroll_lines`/`payroll_runs`/`employees`/`entities` rows into the `payroll` JSON block.
- **Create** `scripts/test_payroll_rollup.py` — pytest for the helpers against in-memory fixtures.
- **Modify** `scripts/build_client_detail.py` — import `payroll_rollup`, call `build_payroll(con)` inside the open-connection block, replace the placeholder at lines 155-156.
- **Modify** `scripts/test_build_client_detail.py:132` — flip the stale `status == "pending"` assertion to `"none"` (a freshly-registered client has no payroll).
- **Modify** `public/dashboard.js` — replace the payroll stub (lines 212-214) with a real tile; add `payrollTile(p)` helper; export it.
- **Modify** `scripts/render_detail_check.js` — extend to assert the Payroll tile shows real content for guitar-works and the empty state for jb-design.

### Column reference (from `payroll_lines`)
`gross, fed_wh, ss_ee, ss_er, medi_ee, medi_er, addl_medi, state_wh, futa, suta, net`.
`payroll_lines` has no date — join `payroll_runs` (`pay_date`, `entity_id`) via `run_id`, and `employees` (`name`, `entity_id`) via `employee_id`.

### Box/line recovery (no new math)
- W-2 Box1 wages = `SUM(gross)`; Box2 = `SUM(fed_wh)`; Box3 SS wages = `round(SUM(ss_ee)/0.062, 2)`; Box4 = `SUM(ss_ee)`; Box5 Medicare wages = `SUM(gross)`; Box6 = `SUM(medi_ee)+SUM(addl_medi)`; Box16 = `SUM(gross)`; Box17 = `SUM(state_wh)`.
- 941 wages = `SUM(gross)`; fed_wh = `SUM(fed_wh)`; ss_tax = `SUM(ss_ee+ss_er)`; medi_tax = `SUM(medi_ee+medi_er+addl_medi)`; total_liability = `SUM(fed_wh+ss_ee+ss_er+medi_ee+medi_er+addl_medi)`.
- Quarter = `((month - 1) // 3) + 1` over `payroll_runs.pay_date`.

---

## Task 1: Latest-year selector + status:none orchestrator skeleton

**Files:**
- Create: `scripts/payroll_rollup.py`
- Test: `scripts/test_payroll_rollup.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_payroll_rollup.py
import sqlite3

import payroll_rollup as pr


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE entities(id INTEGER PRIMARY KEY, name TEXT, type TEXT,
                              is_default INT, active INT);
        CREATE TABLE employees(id INTEGER PRIMARY KEY, entity_id INT, name TEXT,
                               active INT);
        CREATE TABLE payroll_runs(id INTEGER PRIMARY KEY, entity_id INT,
                                  period_start TEXT, period_end TEXT, pay_date TEXT);
        CREATE TABLE payroll_lines(
            id INTEGER PRIMARY KEY, run_id INT, employee_id INT, entry_id INT,
            gross REAL, fed_wh REAL, ss_ee REAL, ss_er REAL, medi_ee REAL,
            medi_er REAL, addl_medi REAL, state_wh REAL, futa REAL, suta REAL,
            net REAL);
        """)
    con.commit()
    return con


def test_latest_year_none_when_no_runs():
    con = _con()
    assert pr.latest_payroll_year(con) is None


def test_latest_year_picks_max():
    con = _con()
    con.executescript(
        """INSERT INTO payroll_runs(id,entity_id,pay_date)
             VALUES(1,1,'2024-03-15'),(2,1,'2026-01-10'),(3,1,'2025-07-01');""")
    con.commit()
    assert pr.latest_payroll_year(con) == "2026"


def test_build_payroll_status_none_when_empty():
    con = _con()
    out = pr.build_payroll(con)
    assert out == {"status": "none", "message": "No payroll runs on file"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && python3 -m pytest test_payroll_rollup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'payroll_rollup'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/payroll_rollup.py
#!/usr/bin/env python3
"""Read-only IRS-form rollups (W-2, 941) over payroll_lines. No tax math —
every figure is recovered from withholding already in the table."""
from __future__ import annotations

import sqlite3


def latest_payroll_year(con: sqlite3.Connection) -> str | None:
    row = con.execute(
        "SELECT MAX(substr(pay_date, 1, 4)) FROM payroll_runs").fetchone()
    return row[0] if row and row[0] else None


def build_payroll(con: sqlite3.Connection) -> dict:
    year = latest_payroll_year(con)
    if year is None:
        return {"status": "none", "message": "No payroll runs on file"}
    raise NotImplementedError  # filled in Task 4
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts && python3 -m pytest test_payroll_rollup.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/payroll_rollup.py scripts/test_payroll_rollup.py
git commit -m "feat(lisza): payroll_rollup latest-year selector + status:none skeleton"
```

---

## Task 2: W-2 rollup (per employee, box identities)

**Files:**
- Modify: `scripts/payroll_rollup.py`
- Test: `scripts/test_payroll_rollup.py`

- [ ] **Step 1: Write the failing test**

Add a shared seeding helper and the W-2 test to `test_payroll_rollup.py`:

```python
def _seed_one_employee(con):
    """One entity, one employee, two runs in 2026. Two lines so SUMs are
    non-trivial: gross 1000+1000, ss_ee 62+62, medi_ee 14.5+14.5,
    addl_medi 0, fed_wh 100+120, state_wh 30+30."""
    con.executescript(
        """INSERT INTO entities(id,name,type,is_default,active)
             VALUES(1,'Guitar Works','company',1,1);
           INSERT INTO employees(id,entity_id,name,active)
             VALUES(1,1,'Dana Whitlock',1);
           INSERT INTO payroll_runs(id,entity_id,period_start,period_end,pay_date)
             VALUES(1,1,'2026-01-01','2026-01-14','2026-01-15'),
                   (2,1,'2026-01-15','2026-01-28','2026-01-29');
           INSERT INTO payroll_lines(run_id,employee_id,gross,fed_wh,ss_ee,ss_er,
                                     medi_ee,medi_er,addl_medi,state_wh,futa,suta,net)
             VALUES(1,1,1000,100,62,62,14.5,14.5,0,30,4.2,21,793.5),
                   (2,1,1000,120,62,62,14.5,14.5,0,30,4.2,21,773.5);""")
    con.commit()


def test_w2_box_identities():
    con = _con()
    _seed_one_employee(con)
    rows = pr.w2_rollup(con, "2026")
    assert len(rows) == 1
    w = rows[0]
    assert w["employee"] == "Dana Whitlock"
    assert w["entity"] == "Guitar Works"
    assert w["box1_wages"] == 2000.0          # SUM(gross)
    assert w["box2_fed_wh"] == 220.0          # SUM(fed_wh)
    assert w["box3_ss_wages"] == 2000.0       # round(SUM(ss_ee)/0.062, 2) = 124/0.062
    assert w["box4_ss_tax"] == 124.0          # SUM(ss_ee)
    assert w["box5_medi_wages"] == 2000.0     # SUM(gross)
    assert w["box6_medi_tax"] == 29.0         # SUM(medi_ee)+SUM(addl_medi)
    assert w["box16_state_wages"] == 2000.0   # SUM(gross)
    assert w["box17_state_tax"] == 60.0       # SUM(state_wh)


def test_w2_only_includes_requested_year():
    con = _con()
    _seed_one_employee(con)
    con.executescript(
        """INSERT INTO payroll_runs(id,entity_id,pay_date)
             VALUES(9,1,'2025-06-01');
           INSERT INTO payroll_lines(run_id,employee_id,gross,fed_wh,ss_ee,ss_er,
                                     medi_ee,medi_er,addl_medi,state_wh,futa,suta,net)
             VALUES(9,1,5000,500,310,310,72.5,72.5,0,150,21,105,3967);""")
    con.commit()
    rows = pr.w2_rollup(con, "2026")
    assert rows[0]["box1_wages"] == 2000.0    # 2025 run excluded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && python3 -m pytest test_payroll_rollup.py::test_w2_box_identities -v`
Expected: FAIL — `AttributeError: module 'payroll_rollup' has no attribute 'w2_rollup'`

- [ ] **Step 3: Write minimal implementation**

Add to `payroll_rollup.py`:

```python
def w2_rollup(con: sqlite3.Connection, year: str) -> list[dict]:
    rows = con.execute(
        """SELECT e.name AS employee, ent.name AS entity,
                  SUM(pl.gross)     AS gross,
                  SUM(pl.fed_wh)    AS fed_wh,
                  SUM(pl.ss_ee)     AS ss_ee,
                  SUM(pl.medi_ee)   AS medi_ee,
                  SUM(pl.addl_medi) AS addl_medi,
                  SUM(pl.state_wh)  AS state_wh
           FROM payroll_lines pl
           JOIN payroll_runs pr ON pr.id = pl.run_id
           JOIN employees e ON e.id = pl.employee_id
           JOIN entities ent ON ent.id = e.entity_id
           WHERE substr(pr.pay_date, 1, 4) = ?
           GROUP BY pl.employee_id
           ORDER BY e.name""", (year,)).fetchall()
    out = []
    for r in rows:
        gross = round(r["gross"], 2)
        out.append({
            "employee": r["employee"],
            "entity": r["entity"],
            "box1_wages": gross,
            "box2_fed_wh": round(r["fed_wh"], 2),
            "box3_ss_wages": round(r["ss_ee"] / 0.062, 2),
            "box4_ss_tax": round(r["ss_ee"], 2),
            "box5_medi_wages": gross,
            "box6_medi_tax": round(r["medi_ee"] + r["addl_medi"], 2),
            "box16_state_wages": gross,
            "box17_state_tax": round(r["state_wh"], 2),
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts && python3 -m pytest test_payroll_rollup.py -v`
Expected: PASS (all W-2 tests + Task 1 tests pass)

- [ ] **Step 5: Commit**

```bash
git add scripts/payroll_rollup.py scripts/test_payroll_rollup.py
git commit -m "feat(lisza): W-2 per-employee rollup with box identities"
```

---

## Task 3: 941 rollup (per entity per quarter) + summary

**Files:**
- Modify: `scripts/payroll_rollup.py`
- Test: `scripts/test_payroll_rollup.py`

- [ ] **Step 1: Write the failing test**

Add to `test_payroll_rollup.py` (reuses `_seed_one_employee`, both runs are Q1):

```python
def test_form941_quarter_bucketing_and_lines():
    con = _con()
    _seed_one_employee(con)
    # add a Q2 run for the same entity to prove bucketing splits quarters
    con.executescript(
        """INSERT INTO payroll_runs(id,entity_id,pay_date)
             VALUES(3,1,'2026-04-10');
           INSERT INTO payroll_lines(run_id,employee_id,gross,fed_wh,ss_ee,ss_er,
                                     medi_ee,medi_er,addl_medi,state_wh,futa,suta,net)
             VALUES(3,1,800,80,49.6,49.6,11.6,11.6,0,24,3.36,16.8,635.84);""")
    con.commit()
    rows = pr.form941_rollup(con, "2026")
    assert len(rows) == 2                       # Q1 and Q2
    q1 = next(r for r in rows if r["quarter"] == 1)
    assert q1["entity"] == "Guitar Works"
    assert q1["year"] == 2026
    assert q1["wages"] == 2000.0                # SUM(gross) over the two Q1 runs
    assert q1["fed_wh"] == 220.0
    assert q1["ss_tax"] == 248.0               # SUM(ss_ee+ss_er) = (62+62)*2
    assert q1["medi_tax"] == 58.0              # SUM(medi_ee+medi_er+addl_medi)
    assert q1["total_liability"] == 526.0      # 220 + 248 + 58
    assert q1["run_count"] == 2
    q2 = next(r for r in rows if r["quarter"] == 2)
    assert q2["wages"] == 800.0
    assert q2["run_count"] == 1


def test_summary_totals():
    con = _con()
    _seed_one_employee(con)
    s = pr.payroll_summary(con, "2026")
    assert s["employees"] == 1
    assert s["run_count"] == 2
    assert s["gross"] == 2000.0
    assert s["net"] == 1567.0                  # 793.5 + 773.5
    assert s["fed_wh"] == 220.0
    assert s["employee_fica"] == 153.0         # SUM(ss_ee+medi_ee+addl_medi)=124+29
    assert s["employer_fica"] == 153.0         # SUM(ss_er+medi_er)=124+29
    assert s["employer_tax_total"] == 203.4    # employer_fica + SUM(futa+suta)=153+8.4+42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && python3 -m pytest test_payroll_rollup.py::test_form941_quarter_bucketing_and_lines -v`
Expected: FAIL — `AttributeError: module 'payroll_rollup' has no attribute 'form941_rollup'`

- [ ] **Step 3: Write minimal implementation**

Add to `payroll_rollup.py`:

```python
def form941_rollup(con: sqlite3.Connection, year: str) -> list[dict]:
    rows = con.execute(
        """SELECT ent.name AS entity,
                  ((CAST(substr(pr.pay_date, 6, 2) AS INTEGER) - 1) / 3) + 1 AS quarter,
                  SUM(pl.gross)     AS wages,
                  SUM(pl.fed_wh)    AS fed_wh,
                  SUM(pl.ss_ee + pl.ss_er)                       AS ss_tax,
                  SUM(pl.medi_ee + pl.medi_er + pl.addl_medi)    AS medi_tax,
                  SUM(pl.fed_wh + pl.ss_ee + pl.ss_er
                      + pl.medi_ee + pl.medi_er + pl.addl_medi)  AS total_liability,
                  COUNT(DISTINCT pr.id) AS run_count
           FROM payroll_lines pl
           JOIN payroll_runs pr ON pr.id = pl.run_id
           JOIN entities ent ON ent.id = pr.entity_id
           WHERE substr(pr.pay_date, 1, 4) = ?
           GROUP BY pr.entity_id, quarter
           ORDER BY ent.name, quarter""", (year,)).fetchall()
    return [{
        "entity": r["entity"],
        "quarter": int(r["quarter"]),
        "year": int(year),
        "wages": round(r["wages"], 2),
        "fed_wh": round(r["fed_wh"], 2),
        "ss_tax": round(r["ss_tax"], 2),
        "medi_tax": round(r["medi_tax"], 2),
        "total_liability": round(r["total_liability"], 2),
        "run_count": r["run_count"],
    } for r in rows]


def payroll_summary(con: sqlite3.Connection, year: str) -> dict:
    r = con.execute(
        """SELECT COUNT(DISTINCT pl.employee_id) AS employees,
                  COUNT(DISTINCT pl.run_id)       AS run_count,
                  SUM(pl.gross)  AS gross,
                  SUM(pl.net)    AS net,
                  SUM(pl.fed_wh) AS fed_wh,
                  SUM(pl.ss_ee + pl.medi_ee + pl.addl_medi) AS employee_fica,
                  SUM(pl.ss_er + pl.medi_er)                AS employer_fica,
                  SUM(pl.ss_er + pl.medi_er + pl.futa + pl.suta) AS employer_tax_total
           FROM payroll_lines pl
           JOIN payroll_runs pr ON pr.id = pl.run_id
           WHERE substr(pr.pay_date, 1, 4) = ?""", (year,)).fetchone()
    return {
        "employees": r["employees"] or 0,
        "run_count": r["run_count"] or 0,
        "gross": round(r["gross"] or 0.0, 2),
        "net": round(r["net"] or 0.0, 2),
        "fed_wh": round(r["fed_wh"] or 0.0, 2),
        "employee_fica": round(r["employee_fica"] or 0.0, 2),
        "employer_fica": round(r["employer_fica"] or 0.0, 2),
        "employer_tax_total": round(r["employer_tax_total"] or 0.0, 2),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts && python3 -m pytest test_payroll_rollup.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/payroll_rollup.py scripts/test_payroll_rollup.py
git commit -m "feat(lisza): 941 per-entity/quarter rollup + payroll summary"
```

---

## Task 4: Wire orchestrator (build_payroll active path)

**Files:**
- Modify: `scripts/payroll_rollup.py`
- Test: `scripts/test_payroll_rollup.py`

- [ ] **Step 1: Write the failing test**

Add to `test_payroll_rollup.py`:

```python
def test_build_payroll_active_block():
    con = _con()
    _seed_one_employee(con)
    out = pr.build_payroll(con)
    assert out["status"] == "active"
    assert out["year"] == 2026
    assert out["summary"]["employees"] == 1
    assert len(out["w2"]) == 1
    assert out["w2"][0]["employee"] == "Dana Whitlock"
    assert len(out["form941"]) == 1            # single Q1 entity
    assert out["form941"][0]["quarter"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && python3 -m pytest test_payroll_rollup.py::test_build_payroll_active_block -v`
Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Write minimal implementation**

Replace the `raise NotImplementedError` line in `build_payroll`:

```python
def build_payroll(con: sqlite3.Connection) -> dict:
    year = latest_payroll_year(con)
    if year is None:
        return {"status": "none", "message": "No payroll runs on file"}
    return {
        "status": "active",
        "year": int(year),
        "summary": payroll_summary(con, year),
        "w2": w2_rollup(con, year),
        "form941": form941_rollup(con, year),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts && python3 -m pytest test_payroll_rollup.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/payroll_rollup.py scripts/test_payroll_rollup.py
git commit -m "feat(lisza): build_payroll active path assembles summary+w2+941"
```

---

## Task 5: Wire into build_client_detail.py

**Files:**
- Modify: `scripts/build_client_detail.py` (import at top; call inside open-connection block; replace placeholder lines 155-156)
- Modify: `scripts/test_build_client_detail.py:132`

- [ ] **Step 1: Update the stale assertion in test_build_client_detail.py**

The end-to-end fixture registers a client with no payroll, so its status is now `none`. Change line 132 from:

```python
    assert d["payroll"]["status"] == "pending"
```

to:

```python
    assert d["payroll"]["status"] == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && python3 -m pytest test_build_client_detail.py::test_build_detail_end_to_end -v`
Expected: FAIL — `assert 'pending' == 'none'` (placeholder still emits "pending")

- [ ] **Step 3: Wire payroll_rollup into the generator**

In `build_client_detail.py`, add the import alongside the existing `import tenancy` (line 10):

```python
import payroll_rollup
import tenancy
```

Inside `build_client_detail`, in the `try:` block that holds the open connection (after `monthly = monthly_trend(...)` on line 120), add:

```python
        payroll = payroll_rollup.build_payroll(con)
```

Then replace the placeholder dict (lines 155-156):

```python
        "payroll": {"status": "pending",
                    "message": "Payroll engine ships in 3B/3C"},
```

with:

```python
        "payroll": payroll,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scripts && python3 -m pytest test_build_client_detail.py -v`
Expected: PASS (end-to-end now sees `status == "none"`; all other tests unaffected)

- [ ] **Step 5: Commit**

```bash
git add scripts/build_client_detail.py scripts/test_build_client_detail.py
git commit -m "feat(lisza): populate payroll key from payroll_rollup in client detail"
```

---

## Task 6: Real Payroll tile in dashboard.js

**Files:**
- Modify: `public/dashboard.js` (add `payrollTile(p)`; replace stub lines 212-214; add to exports line 261)

- [ ] **Step 1: Add the payrollTile helper**

Insert this function just above `renderClientDetail` (before line 176):

```javascript
function w2Rows(list) {
  if (!list || !list.length) return `<div class="muted">No W-2s</div>`;
  return list.map(w =>
    `<div class="kv"><span>${esc(w.employee)} ` +
    `<span class="muted">${esc(w.entity)}</span></span>` +
    `<span class="money">${money(w.box1_wages)} ` +
    `<span class="muted">wages</span> · ` +
    `${money(w.box2_fed_wh)} fed</span></div>`).join("");
}

function form941Rows(list) {
  if (!list || !list.length) return `<div class="muted">No 941 periods</div>`;
  return list.map(f =>
    `<div class="kv"><span>${esc(f.entity)} ` +
    `<span class="muted">Q${esc(f.quarter)} ${esc(f.year)}</span></span>` +
    `<span class="money">${money(f.total_liability)} ` +
    `<span class="muted">liability</span></span></div>`).join("");
}

function payrollTile(p) {
  if (!p || p.status !== "active") {
    return `<div class="card payroll-stub"><h3>Payroll</h3>` +
      `<div class="muted">${esc((p && p.message) || "No payroll runs on file")}</div></div>`;
  }
  const s = p.summary;
  return `<div class="card"><h3>Payroll <span class="muted">${esc(p.year)}</span></h3>` +
    kv("Employees", s.employees) +
    kv("Pay runs", s.run_count) +
    kv("Gross", money(s.gross)) +
    kv("Net", money(s.net)) +
    kv("Employer tax", money(s.employer_tax_total)) +
    `<h4>W-2 (per employee)</h4>${w2Rows(p.w2)}` +
    `<h4>941 (per entity / quarter)</h4>${form941Rows(p.form941)}` +
    `</div>`;
}
```

- [ ] **Step 2: Replace the stub in renderClientDetail**

Replace lines 212-214 (the `<div class="card payroll-stub">…</div>` block) with:

```javascript
        ${payrollTile(d.payroll)}
```

- [ ] **Step 3: Export payrollTile**

Change the exports line (line 261) from:

```javascript
  module.exports = { esc, money, kv, renderClientDetail, renderTiles, parseHash };
```

to:

```javascript
  module.exports = { esc, money, kv, renderClientDetail, renderTiles, parseHash, payrollTile };
```

- [ ] **Step 4: Commit**

```bash
git add public/dashboard.js
git commit -m "feat(lisza): real payroll tile (summary + W-2 + 941) replacing stub"
```

---

## Task 7: Regenerate artifacts + headless render checks

**Files:**
- Modify: `scripts/render_detail_check.js`
- Regenerate: `public/clients/*.json`

- [ ] **Step 1: Extend render_detail_check.js**

After the existing tile-presence loop (after line 23), add payroll-specific assertions that branch on the client's payroll status:

```javascript
const p = detail.payroll || {};
if (p.status === "active") {
  assert(html.includes("W-2 (per employee)"), "W-2 section renders for active payroll");
  assert(html.includes("941 (per entity / quarter)"), "941 section renders for active payroll");
  assert(p.w2 && p.w2.length > 0, "active payroll has at least one W-2");
} else {
  assert(html.includes("No payroll runs on file"), "empty payroll state renders");
}
```

- [ ] **Step 2: Regenerate all client artifacts**

Run: `cd scripts && python3 build_client_detail.py`
Expected: `wrote 3 client detail files`

- [ ] **Step 3: Verify the JSON shape landed**

Run:
```bash
cd /home/workspace/lisza-3c
python3 -c "import json; d=json.load(open('public/clients/guitar-works.json')); p=d['payroll']; print(p['status'], p['year'], len(p['w2']), 'w2s', len(p['form941']), '941s')"
python3 -c "import json; d=json.load(open('public/clients/jb-design.json')); print(d['payroll'])"
```
Expected: `active 2026 3 w2s 2 941s` for guitar-works; `{'status': 'none', 'message': 'No payroll runs on file'}` for jb-design.

- [ ] **Step 4: Run both headless render checks**

Run:
```bash
cd /home/workspace/lisza-3c/scripts
node render_detail_check.js guitar-works
node render_detail_check.js jb-design
```
Expected: both print `OK <slug>: all 5 tiles render (… bytes)` with no FAIL lines.

- [ ] **Step 5: Run the full Python suite**

Run: `cd /home/workspace/lisza-3c/scripts && python3 -m pytest test_*.py -q`
Expected: all tests pass (existing suite + new `test_payroll_rollup.py`).

- [ ] **Step 6: Commit**

```bash
git add scripts/render_detail_check.js public/clients
git commit -m "test(lisza): payroll render checks; regenerate client artifacts"
```

---

## Self-Review

**Spec coverage:**
- Extend generator with real `payroll` + `w2` + `form941` for latest year → Tasks 1-5. ✓
- Real Payroll tile replacing placeholder (per-year totals, W-2 list, 941 list) → Task 6. ✓
- pytest for rollup helpers (box identities, quarter bucketing, status:none, latest-year) → Tasks 1-4 (`test_payroll_rollup.py`). ✓
- Build the three real books, regenerate, assert Guitar Works = 3 W-2s, J.B. Design = status:none → Task 7 Step 3. ✓
- Headless render check: tile renders summary+W-2+941 for guitar-works, empty state for jb-design → Task 7 Steps 1, 4. ✓
- Read-only / no new tax math → all helpers are `SELECT`-only aggregations. ✓
- Out of scope (PDF, multi-year UI, e-file) → not built. ✓

**Type consistency:** `build_payroll`, `latest_payroll_year`, `w2_rollup`, `form941_rollup`, `payroll_summary` named identically across all tasks. Output keys (`box1_wages`…`box17_state_tax`; `wages/fed_wh/ss_tax/medi_tax/total_liability/run_count`; `employees/run_count/gross/net/fed_wh/employee_fica/employer_fica/employer_tax_total`) match the spec output shape and the tile reads exactly those keys.

**Placeholder scan:** No TBD/TODO; every code step shows complete code; expected command outputs given.

**Note on `money()`:** the dashboard's `money()` rounds to whole dollars (no cents) — consistent with every other tile. Exact cents live in the JSON; the screen tile shows rounded dollars by design (spec is screen-only, no PDF).
