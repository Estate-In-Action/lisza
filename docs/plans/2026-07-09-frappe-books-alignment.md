## LISZA → Frappe Books Alignment (Phase 1)

Goal: make LISZA feel less like a generated reporting console and more like a
real day-to-day accounting product, while keeping LISZA's existing strengths:
local-first SQLite, additive ledger history, synthetic demo data, and
bookkeeper-first workflow.

This is not a framework migration plan. We are borrowing product structure and
operator ergonomics, not Vue/Electron.

### What we should copy

1. Document-centered accounting workflow
   - Frappe Books feels like accounting software because the operator works with
     documents: invoices, bills, payments, journal entries, parties.
   - LISZA already has pieces of this in the engine, but the UI still feels like
     a dashboard-first inspector.

2. Unified party model
   - One party directory with roles: customer, vendor, both, lead.
   - This should become the shared substrate for CRM, sales, purchasing, and
     client management.

3. Immutable ledger posture
   - Reversals and adjustments, never destructive edits.
   - LISZA already believes this; the surface should make that rule visible.

4. Schema-driven configurability
   - "Fully configurable" should mean changing field definitions and sections,
     not hand-writing every form.
   - Start with a light registry, not a meta-framework.

5. Number series and document identity
   - Invoice numbers, bill numbers, journal numbers, payment references should
     be first-class data, not ad hoc strings.

6. Tree chart of accounts
   - LISZA can outgrow the flat COA. Parent/child account grouping is the clean
     upgrade path once the core operating shell is in place.

### What we should not copy

- Electron desktop packaging
- Vue-specific architecture
- Full framework-level model generation before the product shell proves itself
- Large engine rewrites before the UI exposes the workflows we already have

### The right first slice

Build a read-only "accounting workspace" shell on top of the current LISZA
data, then wire write actions into it incrementally.

Why this first:
- Lowest risk: it reuses existing generated JSON and existing engine modules.
- Highest leverage: it changes how LISZA feels immediately.
- Honest sequencing: the UI shell can expose where the workflow gaps actually
  are before we invent more backend.

### Phase 1 build order

1. Left-nav accounting shell
   - Top-level destinations:
     - Dashboard
     - Sales
     - Purchases
     - Banking
     - Accounting
     - Contacts
     - Payroll
     - Reports
     - Settings
   - Keep Admin/CRM/Client Management folded into this structure instead of
     growing parallel top-level worlds.

2. Document indexes before document editors
   - Add list views for:
     - Invoices
     - Bills
     - Payments
     - Journal Entries
     - Parties
   - Start read-only if needed. The operator should be able to browse the book
     by business object, not only by client tile.

3. Shared status language
   - Draft
   - Submitted
   - Posted
   - Paid
   - Overdue
   - Reversed
   - This gives LISZA the operational feel Frappe Books has.

4. Number-series substrate
   - Minimal table + helper for invoice, bill, journal, payment numbering.
   - No generalized engine beyond current document types.

5. Lightweight schema registry
   - Per document type: sections, visible fields, quick fields, search fields.
   - Use it to render detail panels and later forms.
   - Keep it local and explicit; do not build a generic app-builder.

### Concrete first implementation target

Target: replace the current dashboard-first shell with a proper accounting
workspace shell while reusing existing client/dashboard/detail JSON.

Definition of done for that slice:
- A persistent left nav exists.
- The current dashboard becomes one destination, not the whole app.
- At least three document indexes exist and are navigable:
  - Invoices
  - Bills
  - Journal Entries
- A Party directory exists as a first-class destination.
- No ledger math changes are required for this slice.

### Files likely touched when implementation starts

- LISZA front-end surface for the console shell
- `scripts/build_client_detail.py`
- `scripts/build_dashboard.py`
- `scripts/party_directory.py`
- sales / purchasing / journal data projection helpers

### Guardrails

- Keep synthetic data only until explicitly told otherwise.
- No destructive edits to posted ledger history.
- Prefer additive registries and projections over schema churn.
- Do not migrate LISZA toward a framework clone; copy the product grammar, not
  the stack.
