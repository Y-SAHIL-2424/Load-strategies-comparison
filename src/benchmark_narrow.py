"""Scenario: a late correction that touches only 2 dates. Where does partition replace win?"""
import csv
import time
import uuid
from pathlib import Path

from src import pipeline

DATA = Path("data/large")
DB = Path("data/narrow.duckdb")


def main():
    if DB.exists():
        DB.unlink()
    con = pipeline.connect(DB)
    pipeline.init_schema(con)
    customer_ids = pipeline.load_customer_ids(DATA / "customers.csv")

    # Load batch 1 (100k rows) through all strategies so every target is full.
    run_id = uuid.uuid4().hex[:8]
    stats = pipeline.ingest_file(con, DATA / "orders_batch_1.csv", customer_ids, run_id)
    for name in pipeline.TARGETS:
        pipeline.run_strategy(con, name, stats, run_id)

    # Build a narrow batch: updates to orders from only the first 2 dates.
    with open(DATA / "orders_batch_1.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    dates = sorted({r["order_date"] for r in rows if r["order_date"][:2] == "20"})[:2]
    narrow = []
    for r in rows:
        if r["order_date"] in dates and r["order_id"].startswith("O0"):
            r = dict(r)
            r["amount"] = f"{float(r['amount']) + 1:.2f}"
            r["updated_at"] = r["updated_at"].replace("2026-10", "2026-11")
            narrow.append(r)
    narrow_path = Path("data/narrow_batch.csv")
    with open(narrow_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(narrow)

    run_id = uuid.uuid4().hex[:8]
    stats = pipeline.ingest_file(con, narrow_path, customer_ids, run_id)
    print(f"narrow batch: {stats['rows_in']} rows touching dates {dates}")
    for name in pipeline.TARGETS:
        t0 = time.perf_counter()
        pipeline.run_strategy(con, name, stats, run_id)
        print(f"  {name:20s} {time.perf_counter() - t0:.4f} s")
    con.close()


if __name__ == "__main__":
    main()