# Bookkeeper Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the bookkeeper a browser dashboard of all active clients (tile / list / rolodex) generated from the registry cache as a static `dashboard.json` + vanilla-JS front-end.

**Architecture:** A pure-Python generator (`build_dashboard.py`) projects the `lisza.db` registry cache into `public/dashboard.json`; a no-framework front-end (`public/index.html` + `public/dashboard.js`) fetches it and renders three layouts with client-side layout/field toggles persisted in `localStorage`. To keep the generator a pure registry read, `refresh_summary()` is extended to cache two per-book fields (`entity_count`, `next_filing_due`).

**Tech Stack:** Python 3 stdlib (`sqlite3`, `json`, `datetime`), `pytest`, vanilla HTML/CSS/JS. No new dependencies.

**Spec:** `docs/specs/2026-06-29-bookkeeper-dashboard-design.md`

**Working dir for all commands:** `/home/workspace/LISZA/scripts` (pytest discovers `test_*.py` there). Front-end files live in `/home/workspace/LISZA/public/`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `scripts/tenancy.py` (modify) | Add `compute_next_filing_due()` pure helper; add `entity_count` column to registry `client_summary` (additive guard); extend `refresh_summary()` to cache `entity_count` + `next_filing_due` |
| `scripts/test_tenancy.py` (modify) | Tests for the helper + the cache extension |
| `scripts/build_dashboard.py` (create) | `build_dashboard()` returns the dict; `write_dashboard()` writes `public/dashboard.json` |
| `scripts/test_build_dashboard.py` (create) | Generator TDD tests |
| `public/index.html` (create) | Front-end shell + styles + controls |
| `public/dashboard.js` (create) | Fetch + render tile/list/rolodex + toggles + localStorage |
| `public/dashboard.json` (generate) | Generated artifact, committed |

---

## Task 1: `compute_next_filing_due()` pure helper

**Files:**
- Modify: `scripts/tenancy.py` (add helper + imports near top)
- Test: `scripts/test_tenancy.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_tenancy.py`:

```python
from datetime import date


def test_next_filing_due_quarterly_monthly_annual():
    # quarterly: next statutory quarter-end filing strictly after the reference
    assert tenancy.compute_next_filing_due("quarterly", date(2026, 6, 9)) == date(2026, 7, 31)
    assert tenancy.compute_next_filing_due("quarterly", date(2026, 11, 2)) == date(2027, 1, 31)
    # monthly: last day of the month following the reference month
    assert tenancy.compute_next_filing_due("monthly", date(2026, 6, 9)) == date(2026, 7, 31)
    assert tenancy.compute_next_filing_due("monthly", date(2026, 12, 3)) == date(2027, 1, 31)
    # annual: Apr 15 of the year after the next fiscal-year-end on/after reference
    assert tenancy.compute_next_filing_due("annual", date(2026, 6, 9), "12-31") == date(2027, 4, 15)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_next_filing_due_quarterly_monthly_annual -v`
Expected: FAIL with `AttributeError: module 'tenancy' has no attribute 'compute_next_filing_due'`

- [ ] **Step 3: Write minimal implementation**

In `scripts/tenancy.py`, change the datetime import at the top. The file currently imports only `from pathlib import Path` and stdlib; add `from datetime import date, timedelta` after the `import uuid` line (line ~12). Then add this function just above the `CASH_ACCOUNTS = (...)` line (~line 180):

```python
def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def compute_next_filing_due(cadence: str, reference: date,
                            fiscal_year_end: str = "12-31") -> date:
    """Next filing deadline strictly after `reference`, per cadence.

    monthly   -> last day of the month following the reference month.
    quarterly -> next of Apr 30 / Jul 31 / Oct 31 / Jan 31 after reference.
    annual    -> Apr 15 of the year after the next fiscal-year-end on/after ref.
    """
    cad = (cadence or "quarterly").lower()
    if cad == "monthly":
        ny, nm = (reference.year + 1, 1) if reference.month == 12 \
            else (reference.year, reference.month + 1)
        return _last_day_of_month(ny, nm)
    if cad == "annual":
        mm, dd = (int(x) for x in fiscal_year_end.split("-"))
        fye = date(reference.year, mm, dd)
        if fye < reference:
            fye = date(reference.year + 1, mm, dd)
        return date(fye.year + 1, 4, 15)
    # quarterly (default): sweep two years so we always find a date after ref
    candidates = []
    for y in (reference.year, reference.year + 1):
        candidates += [date(y, 4, 30), date(y, 7, 31), date(y, 10, 31),
                       date(y + 1, 1, 31)]
    for c in sorted(candidates):
        if c > reference:
            return c
    raise AssertionError("unreachable: two-year sweep always yields a date")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_next_filing_due_quarterly_monthly_annual -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/LISZA && git add scripts/tenancy.py scripts/test_tenancy.py && git commit -m "feat(lisza): compute_next_filing_due helper (monthly/quarterly/annual)"
```

---

## Task 2: Cache `entity_count` + `next_filing_due` in the registry

**Files:**
- Modify: `scripts/tenancy.py` — add `entity_count` to `REGISTRY_SCHEMA` `client_summary`; add `_ensure_registry_columns()`; call it in `registry_db()`; extend `refresh_summary()`
- Test: `scripts/test_tenancy.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_tenancy.py`:

```python
import sqlite3
import client_profiles
import seed_client


def test_refresh_summary_caches_entity_count_and_filing_due(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="jb-design", display_name="J.B. Design",
                            entity_type="sole_prop", filing_cadence="annual")
    seed_client.seed(client_profiles.JB_DESIGN, slug="jb-design")
    tenancy.register_client(slug="harborside-group", display_name="Harborside Group",
                            entity_type="llc", filing_cadence="quarterly")
    seed_client.seed(client_profiles.HARBORSIDE_GROUP, slug="harborside-group")
    tenancy.refresh_all()
    reg = sqlite3.connect(tenancy.registry_path())
    reg.row_factory = sqlite3.Row
    rows = {r["slug"]: r for r in reg.execute(
        "SELECT c.slug, c.next_filing_due, s.entity_count "
        "FROM clients c JOIN client_summary s ON s.client_id=c.client_id").fetchall()}
    reg.close()
    assert rows["jb-design"]["entity_count"] == 1
    assert rows["harborside-group"]["entity_count"] == 3
    assert rows["jb-design"]["next_filing_due"] is not None
    assert rows["harborside-group"]["next_filing_due"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_refresh_summary_caches_entity_count_and_filing_due -v`
Expected: FAIL with `sqlite3.OperationalError: no such column: s.entity_count`

- [ ] **Step 3: Write minimal implementation**

(3a) In `REGISTRY_SCHEMA` (in `scripts/tenancy.py`), add `entity_count` to the `client_summary` table so fresh registries get it. Change:

```python
CREATE TABLE IF NOT EXISTS client_summary (
    client_id   TEXT PRIMARY KEY REFERENCES clients(client_id),
    as_of       TEXT,
    cash        REAL, open_ar REAL, open_ap REAL,
    ar_count    INTEGER, ap_count INTEGER, last_entry_date TEXT
);
```

to:

```python
CREATE TABLE IF NOT EXISTS client_summary (
    client_id   TEXT PRIMARY KEY REFERENCES clients(client_id),
    as_of       TEXT,
    cash        REAL, open_ar REAL, open_ap REAL,
    ar_count    INTEGER, ap_count INTEGER, last_entry_date TEXT,
    entity_count INTEGER
);
```

(3b) Add an additive guard for existing registries. Just below the `registry_db()` function in `scripts/tenancy.py`, add:

```python
def _ensure_registry_columns(con: sqlite3.Connection) -> None:
    if not book_schema._has_column(con, "client_summary", "entity_count"):
        con.execute("ALTER TABLE client_summary ADD COLUMN entity_count INTEGER")
```

Then in `registry_db()`, call it after the `executescript`. Change:

```python
def registry_db() -> Path:
    path = registry_path()
    con = sqlite3.connect(path)
    con.executescript(REGISTRY_SCHEMA)
    con.commit()
    con.close()
    return path
```

to:

```python
def registry_db() -> Path:
    path = registry_path()
    con = sqlite3.connect(path)
    con.executescript(REGISTRY_SCHEMA)
    _ensure_registry_columns(con)
    con.commit()
    con.close()
    return path
```

(3c) Extend `refresh_summary()` to read the two new values from the book and write them. Replace the body between `last_entry = con.execute(...)` and `con.close()` plus the registry writes. The current code is:

```python
    last_entry = con.execute(
        "SELECT MAX(entry_date) FROM entries WHERE status='posted'").fetchone()[0]
    cid = con.execute("SELECT client_id FROM client_profile").fetchone()[0]
    con.close()

    registry_db()
    reg = sqlite3.connect(registry_path())
    reg.execute(
        """INSERT OR REPLACE INTO client_summary
           (client_id, as_of, cash, open_ar, open_ap, ar_count, ap_count, last_entry_date)
           VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?)""",
        (cid, cash, open_ar, open_ap, ar_count, ap_count, last_entry))
    reg.execute("UPDATE clients SET last_close_date=? WHERE client_id=?",
                (last_entry, cid))
    reg.commit()
    reg.close()
    return {"cash": cash, "open_ar": open_ar, "open_ap": open_ap}
```

Replace it with:

```python
    last_entry = con.execute(
        "SELECT MAX(entry_date) FROM entries WHERE status='posted'").fetchone()[0]
    entity_count = con.execute(
        "SELECT COUNT(*) FROM entities WHERE active=1").fetchone()[0]
    cid, cadence, fye = con.execute(
        "SELECT client_id, filing_cadence, fiscal_year_end FROM client_profile"
    ).fetchone()
    con.close()

    next_due = None
    if last_entry:
        next_due = compute_next_filing_due(
            cadence, date.fromisoformat(last_entry), fye or "12-31").isoformat()

    registry_db()
    reg = sqlite3.connect(registry_path())
    reg.execute(
        """INSERT OR REPLACE INTO client_summary
           (client_id, as_of, cash, open_ar, open_ap, ar_count, ap_count,
            last_entry_date, entity_count)
           VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)""",
        (cid, cash, open_ar, open_ap, ar_count, ap_count, last_entry, entity_count))
    reg.execute("UPDATE clients SET last_close_date=?, next_filing_due=? WHERE client_id=?",
                (last_entry, next_due, cid))
    reg.commit()
    reg.close()
    return {"cash": cash, "open_ar": open_ar, "open_ap": open_ap,
            "entity_count": entity_count, "next_filing_due": next_due}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -v`
Expected: PASS (all tenancy tests, including the new one and the pre-existing 8)

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/LISZA && git add scripts/tenancy.py scripts/test_tenancy.py && git commit -m "feat(lisza): cache entity_count + next_filing_due in registry summary"
```

---

## Task 3: `build_dashboard()` generator (returns the dict)

**Files:**
- Create: `scripts/build_dashboard.py`
- Test: `scripts/test_build_dashboard.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/test_build_dashboard.py`:

```python
import client_profiles
import seed_client
import tenancy
import build_dashboard


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="jb-design", display_name="J.B. Design",
                            entity_type="sole_prop", filing_cadence="annual")
    seed_client.seed(client_profiles.JB_DESIGN, slug="jb-design")
    tenancy.register_client(slug="harborside-group", display_name="Harborside Group",
                            entity_type="llc", filing_cadence="quarterly")
    seed_client.seed(client_profiles.HARBORSIDE_GROUP, slug="harborside-group")
    tenancy.refresh_all()


def test_build_dashboard_shape_and_figures(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    data = build_dashboard.build_dashboard()
    assert data["prefs"]["layout"] == "tile"
    assert data["prefs"]["card_fields"] == ["cash", "open_ar", "open_ap", "last_entry"]
    assert "generated_at" in data
    by_slug = {c["slug"]: c for c in data["clients"]}
    # both active clients present, ordered by display name (Harborside before J.B.)
    assert [c["slug"] for c in data["clients"]] == ["harborside-group", "jb-design"]
    # figures equal the cached summary
    s = tenancy.refresh_summary("harborside-group")
    assert by_slug["harborside-group"]["cash"] == s["cash"]
    # consolidates iff entity_count > 1
    assert by_slug["harborside-group"]["consolidates"] is True
    assert by_slug["harborside-group"]["entity_count"] == 3
    assert by_slug["jb-design"]["consolidates"] is False
    assert by_slug["jb-design"]["entity_count"] == 1
    # filing due carried through
    assert by_slug["harborside-group"]["next_filing_due"] is not None


def test_build_dashboard_excludes_archived(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    import sqlite3
    reg = sqlite3.connect(tenancy.registry_path())
    reg.execute("UPDATE clients SET status='archived' WHERE slug='jb-design'")
    reg.commit()
    reg.close()
    data = build_dashboard.build_dashboard()
    assert [c["slug"] for c in data["clients"]] == ["harborside-group"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_build_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'build_dashboard'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/build_dashboard.py`:

```python
#!/usr/bin/env python3
"""Project the lisza.db registry cache into public/dashboard.json.

Pure registry read — opens no client books. Run after tenancy.refresh_all()
so client_summary is current.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import tenancy

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
DEFAULT_CARD_FIELDS = ["cash", "open_ar", "open_ap", "last_entry"]


def _prefs(con: sqlite3.Connection) -> dict:
    row = con.execute(
        "SELECT layout, card_fields_json FROM bookkeeper_prefs LIMIT 1").fetchone()
    layout = row["layout"] if row and row["layout"] else "tile"
    if row and row["card_fields_json"]:
        card_fields = json.loads(row["card_fields_json"])
    else:
        card_fields = list(DEFAULT_CARD_FIELDS)
    return {"layout": layout, "card_fields": card_fields}


def build_dashboard() -> dict:
    tenancy.registry_db()
    con = sqlite3.connect(tenancy.registry_path())
    con.row_factory = sqlite3.Row
    prefs = _prefs(con)
    rows = con.execute(
        """SELECT c.slug, c.display_name, c.entity_type, c.status, c.next_filing_due,
                  s.cash, s.open_ar, s.open_ap, s.ar_count, s.ap_count,
                  s.entity_count, s.last_entry_date
           FROM clients c
           LEFT JOIN client_summary s ON s.client_id = c.client_id
           WHERE c.status = 'active'
           ORDER BY c.display_name""").fetchall()
    con.close()

    clients = []
    for r in rows:
        ec = r["entity_count"] or 1
        clients.append({
            "slug": r["slug"],
            "display_name": r["display_name"],
            "entity_type": r["entity_type"],
            "status": r["status"],
            "cash": r["cash"],
            "open_ar": r["open_ar"],
            "open_ap": r["open_ap"],
            "ar_count": r["ar_count"],
            "ap_count": r["ap_count"],
            "entity_count": ec,
            "consolidates": ec > 1,
            "last_entry": r["last_entry_date"],
            "next_filing_due": r["next_filing_due"],
        })

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prefs": prefs,
        "clients": clients,
    }


def write_dashboard(path: str | Path | None = None) -> Path:
    out = Path(path) if path else PUBLIC_DIR / "dashboard.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_dashboard(), indent=2))
    return out


if __name__ == "__main__":
    p = write_dashboard()
    print(f"wrote {p}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_build_dashboard.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/LISZA && git add scripts/build_dashboard.py scripts/test_build_dashboard.py && git commit -m "feat(lisza): dashboard.json generator (registry projection)"
```

---

## Task 4: `write_dashboard()` file write + regenerate the committed artifact

**Files:**
- Test: `scripts/test_build_dashboard.py` (append)
- Generate: `public/dashboard.json` (from the real registry)

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_build_dashboard.py`:

```python
import json as _json


def test_write_dashboard_writes_file(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = build_dashboard.write_dashboard(tmp_path / "out" / "dashboard.json")
    assert out.exists()
    data = _json.loads(out.read_text())
    assert data["prefs"]["layout"] == "tile"
    assert len(data["clients"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_build_dashboard.py::test_write_dashboard_writes_file -v`
Expected: PASS already if Task 3's `write_dashboard` is in place — this test guards the file-write path. If `write_dashboard` were missing it would FAIL with `AttributeError`. (Confirm it passes; the function exists from Task 3.)

- [ ] **Step 3: Generate the real committed artifact**

Run against the live registry (refresh first so the cache has the new fields):

```bash
cd /home/workspace/LISZA/scripts && python3 -c "import tenancy; tenancy.refresh_all()" && python3 build_dashboard.py
```

Expected: prints `wrote /home/workspace/LISZA/public/dashboard.json`.

- [ ] **Step 4: Verify the artifact content**

Run: `cd /home/workspace/LISZA && python3 -c "import json; d=json.load(open('public/dashboard.json')); print(d['prefs']); print([(c['slug'],c['entity_count'],c['consolidates'],c['next_filing_due']) for c in d['clients']])"`
Expected: prefs shows `{'layout': 'tile', 'card_fields': [...]}` and three clients incl. `('harborside-group', 3, True, <date>)` and `('guitar-works', 1, False, <date>)`.

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/LISZA && git add scripts/test_build_dashboard.py public/dashboard.json && git commit -m "feat(lisza): write_dashboard file path + generate committed artifact"
```

---

## Task 5: Front-end (index.html + dashboard.js)

**Files:**
- Create: `public/index.html`
- Create: `public/dashboard.js`

Not unit-tested — verified in the browser via the Zo local-service proxy. (A companion server + proxy is already running from the brainstorm session; if it has exited, restart per the spec's note and re-run `proxy_local_service` on the static server's port. To serve the `public/` dir for verification: `cd /home/workspace/LISZA/public && python3 -m http.server 8080` then proxy port 8080.)

- [ ] **Step 1: Create `public/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LISZA — Bookkeeper Dashboard</title>
  <style>
    :root { color-scheme: light; }
    body { font-family: -apple-system, Segoe UI, Roboto, sans-serif;
           margin: 0; background: #f6f7f9; color: #1a1a1a; }
    header { background: #fff; border-bottom: 1px solid #e3e3e3;
             padding: 14px 22px; display: flex; align-items: center;
             justify-content: space-between; flex-wrap: wrap; gap: 10px; }
    header h1 { font-size: 18px; margin: 0; }
    .controls { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
    .seg button { border: 1px solid #ccc; background: #fff; padding: 5px 12px;
                  cursor: pointer; font-size: 13px; }
    .seg button:first-child { border-radius: 6px 0 0 6px; }
    .seg button:last-child { border-radius: 0 6px 6px 0; }
    .seg button.active { background: #1a1a1a; color: #fff; border-color: #1a1a1a; }
    .fields { font-size: 12px; color: #555; display: flex; gap: 10px; flex-wrap: wrap; }
    .fields label { cursor: pointer; }
    main { padding: 22px; }
    .stamp { color: #888; font-size: 12px; margin-bottom: 14px; }
    .money { font-variant-numeric: tabular-nums; }
    .pos { color: #1a7f4b; }
    .muted { color: #777; font-size: 12px; }
    /* tile */
    .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px,1fr)); gap: 14px; }
    .card { background: #fff; border: 1px solid #e0e0e0; border-radius: 10px; padding: 16px; }
    .card h3 { margin: 0 0 2px 0; font-size: 16px; }
    .kv { display: flex; justify-content: space-between; font-size: 13px; padding: 3px 0; }
    .kv span:first-child { color: #555; }
    /* list */
    table { width: 100%; border-collapse: collapse; background: #fff; font-size: 13px;
            border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
    th, td { text-align: right; padding: 8px 12px; border-bottom: 1px solid #eee; }
    th:first-child, td:first-child { text-align: left; }
    th { background: #fafafa; color: #555; }
    /* rolodex */
    .rolo { background: #fff; border: 1px solid #e0e0e0; border-radius: 10px;
            padding: 22px; max-width: 460px; margin: 0 auto; }
    .rolo-nav { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
    .rolo-nav button { border: 1px solid #ccc; background: #fff; border-radius: 16px;
                       padding: 4px 14px; cursor: pointer; }
    .big { font-size: 26px; font-weight: 700; }
  </style>
</head>
<body>
  <header>
    <h1>LISZA · Bookkeeper Dashboard</h1>
    <div class="controls">
      <div class="seg" id="layout-seg">
        <button data-layout="tile">Tiles</button>
        <button data-layout="list">List</button>
        <button data-layout="rolodex">Rolodex</button>
      </div>
      <div class="fields" id="field-toggles"></div>
    </div>
  </header>
  <main>
    <div class="stamp" id="stamp"></div>
    <div id="board"></div>
  </main>
  <script src="dashboard.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `public/dashboard.js`**

```javascript
"use strict";

const OPTIONAL_FIELDS = [
  { key: "ar_count", label: "AR count" },
  { key: "ap_count", label: "AP count" },
  { key: "entities", label: "Entities" },
  { key: "next_filing_due", label: "Next filing" },
  { key: "status", label: "Status" },
];

const LS_LAYOUT = "lisza_layout";
const LS_FIELDS = "lisza_card_fields";

let DATA = null;
let layout = "tile";
let cardFields = [];
let roloIndex = 0;

function money(n) {
  if (n === null || n === undefined) return "—";
  return "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function loadPrefs() {
  layout = localStorage.getItem(LS_LAYOUT) || DATA.prefs.layout || "tile";
  const saved = localStorage.getItem(LS_FIELDS);
  cardFields = saved ? JSON.parse(saved) : (DATA.prefs.card_fields || []);
}

function savePrefs() {
  localStorage.setItem(LS_LAYOUT, layout);
  localStorage.setItem(LS_FIELDS, JSON.stringify(cardFields));
}

function has(field) { return cardFields.includes(field); }

function badge(c) { return c.consolidates ? " ⛓" : ""; }

function optionalRows(c) {
  let rows = "";
  if (has("ar_count")) rows += kv("Open invoices", c.ar_count ?? "—");
  if (has("ap_count")) rows += kv("Open bills", c.ap_count ?? "—");
  if (has("entities")) rows += kv("Entities", c.entity_count);
  if (has("next_filing_due")) rows += kv("Next filing", c.next_filing_due || "—");
  if (has("status")) rows += kv("Status", c.status);
  return rows;
}

function kv(k, v) {
  return `<div class="kv"><span>${k}</span><span class="money">${v}</span></div>`;
}

function renderTiles() {
  const cards = DATA.clients.map(c => `
    <div class="card">
      <h3>${c.display_name}${badge(c)}</h3>
      <div class="muted">${c.entity_type || ""}</div>
      <div class="kv"><span>Cash</span><span class="money pos">${money(c.cash)}</span></div>
      <div class="kv"><span>Open AR</span><span class="money">${money(c.open_ar)}</span></div>
      <div class="kv"><span>Open AP</span><span class="money">${money(c.open_ap)}</span></div>
      ${optionalRows(c)}
      <div class="muted" style="margin-top:6px">Last entry ${c.last_entry || "—"}</div>
    </div>`).join("");
  return `<div class="tiles">${cards}</div>`;
}

function renderList() {
  const head = `<tr><th>Client</th><th>Type</th><th>Cash</th><th>Open AR</th>
    <th>Open AP</th><th>Last entry</th></tr>`;
  const body = DATA.clients.map(c => `
    <tr><td>${c.display_name}${badge(c)}</td><td style="text-align:left">${c.entity_type || ""}</td>
    <td class="money">${money(c.cash)}</td><td class="money">${money(c.open_ar)}</td>
    <td class="money">${money(c.open_ap)}</td><td>${c.last_entry || "—"}</td></tr>`).join("");
  return `<table>${head}${body}</table>`;
}

function renderRolodex() {
  if (DATA.clients.length === 0) return "";
  if (roloIndex >= DATA.clients.length) roloIndex = 0;
  const c = DATA.clients[roloIndex];
  return `
    <div class="rolo">
      <div class="rolo-nav">
        <button id="rolo-prev">‹ prev</button>
        <strong>${c.display_name}${badge(c)}</strong>
        <button id="rolo-next">next ›</button>
      </div>
      <div class="muted">${c.entity_type || ""} · client ${roloIndex + 1} of ${DATA.clients.length}</div>
      <div class="big pos money" style="margin-top:10px">${money(c.cash)}
        <span class="muted" style="font-weight:400">cash</span></div>
      <div class="kv"><span>Open AR</span><span class="money">${money(c.open_ar)}</span></div>
      <div class="kv"><span>Open AP</span><span class="money">${money(c.open_ap)}</span></div>
      ${optionalRows(c)}
      <div class="muted" style="margin-top:6px">Last entry ${c.last_entry || "—"}</div>
    </div>`;
}

function render() {
  const board = document.getElementById("board");
  if (!DATA.clients.length) { board.innerHTML = `<p class="muted">No active clients.</p>`; return; }
  if (layout === "list") board.innerHTML = renderList();
  else if (layout === "rolodex") board.innerHTML = renderRolodex();
  else board.innerHTML = renderTiles();

  document.querySelectorAll("#layout-seg button").forEach(b =>
    b.classList.toggle("active", b.dataset.layout === layout));

  if (layout === "rolodex") {
    const prev = document.getElementById("rolo-prev");
    const next = document.getElementById("rolo-next");
    if (prev) prev.onclick = () => { roloIndex = (roloIndex - 1 + DATA.clients.length) % DATA.clients.length; render(); };
    if (next) next.onclick = () => { roloIndex = (roloIndex + 1) % DATA.clients.length; render(); };
  }
}

function buildFieldToggles() {
  const host = document.getElementById("field-toggles");
  host.innerHTML = OPTIONAL_FIELDS.map(f =>
    `<label><input type="checkbox" data-field="${f.key}" ${has(f.key) ? "checked" : ""}> ${f.label}</label>`
  ).join("");
  host.querySelectorAll("input").forEach(inp => {
    inp.onchange = () => {
      const k = inp.dataset.field;
      if (inp.checked) { if (!cardFields.includes(k)) cardFields.push(k); }
      else { cardFields = cardFields.filter(x => x !== k); }
      savePrefs(); render();
    };
  });
}

function initLayoutButtons() {
  document.querySelectorAll("#layout-seg button").forEach(b => {
    b.onclick = () => { layout = b.dataset.layout; savePrefs(); render(); };
  });
}

fetch("dashboard.json").then(r => r.json()).then(d => {
  DATA = d;
  loadPrefs();
  document.getElementById("stamp").textContent = "Data as of " + d.generated_at;
  initLayoutButtons();
  buildFieldToggles();
  render();
}).catch(e => {
  document.getElementById("board").innerHTML =
    `<p class="muted">Could not load dashboard.json (${e}).</p>`;
});
```

- [ ] **Step 3: Verify in the browser**

Serve `public/` and proxy it:

```bash
cd /home/workspace/LISZA/public && python3 -m http.server 8080 --bind 0.0.0.0
```

Then call `proxy_local_service` on port 8080 and open the returned URL. Confirm:
- Tiles render for all three clients with cash/AR/AP and the ⛓ badge on Harborside.
- Layout buttons switch tile ↔ list ↔ rolodex; rolodex prev/next cycles.
- Field checkboxes add/remove rows and the choice survives a page reload (localStorage).
- "Data as of" stamp shows `generated_at`.

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/LISZA && git add public/index.html public/dashboard.js && git commit -m "feat(lisza): bookkeeper dashboard front-end (tile/list/rolodex)"
```

---

## Task 6: Docs + full-suite green

**Files:**
- Modify: `TODO.md`

- [ ] **Step 1: Mark Step 1 dashboard items done in `TODO.md`**

In `TODO.md`, under `### Step 1 — Bookkeeper dashboard (front end, customizable)`, change the two `- [ ]` items to `- [x]` and append a note line after the heading:

```markdown
> Implemented 2026-06-29 (Spec 2) — generator `build_dashboard.py` → `public/dashboard.json`; vanilla-JS front-end (tile default + list/rolodex toggle); prefs hybrid (DB-seed + localStorage). Drill-down + write-back deferred to Spec 3/later.
```

- [ ] **Step 2: Run the full LISZA suite**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest -q`
Expected: all tests pass — the foundation suite plus the 2 new `test_tenancy.py` tests (Tasks 1 & 2) and the 3 new `test_build_dashboard.py` tests (Tasks 3 & 4).

- [ ] **Step 3: Commit**

```bash
cd /home/workspace/LISZA && git add TODO.md && git commit -m "docs(lisza): mark bookkeeper dashboard (Spec 2 Step 1) complete"
```

---

## Notes for the implementer

- **`public/dashboard.json` is a committed artifact** (like niner6's JSON). Regenerate it with `python3 scripts/build_dashboard.py` (after `refresh_all()`) whenever client data changes.
- **No publish in this plan.** `publish_site` to the LISZA Zo Site is operator-gated and triggered manually — do not publish as part of execution.
- **Single bookkeeper assumed.** `_prefs()` reads `bookkeeper_prefs LIMIT 1`; multi-bookkeeper selection is out of scope.
- **`book_schema._has_column(con, table, column)`** already exists (used by the foundation) — reused for the additive registry column guard.
