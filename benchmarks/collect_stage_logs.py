"""Join CloudWatch benchmark stage events to request rows as long-form CSV."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path

import boto3


def parse_timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", nargs="+", required=True, help="CSV files created by benchmarks/run.py")
    parser.add_argument("--profile", default="landerai-dev")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--benchmark", default="architecture-abc")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    requests = {}
    for filename in args.runs:
        with Path(filename).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row.get("request_id"):
                    requests[row["request_id"]] = row
    if not requests:
        raise SystemExit("No request IDs found")

    start_ms = min(parse_timestamp(row["client_started_at"]) for row in requests.values()) - 300_000
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000) + 60_000
    prefix = f"/aws/lambda/landerai-bench-{args.benchmark}"
    log_groups = {
        "a": [f"{prefix}-a-workflow"],
        "b": [f"{prefix}-b-{stage}" for stage in ("embedding", "design", "variant", "persist")],
        "c": [f"{prefix}-c-{stage}" for stage in ("embedding", "design", "variant", "persist")],
    }
    logs = boto3.Session(profile_name=args.profile, region_name=args.region).client("logs")
    output_rows = []
    for group, names in log_groups.items():
        wanted = {request_id for request_id, row in requests.items() if row["group"] == group}
        for log_group in names:
            token = None
            while True:
                query = {"logGroupName": log_group, "startTime": start_ms, "endTime": end_ms}
                if token:
                    query["nextToken"] = token
                response = logs.filter_log_events(**query)
                for event in response.get("events", []):
                    try:
                        payload = json.loads(event["message"])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if payload.get("event") != "benchmark_stage" or payload.get("request_id") not in wanted:
                        continue
                    output_rows.append({
                        "group": group,
                        "request_id": payload["request_id"],
                        "stage": payload.get("stage"),
                        "duration_seconds": payload.get("duration_seconds"),
                        "status": payload.get("status"),
                        "error_type": payload.get("error_type"),
                        "aws_request_id": payload.get("aws_request_id"),
                        "started_at": payload.get("started_at"),
                        "ended_at": payload.get("ended_at"),
                        "log_group": log_group,
                    })
                new_token = response.get("nextToken")
                if not new_token or new_token == token:
                    break
                token = new_token

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["group", "request_id", "stage", "duration_seconds", "status", "error_type",
              "aws_request_id", "started_at", "ended_at", "log_group"]
    with output.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(output_rows, key=lambda row: (row["group"], row["request_id"], row["started_at"] or "")))
    print(f"Saved {len(output_rows)} stage rows to {output}")


if __name__ == "__main__":
    main()
