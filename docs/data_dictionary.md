# Data Dictionary

Conventions: snake_case names, ISO-8601 dates (`YYYY-MM-DD`) and timestamps (`YYYY-MM-DDTHH:MM:SS`),
deterministic IDs (`O` + 7 digits for orders, `C` + 5 digits for customers).

## Source files

### `customers.csv` (reference data)

| Column | Type | Nullable | Notes |
|---|---|---|---|
| customer_id | text | No | Unique, e.g. `C00001`. Used to check referential integrity |
| customer_name | text | No | |
| country | text | No | One of India, USA, UK, Germany, Japan (synthetic) |

Read as a set of valid IDs; not loaded into a table.

### `orders_batch_*.csv` (source orders)

Grain: one row per order **version** (an order can appear more than once with different `updated_at`).

| Column | Type | Nullable | Notes |
|---|---|---|---|
| order_id | text | No | Business key, e.g. `O0000123` |
| customer_id | text | No | Must exist in `customers.csv` |
| order_date | date | No | Partition key for partition replacement |
| amount | decimal | No | Must be >= 0 |
| status | text | No | `placed`, `shipped` or `cancelled` |
| updated_at | timestamp | No | Version marker: latest wins |

## Database tables (DuckDB)

### `source_orders` (all good rows from all files)

| Column | Type | Notes |
|---|---|---|
| order_id | VARCHAR | |
| customer_id | VARCHAR | |
| order_date | DATE | |
| amount | DECIMAL(10,2) | |
| status | VARCHAR | |
| updated_at | TIMESTAMP | |
| run_id | VARCHAR | Run that ingested the row |
| source_file | VARCHAR | File the row came from |

Uniqueness: none enforced across files (history kept). Within a file, duplicates are removed (latest wins).

### `latest_orders` (view)

Same columns as a target table. One row per `order_id`: the row with the greatest `updated_at`
(ties broken by `source_file`). This is the expected result every target is reconciled against.

### `orders_full`, `orders_merge`, `orders_partition` (targets)

| Column | Type | Notes |
|---|---|---|
| order_id | VARCHAR | Unique per table |
| customer_id | VARCHAR | |
| order_date | DATE | |
| amount | DECIMAL(10,2) | |
| status | VARCHAR | |
| updated_at | TIMESTAMP | |
| source_file | VARCHAR | File that supplied this version |

The comparison between targets uses the first six columns (`source_file` is excluded because the
three strategies may record a different file for the same data).

### `rejects`

| Column | Type | Notes |
|---|---|---|
| order_id, customer_id, order_date, amount, status, updated_at | VARCHAR | Stored as raw text, exactly as received |
| reject_reason | VARCHAR | See reject reasons in the README |
| run_id | VARCHAR | |
| source_file | VARCHAR | Makes each reject traceable to its file |

### `run_log`

| Column | Type | Notes |
|---|---|---|
| run_id | VARCHAR | Ingest run identifier |
| strategy | VARCHAR | `full_refresh`, `incremental_merge` or `partition_replace` |
| source_file | VARCHAR | |
| started_at | TIMESTAMP | |
| seconds | DOUBLE | Load time of the strategy step (excludes ingestion) |
| rows_in | INTEGER | Raw rows in the file |
| rows_rejected | INTEGER | |
| duplicates_dropped | INTEGER | |
| rows_in_target | INTEGER | Target row count after the run |
| status | VARCHAR | `success` or `failed` |
| error | VARCHAR | Error message when failed, otherwise null |
