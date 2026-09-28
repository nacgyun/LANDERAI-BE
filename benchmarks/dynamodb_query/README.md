# DynamoDB Scan vs GSI Query benchmark

This benchmark runs against the official DynamoDB Local image. It compares the
current `Scan + FilterExpression` request-list access pattern with a
`user_id + created_at` GSI query using the same table and data.

```powershell
docker compose -f docker-compose.dynamodb-benchmark.yml up -d
python benchmarks/dynamodb_query/run.py
docker compose -f docker-compose.dynamodb-benchmark.yml down
```

For a quick verification run:

```powershell
python benchmarks/dynamodb_query/run.py --sizes 100 1000 --iterations 3
```

Results are written under `benchmark-results/dynamodb-query/<UTC timestamp>/`:

- `raw.csv`: every measured request
- `summary.csv`: mean, median, p95, counts, and pages by data size and method
- `summary.md`: Notion-friendly result table
- `environment.json`: versions and benchmark parameters

There is no separate query warm-up. Table creation and data seeding happen
before measurement, and every measured Scan and Query is included in the CSV.
Odd rounds run Scan first and even rounds run GSI Query first, so each method
goes first the same number of times when the iteration count is even.

The dedicated `DynamoDBQueryBenchmark` table is recreated at the start and
deleted at the end. Pass `--keep-table` to inspect it after the benchmark.
Completed dataset sizes are checkpointed after each measurement, so their CSV
and Markdown results remain available if a later, larger dataset fails.
