# Receipt Classifier Review UI — Design Spec

**Date:** 2026-05-18
**Persona:** Bookkeeper
**Project:** LISZA — strict isolation from HOPE/HIP
**Origin:** Cookbook applicability review (2026-05-17), spaces/111, score 3
**Status:** Design approved, pending implementation plan

## Problem

LISZA Phase 2 intake is operational: `telegram_intake.py` saves receipt photos to `inbox/telegram/`, `code_receipt.py` (Pixtral vision) parses them and writes `parsed_json` / `suggested_account` / `suggested_amount` into `pending_inbox`, transitioning status `new` → `reviewed`. But **nothing actually posts the reviewed row to the double-entry ledger.** It sits in `pending_inbox` indefinitely. the owner has to eyeball the JSON manually and write `INSERT` statements — defeating the whole intake automation.

PLAN.md Phase 2 lists two open todos:
- `post_pending.py` — promote `reviewed` row to a posted entry
- `Review/approve UI` — Sheet `_Pending` tab buttons OR Telegram inline keyboard

This spec resolves both, with a hybrid surface design (Telegram for single-receipt mobile review, Sheet for batch review of accumulated receipts).

## Goal

Make the pending → posted transition a one-tap (TG) or one-cell-edit (Sheet) action that:
- Respects the LISZA 5 Rules (especially Rule #2 "every posted entry must balance" and Rule #3 "we add, we don't subtract").
- Works from the LISZA Books Telegram chat (mobile, low friction) AND the Client Books Google Sheet (batch, full table view).
- Never double-posts a receipt regardless of which surface fires first.
- Allows editing the parsed fields before posting, since Pixtral isn't perfect.

## Non-Goals

- A standalone web review page (cookbook spaces/111's literal form). The hybrid TG + Sheet design covers the same UX needs without introducing a third concept or a new long-running service.
- Bank/CC CSV auto-reconciliation. Out of scope (deferred in PLAN.md to Phase 4).
- Changes to `pending_inbox` schema. Existing columns + `notes` free-text are sufficient.
- Changes to `entries` / `splits` schema. Double-entry rules already enforced by `trg_post_balance_check`.
- HOPE/HIP touch points. Strict isolation — this spec does not reference, depend on, or modify anything outside `LISZA/`.

## Design

### Architecture

```
                     ┌──────────────────────────────┐
                     │   pending_inbox (SQLite)      │
                     │   status: new → reviewed →    │
                     │           posted | rejected   │
                     └────────┬─────────────────────┘
                              │
                ┌─────────────┼─────────────┐
                │             │             │
        ┌───────▼──────┐ ┌────▼─────┐  ┌────▼────────┐
        │ telegram_    │ │ post_    │  │ sheet_sync  │
        │ intake.py    │ │ pending  │  │   .py       │
        │ (extend)     │ │   .py    │  │  (extend)   │
        │              │ │  (NEW)   │  │             │
        │ inline       │ │          │  │ _Pending    │
        │ keyboard     │ │ library  │  │ action col  │
        │ callbacks    │◄┤ + CLI    ├──┤ bidir poll  │
        └──────────────┘ └──────────┘  └─────────────┘
              ▲                              ▲
              │                              │
        LISZA Books TG chat                Client Books Sheet
        (mobile, single)            (browser, batch)
```

### Shared Backend — `post_pending.py` (new)

Library + CLI. Single source of truth for `pending_inbox` state transitions.

```python
def approve_row(row_id: int, *,
                account_override: str | None = None,
                amount_override: float | None = None,
                payment_account: str = "102") -> int | None:
    """
    Promote pending_inbox row to a posted double-entry transaction.
    Returns entries.id on success, None if row was not in 'reviewed' state.

    Raises ValueError if account_override is not in chart of accounts,
    or if amount is non-positive, or if balance check trigger fires.

    Concurrency-safe via BEGIN IMMEDIATE + status guard:
      UPDATE pending_inbox SET status='posted' WHERE id=? AND status='reviewed'
    A 0-row update means another caller (TG or Sheet) won the race; we return None.
    """

def reject_row(row_id: int, *, reason: str = "") -> bool:
    """Set status='rejected', append reason to notes. Returns True on transition."""

def edit_row(row_id: int, *, field: str, new_value) -> bool:
    """
    Update one of suggested_account / suggested_amount / parsed_json (date, payee, memo).
    Row stays in 'reviewed' status. Returns True on success.
    Appends '...edited via {source} at {ts}' to notes.
    """
```

**CLI:**
```bash
python3 post_pending.py --row-id 42 --approve
python3 post_pending.py --row-id 42 --approve --account 591 --amount 45.00
python3 post_pending.py --row-id 42 --reject --reason "duplicate of #38"
python3 post_pending.py --row-id 42 --edit account=591
```

**State machine (enforced in SQL):**
- `new` is set by `telegram_intake.py` on photo arrival. Out of our scope.
- `reviewed` is set by `code_receipt.py` after vision parse. Existing.
- `posted` and `rejected` are terminal. Per Rule #3, no row is ever deleted; corrections happen via offsetting entries.
- Only `reviewed` rows accept `approve` / `reject` / `edit`. Status guards in WHERE clauses.

### Surface A — Telegram inline keyboard (extends `telegram_intake.py`)

After `code_receipt.py` transitions a row to `reviewed`, `telegram_intake.py` posts to the LISZA Books chat:

```
[receipt photo thumbnail]
🧾 Receipt #42
   Payee: AMAZON.COM
   Amount: $42.50
   Date:   2026-05-18
   Suggested: 591 Supplies (DR) ← 260 Mastercard (CR)
   Confidence: 0.87

   [✅ Approve]  [✏️ Edit]  [❌ Reject]
```

**Callback IDs:**
- `approve:42` → `from post_pending import approve_row; approve_row(42)` → edit message to `✅ POSTED as entry #N`.
- `edit:42` → swap keyboard to field-picker:
  ```
  [Account] [Amount] [Date] [Payee] [Memo] [⬅️ Back]
  ```
  → user taps `Amount` → bot replies "Reply with new amount" → user types `45.00` → `edit_row(42, field='amount', new_value=45.00)` → bot edits original message with updated values + restores approve/reject keyboard. Edit can be repeated.
- `reject:42` → bot asks "Reply with reason (or send /skip)" → `reject_row(42, reason=text)` → edit message to `❌ REJECTED: <reason>`.

**Per-callback edit state:** stored as JSON in `pending_inbox.notes` so a session restart doesn't drop state. One human owns the bot — no per-user keying needed.

**Error path:** if `approve_row` returns None (row already actioned via Sheet), bot edits message to `⚠️ Already actioned at HH:MM (status=<actual>)`.

### Surface B — Sheet bidirectional (extends `sheet_sync.py`)

**New `_Pending` tab schema:**

```
| id | received_at | source | payee | amount | suggested_account | confidence | action | action_result |
                                                                                  ↑user↑   ↑DB↑
```

**`action` cell grammar (user-typed):**
- `approve` → `approve_row(id)`
- `approve:account=591` or `approve:amount=45.00` or `approve:account=591,amount=45.00` → `approve_row(id, account_override=..., amount_override=...)`
- `reject` or `reject:duplicate of #38` → `reject_row(id, reason=...)`
- `edit:account=591` (any single field) → `edit_row(id, field='account', new_value='591')`
- empty → no action

Parsing: split on first `:` to get action verb; split remainder on `,` then on `=` for key/value pairs. Tolerant of whitespace. Validation failures write to `action_result` and leave the cell for the user to fix.

**`action_result` cell (DB-written):**
- `✅ POSTED as #N at 15:32 UTC`
- `❌ REJECTED: <reason>`
- `✏️ EDITED: account=591 → re-review`
- `⚠️ Already posted via Telegram at 15:30 UTC`
- `⚠️ ERROR: account 999 not in chart of accounts`

**New function in `sheet_sync.py`:**

```python
def process_sheet_actions(ws, conn) -> dict[str, int]:
    """
    Read _Pending rows from Sheet. For each with non-empty action and
    empty action_result, parse + dispatch to post_pending functions.
    Write result back. Clear action cell on success (leaves error visible).
    Returns counters for log: {'approved': n, 'rejected': n, 'edited': n, 'errors': n}.
    """
```

**Concurrency note:** `process_sheet_actions` reads the sheet, calls `post_pending` functions (which respect SQL state guards), then writes back per-row. A TG action that fires between the read and the dispatch will cause the dispatch to return None, which writes a `⚠️ Already actioned` result — correct outcome.

**CLI flag:** `sheet_sync.py --bidirectional` runs push (DB→Sheet) followed by pull (Sheet→DB). Default behavior remains push-only for backward compatibility.

**Cron entry:**
```
*/2 * * * * /usr/local/bin/python3 /home/workspace/LISZA/scripts/sheet_sync.py --bidirectional >> /home/workspace/LISZA/logs/sheet_sync.log 2>&1
```

2-minute lag is acceptable for batch review. Faster cadence (e.g. 30s) hits Google Sheets API quota for marginal UX gain.

### Concurrency Model

**Race:** TG callback fires `approve_row(42)` at T+0.1s. Sheet pull fires `approve_row(42)` at T+1.5s (poll cycle).

**Resolution — single SQL guard:**

```sql
BEGIN IMMEDIATE;
UPDATE pending_inbox SET status='posted', entry_id=?
  WHERE id=? AND status='reviewed';
-- Caller checks rowcount; if 0, someone else already won.
```

- **TG wins** (first call): `rowcount=1`, inserts `entries` + `splits`, commits. Edits message to `✅ POSTED as #N`.
- **Sheet loses** (second call): `rowcount=0` because status is now `posted`. `approve_row` returns None. `process_sheet_actions` writes `⚠️ Already posted via Telegram at HH:MM` to `action_result`, clears `action` cell.
- **No double-post.** `entries` table has exactly one row for receipt #42.

Same model handles reject/approve race, edit/approve race, etc. The SQL status guard is the single point of truth.

### File Changes

| File | Change | Lines | Type |
|---|---|---|---|
| `LISZA/scripts/post_pending.py` | **NEW** — library + CLI for state transitions. | ~250 | New (pre-approved in PLAN.md Phase 2 todos) |
| `LISZA/scripts/telegram_intake.py` | Extend — post inline keyboard after `code_receipt`, handle approve/edit/reject callbacks, edit-state cleanup. | +180 | Extension |
| `LISZA/scripts/sheet_sync.py` | Extend — add `action` / `action_result` columns, `process_sheet_actions()` function, `--bidirectional` CLI flag. | +120 | Extension |
| `LISZA/ledger.db` | No schema change. | 0 | None |
| `crontab` | +1 entry: `*/2 * * * *` `sheet_sync.py --bidirectional` | +1 | Crontab |
| `LISZA/logs/sheet_sync.log` | New log path (auto-created by cron append). | new | Log |
| `LISZA/PLAN.md` | Update Phase 2 checkboxes: `post_pending.py` ✅, `Review/approve UI` ✅. | update | Docs |
| `LISZA-dev/` | Bootstrap directory (does not currently exist) + mirror all three script changes per Rule #5. Includes a fresh copy of `ledger.db` (NOT symlinked). | bootstrap + mirror | Dev sandbox |

**Persona discipline:** all changes are LISZA-scoped. No file outside `LISZA/` or `LISZA-dev/` is touched.

## Testing & Deploy

0. **Bootstrap `LISZA-dev/`** *(one-time, if not done yet — PLAN.md Rule #5 references this sandbox but it doesn't exist as of this spec)*. Create `LISZA-dev/scripts/` (copy from `LISZA/scripts/`) and `LISZA-dev/ledger.db` as a fresh copy of `LISZA/ledger.db` (NOT a symlink — must be isolated so destructive tests don't corrupt prod). Confirm with `sqlite3 LISZA-dev/ledger.db "SELECT count(*) FROM entries"` matches prod at copy time. After this spec ships, `LISZA-dev/` becomes the standard test sandbox going forward.
1. **Backend first.** Build `post_pending.py` in `LISZA-dev/scripts/` against `LISZA-dev/ledger.db`. Unit tests:
   - `approve_row(42)` on a `reviewed` row → returns int, row status becomes `posted`, `entries` row exists, `splits` balance.
   - `approve_row(42)` again → returns None, no second `entries` row created.
   - `approve_row(99999)` (non-existent) → returns None, no error.
   - `approve_row(N, account_override='ZZZ')` → raises ValueError.
   - `reject_row` / `edit_row` analogous.
   - Concurrency test: two shell processes call `approve_row(42)` simultaneously (`&` background). Exactly one creates an entries row.

2. **TG surface in dev.** Extend `LISZA-dev/scripts/telegram_intake.py`. Temporarily edit `LISZA-dev/config.json` to point at a test Telegram chat (not LISZA Books group). Walk through:
   - Send a test receipt photo → see inline keyboard appear.
   - Tap Approve → message edits to `✅ POSTED`. Verify in `ledger.db`.
   - Send another → tap Edit → tap Amount → reply "99.00" → see message update. Tap Approve → verify amount is 99.00 in entries.
   - Send another → tap Reject → reply "test" → see `❌ REJECTED`.
   - Send another → tap Approve, then tap Approve again on the same already-actioned message → see `⚠️ Already actioned`.

3. **Sheet surface in dev.** Duplicate the Personal Accounts spreadsheet for testing. Run `sheet_sync.py --bidirectional` once to seed schema. Manually type test actions in cells. Verify `action_result` writes back, `action` clears on success, errors persist `action` for fixing.

4. **Hybrid concurrency.** With one row in `reviewed`: simultaneously (a) tap TG Approve, (b) type `approve` in Sheet, (c) wait 2 min. Verify TG message says POSTED, Sheet `action_result` says "Already posted via Telegram", `entries` has exactly one row.

5. **Promote to prod.** `diff LISZA-dev/scripts/{post_pending,telegram_intake,sheet_sync}.py LISZA/scripts/` — copy with confirmation. Restart `fin-telegram-intake` user service to pick up the extended `telegram_intake.py`.

6. **Live test.** Send a real receipt via the LISZA Books Telegram chat. Approve via TG. Verify ledger has the entry. Verify the Personal Accounts `_Pending` tab on next 2-min sync reflects the state change. Cross-check `v_check_register` shows the new transaction.

7. **PLAN.md updates.** Check off `post_pending.py` and `Review/approve UI` boxes in Phase 2. Commit.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| TG callback handler crashes mid-edit, leaves row in inconsistent edit-state | All state in `pending_inbox.notes` as JSON; on next callback, parser tolerates missing keys; user can always tap Cancel or restart edit. Status guard means the row is recoverable. |
| Sheet polling double-fires (two `sheet_sync.py --bidirectional` overlap) | Cron is `*/2 *` — slowest realistic case is 2 simultaneous fires. SQL status guard prevents double-action. Lockfile is a future hardening. |
| Pixtral mis-parses amount badly (off by factor of 100) | Edit flow exists for exactly this case. Confidence score surfaced in TG message so user knows when to verify. |
| User types malformed action in Sheet (e.g. `approve:acount=591`) | Parser writes `⚠️ ERROR: unknown field 'acount'` to action_result. User fixes typo; next poll succeeds. |
| Google Sheets API quota burned | 2-min cadence × 4 sheet reads/writes per cycle = ~120 ops/hour, well under free-tier limits. |
| `LISZA_SHEETS_SA_JSON` revoked or expired | Existing failure mode — `sheet_sync.py --bidirectional` exits non-zero with auth error; log shows it; cron silently retries. No data loss because the source of truth is `ledger.db`. |
| TG bot offline (network, token revoked) | Inline keyboard messages get sent on next bot restart. Sheet surface is fully independent — review backlog still actionable. |
| Concurrent edits to the same row's notes JSON | SQLite `notes` updates are atomic single-statement UPDATEs. Last write wins, which is fine because notes is human-audit only. |

## Acceptance Criteria

- [ ] `post_pending.py --row-id N --approve` posts a balanced double-entry transaction and sets `pending_inbox.status='posted'`.
- [ ] Calling `approve_row` twice in parallel on the same row creates exactly one `entries` row.
- [ ] TG inline keyboard appears for every new `reviewed` row.
- [ ] TG Edit → Amount → reply flow updates `suggested_amount` and re-shows approve keyboard.
- [ ] Sheet `_Pending` tab has `action` + `action_result` columns; typing `approve` in action posts the row.
- [ ] Hybrid concurrency test passes: TG wins, Sheet writes "Already posted" message, one entry exists.
- [ ] Cron `*/2 *` entry installed; one full hour of `sheet_sync.log` shows expected cadence.
- [ ] `LISZA/PLAN.md` Phase 2 checkboxes updated.

## Future Hooks (not in this spec)

- Telegram batch approve: `/approve_all` command for the top N pending. Useful for trusted repeat payees.
- Auto-approve via `payee_rules` table when confidence > 0.9 and rule match exists. Touches `code_receipt.py` more than this UI spec.
- Gmail intake (PLAN.md Phase 2 still open) reuses `post_pending` library directly — no UI changes needed.
- Per-receipt receipt-image URL in TG so the photo isn't re-uploaded each time.
