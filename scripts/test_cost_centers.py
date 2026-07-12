import json
import os
import sqlite3
import subprocess

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

import cost_centers
import post_entry
import tenancy


def _mk_client(tmp_path, monkeypatch, slug="acme"):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug=slug, display_name=slug.title())
    return slug


def _post(slug, splits, *, entry_date="2026-07-10", desc="test"):
    return post_entry.post_json(
        {"entry_date": entry_date, "description": desc, "post": True, "splits": splits},
        str(tenancy.resolve_db(slug)),
    )


def _split_ids(slug, entry_id):
    con = sqlite3.connect(tenancy.resolve_db(slug))
    try:
        return {r[0]: r[1] for r in con.execute(
            "SELECT account, id FROM splits WHERE entry_id=?", (entry_id,))}
    finally:
        con.close()


def _revenue(slug, amount=1000.0, **kw):
    r = _post(slug, [{"account": "110", "dr": amount, "cr": 0},
                     {"account": "400", "dr": 0, "cr": amount}], desc="revenue", **kw)
    return r["entry_id"]


def _expense(slug, amount=400.0, **kw):
    r = _post(slug, [{"account": "500", "dr": amount, "cr": 0},
                     {"account": "102", "dr": 0, "cr": amount}], desc="expense", **kw)
    return r["entry_id"]


# ---- registry -----------------------------------------------------------

def test_set_and_list_cost_center(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West Region")
    rows = cost_centers.list_cost_centers(slug)
    assert len(rows) == 1
    assert rows[0]["code"] == "WEST"
    assert rows[0]["name"] == "West Region"
    assert rows[0]["kind"] == "cost_center"


def test_set_cost_center_upserts(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    cost_centers.set_cost_center(slug, code="WEST", name="West Region", kind="department")
    rows = cost_centers.list_cost_centers(slug)
    assert len(rows) == 1
    assert rows[0]["name"] == "West Region"
    assert rows[0]["kind"] == "department"


def test_list_filters_by_kind(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West", kind="cost_center")
    cost_centers.set_cost_center(slug, code="APOLLO", name="Apollo", kind="project")
    projects = cost_centers.list_cost_centers(slug, kind="project")
    assert [r["code"] for r in projects] == ["APOLLO"]


def test_deactivate_keeps_row(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    cost_centers.deactivate_cost_center(slug, "WEST")
    assert cost_centers.list_cost_centers(slug) == []              # hidden from active
    allrows = cost_centers.list_cost_centers(slug, active_only=False)
    assert len(allrows) == 1 and allrows[0]["active"] == 0         # row preserved


# ---- tagging ------------------------------------------------------------

def test_tag_split_assigns_dimension(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    eid = _expense(slug)
    sid = _split_ids(slug, eid)["500"]
    cost_centers.tag_split(slug, sid, "WEST")
    rep = cost_centers.dimension_report(slug, start="2026-07-01", end="2026-07-31")
    west = next(c for c in rep["centers"] if c["code"] == "WEST")
    assert west["expense"] == 400.0
    assert west["net"] == -400.0


def test_tag_split_rejects_unknown_split(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    with pytest.raises(ValueError):
        cost_centers.tag_split(slug, 999999, "WEST")


def test_tag_split_rejects_unknown_center(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    eid = _expense(slug)
    sid = _split_ids(slug, eid)["500"]
    with pytest.raises(ValueError):
        cost_centers.tag_split(slug, sid, "NOPE")


def test_tag_split_rejects_inactive_center(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    cost_centers.deactivate_cost_center(slug, "WEST")
    eid = _expense(slug)
    sid = _split_ids(slug, eid)["500"]
    with pytest.raises(ValueError):
        cost_centers.tag_split(slug, sid, "WEST")


def test_tag_split_is_idempotent_reassign(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    cost_centers.set_cost_center(slug, code="EAST", name="East")
    eid = _expense(slug)
    sid = _split_ids(slug, eid)["500"]
    cost_centers.tag_split(slug, sid, "WEST")
    cost_centers.tag_split(slug, sid, "EAST")                      # reassign, not duplicate
    rep = cost_centers.dimension_report(slug, start="2026-07-01", end="2026-07-31")
    codes = {c["code"]: c for c in rep["centers"]}
    assert "WEST" not in codes                                    # no lingering WEST activity
    assert codes["EAST"]["expense"] == 400.0


def test_untag_split(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    eid = _expense(slug)
    sid = _split_ids(slug, eid)["500"]
    cost_centers.tag_split(slug, sid, "WEST")
    cost_centers.untag_split(slug, sid)
    rep = cost_centers.dimension_report(slug, start="2026-07-01", end="2026-07-31")
    assert all(c["code"] != "WEST" for c in rep["centers"])
    assert rep["unassigned"]["expense"] == 400.0


def test_tag_entry_tags_all_splits(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    eid = _expense(slug)
    n = cost_centers.tag_entry(slug, eid, "WEST")
    assert n == 2                                                 # both legs tagged
    rep = cost_centers.dimension_report(slug, start="2026-07-01", end="2026-07-31")
    west = next(c for c in rep["centers"] if c["code"] == "WEST")
    # 500 expense leg contributes 400 expense; 102 (asset) leg contributes nothing to P&L
    assert west["expense"] == 400.0


# ---- dimension report ---------------------------------------------------

def test_report_groups_income_and_expense(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    reid = _revenue(slug, 1000.0)
    eeid = _expense(slug, 400.0)
    cost_centers.tag_split(slug, _split_ids(slug, reid)["400"], "WEST")
    cost_centers.tag_split(slug, _split_ids(slug, eeid)["500"], "WEST")
    rep = cost_centers.dimension_report(slug, start="2026-07-01", end="2026-07-31")
    west = next(c for c in rep["centers"] if c["code"] == "WEST")
    assert west["income"] == 1000.0
    assert west["expense"] == 400.0
    assert west["net"] == 600.0


def test_report_unassigned_bucket(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    _expense(slug, 250.0)                                          # never tagged
    rep = cost_centers.dimension_report(slug, start="2026-07-01", end="2026-07-31")
    assert rep["unassigned"]["expense"] == 250.0
    assert rep["centers"] == []


def test_report_respects_period(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    eid = _expense(slug, 400.0, entry_date="2026-06-15")          # out of window
    cost_centers.tag_split(slug, _split_ids(slug, eid)["500"], "WEST")
    rep = cost_centers.dimension_report(slug, start="2026-07-01", end="2026-07-31")
    assert rep["centers"] == []
    assert rep["unassigned"]["expense"] == 0.0


def test_report_posted_only(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    # pending (unbalanced-safe but post=False) entry should not count
    r = post_entry.post_json(
        {"entry_date": "2026-07-10", "description": "pending", "post": False,
         "splits": [{"account": "500", "dr": 400.0, "cr": 0},
                    {"account": "102", "dr": 0, "cr": 400.0}]},
        str(tenancy.resolve_db(slug)))
    sid = _split_ids(slug, r["entry_id"])["500"]
    cost_centers.tag_split(slug, sid, "WEST")
    rep = cost_centers.dimension_report(slug, start="2026-07-01", end="2026-07-31")
    assert rep["centers"] == []
    assert rep["unassigned"]["expense"] == 0.0


def test_cost_center_pnl_single(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    eid = _expense(slug, 400.0)
    cost_centers.tag_split(slug, _split_ids(slug, eid)["500"], "WEST")
    pnl = cost_centers.cost_center_pnl(slug, "WEST", start="2026-07-01", end="2026-07-31")
    assert pnl["code"] == "WEST"
    assert pnl["expense"] == 400.0
    assert pnl["net"] == -400.0
    assert any(line["account"] == "500" for line in pnl["lines"])


# ---- CLI ----------------------------------------------------------------

def test_cli_cc_json_report(tmp_path, monkeypatch):
    slug = _mk_client(tmp_path, monkeypatch)
    cost_centers.set_cost_center(slug, code="WEST", name="West")
    eid = _expense(slug, 400.0)
    cost_centers.tag_split(slug, _split_ids(slug, eid)["500"], "WEST")
    payload = json.dumps({"action": "report", "start": "2026-07-01", "end": "2026-07-31"})
    out = subprocess.run(
        ["python3", "cost_centers.py", "cc-json", "--client", slug, "--payload", payload],
        cwd=SCRIPTS_DIR, capture_output=True, text=True,
        env={**os.environ, "LISZA_HOME": str(tmp_path)},
    )
    res = json.loads(out.stdout)
    assert res["ok"] is True
    west = next(c for c in res["centers"] if c["code"] == "WEST")
    assert west["expense"] == 400.0
