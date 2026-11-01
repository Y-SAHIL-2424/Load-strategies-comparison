# Design and Trade-off Note

## Goal

Load the same order data with three strategies, prove they give the same final table, and compare
runtime, complexity and failure recovery.

## Key design decisions

1. **Keep full history in `source_orders`, derive truth in `latest_orders`.** All three strategies read
   from the same place, so they are comparable and "latest `updated_at` wins" is defined once.
2. **Validate before loading.** Bad rows go to `rejects` with a reason and source file instead of being
   silently dropped. Duplicates are resolved, not rejected.
3. **Transaction per strategy run.** Each load is all-or-nothing; failures are recorded in `run_log`.
4. **Idempotent ingestion.** A file's rows are deleted and re-inserted in one transaction.
5. **DuckDB + CSV.** Runs locally with one `pip install`, handles 100k+ rows in a fraction of a second.

## Comparison

Measured on 100k+ rows in the target (single runs, Windows, DuckDB). Times are the strategy load step only.

| | Full refresh | Incremental merge | Partition replace |
|---|---|---|---|
| Work done | Rebuilds everything | Only rows in the file | Whole dates touched by the file |
| Batch touching all 30 dates | 0.12 s | 0.056 s | 0.33 s |
| Batch touching 2 of 30 dates | 0.19 s | 0.036 s | 0.041 s |
| Cost depends on | Total table size | Number of changed rows | Number of dates touched |
| Code complexity | Lowest | Highest (compare versions, delete then insert) | Medium |
| Rerun same file | Safe | Safe here (needs `updated_at` comparison) | Safe |

### Runtime

- Full refresh costs about the same whatever the size of the change, so it is the right choice only for
  small tables or when simplicity matters most.
- Incremental merge was fastest or close to fastest in both scenarios because its cost follows the
  number of changed rows.
- Partition replace wins when changes are concentrated in few dates (0.041 s vs 0.19 s for full refresh
  with 2 of 30 dates), but was the slowest when the batch touched every date (0.33 s), because nearly
  every partition was rebuilt and the extra delete and filter steps were added on top.
- The biggest cost in the whole pipeline was not any strategy but row-by-row ingestion (87 s for 100k
  rows). Switching to a DataFrame bulk insert brought it to 0.62 s with identical results.

### Complexity

- Full refresh: two statements, no key logic.
- Partition replace: needs a reliable partition key and an assumption that a record never moves to a
  different partition.
- Incremental merge: needs a business key and a version column, plus care with late and duplicate data.

### Failure recovery

- All three run inside a transaction and roll back on error, so a crash between "delete" and "insert"
  cannot leave a table empty or half-loaded. This was tested with a simulated crash after the delete.
- Full refresh has the largest blast radius if there is no transaction (the entire table is empty
  mid-load) and the longest retry because it redoes everything.
- Partition replace limits the blast radius to the touched dates; retry reloads only those dates.
- Incremental merge retries are cheap, but a partial failure without a transaction could leave some keys
  updated and others not. The `updated_at >=` rule makes a rerun converge to the same result.

## Correctness evidence

- `run_compare` reports MATCH between full vs merge and full vs partition.
- The reconciliation report checks, for every target: row count and `SUM(amount)` against
  `latest_orders`, no duplicate `order_id`, valid statuses; plus that rejects are traceable.
  16/16 checks pass on the sample data.
- 9 automated tests cover the happy path, empty input, malformed rows, duplicates, nulls, rerun,
  schema change, late-arriving data and failure with retry.
- A problem found during development: a comparison can say MATCH when both tables are empty. This
  happened once when the loading step was accidentally skipped. The reconciliation report now also
  requires `latest_orders` to be non-empty and each target to equal it.

## Limitations

See the README "Known limitations". The main ones: partition replace assumes `order_date` never changes
for an order, source deletes are not handled, and timings are single runs rather than averages.

## What I would do next

Repeat benchmarks (for example 5 runs, report median), test a table with many more rows and a more
realistic update pattern, partitioned Parquet files for partition replacement, and handle deletes.
