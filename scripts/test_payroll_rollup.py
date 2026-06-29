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
    assert w["box1_wages"] == 2000.0
    assert w["box2_fed_wh"] == 220.0
    assert w["box3_ss_wages"] == 2000.0
    assert w["box4_ss_tax"] == 124.0
    assert w["box5_medi_wages"] == 2000.0
    assert w["box6_medi_tax"] == 29.0
    assert w["box16_state_wages"] == 2000.0
    assert w["box17_state_tax"] == 60.0


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
    assert rows[0]["box1_wages"] == 2000.0


def test_form941_quarter_bucketing_and_lines():
    con = _con()
    _seed_one_employee(con)
    con.executescript(
        """INSERT INTO payroll_runs(id,entity_id,pay_date)
             VALUES(3,1,'2026-04-10');
           INSERT INTO payroll_lines(run_id,employee_id,gross,fed_wh,ss_ee,ss_er,
                                     medi_ee,medi_er,addl_medi,state_wh,futa,suta,net)
             VALUES(3,1,800,80,49.6,49.6,11.6,11.6,0,24,3.36,16.8,635.84);""")
    con.commit()
    rows = pr.form941_rollup(con, "2026")
    assert len(rows) == 2
    q1 = next(r for r in rows if r["quarter"] == 1)
    assert q1["entity"] == "Guitar Works"
    assert q1["year"] == 2026
    assert q1["wages"] == 2000.0
    assert q1["fed_wh"] == 220.0
    assert q1["ss_tax"] == 248.0
    assert q1["medi_tax"] == 58.0
    assert q1["total_liability"] == 526.0
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
    assert s["net"] == 1567.0
    assert s["fed_wh"] == 220.0
    assert s["employee_fica"] == 153.0
    assert s["employer_fica"] == 153.0
    assert s["employer_tax_total"] == 203.4


def test_build_payroll_active_block():
    con = _con()
    _seed_one_employee(con)
    out = pr.build_payroll(con)
    assert out["status"] == "active"
    assert out["year"] == 2026
    assert out["summary"]["employees"] == 1
    assert len(out["w2"]) == 1
    assert out["w2"][0]["employee"] == "Dana Whitlock"
    assert len(out["form941"]) == 1
    assert out["form941"][0]["quarter"] == 1
