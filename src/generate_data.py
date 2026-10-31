"""Generate reproducible synthetic data for the load-strategies project."""
import argparse
import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 42
STATUSES = ["placed", "shipped", "cancelled"]
COUNTRIES = ["India", "USA", "UK", "Germany", "Japan"]


def make_customers(n):
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "customer_id": f"C{i:05d}",
            "customer_name": f"Customer {i}",
            "country": random.choice(COUNTRIES),
        })
    return rows


def make_order(order_num, customer_count, base_day, day_span):
    order_day = base_day + timedelta(days=random.randint(0, day_span - 1))
    ts = datetime.combine(order_day, datetime.min.time()) + timedelta(
        hours=random.randint(0, 23), minutes=random.randint(0, 59)
    )
    return {
        "order_id": f"O{order_num:07d}",
        "customer_id": f"C{random.randint(1, customer_count):05d}",
        "order_date": order_day.isoformat(),
        "amount": round(random.uniform(5, 500), 2),
        "status": random.choice(STATUSES),
        "updated_at": ts.isoformat(),
    }


def make_batch_1(n_orders, customer_count, base_day, day_span, include_bad):
    orders = [make_order(i, customer_count, base_day, day_span)
              for i in range(1, n_orders + 1)]
    if include_bad and n_orders >= 10:
        orders.append(dict(orders[0]))                                 # exact duplicate
        orders.append({**orders[1], "order_id": "O9000001", "amount": ""})          # missing amount
        orders.append({**orders[2], "order_id": "O9000002", "amount": -50})         # negative amount
        orders.append({**orders[3], "order_id": "O9000003", "customer_id": "C99999"})  # bad customer
        orders.append({**orders[4], "order_id": "O9000004", "order_date": "not-a-date"})  # bad date
    return orders


def make_batch_2(batch1, n_new, customer_count, base_day, day_span):
    """Day 2: updates to some old orders (newer updated_at) plus brand-new orders."""
    updates = []
    for old in random.sample(batch1, k=max(1, len(batch1) // 10)):
        if old["order_id"].startswith("O9"):      # skip the deliberately bad rows
            continue
        new = dict(old)
        new["amount"] = round(float(old["amount"]) + random.uniform(1, 50), 2)
        new["status"] = random.choice(STATUSES)
        new["updated_at"] = (datetime.fromisoformat(old["updated_at"])
                             + timedelta(days=1)).isoformat()
        updates.append(new)
    start = len(batch1) + 1
    new_orders = [make_order(start + i, customer_count, base_day, day_span)
                  for i in range(n_new)]
    return updates + new_orders


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", type=int, default=50, help="orders in batch 1")
    parser.add_argument("--customers", type=int, default=10)
    parser.add_argument("--days", type=int, default=5, help="date range size")
    parser.add_argument("--out", default="data/sample")
    args = parser.parse_args()

    random.seed(SEED)
    out = Path(args.out)
    base_day = date(2026, 10, 1)

    customers = make_customers(args.customers)
    batch1 = make_batch_1(args.orders, args.customers, base_day, args.days, True)
    batch2 = make_batch_2(batch1, max(1, args.orders // 5), args.customers, base_day, args.days)

    order_fields = ["order_id", "customer_id", "order_date", "amount", "status", "updated_at"]
    write_csv(out / "customers.csv", customers, ["customer_id", "customer_name", "country"])
    write_csv(out / "orders_batch_1.csv", batch1, order_fields)
    write_csv(out / "orders_batch_2.csv", batch2, order_fields)
    print(f"Wrote {len(customers)} customers, {len(batch1)} + {len(batch2)} order rows to {out}")


if __name__ == "__main__":
    main()