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
