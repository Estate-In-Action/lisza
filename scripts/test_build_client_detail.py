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


def test_mask_ein_keeps_last_four():
    assert bcd.mask_ein("47-2201234") == "••-•••1234"


def test_mask_ein_none_and_short():
    assert bcd.mask_ein(None) is None
    assert bcd.mask_ein("") is None
    assert bcd.mask_ein("12") is None


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
    repo = "/home/workspace/LISZA-spec3a"
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


def test_client_isolation(tmp_path, monkeypatch):
    """Build for client_a must reflect only client_a's data, not client_b's."""
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    import tenancy
    importlib.reload(tenancy)
    importlib.reload(bcd)
    monkeypatch.setattr(tenancy, "COA_PATH",
                        __import__("pathlib").Path("/home/workspace/LISZA") / "coa.csv")

    # Register two clients
    tenancy.register_client(slug="client_a", display_name="Alpha Co", entity_type="llc")
    tenancy.register_client(slug="client_b", display_name="Beta Co", entity_type="llc")

    con_a = __import__("sqlite3").connect(tenancy.resolve_db("client_a"))
    con_a.executescript(
        """INSERT INTO entries(id,entry_date,description,source,status)
             VALUES(1,'2026-06-09','rev','x','posted');
           INSERT INTO splits(entry_id,account,dr,cr) VALUES(1,'400',0,1000);
           INSERT INTO invoices(party,issue_date,due_date,amount,status)
             VALUES('Cust-A','2026-05-01','2026-05-15',1111,'open');
           INSERT INTO bills(party,issue_date,due_date,amount,status)
             VALUES('Vend-A','2026-05-01','2026-05-20',222,'unpaid');""")
    con_a.commit()
    con_a.close()

    con_b = __import__("sqlite3").connect(tenancy.resolve_db("client_b"))
    con_b.executescript(
        """INSERT INTO entries(id,entry_date,description,source,status)
             VALUES(1,'2026-06-09','rev','x','posted');
           INSERT INTO splits(entry_id,account,dr,cr) VALUES(1,'400',0,9999);
           INSERT INTO invoices(party,issue_date,due_date,amount,status)
             VALUES('Cust-B','2026-05-01','2026-05-15',8888,'open');
           INSERT INTO bills(party,issue_date,due_date,amount,status)
             VALUES('Vend-B','2026-05-01','2026-05-20',7777,'unpaid');""")
    con_b.commit()
    con_b.close()

    d = bcd.build_client_detail("client_a")

    # Must reflect client_a's figures only
    assert d["slug"] == "client_a"
    assert d["ar"]["open_total"] == 1111.0, (
        f"Expected client_a AR 1111.0, got {d['ar']['open_total']}")
    assert d["ap"]["open_total"] == 222.0, (
        f"Expected client_a AP 222.0, got {d['ap']['open_total']}")

    # Must NOT contain any of client_b's figures
    assert d["ar"]["open_total"] != 8888.0
    assert d["ap"]["open_total"] != 7777.0


def test_aging_sum_equals_open_total_ar_and_ap(tmp_path, monkeypatch):
    """AR aging buckets must sum to open_total, and AP aging buckets must sum to
    open_total, in a full end-to-end build_client_detail call."""
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    import tenancy
    importlib.reload(tenancy)
    importlib.reload(bcd)
    monkeypatch.setattr(tenancy, "COA_PATH",
                        __import__("pathlib").Path("/home/workspace/LISZA") / "coa.csv")

    tenancy.register_client(slug="aging_co", display_name="Aging Co", entity_type="llc",
                            ein="47-2201234", filing_cadence="quarterly")
    db = tenancy.resolve_db("aging_co")
    con = __import__("sqlite3").connect(db)
    con.executescript(
        """INSERT INTO entries(id,entry_date,description,source,status)
             VALUES(1,'2026-06-09','rev','x','posted');
           INSERT INTO splits(entry_id,account,dr,cr) VALUES(1,'400',0,500);
           -- Two open invoices spanning different aging buckets
           INSERT INTO invoices(party,issue_date,due_date,amount,status)
             VALUES('Cust1','2026-05-01','2026-06-09',300,'open');
           INSERT INTO invoices(party,issue_date,due_date,amount,status)
             VALUES('Cust2','2026-04-01','2026-05-01',700,'open');
           -- Two unpaid bills spanning different aging buckets
           INSERT INTO bills(party,issue_date,due_date,amount,status)
             VALUES('Vend1','2026-05-01','2026-06-09',150,'unpaid');
           INSERT INTO bills(party,issue_date,due_date,amount,status)
             VALUES('Vend2','2026-04-01','2026-05-01',250,'unpaid');""")
    con.commit()
    con.close()

    d = bcd.build_client_detail("aging_co")

    ar = d["ar"]
    ap = d["ap"]

    assert sum(ar["aging"].values()) == ar["open_total"], (
        f"AR aging sum {sum(ar['aging'].values())} != open_total {ar['open_total']}")
    assert sum(ap["aging"].values()) == ap["open_total"], (
        f"AP aging sum {sum(ap['aging'].values())} != open_total {ap['open_total']}")


def test_write_client_detail_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    import tenancy
    importlib.reload(tenancy)
    importlib.reload(bcd)
    monkeypatch.setattr(tenancy, "COA_PATH",
                        __import__("pathlib").Path("/home/workspace/LISZA-spec3a") / "coa.csv")
    tenancy.register_client(slug="acme", display_name="Acme", entity_type="llc")
    out = bcd.write_client_detail("acme", path=tmp_path / "acme.json")
    assert out.exists()
    import json
    j = json.loads(out.read_text())
    assert j["slug"] == "acme"
