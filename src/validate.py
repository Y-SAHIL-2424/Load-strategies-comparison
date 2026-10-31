"""Validate order rows. Returns (good_rows, rejected_rows)."""
from datetime import date, datetime

VALID_STATUSES = {"placed", "shipped", "cancelled"}
REQUIRED_COLUMNS = ["order_id", "customer_id", "order_date", "amount", "status", "updated_at"]


class SchemaError(Exception):
    """Raised when the file has the wrong columns."""


def check_schema(columns):
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise SchemaError(f"missing columns: {missing}")


def validate_row(row, valid_customer_ids):
    """Return a reject reason string, or None if the row is good."""
    if not (row.get("order_id") or "").strip():
        return "missing_order_id"
    if not (row.get("customer_id") or "").strip():
        return "missing_customer_id"
    if row["customer_id"] not in valid_customer_ids:
        return "invalid_customer"
    try:
        date.fromisoformat(row["order_date"])
    except (ValueError, TypeError):
        return "invalid_date"
    try:
        datetime.fromisoformat(row["updated_at"])
    except (ValueError, TypeError):
        return "invalid_updated_at"
    if row.get("amount") in (None, ""):
        return "missing_amount"
    try:
        amount = float(row["amount"])
    except ValueError:
        return "invalid_amount"
    if amount < 0:
        return "negative_amount"
    if row.get("status") not in VALID_STATUSES:
        return "invalid_status"
    return None


def dedupe_latest(rows):
    """Keep only the row with the latest updated_at for each order_id."""
    latest = {}
    for row in rows:
        key = row["order_id"]
        if key not in latest or row["updated_at"] > latest[key]["updated_at"]:
            latest[key] = row
    return list(latest.values())


def validate_rows(rows, valid_customer_ids):
    good, rejected = [], []
    for row in rows:
        reason = validate_row(row, valid_customer_ids)
        if reason:
            rejected.append({**row, "reject_reason": reason})
        else:
            good.append(row)
    deduped = dedupe_latest(good)
    duplicates_dropped = len(good) - len(deduped)
    return deduped, rejected, duplicates_dropped