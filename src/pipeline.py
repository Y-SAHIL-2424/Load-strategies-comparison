"""Core pipeline: ingest a file, then load it with three different strategies."""
import csv
import time
from datetime import datetime
import pandas as pd
from pathlib import Path

import duckdb

from src.validate import validate_rows, check_schema


COLS = "order_id, customer_id, order_date, amount, status, updated_at"
TARGETS = {
    "full_refresh": "orders_full",
    "incremental_merge": "orders_merge",
    "partition_replace": "orders_partition",
}


def connect(db_path):
    return duckdb.connect(str(db_path))


def init_schema(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS source_orders (
            order_id VARCHAR, customer_id VARCHAR, order_date DATE,
            amount DECIMAL(10,2), status VARCHAR, updated_at TIMESTAMP,
            run_id VARCHAR, source_file VARCHAR)""")
    for table in TARGETS.values():
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                order_id VARCHAR, customer_id VARCHAR, order_date DATE,
                amount DECIMAL(10,2), status VARCHAR, updated_at TIMESTAMP,
                source_file VARCHAR)""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS rejects (
            order_id VARCHAR, customer_id VARCHAR, order_date VARCHAR,
            amount VARCHAR, status VARCHAR, updated_at VARCHAR,
            reject_reason VARCHAR, run_id VARCHAR, source_file VARCHAR)""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS run_log (
            run_id VARCHAR, strategy VARCHAR, source_file VARCHAR,
            started_at TIMESTAMP, seconds DOUBLE,
            rows_in INTEGER, rows_rejected INTEGER, duplicates_dropped INTEGER,
            rows_in_target INTEGER, status VARCHAR, error VARCHAR)""")
    con.execute(f"""
        CREATE OR REPLACE VIEW latest_orders AS
        SELECT {COLS}, source_file FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY order_id ORDER BY updated_at DESC, source_file DESC) AS rn
            FROM source_orders) WHERE rn = 1""")


def load_customer_ids(customers_path):
    with open(customers_path, newline="", encoding="utf-8") as f:
        return {r["customer_id"] for r in csv.DictReader(f)}


def ingest_file(con, orders_path, customer_ids, run_id):
    """Read a CSV, validate it, store good rows in source_orders and bad rows in rejects.
    Safe to rerun: rows from the same file are deleted first, then re-added."""
    source_file = Path(orders_path).name
    with open(orders_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        check_schema(reader.fieldnames or [])
        rows = list(reader)

    good, rejected, dupes = validate_rows(rows, customer_ids)

    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM source_orders WHERE source_file = ?", [source_file])
        con.execute("DELETE FROM rejects WHERE source_file = ?", [source_file])
        if good:
            con.register("good_df", pd.DataFrame([
                (r["order_id"], r["customer_id"], r["order_date"], r["amount"],
                 r["status"], r["updated_at"], run_id, source_file) for r in good],
                columns=["order_id", "customer_id", "order_date", "amount",
                         "status", "updated_at", "run_id", "source_file"]))
            con.execute("""
                INSERT INTO source_orders
                SELECT order_id, customer_id, CAST(order_date AS DATE),
                       CAST(amount AS DECIMAL(10,2)), status,
                       CAST(updated_at AS TIMESTAMP), run_id, source_file
                FROM good_df""")
        if rejected:
            con.register("rej_df", pd.DataFrame([
                (r.get("order_id"), r.get("customer_id"), r.get("order_date"),
                 r.get("amount"), r.get("status"), r.get("updated_at"),
                 r["reject_reason"], run_id, source_file) for r in rejected],
                columns=["order_id", "customer_id", "order_date", "amount", "status",
                         "updated_at", "reject_reason", "run_id", "source_file"]))
            con.execute("INSERT INTO rejects SELECT * FROM rej_df")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"source_file": source_file, "rows_in": len(rows),
            "rows_rejected": len(rejected), "duplicates_dropped": dupes}


# ---------- The three strategies ----------

def load_full_refresh(con, source_file):
    con.execute("DELETE FROM orders_full")
    con.execute(f"INSERT INTO orders_full SELECT {COLS}, source_file FROM latest_orders")


def load_incremental_merge(con, source_file):
    # 1) remove old versions of orders that this file updates
    con.execute("""
        DELETE FROM orders_merge WHERE EXISTS (
            SELECT 1 FROM source_orders s
            WHERE s.source_file = ? AND s.order_id = orders_merge.order_id
              AND s.updated_at >= orders_merge.updated_at)""", [source_file])
    # 2) insert new orders and the new versions of updated ones
    con.execute(f"""
        INSERT INTO orders_merge
        SELECT {COLS}, source_file FROM source_orders s
        WHERE s.source_file = ?
          AND NOT EXISTS (SELECT 1 FROM orders_merge t WHERE t.order_id = s.order_id)""",
                [source_file])


def load_partition_replace(con, source_file):
    dates = "SELECT DISTINCT order_date FROM source_orders WHERE source_file = ?"
    con.execute(f"DELETE FROM orders_partition WHERE order_date IN ({dates})", [source_file])
    con.execute(f"""
        INSERT INTO orders_partition
        SELECT {COLS}, source_file FROM latest_orders
        WHERE order_date IN ({dates})""", [source_file])


STRATEGY_FUNCS = {
    "full_refresh": load_full_refresh,
    "incremental_merge": load_incremental_merge,
    "partition_replace": load_partition_replace,
}


def run_strategy(con, name, ingest_stats, run_id):
    """Run one strategy inside a transaction and write a row to run_log."""
    started = datetime.now()
    t0 = time.perf_counter()
    status, error = "success", None
    con.execute("BEGIN")
    try:
        STRATEGY_FUNCS[name](con, ingest_stats["source_file"])
        con.execute("COMMIT")
    except Exception as exc:
        con.execute("ROLLBACK")          # target stays exactly as it was
        status, error = "failed", str(exc)
    seconds = time.perf_counter() - t0
    in_target = con.execute(f"SELECT COUNT(*) FROM {TARGETS[name]}").fetchone()[0]
    con.execute("INSERT INTO run_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, name, ingest_stats["source_file"], started, seconds,
                 ingest_stats["rows_in"], ingest_stats["rows_rejected"],
                 ingest_stats["duplicates_dropped"], in_target, status, error])
    return status