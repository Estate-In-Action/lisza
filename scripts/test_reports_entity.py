import sqlite3
from pathlib import Path

import book_schema
import reports_entity


def _book_with_two_entities(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "ledger.db")
    con.executescript(
        """
        CREATE TABLE accounts(code TEXT PRIMARY KEY, name TEXT, type TEXT,
            sign_normal TEXT, grp TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE entries(id INTEGER PRIMARY KEY AUTOINCREMENT, entry_date TEXT,
            description TEXT, payee TEXT, source TEXT, status TEXT DEFAULT 'pending');
        CREATE TABLE splits(id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id INTEGER,
            account TEXT, dr REAL DEFAULT 0, cr REAL DEFAULT 0);
        INSERT INTO accounts VALUES('102','Checking','asset','debit','x',1);
        INSERT INTO accounts VALUES('400','Revenue','income','credit','x',1);
        """
    )
    book_schema.ensure_book_schema(con)
    # second (non-default) entity
    con.execute("INSERT INTO entities(name,type,is_default) VALUES('Loc B','location',0)")
    return con


def _post(con, entity_id, dr_acct, cr_acct, amt, intercompany=0):
    con.execute("INSERT INTO entries(entry_date,description,source,status,entity_id,is_intercompany) "
                "VALUES('2026-02-01','t','synthetic','posted',?,?)", (entity_id, intercompany))
    eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,?,0)", (eid, dr_acct, amt))
    con.execute("INSERT INTO splits(entry_id,account,dr,cr) VALUES(?,?,0,?)", (eid, cr_acct, amt))


def test_per_entity_balance_isolated(tmp_path):
    con = _book_with_two_entities(tmp_path)
    a = con.execute("SELECT id FROM entities WHERE is_default=1").fetchone()[0]
    b = con.execute("SELECT id FROM entities WHERE is_default=0").fetchone()[0]
    _post(con, a, "102", "400", 100)
    _post(con, b, "102", "400", 250)
    con.commit()
    bal_a = reports_entity.account_balance(con, "400", entity_id=a)
    bal_b = reports_entity.account_balance(con, "400", entity_id=b)
    assert bal_a == 100.0 and bal_b == 250.0


def test_consolidated_nets_out_intercompany(tmp_path):
    con = _book_with_two_entities(tmp_path)
    a = con.execute("SELECT id FROM entities WHERE is_default=1").fetchone()[0]
    b = con.execute("SELECT id FROM entities WHERE is_default=0").fetchone()[0]
    _post(con, a, "102", "400", 100)
    _post(con, b, "102", "400", 250)
    _post(con, a, "102", "400", 999, intercompany=1)  # must be excluded
    con.commit()
    consolidated = reports_entity.account_balance(con, "400", consolidated=True)
    assert consolidated == 350.0
