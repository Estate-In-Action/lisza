# Nav Shell + House-Tenant Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a four-destination top-level nav (Books / Admin / CRM / Client Management) to the LISZA console, with **Admin** fully built on the house-tenant model (the bookkeeper as a hidden `_house` tenant reusing the entire existing console), and CRM + Client Management shipping as honest "coming next" stubs.

**Architecture:** The bookkeeper becomes their own tenant — a book at `clients/_house/ledger.db` registered with `kind='house'` and hidden from the client roster. Admin points the existing Overview/Reports/Categorize/Payroll machinery at `_house`. Because `_house` gets the identical schema as any client book, every existing engine works against it untouched. The only schema touch is one additive `kind` column on the registry `clients` table.

**Tech Stack:** Python 3 (`tenancy.py`, `book_schema.py`, stdlib `sqlite3`), pytest (`test_tenancy.py`), zo.space routes (Hono API `/api/lisza` + React page `/lisza/console`, `bun:sqlite`). Registry is `LISZA/lisza.db`; per-client books are `LISZA/clients/<slug>/ledger.db`.

**Source spec:** `docs/specs/2026-06-29-admin-nav-shell-design.md` (committed `09c9dc0`).

**Scope guardrails (from spec §Scope "Out of scope"):**
- Do NOT build CRM or Client Management beyond a placeholder.
- Do NOT build payroll ingestion (separate cycle).
- Do NOT add any new accounting engine, report type, or tax math — Admin is pure reuse.
- Do NOT ship bespoke housekeeping tiles beyond proving the default config + extension seam.
- LISZA stays on `main` / local only — **no push** (public-repo data-wipe gate still pending).
- Do NOT touch the untracked `scripts/ledger_tools.py` / `scripts/test_ledger_tools.py` (operator's separate in-progress work).

---

## Ground Truth (read before starting)

These are the exact current shapes the tasks build on. Verified against the live files at plan time.

**`scripts/tenancy.py`:**
- `REGISTRY_SCHEMA` `clients` columns: `client_id, slug, db_path, status, created_at, display_name, entity_type, last_close_date, next_filing_due`. No `kind`.
- `_ensure_registry_columns(con)` currently holds exactly one guard — the `entity_count` precedent:
  ```python
  def _ensure_registry_columns(con: sqlite3.Connection) -> None:
      if not book_schema._has_column(con, "client_summary", "entity_count"):
          con.execute("ALTER TABLE client_summary ADD COLUMN entity_count INTEGER")
  ```
- `register_client(*, slug, display_name, legal_name=None, entity_type=None, ein=None, fiscal_year_end="12-31", filing_cadence="quarterly", active_window="1y") -> str`. Its registry INSERT:
  ```python
  reg.execute(
      """INSERT OR REPLACE INTO clients
         (client_id, slug, db_path, status, display_name, entity_type)
         VALUES (?,?,?,?,?,?)""",
      (client_id, slug, str(db), "active", display_name, entity_type))
  ```
  It builds the book first: `_load_coa` (accounts) + `_ledger_core_schema` (entries, splits, invoices, bills, payee_rules) + `book_schema.ensure_book_schema` (client_profile, entities, employees, payroll_runs, payroll_lines).
- `list_clients(status="active") -> list[ClientRow]`: `SELECT * FROM clients WHERE status=? ORDER BY slug`. `ClientRow` fields: `client_id, slug, db_path, status, display_name, entity_type`.
- `resolve_db(slug)` = `lisza_home() / "clients" / slug / "ledger.db"` — handles `_house` with no special-casing.
- `book_schema._has_column(con, table, column) -> bool` is the additive-migration guard.

**`/api/lisza` route (zo.space, Hono/`bun:sqlite`):**
- Slug validator rejects `_house` today (no underscore in the class):
  ```ts
  function resolveLedger(slug: string | undefined): string | null {
    if (!slug) return LEGACY_DB;
    if (!/^[a-z0-9-]+$/.test(slug)) return null;
    const reg = new Database(REGISTRY, { readonly: true });
    try {
      const row = reg.query("SELECT 1 FROM clients WHERE slug=? AND status='active'").get(slug);
      return row ? `${HOME}/clients/${slug}/ledger.db` : null;
    } finally { reg.close(); }
  }
  ```
- `mode=clients` SQL filters only `WHERE c.status = 'active'` (NO `kind` filter). This is the query the console landing list reads — so roster-hiding must be applied **here**, not just in Python `list_clients`.

**`/lisza/console` route (zo.space, React page):**
- `export default function Console()` is the shell. State: `client` (null = landing) and `tab`. When `!client` it renders `<ClientList onPick={pick} />`; when `client` it renders the tab strip `[overview, reports, categorize, payroll]` plus the matching panel component (`Overview` / `Reports` / `Categorize` / `Payroll`), each already taking a `client` slug prop.
- There is **no** top-level section nav today.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `scripts/tenancy.py` | Registry + DB resolution + house tenant | Modify: `kind` migration, `register_client(kind=)`, `ensure_house()`, `list_clients` hide house, house-config getters/setter |
| `scripts/test_tenancy.py` | Registry tests | Modify: append migration-idempotency, roster-hiding, house-resolvability, house-config tests |
| `scripts/ensure_house.py` | One-shot bootstrap that calls `ensure_house()` against prod `LISZA_HOME` | Create |
| `/api/lisza` (zo.space route) | Tenant ledger reader | Modify: `_house` allow-list in `resolveLedger`; `kind != 'house'` in `mode=clients` |
| `/lisza/console` (zo.space route) | Console UI | Modify: top-level nav shell; Admin binds tabs to `_house`; CRM + Client Mgmt stubs; Housekeeping panel |

Tasks 1–4 and 7 are pure-Python TDD (red → green → commit). Task 5 (API) and Task 6 (UI) are zo.space TSX — not pytest-runnable — so they are verified with `curl` against the live API and a headless render check, with an explicit verification checklist per task.

---

### Task 1: Add `kind` column to the registry (additive migration)

**Files:**
- Modify: `scripts/tenancy.py` (`REGISTRY_SCHEMA`, `_ensure_registry_columns`)
- Test: `scripts/test_tenancy.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_tenancy.py`:

```python
def test_kind_column_migration_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    # First init creates the registry + runs column ensures.
    tenancy.registry_db()
    # Run the whole ensure path again — must not raise, must not duplicate.
    tenancy.registry_db()
    con = sqlite3.connect(tenancy.registry_path())
    kind_cols = [r for r in con.execute("PRAGMA table_info(clients)") if r[1] == "kind"]
    con.close()
    assert len(kind_cols) == 1

def test_existing_client_rows_default_to_kind_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="defaults-co", display_name="Defaults Co")
    con = sqlite3.connect(tenancy.registry_path())
    kind = con.execute("SELECT kind FROM clients WHERE slug='defaults-co'").fetchone()[0]
    con.close()
    assert kind == "client"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_kind_column_migration_is_idempotent test_tenancy.py::test_existing_client_rows_default_to_kind_client -v`
Expected: FAIL — `sqlite3.OperationalError: no such column: kind`.

- [ ] **Step 3: Add the migration**

In `scripts/tenancy.py`, extend `_ensure_registry_columns` (keep the existing `entity_count` guard):

```python
def _ensure_registry_columns(con: sqlite3.Connection) -> None:
    if not book_schema._has_column(con, "client_summary", "entity_count"):
        con.execute("ALTER TABLE client_summary ADD COLUMN entity_count INTEGER")
    if not book_schema._has_column(con, "clients", "kind"):
        con.execute("ALTER TABLE clients ADD COLUMN kind TEXT NOT NULL DEFAULT 'client'")
```

Also add `kind` to the `REGISTRY_SCHEMA` `clients` CREATE so fresh registries get it directly (place it after `entity_type`):

```sql
    entity_type  TEXT,
    kind         TEXT NOT NULL DEFAULT 'client',
    last_close_date TEXT,
    next_filing_due TEXT
```

Both paths are needed: `REGISTRY_SCHEMA` covers brand-new registries; `_ensure_registry_columns` covers already-existing prod registries (additive `ALTER`). `_has_column` makes the `ALTER` a no-op when the column is already present.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_kind_column_migration_is_idempotent test_tenancy.py::test_existing_client_rows_default_to_kind_client -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full tenancy suite (no regressions)**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -v`
Expected: PASS (all prior tests + 2 new).

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/tenancy.py scripts/test_tenancy.py
git commit -m "feat(lisza): add idempotent kind column to client registry"
```

---

### Task 2: `register_client` accepts and writes `kind`

**Files:**
- Modify: `scripts/tenancy.py` (`register_client`)
- Test: `scripts/test_tenancy.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_tenancy.py`:

```python
def test_register_client_writes_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="house-ish", display_name="Houseish", kind="house")
    tenancy.register_client(slug="plain-co", display_name="Plain Co")  # default
    con = sqlite3.connect(tenancy.registry_path())
    kinds = dict(con.execute("SELECT slug, kind FROM clients").fetchall())
    con.close()
    assert kinds["house-ish"] == "house"
    assert kinds["plain-co"] == "client"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_register_client_writes_kind -v`
Expected: FAIL — `TypeError: register_client() got an unexpected keyword argument 'kind'`.

- [ ] **Step 3: Add the `kind` parameter and write it**

In `scripts/tenancy.py`, change the `register_client` signature to add `kind` (keyword-only, defaulting to `'client'`) — insert it after `active_window`:

```python
def register_client(*, slug: str, display_name: str, legal_name: str | None = None,
                    entity_type: str | None = None, ein: str | None = None,
                    fiscal_year_end: str = "12-31", filing_cadence: str = "quarterly",
                    active_window: str = "1y", kind: str = "client") -> str:
```

Update the registry INSERT to include `kind`:

```python
    reg.execute(
        """INSERT OR REPLACE INTO clients
           (client_id, slug, db_path, status, display_name, entity_type, kind)
           VALUES (?,?,?,?,?,?,?)""",
        (client_id, slug, str(db), "active", display_name, entity_type, kind))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_register_client_writes_kind -v`
Expected: PASS.

- [ ] **Step 5: Run the full tenancy suite**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/tenancy.py scripts/test_tenancy.py
git commit -m "feat(lisza): register_client accepts kind param"
```

---

### Task 3: `ensure_house()` — idempotent house-tenant registration

**Files:**
- Modify: `scripts/tenancy.py` (`HOUSE_SLUG` const + `ensure_house`)
- Test: `scripts/test_tenancy.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_tenancy.py`:

```python
def test_ensure_house_registers_hidden_full_schema_book(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    cid1 = tenancy.ensure_house()
    cid2 = tenancy.ensure_house()  # idempotent: same id, no duplicate row
    assert cid1 == cid2

    con = sqlite3.connect(tenancy.registry_path())
    rows = con.execute("SELECT slug, kind FROM clients WHERE slug='_house'").fetchall()
    con.close()
    assert rows == [("_house", "house")]

    # resolvable to the conventional path
    p = tenancy.resolve_db("_house")
    assert p == tmp_path / "clients" / "_house" / "ledger.db"
    assert p.exists()

    # full book schema present (core ledger + entity + payroll tables)
    book = sqlite3.connect(p)
    tables = {r[0] for r in book.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    book.close()
    assert {"accounts", "entries", "splits", "invoices", "bills",
            "entities", "client_profile", "employees",
            "payroll_runs", "payroll_lines"} <= tables
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_ensure_house_registers_hidden_full_schema_book -v`
Expected: FAIL — `AttributeError: module 'tenancy' has no attribute 'ensure_house'`.

- [ ] **Step 3: Implement `ensure_house`**

In `scripts/tenancy.py`, add a module-level constant near the top (after `COA_PATH`):

```python
HOUSE_SLUG = "_house"
```

Then add this function (place it after `register_client`):

```python
def ensure_house() -> str:
    """Register the bookkeeper's own 'house' book idempotently.

    Returns the existing client_id if `_house` is already registered, otherwise
    registers it (kind='house', hidden from the roster) and returns the new id.
    """
    registry_db()
    reg = sqlite3.connect(registry_path())
    row = reg.execute(
        "SELECT client_id FROM clients WHERE slug=?", (HOUSE_SLUG,)).fetchone()
    reg.close()
    if row:
        return row[0]
    return register_client(
        slug=HOUSE_SLUG, display_name="House (My Books)", kind="house")
```

The existence check before `register_client` is what makes it idempotent without churning the `client_id` (re-calling `register_client` would mint a fresh `client_id` and `INSERT OR REPLACE` would orphan the `client_summary` FK row).

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_ensure_house_registers_hidden_full_schema_book -v`
Expected: PASS.

- [ ] **Step 5: Run the full tenancy suite**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/tenancy.py scripts/test_tenancy.py
git commit -m "feat(lisza): ensure_house registers hidden house-tenant book"
```

---

### Task 4: `list_clients` hides the house book

**Files:**
- Modify: `scripts/tenancy.py` (`list_clients`)
- Test: `scripts/test_tenancy.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_tenancy.py`:

```python
def test_list_clients_excludes_house(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug="real-co", display_name="Real Co")
    tenancy.ensure_house()
    slugs = [r.slug for r in tenancy.list_clients()]
    assert "real-co" in slugs
    assert "_house" not in slugs
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_list_clients_excludes_house -v`
Expected: FAIL — `_house` is present in the returned slugs (assertion error on `"_house" not in slugs`).

- [ ] **Step 3: Add the roster filter**

In `scripts/tenancy.py`, change the `list_clients` query to exclude house books:

```python
    rows = con.execute(
        "SELECT * FROM clients WHERE status=? AND kind != 'house' ORDER BY slug",
        (status,)).fetchall()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_list_clients_excludes_house -v`
Expected: PASS.

- [ ] **Step 5: Run the full tenancy suite (guards `refresh_all` etc. don't sweep `_house` unexpectedly)**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -v`
Expected: PASS. (`refresh_all` iterates `list_clients`, so it now skips `_house` — acceptable; Admin reads the book directly, not the cached summary.)

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/tenancy.py scripts/test_tenancy.py
git commit -m "feat(lisza): list_clients hides house book from roster"
```

---

### Task 5: `/api/lisza` admits `_house` and hides it from the roster query

**Files:**
- Modify: zo.space route `/api/lisza` (via `mcp__zo__get_space_route` to read, `mcp__zo__write_space_route` / `mcp__zo__edit_space_route` to update)

This route is server-side TSX on zo.space; it is not pytest-runnable. Verify with `curl` against the live endpoint.

- [ ] **Step 1: Read the current route**

Use `mcp__zo__get_space_route` with `path="/api/lisza"`. Confirm `resolveLedger` and the `mode=clients` query still match the Ground Truth above before editing.

- [ ] **Step 2: Add a fixed system-slug allow-list to `resolveLedger`**

Replace the validator so `_house` (and only an explicit allow-list of underscore-prefixed system slugs) is permitted. The DB lookup still gates on an active registry row, so this never widens path-traversal exposure:

```ts
// Underscore-prefixed system slugs that are reachable by nav but hidden from
// the roster. Fixed allow-list — never accept arbitrary "_"-prefixed input.
const SYSTEM_SLUGS = new Set(["_house"]);

function resolveLedger(slug: string | undefined): string | null {
  if (!slug) return LEGACY_DB;
  const ok = SYSTEM_SLUGS.has(slug) || /^[a-z0-9-]+$/.test(slug);
  if (!ok) return null;
  const reg = new Database(REGISTRY, { readonly: true });
  try {
    const row = reg.query("SELECT 1 FROM clients WHERE slug=? AND status='active'").get(slug);
    return row ? `${HOME}/clients/${slug}/ledger.db` : null;
  } finally { reg.close(); }
}
```

- [ ] **Step 3: Hide `_house` from the `mode=clients` roster query**

In the `mode === "clients"` branch, add the `kind` filter to the WHERE clause so the console landing list never shows the house book:

```sql
        FROM clients c
        LEFT JOIN client_summary s ON s.client_id = c.client_id
        WHERE c.status = 'active' AND c.kind != 'house'
        ORDER BY c.display_name
```

- [ ] **Step 4: Write the route**

Use `mcp__zo__write_space_route` (or `edit_space_route`) with `path="/api/lisza"` and the full updated code.

- [ ] **Step 5: Verify with curl (bootstrap `_house` first — see Task 8 if not yet run)**

```bash
# roster must NOT contain _house
curl -s "https://dadadanja.zo.space/api/lisza?mode=clients" | python3 -c "import sys,json; d=json.load(sys.stdin); slugs=[c['slug'] for c in d['clients']]; print('roster slugs:', slugs); assert '_house' not in slugs, 'house leaked into roster'; print('OK roster hides _house')"

# Admin can read the house overview (200, not 404)
curl -s -o /dev/null -w "%{http_code}\n" "https://dadadanja.zo.space/api/lisza?client=_house"
# Expected: 200

# A bogus underscore slug is still rejected (404)
curl -s -o /dev/null -w "%{http_code}\n" "https://dadadanja.zo.space/api/lisza?client=_evil"
# Expected: 404
```

Expected: roster assertion prints `OK`, `_house` returns `200`, `_evil` returns `404`.

- [ ] **Step 6: Commit**

zo.space routes are stored server-side, but record the change in the repo journal:

```bash
cd /home/workspace/LISZA
git commit --allow-empty -m "feat(lisza): /api/lisza admits _house via system allow-list, hides it from roster"
```

(If the route source is mirrored into the repo under a routes/ snapshot directory, add that file instead of an empty commit.)

---

### Task 6: `/lisza/console` nav shell + Admin + stubs

**Files:**
- Modify: zo.space route `/lisza/console`

Adds a top-level section nav. **Books** is the current behavior unchanged. **Admin** reuses the four tab components bound to `_house` plus a Housekeeping panel. **CRM** and **Client Management** render honest placeholders.

- [ ] **Step 1: Read the current route**

Use `mcp__zo__get_space_route` with `path="/lisza/console"`. Confirm `Console()`, `ClientList`, and the four tab components (`Overview`, `Reports`, `Categorize`, `Payroll`) match Ground Truth.

- [ ] **Step 2: Add a `BooksSection` wrapper around the current client/tab behavior**

Extract the existing `Console` body (client landing + per-client tabs) into a `BooksSection` component. Its internals are unchanged — it still owns `client`/`tab` state and renders `ClientList` then the tab panels:

```tsx
function ClientTabs({ slug, name, onBack }: { slug: string; name: string; onBack: () => void }) {
  const [tab, setTab] = useState("overview");
  const TABS = [["overview", "Overview"], ["reports", "Reports"], ["categorize", "Categorize"], ["payroll", "Payroll"]];
  return (
    <>
      {onBack && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "10px 0 6px", fontSize: 14 }}>
          <button onClick={onBack} style={{ border: "none", background: "none", padding: 0, cursor: "pointer", color: C.accent, fontWeight: 600 }}>All clients</button>
          <span style={{ color: C.faint }}>›</span>
          <span style={{ fontWeight: 700 }}>{name}</span>
        </div>
      )}
      <h1 style={{ fontSize: 28, fontWeight: 800, margin: "0 0 14px" }}>{name}</h1>
      <div style={{ display: "flex", gap: 8, marginBottom: 24, borderBottom: `1px solid ${C.line}` }}>
        {TABS.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} style={{
            padding: "8px 14px", border: "none", background: "none", cursor: "pointer", fontSize: 14, fontWeight: 600,
            color: tab === k ? C.accent : C.muted, borderBottom: `2px solid ${tab === k ? C.accent : "transparent"}`, marginBottom: -1 }}>{l}</button>
        ))}
      </div>
      {tab === "overview" && <Overview client={slug} />}
      {tab === "reports" && <Reports client={slug} />}
      {tab === "categorize" && <Categorize client={slug} />}
      {tab === "payroll" && <Payroll client={slug} />}
    </>
  );
}

function BooksSection() {
  const [client, setClient] = useState<{ slug: string; name: string } | null>(null);
  if (!client) {
    return (
      <>
        <h1 style={{ fontSize: 30, fontWeight: 800, margin: "8px 0 22px" }}>Clients</h1>
        <ClientList onPick={(slug, name) => setClient({ slug, name })} />
      </>
    );
  }
  return <ClientTabs slug={client.slug} name={client.name} onBack={() => setClient(null)} />;
}
```

- [ ] **Step 3: Add the Admin section bound to `_house`**

Admin reuses `ClientTabs` against the fixed `_house` slug — no roster, no back button:

```tsx
function AdminSection() {
  const [tab, setTab] = useState("housekeeping");
  const TABS = [["housekeeping", "Housekeeping"], ["overview", "Overview"], ["reports", "Reports"], ["categorize", "Categorize"], ["payroll", "Payroll"]];
  return (
    <>
      <h1 style={{ fontSize: 28, fontWeight: 800, margin: "0 0 4px" }}>Admin — My Books</h1>
      <div style={{ fontSize: 13, color: C.muted, marginBottom: 14 }}>The bookkeeper's own books, AR/AP, payroll &amp; tax forms (house tenant).</div>
      <div style={{ display: "flex", gap: 8, marginBottom: 24, borderBottom: `1px solid ${C.line}` }}>
        {TABS.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} style={{
            padding: "8px 14px", border: "none", background: "none", cursor: "pointer", fontSize: 14, fontWeight: 600,
            color: tab === k ? C.accent : C.muted, borderBottom: `2px solid ${tab === k ? C.accent : "transparent"}`, marginBottom: -1 }}>{l}</button>
        ))}
      </div>
      {tab === "housekeeping" && <Housekeeping />}
      {tab === "overview" && <Overview client="_house" />}
      {tab === "reports" && <Reports client="_house" />}
      {tab === "categorize" && <Categorize client="_house" />}
      {tab === "payroll" && <Payroll client="_house" />}
    </>
  );
}
```

- [ ] **Step 4: Add the Housekeeping panel (reads the config seam from Task 7)**

A minimal, honest "configurable" surface that reads the default `_house` config from the API and lists its tiles. It proves the seam without shipping bespoke tiles:

```tsx
function Housekeeping() {
  const [cfg, setCfg] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    fetch("/api/lisza?mode=house_config")
      .then(r => r.json())
      .then(j => j.error ? setErr(j.error) : setCfg(j))
      .catch(() => setErr("Could not load housekeeping config."));
  }, []);
  if (err) return <Card><div style={{ color: C.red, fontSize: 13 }}>{err}</div></Card>;
  if (!cfg) return <div style={{ color: C.faint }}>Loading…</div>;
  return (
    <Card>
      <H2>Housekeeping <span style={{ textTransform: "none", color: C.faint, fontWeight: 400 }}>· configurable surface (default config)</span></H2>
      <div style={{ fontSize: 13, color: C.muted, marginBottom: 10 }}>These tiles are driven by a stored config. Add tiles to the config to extend this surface — no console fork needed.</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(180px,1fr))", gap: 12 }}>
        {(cfg.tiles || []).map((t: any) => (
          <div key={t.key} style={{ border: `1px solid ${C.line}`, borderRadius: 10, padding: 14 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>{t.label}</div>
            <div style={{ fontSize: 12, color: C.faint, marginTop: 4 }}>{t.hint || "—"}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}
```

- [ ] **Step 5: Add CRM + Client Management stubs**

```tsx
function ComingNext({ title, blurb }: { title: string; blurb: string }) {
  return (
    <>
      <h1 style={{ fontSize: 28, fontWeight: 800, margin: "0 0 4px" }}>{title}</h1>
      <Card style={{ marginTop: 14 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: C.accent, marginBottom: 6 }}>Coming next</div>
        <div style={{ fontSize: 13, color: C.muted }}>{blurb}</div>
      </Card>
    </>
  );
}
```

- [ ] **Step 6: Replace `Console()` with the section shell**

```tsx
export default function Console() {
  const [section, setSection] = useState("books");
  const NAV = [["books", "Books"], ["admin", "Admin"], ["crm", "CRM"], ["clients", "Client Management"]];
  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.ink, fontFamily: "'Inter',-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <div style={{ maxWidth: 980, margin: "0 auto", padding: "40px 20px 64px" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: 13, letterSpacing: 4, fontWeight: 900, color: C.accent }}>LISZA</span>
          <span style={{ fontSize: 14, color: C.muted }}>Bookkeeper Console</span>
        </div>
        <div style={{ display: "flex", gap: 6, margin: "18px 0 26px", flexWrap: "wrap" }}>
          {NAV.map(([k, l]) => (
            <button key={k} onClick={() => setSection(k)} style={{
              padding: "8px 16px", borderRadius: 999, fontSize: 14, fontWeight: 700, cursor: "pointer",
              border: `1px solid ${section === k ? C.accent : C.line}`,
              background: section === k ? C.accent : C.panel, color: section === k ? "#fff" : C.muted }}>{l}</button>
          ))}
        </div>
        {section === "books" && <BooksSection />}
        {section === "admin" && <AdminSection />}
        {section === "crm" && <ComingNext title="CRM" blurb="Lead funnel, intake / in-process / annual-renewal forms, and convert a prospect into a client. Building next." />}
        {section === "clients" && <ComingNext title="Client Management" blurb="Add / archive clients, modify terms, and manage contracts. Building after CRM." />}
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Write the route**

Use `mcp__zo__write_space_route` with `path="/lisza/console"` and the full updated code (keep all the existing helper components — `Card`, `H2`, `Row`, `SummaryStat`, `ClientList`, `Reports`, `Categorize`, `Overview` and its sub-views, `Payroll` — unchanged).

- [ ] **Step 8: Verify in a browser**

Open `https://dadadanja.zo.space/lisza/console`. Confirm:
- Four nav pills render: Books / Admin / CRM / Client Management.
- **Books** behaves exactly as before (roster with tile/list/rolodex switch + field toggles; click a client → Overview/Reports/Categorize/Payroll; `_house` is NOT in the roster).
- **Admin** shows Housekeeping + Overview/Reports/Categorize/Payroll bound to `_house`; tabs render without error, including the empty-state path (Payroll shows "No payroll runs on file" if `_house` has none).
- **CRM** and **Client Management** show the "Coming next" card, not a blank or crash.

- [ ] **Step 9: Commit**

```bash
cd /home/workspace/LISZA
git commit --allow-empty -m "feat(lisza): console nav shell — Books/Admin/CRM/Client-Mgmt, Admin on house tenant"
```

---

### Task 7: Housekeeping config seam (`get_house_config` / `set_house_config` + API mode)

**Files:**
- Modify: `scripts/tenancy.py` (config getters/setter)
- Test: `scripts/test_tenancy.py`
- Modify: zo.space route `/api/lisza` (add `mode=house_config`)

Stores the `_house` housekeeping config in the existing `bookkeeper_prefs.card_fields_json` shape (bookkeeper_id `'_house'`), honoring the spec's "reuse the `bookkeeper_prefs.card_fields_json` shape" without any schema change. Ships a sensible default + a documented extension seam; it does NOT ship bespoke tiles.

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_tenancy.py`:

```python
import json as _json

def test_house_config_default_then_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.ensure_house()
    # default config has at least one tile and is well-formed
    cfg = tenancy.get_house_config()
    assert isinstance(cfg["tiles"], list) and len(cfg["tiles"]) >= 1
    assert all("key" in t and "label" in t for t in cfg["tiles"])
    # override persists and round-trips (proves the extension seam)
    new_cfg = {"tiles": cfg["tiles"] + [{"key": "custom_x", "label": "Custom X", "hint": "added"}]}
    tenancy.set_house_config(new_cfg)
    again = tenancy.get_house_config()
    assert any(t["key"] == "custom_x" for t in again["tiles"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_house_config_default_then_override -v`
Expected: FAIL — `AttributeError: module 'tenancy' has no attribute 'get_house_config'`.

- [ ] **Step 3: Implement the config seam**

In `scripts/tenancy.py`, add near `HOUSE_SLUG`:

```python
# Default housekeeping config for the house tenant. The seam: add tiles here
# (or via set_house_config) and the Admin → Housekeeping surface renders them.
HOUSE_CONFIG_DEFAULT = {
    "tiles": [
        {"key": "my_pnl", "label": "My P&L", "hint": "Overview tab"},
        {"key": "my_ar", "label": "Fees receivable", "hint": "what clients owe me"},
        {"key": "my_payroll", "label": "Staff payroll", "hint": "Payroll tab"},
    ]
}
```

Then add the getter/setter (place after `ensure_house`):

```python
def get_house_config() -> dict:
    """Read the house housekeeping config, falling back to the default."""
    registry_db()
    reg = sqlite3.connect(registry_path())
    row = reg.execute(
        "SELECT card_fields_json FROM bookkeeper_prefs WHERE bookkeeper_id=?",
        (HOUSE_SLUG,)).fetchone()
    reg.close()
    if row and row[0]:
        import json
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            pass
    return dict(HOUSE_CONFIG_DEFAULT)

def set_house_config(config: dict) -> None:
    """Persist a housekeeping config for the house tenant (extension seam)."""
    import json
    registry_db()
    reg = sqlite3.connect(registry_path())
    reg.execute(
        """INSERT OR REPLACE INTO bookkeeper_prefs (bookkeeper_id, card_fields_json, updated_at)
           VALUES (?, ?, datetime('now'))""",
        (HOUSE_SLUG, json.dumps(config)))
    reg.commit()
    reg.close()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py::test_house_config_default_then_override -v`
Expected: PASS.

- [ ] **Step 5: Run the full tenancy suite**

Run: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_tenancy.py -v`
Expected: PASS.

- [ ] **Step 6: Add the `house_config` API mode**

In `/api/lisza`, add this branch alongside the other `mode` checks (e.g., right after the `mode === "clients"` block). It shells out to the Python getter so the default/override logic lives in one place:

```ts
  if (mode === "house_config") {
    const proc = Bun.spawnSync(["python3",
      `${HOME}/scripts/house_config_cli.py`], { cwd: `${HOME}/scripts` });
    const out = proc.stdout.toString().trim();
    try {
      return c.json(JSON.parse(out), 200, CORS);
    } catch {
      return c.json({ error: "house config unavailable" }, 500, CORS);
    }
  }
```

Create `scripts/house_config_cli.py`:

```python
#!/usr/bin/env python3
"""Emit the house housekeeping config as JSON (for the /api/lisza house_config mode)."""
import json
import tenancy

if __name__ == "__main__":
    print(json.dumps(tenancy.get_house_config()))
```

(Alternative if `Bun.spawnSync` is undesirable in the route: read `bookkeeper_prefs` for `bookkeeper_id='_house'` directly with `bun:sqlite` and fall back to a hard-coded default mirroring `HOUSE_CONFIG_DEFAULT`. Prefer the CLI shell-out to keep the default in one place.)

- [ ] **Step 7: Write the route + verify**

Write `/api/lisza` via `mcp__zo__write_space_route`. Then:

```bash
curl -s "https://dadadanja.zo.space/api/lisza?mode=house_config" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d.get('tiles'), list) and d['tiles']; print('OK house_config tiles:', len(d['tiles']))"
```

Expected: prints `OK house_config tiles: 3` (or more).

- [ ] **Step 8: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/tenancy.py scripts/test_tenancy.py scripts/house_config_cli.py
git commit -m "feat(lisza): house housekeeping config seam (default + override) + api mode"
```

---

### Task 8: Bootstrap `_house` in production + end-to-end verification

**Files:**
- Create: `scripts/ensure_house.py`

The registry migration is lazy (runs on next `registry_db()`), but `_house` must be explicitly registered once against the prod `LISZA_HOME` so Admin has a book to read.

- [ ] **Step 1: Create the bootstrap script**

Create `scripts/ensure_house.py`:

```python
#!/usr/bin/env python3
"""One-shot: ensure the house tenant exists in this LISZA_HOME. Idempotent."""
import tenancy

if __name__ == "__main__":
    cid = tenancy.ensure_house()
    print(f"_house ready: client_id={cid}")
    print(f"book: {tenancy.resolve_db('_house')}")
```

- [ ] **Step 2: Run it against prod LISZA_HOME**

```bash
cd /home/workspace/LISZA/scripts && python3 ensure_house.py
```

Expected: prints `_house ready: client_id=...` and the book path; running it twice prints the same `client_id` (idempotent).

- [ ] **Step 3: Confirm the registry + book on disk**

```bash
cd /home/workspace/LISZA
python3 -c "import sqlite3; c=sqlite3.connect('lisza.db'); print(c.execute(\"SELECT slug, kind FROM clients WHERE slug='_house'\").fetchall())"
ls -la clients/_house/ledger.db
```

Expected: `[('_house', 'house')]` and the `ledger.db` file exists.

- [ ] **Step 4: Full end-to-end check (after Tasks 5–7 routes are live)**

```bash
# roster hides house
curl -s "https://dadadanja.zo.space/api/lisza?mode=clients" | python3 -c "import sys,json; d=json.load(sys.stdin); assert '_house' not in [c['slug'] for c in d['clients']]; print('roster OK')"
# admin can read house overview
curl -s -o /dev/null -w "house overview: %{http_code}\n" "https://dadadanja.zo.space/api/lisza?client=_house"
# house config served
curl -s "https://dadadanja.zo.space/api/lisza?mode=house_config" | python3 -c "import sys,json; print('config tiles:', len(json.load(sys.stdin)['tiles']))"
```

Expected: `roster OK`, `house overview: 200`, `config tiles: 3`.

- [ ] **Step 5: Browser smoke (the four sections)**

Open `https://dadadanja.zo.space/lisza/console`; walk Books → Admin → CRM → Client Management per Task 6 Step 8.

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/LISZA
git add scripts/ensure_house.py
git commit -m "chore(lisza): bootstrap house tenant + e2e verification script"
```

---

## Final Verification

- [ ] Full LISZA test suite green: `cd /home/workspace/LISZA/scripts && python3 -m pytest test_*.py -v`
- [ ] `git status` shows `scripts/ledger_tools.py` and `scripts/test_ledger_tools.py` still untracked and unmodified (operator's separate work — must not be staged).
- [ ] No push performed (LISZA stays local pending the data-wipe gate).
- [ ] Spec coverage confirmed: `kind` migration (idempotent, defaults to `client`) ✓; `register_client(kind=)` ✓; `ensure_house()` (idempotent, hidden, full schema) ✓; `list_clients` hides house ✓; `/api/lisza` `_house` allow-list + roster hide ✓; nav shell (4 destinations) ✓; Admin tabs on `_house` ✓; housekeeping config default + seam ✓; CRM + Client Management honest stubs ✓.

---

## Self-Review Notes (plan author)

1. **Spec coverage** — every In-scope bullet in the spec maps to a task: registry `kind` column → Task 1; `register_client` kind → Task 2; `ensure_house` → Task 3; `list_clients` exclude → Task 4; Admin tabs on `_house` → Task 6; housekeeping seam → Task 7; tests (migration idempotency / roster hiding / house resolvability / Admin render / stub honesty) → Tasks 1,3,4,6,8; flagged risk (`_house` validator allow-list) → Task 5.
2. **Type consistency** — `kind` is `TEXT NOT NULL DEFAULT 'client'` everywhere; `HOUSE_SLUG = "_house"` is the single source for the slug in Python; `SYSTEM_SLUGS`/`"_house"` in the route; `get_house_config()`/`set_house_config()` round-trip the `{"tiles": [...]}` shape used by the Housekeeping panel and the `mode=house_config` API; `ClientTabs` is reused by both `BooksSection` and `AdminSection` with the same tab keys.
3. **Out-of-scope honored** — no new engine/report/tax math; CRM + Client Management are placeholders only; payroll ingestion untouched; only the default housekeeping config ships.
4. **Known seam** — `refresh_all()` now skips `_house` (it iterates `list_clients`). Admin reads the house book directly rather than the cached `client_summary`, so the skipped refresh is fine; noted in Task 4 Step 5.
