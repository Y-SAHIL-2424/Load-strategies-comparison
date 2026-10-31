import csv
from src.validate import validate_rows, check_schema

with open("data/sample/customers.csv", newline="") as f:
    customer_ids = {r["customer_id"] for r in csv.DictReader(f)}

with open("data/sample/orders_batch_1.csv", newline="") as f:
    reader = csv.DictReader(f)
    check_schema(reader.fieldnames)
    rows = list(reader)

good, rejected, dupes = validate_rows(rows, customer_ids)
print("input:", len(rows), "good:", len(good), "rejected:", len(rejected), "duplicates dropped:", dupes)
for r in rejected:
    print(r["order_id"], "->", r["reject_reason"])