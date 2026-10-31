"""Run batch 1 and batch 2 through all three strategies, then compare the results."""
import argparse
import uuid
import time
from pathlib import Path

from src import pipeline

BATCHES = ["orders_batch_1.csv", "orders_batch_2.csv"]


def same_data(con, table_a, table_b):
    cols = pipeline.COLS
    a_minus_b = con.execute(
        f"SELECT COUNT(*) FROM (SELECT {cols} FROM {table_a} EXCEPT SELECT {cols} FROM {table_b})").fetchone()[0]
    b_minus_a = con.execute(
        f"SELECT COUNT(*) FROM (SELECT {cols} FROM {table_b} EXCEPT SELECT {cols} FROM {table_a})").fetchone()[0]
    return a_minus_b == 0 and b_minus_a == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/sample")
    parser.add_argument("--db", default="data/sample.duckdb")
    args = parser.parse_args()

    db_path = Path(args.db)
    if db_path.exists():
        db_path.unlink()                      # start clean every time

    data = Path(args.data)
    con = pipeline.connect(db_path)
    pipeline.init_schema(con)
    customer_ids = pipeline.load_customer_ids(data / "customers.csv")

    for batch in BATCHES:
        run_id = uuid.uuid4().hex[:8]
        t0 = time.perf_counter()
        stats = pipeline.ingest_file(con, data / batch, customer_ids, run_id)
        ingest_seconds = time.perf_counter() - t0
        print(f"{batch}: in={stats['rows_in']} rejected={stats['rows_rejected']} "
              f"duplicates_dropped={stats['duplicates_dropped']} ingest_seconds={ingest_seconds:.2f}")
        for name in pipeline.TARGETS:
            pipeline.run_strategy(con, name, stats, run_id)
    print("\nRun log:")
    for row in con.execute(
            "SELECT strategy, source_file, status, ROUND(seconds, 4), rows_in_target "
            "FROM run_log ORDER BY started_at").fetchall():
        print(row)

    print("\nComparison:")
    print("full vs merge     :", "MATCH" if same_data(con, "orders_full", "orders_merge") else "DIFFERENT")
    print("full vs partition :", "MATCH" if same_data(con, "orders_full", "orders_partition") else "DIFFERENT")
    con.close()


if __name__ == "__main__":
    main()