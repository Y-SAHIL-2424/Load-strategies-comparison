1.hey Claude , i got selected to round3 for a company in Hyderabad and its a 72hour coding challenge where 24 hours is completed and i have started nothing, my goal is to clear this round 3.

2.so this is everything, the next 15 min we will try to understand the problem as simple as easy.

3.yes now i understood the problem , so whats the next step ? and remember that i am fresher so while explaining things please explain it clearly and in simple.

4.Runned the try script and its a success 
"input: 55 good: 50 rejected: 4 duplicates dropped: 1
O9000001 -> missing_amount
O9000002 -> negative_amount
O9000003 -> invalid_customer
O9000004 -> invalid_date"

5.python -m src.run_compare
orders_batch_1.csv: in=55 rejected=4 duplicates_dropped=1
orders_batch_2.csv: in=15 rejected=0 duplicates_dropped=0

Run log:
('full_refresh', 'orders_batch_1.csv', 'success', 0.0107, 50)
('incremental_merge', 'orders_batch_1.csv', 'success', 0.0092, 50)
('partition_replace', 'orders_batch_1.csv', 'success', 0.0144, 50)
('full_refresh', 'orders_batch_2.csv', 'success', 0.0289, 60)
('incremental_merge', 'orders_batch_2.csv', 'success', 0.0067, 60)
('partition_replace', 'orders_batch_2.csv', 'success', 0.0147, 60)

Comparison:
full vs merge     : MATCH
full vs partition : MATCH

6.I need three target tables, one per strategy, so we can compare them. We also need a place that holds all the data received so far, so “full refresh” and “partition replacement” have something to rebuild from.

7.what should be present in gitgnore


