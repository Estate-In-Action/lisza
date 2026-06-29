# Client Detail View (Spec 3A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a drill-in per-client detail page (AR / AP / admin / historical tiles + payroll placeholder) reached by clicking a client on the dashboard.

**Architecture:** A Python generator opens each client `ledger.db` (read-only) and writes `public/clients/<slug>.json`; the existing vanilla-JS front-end gains a hash router (`#client/<slug>`) that fetches and renders the detail tiles. Static-only, matching Spec 2.

**Tech Stack:** Python 3 + sqlite3 (stdlib), vanilla JS, Node 22 for the headless render check.

---

## File structure

- Create `scripts/build_client_detail.py` — detail JSON generator (pure helpers + orchestration + CLI).
- Create `scripts/test_build_client_detail.py` — pytest for the generator.
- Modify `public/dashboard.js` — add detail render + hash router + clickable client names + node export guard.
- Modify `public/index.html` — detail-view styles.
- Create `scripts/render_detail_check.js` — headless Node render check.
- Generated artifacts: `public/clients/<slug>.json` (committed).

Conventions to follow (from Spec 2): `esc()` on every interpolated string; `money()` formatter; `kv()` row helper; revenue accounts are type `income` (credit-normal), expenses type `expense` (debit-normal); only `entries.status='posted'` counts.

---

### Task 1: AR/AP aging helper (pure)

**Files:**
- Create: `scripts/build_client_detail.py`
- Test: `scripts/test_build_client_detail.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_build_client_detail.py
import build_client_detail as bcd


def test_aging_buckets_classify_and_total():
    items = [
        {"party": "A", "due_date": "2026-06-09", "amount": 100.0},   # due == as_of -> current
        {"party": "B", "due_date": "2026-06-01", "amount": 200.0},   # 8d -> d1_30
        {"party": "C", "due_date": "2026-05-01", "amount": 300.0},   # 39d -> d31_60
        {"party": "D", "due_date": "2026-04-01", "amount": 400.0},   # 69d -> d61_90
        {"party": "E", "due_date": "2026-01-01", "amount": 500.0},   # 159d -> d90_plus
    ]
    r = bcd.aging_buckets(items, "2026-06-09")
    assert r["open_total"] == 1500.0
    assert r["open_count"] == 5
    assert r["aging"]["current"] == 100.0
    assert r["aging"]["d1_30"] == 200.0
    assert r["aging"]["d31_60"] == 300.0
    assert r["aging"]["d61_90"] == 400.0
    assert r["aging"]["d90_plus"] == 500.0
    assert sum(r["aging"].values()) == r["open_total"]


def test_aging_top_open_sorted_and_capped():
    items = [{"party": f"P{i}", "due_date": "2026-06-01", "amount": float(i)}
             for i in range(1, 12)]
    r = bcd.aging_buckets(items, "2026-06-09")
    amounts = [t["amount"] for t in r["top_open"]]
    assert amounts == sorted(amounts, reverse=True)
    assert len(r["top_open"]) == 8
    assert r["top_open"][0]["days_past_due"] == 8


def test_aging_empty():
    r = bcd.aging_buckets([], "2026-06-09")
    assert r["open_total"] == 0.0
    assert r["open_count"] == 0
    assert r["top_open"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_build_client_detail.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'build_client_detail'`)

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/build_client_detail.py
#!/usr/bin/env python3
"""Project each client ledger.db into public/clients/<slug>.json (read-only)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import tenancy

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
TOP_N = 8


def aging_buckets(items, as_of: str) -> dict:
    """Bucket AR/AP items by days-past-due vs as_of. items: dicts with
    party, due_date (ISO), amount."""
    ref = date.fromisoformat(as_of)
    buckets = {"current": 0.0, "d1_30": 0.0, "d31_60": 0.0,
               "d61_90": 0.0, "d90_plus": 0.0}
    total = 0.0
    count = 0
    enriched = []
    for it in items:
        amt = float(it["amount"])
        dpd = (ref - date.fromisoformat(it["due_date"])).days
        if dpd <= 0:
            buckets["current"] += amt
        elif dpd <= 30:
            buckets["d1_30"] += amt
        elif dpd <= 60:
            buckets["d31_60"] += amt
        elif dpd <= 90:
            buckets["d61_90"] += amt
        else:
            buckets["d90_plus"] += amt
        total += amt
        count += 1
        enriched.append({"party": it["party"], "due_date": it["due_date"],
                         "amount": round(amt, 2), "days_past_due": dpd})
    buckets = {k: round(v, 2) for k, v in buckets.items()}
    top = sorted(enriched, key=lambda r: r["amount"], reverse=True)[:TOP_N]
    return {"open_total": round(total, 2), "open_count": count,
            "aging": buckets, "top_open": top}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_build_client_detail.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/build_client_detail.py scripts/test_build_client_detail.py
git commit -m "feat(lisza): AR/AP aging-bucket helper for client detail"
```

---

### Task 2: EIN masking helper

**Files:**
- Modify: `scripts/build_client_detail.py`
- Test: `scripts/test_build_client_detail.py`

- [ ] **Step 1: Write the failing test**

```python
def test_mask_ein_keeps_last_four():
    assert bcd.mask_ein("47-2201234") == "••-•••1234"


def test_mask_ein_none_and_short():
    assert bcd.mask_ein(None) is None
    assert bcd.mask_ein("") is None
    assert bcd.mask_ein("12") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_build_client_detail.py::test_mask_ein_keeps_last_four -v`
Expected: FAIL (`AttributeError: module 'build_client_detail' has no attribute 'mask_ein'`)

- [ ] **Step 3: Write minimal implementation**

Add to `build_client_detail.py` (after `aging_buckets`):

```python
def mask_ein(ein: str | None) -> str | None:
    if not ein:
        return None
    digits = "".join(ch for ch in ein if ch.isdigit())
    if len(digits) < 4:
        return None
    return "••-•••" + digits[-4:]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_build_client_detail.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/build_client_detail.py scripts/test_build_client_detail.py
git commit -m "feat(lisza): EIN masking helper (last 4 only)"
```

---

### Task 3: Monthly historical trend (reads a connection)

**Files:**
- Modify: `scripts/build_client_detail.py`
- Test: `scripts/test_build_client_detail.py`

- [ ] **Step 1: Write the failing test**

```python
import sqlite3


def _trend_fixture():
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE accounts(code TEXT PRIMARY KEY, type TEXT);
        CREATE TABLE entries(id INTEGER PRIMARY KEY, entry_date TEXT, status TEXT);
        CREATE TABLE splits(entry_id INTEGER, account TEXT, dr REAL, cr REAL);
        INSERT INTO accounts VALUES('400','income'),('500','expense');
        INSERT INTO entries VALUES(1,'2026-06-05','posted'),(2,'2026-06-20','posted'),
                                  (3,'2026-05-10','posted'),(4,'2026-06-01','pending');
        INSERT INTO splits VALUES(1,'400',0,1000),(2,'500',300,0),
                                 (3,'400',0,500),(4,'400',0,9999);
        """)
    con.commit()
    return con


def test_monthly_trend_signs_and_window():
    con = _trend_fixture()
    rows = bcd.monthly_trend(con, "2026-06-20", n=12)
    assert len(rows) == 12
    assert rows[-1]["month"] == "2026-06"
    jun = rows[-1]
    assert jun["revenue"] == 1000.0     # credit on income, posted only (pending excluded)
    assert jun["expense"] == 300.0      # debit on expense
    assert jun["net"] == 700.0
    assert jun["entries"] == 2
    may = rows[-2]
    assert may["month"] == "2026-05" and may["revenue"] == 500.0
    # a month with no activity is present and zeroed
    assert rows[0]["revenue"] == 0.0 and rows[0]["entries"] == 0


def test_span_and_entry_count():
    con = _trend_fixture()
    first, last, n = bcd.posted_span(con)
    assert first == "2026-05-10" and last == "2026-06-20" and n == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_build_client_detail.py::test_monthly_trend_signs_and_window -v`
Expected: FAIL (`AttributeError: ... 'monthly_trend'`)

- [ ] **Step 3: Write minimal implementation**

Add to `build_client_detail.py`:

```python
def _months_back(as_of: str, n: int) -> list[str]:
    ref = date.fromisoformat(as_of)
    y, m = ref.year, ref.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def monthly_trend(con: sqlite3.Connection, as_of: str, n: int = 12) -> list[dict]:
    months = _months_back(as_of, n)
    rows = con.execute(
        """SELECT substr(e.entry_date,1,7) AS ym,
                  ROUND(SUM(CASE WHEN a.type='income'  THEN s.cr-s.dr ELSE 0 END),2) rev,
                  ROUND(SUM(CASE WHEN a.type='expense' THEN s.dr-s.cr ELSE 0 END),2) exp,
                  COUNT(DISTINCT e.id) ent
           FROM splits s
           JOIN entries e ON e.id=s.entry_id AND e.status='posted'
           JOIN accounts a ON a.code=s.account
           WHERE substr(e.entry_date,1,7) >= ? AND substr(e.entry_date,1,7) <= ?
           GROUP BY ym""", (months[0], months[-1])).fetchall()
    by = {r[0]: r for r in rows}
    out = []
    for ym in months:
        r = by.get(ym)
        rev = (r[1] if r and r[1] is not None else 0.0)
        exp = (r[2] if r and r[2] is not None else 0.0)
        ent = (r[3] if r else 0)
        out.append({"month": ym, "revenue": rev, "expense": exp,
                    "net": round(rev - exp, 2), "entries": ent})
    return out


def posted_span(con: sqlite3.Connection):
    first, last = con.execute(
        "SELECT MIN(entry_date), MAX(entry_date) FROM entries "
        "WHERE status='posted'").fetchone()
    n = con.execute(
        "SELECT COUNT(*) FROM entries WHERE status='posted'").fetchone()[0]
    return first, last, n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_build_client_detail.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/build_client_detail.py scripts/test_build_client_detail.py
git commit -m "feat(lisza): monthly revenue/expense trend + posted span"
```

---

### Task 4: Assemble build_client_detail + writers + isolation

**Files:**
- Modify: `scripts/build_client_detail.py`
- Test: `scripts/test_build_client_detail.py`

- [ ] **Step 1: Write the failing test**

```python
import os
import tempfile
import importlib


def _isolated_home(tmp):
    os.environ["LISZA_HOME"] = tmp
    import tenancy
    importlib.reload(tenancy)
    importlib.reload(bcd)


def test_build_detail_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    import tenancy
    importlib.reload(tenancy)
    importlib.reload(bcd)
    # COA must resolve: point at the repo coa.csv
    repo = "/home/workspace/LISZA"
    monkeypatch.setattr(tenancy, "COA_PATH",
                        __import__("pathlib").Path(repo) / "coa.csv")
    cid = tenancy.register_client(slug="acme", display_name="Acme LLC",
                                  legal_name="Acme LLC", entity_type="llc",
                                  ein="47-2201234", filing_cadence="quarterly")
    db = tenancy.resolve_db("acme")
    con = __import__("sqlite3").connect(db)
    con.executescript(
        """INSERT INTO invoices(party,issue_date,due_date,amount,status)
             VALUES('Cust','2026-05-01','2026-05-15',1000,'open');
           INSERT INTO bills(party,issue_date,due_date,amount,status)
             VALUES('Vend','2026-05-01','2026-05-20',400,'unpaid');
           INSERT INTO entries(id,entry_date,description,source,status)
             VALUES(1,'2026-06-09','rev','x','posted');
           INSERT INTO splits(entry_id,account,dr,cr) VALUES(1,'400',0,1000);""")
    con.commit()
    con.close()

    d = bcd.build_client_detail("acme")
    assert d["slug"] == "acme"
    assert d["as_of"] == "2026-06-09"
    assert d["ar"]["open_total"] == 1000.0
    assert d["ap"]["open_total"] == 400.0
    assert d["admin"]["ein_masked"].endswith("1234")
    assert d["admin"]["next_filing_due"] is not None
    assert d["historical"]["entry_count"] == 1
    assert d["payroll"]["status"] == "pending"
    assert len(d["historical"]["monthly"]) == 12


def test_write_client_detail_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    import tenancy
    importlib.reload(tenancy)
    importlib.reload(bcd)
    monkeypatch.setattr(tenancy, "COA_PATH",
                        __import__("pathlib").Path("/home/workspace/LISZA") / "coa.csv")
    tenancy.register_client(slug="acme", display_name="Acme", entity_type="llc")
    out = bcd.write_client_detail("acme", path=tmp_path / "acme.json")
    assert out.exists()
    import json
    j = json.loads(out.read_text())
    assert j["slug"] == "acme"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_build_client_detail.py::test_build_detail_end_to_end -v`
Expected: FAIL (`AttributeError: ... 'build_client_detail'`)

- [ ] **Step 3: Write minimal implementation**

Add to `build_client_detail.py`:

```python
def build_client_detail(slug: str) -> dict:
    db = tenancy.resolve_db(slug)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    as_of = con.execute(
        "SELECT MAX(entry_date) FROM entries WHERE status='posted'").fetchone()[0]
    inv = [dict(r) for r in con.execute(
        "SELECT party, due_date, amount FROM invoices WHERE status='open'")]
    bil = [dict(r) for r in con.execute(
        "SELECT party, due_date, amount FROM bills WHERE status='unpaid'")]
    prof = con.execute(
        "SELECT slug, display_name, legal_name, ein, entity_type, "
        "fiscal_year_end, filing_cadence FROM client_profile").fetchone()
    ents = [dict(r) for r in con.execute(
        "SELECT name, type FROM entities WHERE active=1 "
        "ORDER BY is_default DESC, id")]
    first, last, entry_count = posted_span(con)
    monthly = monthly_trend(con, as_of) if as_of else []
    con.close()

    next_due = None
    if as_of:
        next_due = tenancy.compute_next_filing_due(
            (prof["filing_cadence"] or "quarterly"),
            date.fromisoformat(as_of),
            (prof["fiscal_year_end"] or "12-31")).isoformat()

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "slug": prof["slug"],
        "display_name": prof["display_name"],
        "entity_type": prof["entity_type"],
        "status": "active",
        "as_of": as_of,
        "ar": aging_buckets(inv, as_of) if as_of else aging_buckets([], "1970-01-01"),
        "ap": aging_buckets(bil, as_of) if as_of else aging_buckets([], "1970-01-01"),
        "admin": {
            "legal_name": prof["legal_name"],
            "ein_masked": mask_ein(prof["ein"]),
            "fiscal_year_end": prof["fiscal_year_end"],
            "filing_cadence": prof["filing_cadence"],
            "next_filing_due": next_due,
            "entities": ents,
        },
        "historical": {
            "span": {"first": first, "last": last},
            "monthly": monthly,
            "entry_count": entry_count,
        },
        "payroll": {"status": "pending",
                    "message": "Payroll engine ships in 3B/3C"},
    }


def write_client_detail(slug: str, path=None) -> Path:
    out = Path(path) if path else PUBLIC_DIR / "clients" / f"{slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_client_detail(slug), indent=2))
    return out


def write_all() -> int:
    n = 0
    for row in tenancy.list_clients():
        write_client_detail(row.slug)
        n += 1
    return n


if __name__ == "__main__":
    print(f"wrote {write_all()} client detail files")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_build_client_detail.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/build_client_detail.py scripts/test_build_client_detail.py
git commit -m "feat(lisza): build_client_detail + write_client_detail/write_all"
```

---

### Task 5: Generate the real client detail artifacts

**Files:**
- Create: `public/clients/guitar-works.json`, `public/clients/harborside-group.json`, `public/clients/jb-design.json`

- [ ] **Step 1: Run the generator against the real books**

Run: `cd /home/workspace/LISZA/scripts && python3 build_client_detail.py`
Expected: `wrote 3 client detail files`

- [ ] **Step 2: Spot-check one artifact**

Run: `python3 -c "import json; d=json.load(open('../public/clients/guitar-works.json')); print(d['as_of'], d['ar']['open_total'], d['admin']['ein_masked'], len(d['historical']['monthly']))"`
Expected: a date, a positive AR total, a masked EIN ending in digits, and `12`.

- [ ] **Step 3: Commit**

```bash
cd /home/workspace/LISZA
git add public/clients/guitar-works.json public/clients/harborside-group.json public/clients/jb-design.json
git commit -m "feat(lisza): generate client detail JSON artifacts (3 clients)"
```

---

### Task 6: Front-end — detail render + router + clickable names

**Files:**
- Modify: `public/dashboard.js`

- [ ] **Step 1: Add detail render helpers and `renderClientDetail`**

Insert before the bootstrap `fetch(...)` block at the bottom of `dashboard.js`:

```javascript
function agingRows(a) {
  const b = a.aging;
  return [["Current", b.current], ["1–30", b.d1_30], ["31–60", b.d31_60],
          ["61–90", b.d61_90], ["90+", b.d90_plus]]
    .map(([k, v]) => kv(k, money(v))).join("");
}

function topOpenRows(items) {
  if (!items || !items.length) return `<div class="muted">None open</div>`;
  return items.map(it =>
    `<div class="kv"><span>${esc(it.party)} ` +
    `<span class="muted">${esc(it.due_date)}` +
    `${it.days_past_due > 0 ? " · " + it.days_past_due + "d" : ""}</span></span>` +
    `<span class="money">${money(it.amount)}</span></div>`).join("");
}

function trendRows(monthly) {
  return (monthly || []).slice(-6).map(m =>
    `<div class="kv"><span>${esc(m.month)}</span>` +
    `<span class="money">${money(m.revenue)} ` +
    `<span class="muted">rev</span> · ` +
    `<span class="${m.net >= 0 ? "pos" : ""}">${money(m.net)} net</span>` +
    `</span></div>`).join("");
}

function renderClientDetail(d) {
  const ents = (d.admin.entities || []);
  const entRows = ents.length > 1
    ? "<h4>Entities</h4>" + ents.map(e =>
        `<div class="kv"><span>${esc(e.name)}</span>` +
        `<span class="muted">${esc(e.type)}</span></div>`).join("")
    : "";
  return `
    <div class="detail">
      <a class="back" href="#">‹ all clients</a>
      <h2>${esc(d.display_name)} <span class="muted">${esc(d.entity_type || "")}</span></h2>
      <div class="stamp">As of ${esc(d.as_of || "—")}</div>
      <div class="detail-tiles">
        <div class="card"><h3>Accounts Receivable</h3>
          <div class="kv"><span>Open</span><span class="money">${money(d.ar.open_total)} · ${esc(d.ar.open_count)}</span></div>
          ${agingRows(d.ar)}
          <h4>Top open</h4>${topOpenRows(d.ar.top_open)}
        </div>
        <div class="card"><h3>Accounts Payable</h3>
          <div class="kv"><span>Open</span><span class="money">${money(d.ap.open_total)} · ${esc(d.ap.open_count)}</span></div>
          ${agingRows(d.ap)}
          <h4>Top open</h4>${topOpenRows(d.ap.top_open)}
        </div>
        <div class="card"><h3>Admin</h3>
          ${kv("Legal name", d.admin.legal_name || "—")}
          ${kv("EIN", d.admin.ein_masked || "—")}
          ${kv("Fiscal year end", d.admin.fiscal_year_end || "—")}
          ${kv("Filing cadence", d.admin.filing_cadence || "—")}
          ${kv("Next filing due", d.admin.next_filing_due || "—")}
          ${entRows}
        </div>
        <div class="card"><h3>Historical</h3>
          ${kv("Data span", (d.historical.span.first || "—") + " → " + (d.historical.span.last || "—"))}
          ${kv("Posted entries", d.historical.entry_count)}
          <h4>Recent months</h4>${trendRows(d.historical.monthly)}
        </div>
        <div class="card payroll-stub"><h3>Payroll</h3>
          <div class="muted">${esc(d.payroll.message)}</div>
        </div>
      </div>
    </div>`;
}

function parseHash() {
  const h = (typeof location !== "undefined" ? location.hash : "").replace(/^#/, "");
  if (h.indexOf("client/") === 0) return { view: "client", slug: h.slice(7) };
  return { view: "dashboard" };
}

function route() {
  const r = parseHash();
  const controls = document.querySelector(".controls");
  const board = document.getElementById("board");
  const stamp = document.getElementById("stamp");
  if (r.view === "client") {
    if (controls) controls.style.visibility = "hidden";
    if (stamp) stamp.textContent = "";
    board.innerHTML = `<p class="muted">Loading…</p>`;
    fetch(`clients/${encodeURIComponent(r.slug)}.json`)
      .then(x => x.json())
      .then(d => { board.innerHTML = renderClientDetail(d); })
      .catch(e => { board.innerHTML =
        `<p class="muted">Could not load client (${esc(e)}).</p>`; });
  } else {
    if (controls) controls.style.visibility = "visible";
    if (stamp && DATA) stamp.textContent = "Data as of " + DATA.generated_at;
    render();
  }
}
```

- [ ] **Step 2: Make client names link to the detail route**

In `renderTiles()`, change the `<h3>` line to:

```javascript
      <h3><a class="clink" href="#client/${esc(c.slug)}">${esc(c.display_name)}</a>${badge(c)}</h3>
```

In `renderList()`, change the first `<td>` to:

```javascript
    <tr><td><a class="clink" href="#client/${esc(c.slug)}">${esc(c.display_name)}</a>${badge(c)}</td><td style="text-align:left">${esc(c.entity_type || "")}</td>
```

In `renderRolodex()`, change the `<strong>` line to:

```javascript
        <strong><a class="clink" href="#client/${esc(c.slug)}">${esc(c.display_name)}</a>${badge(c)}</strong>
```

- [ ] **Step 3: Replace the bootstrap block with a guarded, router-aware version**

Replace the final `fetch("dashboard.json")...` block with:

```javascript
if (typeof document !== "undefined" && document.getElementById) {
  window.addEventListener("hashchange", route);
  fetch("dashboard.json").then(r => r.json()).then(d => {
    DATA = d;
    loadPrefs();
    initLayoutButtons();
    buildFieldToggles();
    route();
  }).catch(e => {
    document.getElementById("board").innerHTML =
      `<p class="muted">Could not load dashboard.json (${esc(e)}).</p>`;
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { esc, money, kv, renderClientDetail, renderTiles, parseHash };
}
```

- [ ] **Step 4: Sanity-check JS syntax**

Run: `cd /home/workspace/LISZA && node -e "require('./public/dashboard.js'); console.log('loads ok')"`
Expected: `loads ok` (no document defined → bootstrap skipped; exports resolve).

- [ ] **Step 5: Commit**

```bash
git add public/dashboard.js
git commit -m "feat(lisza): client detail view + hash router + clickable names"
```

---

### Task 7: Detail-view styles

**Files:**
- Modify: `public/index.html`

- [ ] **Step 1: Add styles**

Insert before the closing `</style>` tag:

```css
    /* detail */
    .detail h2 { margin: 0 0 2px 0; font-size: 20px; }
    .back { display: inline-block; margin-bottom: 10px; font-size: 13px;
            color: #1a1a1a; text-decoration: none; }
    .back:hover { text-decoration: underline; }
    .detail-tiles { display: grid; gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr)); }
    .detail .card h4 { margin: 10px 0 4px 0; font-size: 12px; color: #777;
                       text-transform: uppercase; letter-spacing: .04em; }
    .payroll-stub { border-style: dashed; background: #fafafa; }
    .clink { color: inherit; text-decoration: none; }
    .clink:hover { text-decoration: underline; }
```

- [ ] **Step 2: Commit**

```bash
git add public/index.html
git commit -m "feat(lisza): detail-view styles (mobile-collapsing tile grid)"
```

---

### Task 8: Headless render check

**Files:**
- Create: `scripts/render_detail_check.js`

- [ ] **Step 1: Write the harness**

```javascript
// scripts/render_detail_check.js — verify detail tiles render + escape, no browser.
"use strict";
const fs = require("fs");
const path = require("path");

const mod = require(path.resolve(__dirname, "../public/dashboard.js"));
const { renderClientDetail, esc } = mod;

function assert(cond, msg) {
  if (!cond) { console.error("FAIL:", msg); process.exit(1); }
}

assert(esc("<b>&\"") === "&lt;b&gt;&amp;&quot;", "esc escapes");

const slug = process.argv[2] || "guitar-works";
const detail = JSON.parse(fs.readFileSync(
  path.resolve(__dirname, `../public/clients/${slug}.json`), "utf8"));
const html = renderClientDetail(detail);

for (const tile of ["Accounts Receivable", "Accounts Payable", "Admin",
                    "Historical", "Payroll"]) {
  assert(html.includes(tile), `${tile} tile present`);
}
assert(html.includes("‹ all clients"), "back link present");
assert(!/\b\d{2}-\d{7}\b/.test(html), "raw EIN must not appear");
assert(html.includes("•"), "masked EIN dots present");
console.log(`OK ${slug}: all 5 tiles render (${html.length} bytes)`);
```

- [ ] **Step 2: Run it against all three clients**

Run:
```bash
cd /home/workspace/LISZA
for s in guitar-works harborside-group jb-design; do node scripts/render_detail_check.js $s; done
```
Expected: three `OK <slug>: all 5 tiles render` lines.

- [ ] **Step 3: Commit**

```bash
git add scripts/render_detail_check.js
git commit -m "test(lisza): headless render check for client detail view"
```

---

### Task 9: Full suite + generator wiring note

**Files:**
- Modify: `scripts/build_dashboard.py` (docstring note only) — optional

- [ ] **Step 1: Run the full Python suite**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest -q`
Expected: all tests pass (existing 26 + new 9 = 35).

- [ ] **Step 2: Regenerate both dashboard + detail artifacts together**

Run:
```bash
cd /home/workspace/LISZA/scripts
python3 -c "import tenancy; tenancy.refresh_all()"
python3 build_dashboard.py
python3 build_client_detail.py
```
Expected: dashboard.json + 3 client JSONs refreshed.

- [ ] **Step 3: Commit any regenerated artifacts**

```bash
cd /home/workspace/LISZA
git add public/dashboard.json public/clients/*.json
git commit -m "chore(lisza): regenerate dashboard + client detail artifacts" || echo "nothing to commit"
```

---

## Self-review checklist (run before handing off)

- [ ] Spec coverage: AR tile (T1,T6), AP tile (T1,T6), admin tile + EIN mask (T2,T4,T6), historical tile (T3,T6), payroll placeholder (T4,T6), detail JSON generator (T4), router + clickable drill-in (T6), as-of = last posted entry (T4), headless front-end check (T8). All spec sections mapped.
- [ ] No placeholders: every code step has full code.
- [ ] Type/name consistency: `aging_buckets`, `mask_ein`, `monthly_trend`, `posted_span`, `build_client_detail`, `write_client_detail`, `write_all`, `renderClientDetail`, `route`, `parseHash` used identically across tasks.
- [ ] `income` (not `revenue`) used for revenue account type throughout.
```
