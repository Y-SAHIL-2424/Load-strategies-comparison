# Load Strategies Comparison: Full Refresh vs Incremental Merge vs Partition Replacement

A small, reproducible data-engineering project that loads the same order data into a target table
in three different ways, proves the final results are identical, and measures the trade-offs
(runtime, complexity, failure recovery).

Stack: Python, DuckDB (local, no server needed), pandas (bulk insert), pytest.

## The three strategies

| Strategy | What it does | Target table |
|---|---|---|
| Full refresh | Deletes the whole target and reloads every current order | `orders_full` |
| Incremental merge | Touches only the orders in the new file: removes older versions, inserts new/updated ones | `orders_merge` |
| Partition replacement | Finds the `order_date` values in the new file, deletes those dates from the target, reloads those dates | `orders_partition` |

Business rule shared by all three: **the version of an order with the latest `updated_at` wins.**
That single rule is what makes the three results identical.

## Project layout

```
src/
  generate_data.py     synthetic data generator (fixed seed 42)
  validate.py          schema check, row validation, duplicate handling
  pipeline.py          tables, ingestion, the three strategies, run logging
  reconcile.py         reconciliation report (counts, totals, keys)
  run_compare.py       runs 2 batches through all strategies and compares
  benchmark_narrow.py  scenario: a batch touching only 2 dates
tests/test_pipeline.py automated tests (pytest)
data/sample/           small committed sample (~55 + 15 order rows)
docs/                  data dictionary and trade-off note
prompts/prompt_log.md  prompt history for AI tools used
```

## Setup

```
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows PowerShell
# source .venv/bin/activate       # Mac / Linux
pip install -r requirements.txt
```

## How to run

```
# 1. Generate data (small sample is already committed; large data is not)
python src/generate_data.py
python src/generate_data.py --orders 100000 --customers 5000 --days 30 --out data/large

# 2. Run both batches through all three strategies + comparison + reconciliation report
python -m src.run_compare
python -m src.run_compare --data data/large --db data/large.duckdb

# 3. Narrow-batch scenario (needs data/large to exist)
python -m src.benchmark_narrow

# 4. Tests
python -m pytest -v
```

`run_compare` deletes and recreates its `.duckdb` file on each run, so it always starts clean.
Generated `.duckdb` files and `data/large/` are git-ignored.

## Design

Data flow: CSV file -> schema check -> row validation -> `source_orders` (good rows) or `rejects`
(bad rows) -> one of the three strategies -> target table. Every run is recorded in `run_log`.

| Table | Purpose |
|---|---|
| `source_orders` | All good rows from every ingested file, tagged with `run_id` and `source_file` (history is kept) |
| `latest_orders` | View: the newest version of each `order_id` (by `updated_at`, ties broken by `source_file`). This is the "expected answer" every target must equal |
| `orders_full`, `orders_merge`, `orders_partition` | The three targets |
| `rejects` | Rejected rows with `reject_reason`, `run_id`, `source_file` |
| `run_log` | One row per strategy run: run_id, strategy, file, start time, seconds, rows in, rejected, duplicates dropped, rows in target, status, error |

Naming: snake_case tables/columns, ISO-8601 dates and timestamps, 8-character `run_id`, and
deterministic order/customer IDs (`O0000001`, `C00001`). See `docs/data_dictionary.md`.

## Validation rules and reject reasons

| Reject reason | Trigger |
|---|---|
| `missing_order_id` | `order_id` empty |
| `missing_customer_id` | `customer_id` empty |
| `invalid_customer` | `customer_id` not in `customers.csv` (referential integrity) |
| `invalid_date` | `order_date` is not a valid ISO date |
| `invalid_updated_at` | `updated_at` is not a valid ISO timestamp |
| `missing_amount` | `amount` empty |
| `invalid_amount` | `amount` not a number |
| `negative_amount` | `amount` < 0 |
| `invalid_status` | status not `placed`, `shipped` or `cancelled` |

Only the first failing rule is recorded per row. A file with a missing required column is not
processed at all: `SchemaError` is raised. Duplicate `order_id`s within a file are not errors: the
latest `updated_at` is kept and the number dropped is reported as `duplicates_dropped`.

## Idempotency, retry, restart and backfill

- **Rerun of the same file:** ingestion first deletes that file's rows from `source_orders` and
  `rejects`, then inserts them again, inside one transaction. Running it twice gives the same result.
- **Each strategy runs in a transaction.** On any error it rolls back, so the target stays exactly as it
  was, and `run_log` records `failed` with the error message. (Tested by `test_mid_load_failure_...`.)
- **Retry / restart:** ingestion and strategy loading are separate transactions. If a strategy fails, the
  file's rows are already safely in `source_orders`, so the strategy can simply be run again.
- **Backfill / late data:** ingesting an older file is safe. An older `updated_at` never overwrites a newer
  version in any of the three strategies (tested by `test_late_arriving_older_update_...`).

## Results

Machine: Windows, Python 3.15, DuckDB. Data: 100,005 rows in batch 1, 30,000 in batch 2 (10,000
updates + 20,000 new orders), target ends at 120,000 rows. All numbers are **single runs**.

Reconciliation on the large run: all strategy targets match on row count, `SUM(amount)`, key
uniqueness and valid statuses; full vs merge and full vs partition report MATCH.

**Strategy load time (batch 2):**

| Scenario | Full refresh | Incremental merge | Partition replace |
|---|---|---|---|
| Batch touches all 30 dates | 0.12 s | 0.056 s | 0.33 s |
| Batch touches 2 of 30 dates (6,523 rows) | 0.19 s | 0.036 s | 0.041 s |

**Optimization: bulk insert during ingestion.** Row-by-row `executemany` was 99% of total time.
Inserting from a pandas DataFrame instead:

| Step | Before | After |
|---|---|---|
| Ingest batch 1 (100,005 rows) | 87.27 s | 0.62 s |
| Ingest batch 2 (30,000 rows) | 28.65 s | 0.27 s |

Row counts were identical before and after, and all tests still passed.

## Tests

`python -m pytest -v` runs 9 tests: happy path, empty file, malformed rows with reasons, missing
`order_id`, duplicates, rerun of the same file, missing column (schema change), late-arriving older
update, and mid-load failure with rollback and retry.

## Known limitations

- Single machine, CSV input only, one file per ingest; timings are single runs, not averages.
- Deletes in the source are not handled.
- Partition replacement assumes an order never changes its `order_date`. If it did, the old version
  would stay in its old date partition until that partition is replaced. Merge does not have this problem.
- Partition replacement here rebuilds partitions from `source_orders` (full history), not only from the
  incoming file, so it does not lose rows when a file holds part of a day. This requires keeping
  `source_orders` history.
- Re-ingesting a changed version of an already-loaded file does not remove target rows that no longer
  exist in the file.
- Rejects keep only the first failing rule per row.
- `customers.csv` is read as a set of valid IDs; it is not loaded as a table.

## Possible next steps

Repeat benchmarks several times and report averages, add a `customers` table with history, add
Parquet partitioned storage for partition replacement, handle source deletes, add logging to a file.

## AI usage

- Tool used: Claude (chat). All my prompts are in `prompts/prompt_log.md`.
- Where AI accelerated the work: the project plan, the data generator, the validation module,
  the pipeline, the tests, the reconciliation report, the benchmark scripts, and drafts of the
  documentation.
- Where I relied on it heavily: all the SQL. I had no prior SQL experience, so the SQL statements
  (the three load strategies, the `latest_orders` view and the reconciliation queries) were written
  by the AI. I am learning to read and explain them.
- - What I did myself: set up the environment and repo, ran every command, and fixed the errors that came up
  (empty files caused by unsaved edits, misplaced files, a circular import and an indentation error).
  I checked results through row counts, the MATCH output, the reconciliation report and the tests, and I
  measured the before/after ingestion timings.
- What I reviewed or changed: - What I reviewed or changed: I reviewed the output of every step by running it myself. I checked that
  all three strategies produce the same result, that the row counts and totals reconcile, and that
  all 9 tests pass, both before and after the ingestion optimization. I fixed the errors that came up
  while building (a circular import from an auto-added line, an indentation error, and files that were
  not saved). I did not write the SQL or the original code myself; I am learning to read and explain it,
  and I can explain how the three load strategies work and why partition replacement is slower when a
  batch touches every date.