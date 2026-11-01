"""Reconciliation checks: do the numbers add up across source, rejects and targets?"""
from src import pipeline


def scalar(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def reconcile(con, rows_in_per_file):
    """Return a list of (check_name, passed, detail). rows_in_per_file = {file: raw row count}."""
    results = []

    # 1) Every input row is either loaded (before dedupe) or rejected.
    for source_file, rows_in in rows_in_per_file.items():
        rejected = scalar(con, "SELECT COUNT(*) FROM rejects WHERE source_file = ?", [source_file])
        loaded = scalar(con, "SELECT COUNT(*) FROM source_orders WHERE source_file = ?", [source_file])
        # rows_in = rejected + loaded + duplicates dropped, so loaded + rejected <= rows_in
        results.append((f"{source_file}: loaded + rejected <= rows in",
                        loaded + rejected <= rows_in,
                        f"rows_in={rows_in} loaded={loaded} rejected={rejected}"))

    # 2) Expected result is the latest version of each order.
    expected = scalar(con, "SELECT COUNT(*) FROM latest_orders")
    expected_sum = scalar(con, "SELECT COALESCE(SUM(amount), 0) FROM latest_orders")
    results.append(("latest_orders is not empty", expected > 0, f"rows={expected}"))

    # 3) Each target must match the expected result on count, total amount and keys.
    for table in pipeline.TARGETS.values():
        n = scalar(con, f"SELECT COUNT(*) FROM {table}")
        total = scalar(con, f"SELECT COALESCE(SUM(amount), 0) FROM {table}")
        distinct_ids = scalar(con, f"SELECT COUNT(DISTINCT order_id) FROM {table}")
        results.append((f"{table}: row count = latest_orders", n == expected, f"{n} vs {expected}"))
        results.append((f"{table}: SUM(amount) = latest_orders", total == expected_sum,
                        f"{total} vs {expected_sum}"))
        results.append((f"{table}: no duplicate order_id", n == distinct_ids,
                        f"rows={n} distinct={distinct_ids}"))
        orphans = scalar(con, f"""SELECT COUNT(*) FROM {table} t
                                  WHERE t.status NOT IN ('placed','shipped','cancelled')""")
        results.append((f"{table}: all statuses valid", orphans == 0, f"bad={orphans}"))

    # 4) Every reject has a reason and a source file (traceability).
    untraceable = scalar(con, """SELECT COUNT(*) FROM rejects
                                 WHERE reject_reason IS NULL OR source_file IS NULL""")
    results.append(("rejects are traceable", untraceable == 0, f"untraceable={untraceable}"))
    return results


def print_report(results):
    print("\nReconciliation report:")
    for name, passed, detail in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}  ({detail})")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return not failed