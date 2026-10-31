"""Automated tests for the load-strategies pipeline."""
import csv

import pytest

from src import pipeline
from src.run_compare import same_data
from src.validate import SchemaError

FIELDS = ["order_id", "customer_id", "order_date", "amount", "status", "updated_at"]
CUSTOMERS = {"C00001", "C00002"}


def make_row(**overrides):
    row = {"order_id": "O0000001", "customer_id": "C00001", "order_date": "2026-10-01",
           "amount": "100.00", "status": "placed", "updated_at": "2026-10-01T10:00:00"}
    row.update(overrides)
    return row


def write_csv(path, rows, fields=FIELDS):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def con(tmp_path):
    connection = pipeline.connect(tmp_path / "test.duckdb")
    pipeline.init_schema(connection)
    yield connection
    connection.close()


def run_all(con, path, run_id="r1"):
    """Ingest one file and run all three strategies on it."""
    stats = pipeline.ingest_file(con, path, CUSTOMERS, run_id)
    for name in pipeline.TARGETS:
        pipeline.run_strategy(con, name, stats, run_id)
    return stats


def count(con, table):
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_happy_path_all_strategies_match(con, tmp_path):
    rows = [make_row(order_id="O1"), make_row(order_id="O2", customer_id="C00002")]
    run_all(con, write_csv(tmp_path / "b1.csv", rows))
    for table in pipeline.TARGETS.values():
        assert count(con, table) == 2
    assert same_data(con, "orders_full", "orders_merge")
    assert same_data(con, "orders_full", "orders_partition")


def test_empty_file_does_not_crash(con, tmp_path):
    stats = run_all(con, write_csv(tmp_path / "empty.csv", []))
    assert stats["rows_in"] == 0
    for table in pipeline.TARGETS.values():
        assert count(con, table) == 0
    statuses = {r[0] for r in con.execute("SELECT status FROM run_log").fetchall()}
    assert statuses == {"success"}


def test_malformed_rows_go_to_rejects_with_reason(con, tmp_path):
    rows = [make_row(order_id="O1"),
            make_row(order_id="O2", amount="-5"),
            make_row(order_id="O3", amount=""),
            make_row(order_id="O4", customer_id="C99999"),
            make_row(order_id="O5", order_date="not-a-date"),
            make_row(order_id="O6", status="weird")]
    run_all(con, write_csv(tmp_path / "bad.csv", rows))
    reasons = dict(con.execute("SELECT order_id, reject_reason FROM rejects").fetchall())
    assert reasons == {"O2": "negative_amount", "O3": "missing_amount",
                       "O4": "invalid_customer", "O5": "invalid_date", "O6": "invalid_status"}
    assert count(con, "orders_full") == 1          # only O1 is good
    source = con.execute("SELECT DISTINCT source_file FROM rejects").fetchall()
    assert source == [("bad.csv",)]                # rejects stay traceable


def test_missing_order_id_is_rejected(con, tmp_path):
    run_all(con, write_csv(tmp_path / "nulls.csv", [make_row(order_id="")]))
    reason = con.execute("SELECT reject_reason FROM rejects").fetchone()[0]
    assert reason == "missing_order_id"
    assert count(con, "orders_full") == 0


def test_duplicates_keep_latest_version(con, tmp_path):
    rows = [make_row(order_id="O1", amount="100.00", updated_at="2026-10-01T10:00:00"),
            make_row(order_id="O1", amount="150.00", updated_at="2026-10-01T12:00:00")]
    stats = run_all(con, write_csv(tmp_path / "dupes.csv", rows))
    assert stats["duplicates_dropped"] == 1
    for table in pipeline.TARGETS.values():
        assert count(con, table) == 1
        assert float(con.execute(f"SELECT amount FROM {table}").fetchone()[0]) == 150.00


def test_rerun_of_same_file_creates_no_duplicates(con, tmp_path):
    path = write_csv(tmp_path / "b1.csv", [make_row(order_id="O1"), make_row(order_id="O2")])
    run_all(con, path, "r1")
    run_all(con, path, "r2")                        # same file again
    assert count(con, "source_orders") == 2
    for table in pipeline.TARGETS.values():
        assert count(con, table) == 2
    assert same_data(con, "orders_full", "orders_merge")
    assert same_data(con, "orders_full", "orders_partition")


def test_missing_column_raises_schema_error(con, tmp_path):
    fields = [f for f in FIELDS if f != "amount"]
    row = {k: v for k, v in make_row().items() if k != "amount"}
    path = write_csv(tmp_path / "schema.csv", [row], fields)
    with pytest.raises(SchemaError):
        pipeline.ingest_file(con, path, CUSTOMERS, "r1")


def test_late_arriving_older_update_does_not_overwrite(con, tmp_path):
    b1 = write_csv(tmp_path / "b1.csv",
                   [make_row(order_id="O1", amount="200.00", updated_at="2026-10-02T10:00:00")])
    b2 = write_csv(tmp_path / "b2.csv",
                   [make_row(order_id="O1", amount="999.00", updated_at="2026-10-01T08:00:00")])
    run_all(con, b1, "r1")
    run_all(con, b2, "r2")                          # arrives later but is OLDER data
    for table in pipeline.TARGETS.values():
        assert float(con.execute(f"SELECT amount FROM {table}").fetchone()[0]) == 200.00


def test_mid_load_failure_rolls_back_and_retry_succeeds(con, tmp_path, monkeypatch):
    path = write_csv(tmp_path / "b1.csv", [make_row(order_id="O1"), make_row(order_id="O2")])
    stats = pipeline.ingest_file(con, path, CUSTOMERS, "r1")
    assert pipeline.run_strategy(con, "full_refresh", stats, "r1") == "success"
    assert count(con, "orders_full") == 2

    def broken_load(connection, source_file):       # deletes, then crashes
        connection.execute("DELETE FROM orders_full")
        raise RuntimeError("simulated crash")

    monkeypatch.setitem(pipeline.STRATEGY_FUNCS, "full_refresh", broken_load)
    assert pipeline.run_strategy(con, "full_refresh", stats, "r2") == "failed"
    assert count(con, "orders_full") == 2           # rollback restored the table
    error = con.execute("SELECT error FROM run_log WHERE status = 'failed'").fetchone()[0]
    assert "simulated crash" in error

    monkeypatch.undo()                              # put the real function back
    assert pipeline.run_strategy(con, "full_refresh", stats, "r3") == "success"
    assert count(con, "orders_full") == 2