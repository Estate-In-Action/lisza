"""
Export raw_transactions from lisza.duckdb → lisza_raw.db (SQLite)
so the Bun/Hono API route can query it with bun:sqlite.
Run after every ingest_txns.py run.
"""
import duckdb
import sqlite3
from pathlib import Path

DUCK_PATH   = Path(__file__).parent.parent / "data" / "lisza.duckdb"
SQLITE_PATH = Path(__file__).parent.parent / "data" / "lisza_raw.db"

duck = duckdb.connect(str(DUCK_PATH), read_only=True)
rows = duck.execute("""
    SELECT id, txn_date::VARCHAR, post_date::VARCHAR, description,
           amount, is_debit::INTEGER, raw_category, normalized_category,
           account, cardholder, source_file,
           ingested_at::VARCHAR
    FROM raw_transactions
    ORDER BY txn_date
""").fetchall()
duck.close()

sql = sqlite3.connect(str(SQLITE_PATH))
sql.execute("""
CREATE TABLE IF NOT EXISTS raw_transactions (
    id TEXT PRIMARY KEY,
    txn_date TEXT, post_date TEXT,
    description TEXT, amount REAL, is_debit INTEGER,
    raw_category TEXT, normalized_category TEXT,
    account TEXT, cardholder TEXT,
    source_file TEXT, ingested_at TEXT
)
""")
sql.execute("DELETE FROM raw_transactions")
sql.executemany("""
    INSERT INTO raw_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
""", rows)
sql.execute("CREATE INDEX IF NOT EXISTS idx_txn_date ON raw_transactions(txn_date)")
sql.execute("CREATE INDEX IF NOT EXISTS idx_account ON raw_transactions(account)")
sql.execute("CREATE INDEX IF NOT EXISTS idx_category ON raw_transactions(normalized_category)")
sql.commit()

count = sql.execute("SELECT COUNT(*) FROM raw_transactions").fetchone()[0]
print(f"Exported {count} rows to {SQLITE_PATH}")
sql.close()
