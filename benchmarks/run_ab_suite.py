"""Run the A/B benchmark suite with the same workload for both environments."""

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys


ENDPOINTS = {
    "a": "https://rqz4plb9mg.execute-api.ap-northeast-2.amazonaws.com",
    "b": "https://qfpqszg5b7.execute-api.ap-northeast-2.amazonaws.com",
}

BENCHMARK_SCENARIOS = (
    ("c5", 5, 2),
    ("c20", 20, 1),
)

ROUND_ORDERS = (
    ("round1", ("a", "b")),
    ("round2", ("b", "a")),
)


def report_server_failures(result_file: Path) -> None:
    with result_file.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    failed = [row for row in rows if row["status"] == "FAILED"]
    if failed:
        details = ", ".join(
            f"{row['request_id'] or '<no request id>'}:{row['status']}:{row.get('error_type') or '-'}"
            for row in failed
        )
        print(f"Server failures recorded in {result_file}: {details}")


def merge_results(input_files: list[Path], output: Path) -> None:
    fieldnames = None
    rows = []
    for input_file in input_files:
        with input_file.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = fieldnames or reader.fieldnames
            rows.extend(reader)
    with output.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run warm-up, concurrency 5, and concurrency 20 tests for A and B."
    )
    parser.add_argument("--input", default="benchmarks/input.json")
    parser.add_argument("--profile", default="landerai-dev")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--results-dir", default="benchmark-results")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--poll-interval", type=float, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_file = Path(args.input)
    if not input_file.is_file():
        parser.error(f"Input file does not exist: {input_file}")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.results_dir) / f"ab-{run_id}"
    run_script = Path(__file__).with_name("run.py")
    cost_script = Path(__file__).with_name("estimate_request_costs.py")
    result_files = []
    partial_results = {
        (group, scenario): []
        for group in ("a", "b")
        for scenario in ("warmup", "c5", "c20")
    }

    print(f"Results: {run_dir}")
    for round_name, group_order in ROUND_ORDERS:
        print(f"\n=== {round_name}: {' -> '.join(group.upper() for group in group_order)} ===")
        for group in group_order:
            scenarios = (("warmup", 1, 1), *BENCHMARK_SCENARIOS)
            for scenario, concurrency, batches in scenarios:
                output = run_dir / f".{round_name}-{group}-{scenario}.csv"
                partial_results[(group, scenario)].append(output)
                command = [
                    sys.executable,
                    str(run_script),
                    "--group", group,
                    "--experiment-round", round_name,
                    "--endpoint", ENDPOINTS[group],
                    "--input", str(input_file),
                    "--profile", args.profile,
                    "--region", args.region,
                    "--concurrency", str(concurrency),
                    "--batches", str(batches),
                    "--timeout", str(args.timeout),
                    "--poll-interval", str(args.poll_interval),
                    "--output", str(output),
                ]
                print(
                    f"\n[{round_name}] group={group} scenario={scenario} "
                    f"concurrency={concurrency} batches={batches}"
                )
                print(" ".join(command))
                if args.dry_run:
                    continue
                subprocess.run(command, check=True)
                report_server_failures(output)

    if not args.dry_run:
        for group in ("a", "b"):
            for scenario in ("warmup", "c5", "c20"):
                output = run_dir / f"{group}-{scenario}.csv"
                inputs = partial_results[(group, scenario)]
                merge_results(inputs, output)
                result_files.append(output)
                for input_file in inputs:
                    input_file.unlink()

    if not args.dry_run:
        print(f"\nCompleted. Raw request results: {run_dir}")
        print("Warm-up files are for validation only and should be excluded from statistics.")
        cost_output = run_dir / "estimated-request-costs.csv"
        cost_command = [
            sys.executable,
            str(cost_script),
            "--runs", *(str(path) for path in result_files),
            "--profile", args.profile,
            "--region", args.region,
            "--output", str(cost_output),
        ]
        print("\nEstimating attributable per-request AWS cost...")
        try:
            subprocess.run(cost_command, check=True)
        except subprocess.CalledProcessError:
            print("Cost collection was not ready. Benchmark CSV files are safe; retry with:")
            print(" ".join(cost_command))


if __name__ == "__main__":
    main()
