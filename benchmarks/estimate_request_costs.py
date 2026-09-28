"""Estimate attributable per-request AWS cost from billed usage, excluding free tier/discounts."""

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

import boto3


# Public list-price inputs. Keep them explicit and overridable because AWS pricing can change.
DEFAULT_LAMBDA_GB_SECOND_USD = 0.0000166667
DEFAULT_LAMBDA_REQUEST_USD = 0.0000002
# AWS Price List API: AmazonStates / APN2-StateTransition, effective 2025-07-01.
DEFAULT_SFN_TRANSITION_USD = 0.0000271
DEFAULT_HTTP_API_REQUEST_USD = 0.000001

REPORT_PATTERN = re.compile(
    r"REPORT RequestId:\s*(\S+).*?Billed Duration:\s*([\d.]+) ms"
    r".*?Memory Size:\s*(\d+) MB",
    re.DOTALL,
)


def parse_timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def read_requests(filenames: list[str]) -> dict[str, dict[str, str]]:
    requests = {}
    for filename in filenames:
        with Path(filename).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                request_id = row.get("request_id")
                if request_id:
                    if request_id in requests:
                        raise SystemExit(f"Duplicate request_id in input CSV files: {request_id}")
                    requests[request_id] = row
    if not requests:
        raise SystemExit("No request IDs found")
    return requests


def iter_log_events(client, log_group: str, start_ms: int, end_ms: int):
    token = None
    while True:
        query = {"logGroupName": log_group, "startTime": start_ms, "endTime": end_ms}
        if token:
            query["nextToken"] = token
        response = client.filter_log_events(**query)
        yield from response.get("events", [])
        new_token = response.get("nextToken")
        if not new_token or new_token == token:
            break
        token = new_token


def collect_lambda_usage(logs, requests, benchmark, start_ms, end_ms):
    prefix = f"/aws/lambda/landerai-bench-{benchmark}"
    groups = {
        "a": [f"{prefix}-a-workflow"],
        "b": [f"{prefix}-b-{stage}" for stage in ("embedding", "design", "variant", "persist")],
        "c": [f"{prefix}-c-{stage}" for stage in ("embedding", "design", "variant", "persist")],
    }
    usage = defaultdict(lambda: {"invocations": 0, "billed_ms": 0.0, "gb_seconds": 0.0})
    for group, log_groups in groups.items():
        wanted = {request_id for request_id, row in requests.items() if row["group"] == group}
        if not wanted:
            continue
        for log_group in log_groups:
            events = list(iter_log_events(logs, log_group, start_ms, end_ms))
            aws_to_request = {}
            for event in events:
                try:
                    payload = json.loads(event["message"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if payload.get("event") == "benchmark_stage" and payload.get("request_id") in wanted:
                    aws_request_id = payload.get("aws_request_id")
                    if aws_request_id:
                        aws_to_request[aws_request_id] = payload["request_id"]
            for event in events:
                match = REPORT_PATTERN.search(event["message"])
                if not match or match.group(1) not in aws_to_request:
                    continue
                request_id = aws_to_request[match.group(1)]
                billed_ms = float(match.group(2))
                memory_mb = int(match.group(3))
                usage[request_id]["invocations"] += 1
                usage[request_id]["billed_ms"] += billed_ms
                usage[request_id]["gb_seconds"] += billed_ms / 1000 * memory_mb / 1024
    return usage


def count_state_transitions(sfn, execution_arn: str) -> int:
    entered_states = 0
    entered_tasks = 0
    scheduled_lambda_attempts = 0
    token = None
    while True:
        query = {"executionArn": execution_arn, "includeExecutionData": False, "maxResults": 1000}
        if token:
            query["nextToken"] = token
        response = sfn.get_execution_history(**query)
        for event in response.get("events", []):
            event_type = event["type"]
            entered_states += event_type.endswith("StateEntered")
            entered_tasks += event_type == "TaskStateEntered"
            scheduled_lambda_attempts += event_type == "LambdaFunctionScheduled"
        token = response.get("nextToken")
        if not token:
            # A retry can schedule the Lambda again without re-entering the Task state.
            retry_transitions = max(0, scheduled_lambda_attempts - entered_tasks)
            return entered_states + retry_transitions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", nargs="+", required=True, help="CSV files created by benchmarks/run.py")
    parser.add_argument("--profile", default="landerai-dev")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--benchmark", default="architecture-abc")
    parser.add_argument("--lambda-gb-second-usd", type=float, default=DEFAULT_LAMBDA_GB_SECOND_USD)
    parser.add_argument("--lambda-request-usd", type=float, default=DEFAULT_LAMBDA_REQUEST_USD)
    parser.add_argument("--sfn-transition-usd", type=float, default=DEFAULT_SFN_TRANSITION_USD)
    parser.add_argument("--http-api-request-usd", type=float, default=DEFAULT_HTTP_API_REQUEST_USD)
    parser.add_argument("--log-wait-attempts", type=int, default=6)
    parser.add_argument("--log-wait-seconds", type=float, default=20)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    requests = read_requests(args.runs)
    missing_poll_counts = [request_id for request_id, row in requests.items() if not row.get("status_poll_count")]
    if missing_poll_counts:
        raise SystemExit(
            "Input CSV predates status_poll_count collection; rerun the benchmark before estimating API cost"
        )

    start_ms = min(parse_timestamp(row["client_started_at"]) for row in requests.values()) - 300_000
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000) + 60_000
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    completed_ids = {
        request_id for request_id, row in requests.items()
        if row["status"] == "COMPLETED"
    }
    lambda_usage = {}
    missing_usage = completed_ids
    for attempt in range(1, args.log_wait_attempts + 1):
        lambda_usage = collect_lambda_usage(
            session.client("logs"), requests, args.benchmark, start_ms, end_ms
        )
        missing_usage = completed_ids - set(lambda_usage)
        if not missing_usage:
            break
        if attempt < args.log_wait_attempts:
            print(
                f"CloudWatch REPORT logs pending for {len(missing_usage)} completed request(s); "
                f"retrying in {args.log_wait_seconds:g}s "
                f"({attempt}/{args.log_wait_attempts})..."
            )
            time.sleep(args.log_wait_seconds)
            end_ms = int(datetime.now(timezone.utc).timestamp() * 1000) + 60_000
    if missing_usage:
        sample = sorted(missing_usage)[0]
        raise SystemExit(
            f"No Lambda REPORT usage found for {len(missing_usage)} completed request(s), "
            f"including {sample}; retry this cost command later"
        )
    sfn = session.client("stepfunctions")

    output_rows = []
    for request_id, request in requests.items():
        usage = lambda_usage.get(request_id)
        if request["status"] == "COMPLETED" and not usage:
            raise SystemExit(
                f"No Lambda REPORT usage found for completed request {request_id}; wait for CloudWatch Logs and retry"
            )
        usage = usage or {"invocations": 0, "billed_ms": 0.0, "gb_seconds": 0.0}
        execution_arn = request.get("execution_arn") or ""
        transitions = count_state_transitions(sfn, execution_arn) if execution_arn else 0
        api_requests = 1 + int(request["status_poll_count"])
        lambda_compute_cost = usage["gb_seconds"] * args.lambda_gb_second_usd
        lambda_request_cost = usage["invocations"] * args.lambda_request_usd
        sfn_cost = transitions * args.sfn_transition_usd
        api_cost = api_requests * args.http_api_request_usd
        total = lambda_compute_cost + lambda_request_cost + sfn_cost + api_cost
        output_rows.append({
            "group": request["group"],
            "request_id": request_id,
            "status": request["status"],
            "concurrency": request.get("concurrency"),
            "server_total_seconds": request.get("server_total_seconds"),
            "worker_lambda_invocations": usage["invocations"],
            "worker_lambda_billed_ms": round(usage["billed_ms"], 3),
            "worker_lambda_gb_seconds": round(usage["gb_seconds"], 9),
            "step_function_transitions": transitions,
            "api_gateway_requests": api_requests,
            "estimated_lambda_compute_usd": f"{lambda_compute_cost:.12f}",
            "estimated_lambda_request_usd": f"{lambda_request_cost:.12f}",
            "estimated_step_functions_usd": f"{sfn_cost:.12f}",
            "estimated_api_gateway_usd": f"{api_cost:.12f}",
            "estimated_attributable_total_usd": f"{total:.12f}",
        })

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(output_rows[0])
    with output.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(output_rows, key=lambda row: (row["group"], row["request_id"])))

    metadata = {
        "currency": "USD",
        "pricing_basis": "configurable public list-price inputs; free tier, discounts, credits, and taxes excluded",
        "rates": {
            "lambda_gb_second_usd": args.lambda_gb_second_usd,
            "lambda_request_usd": args.lambda_request_usd,
            "step_functions_transition_usd": args.sfn_transition_usd,
            "http_api_request_usd": args.http_api_request_usd,
        },
        "step_functions_price_source": {
            "provider": "AWS Price List API",
            "service_code": "AmazonStates",
            "usage_type": "APN2-StateTransition",
            "region": "ap-northeast-2",
            "effective_date": "2025-07-01",
        },
        "included": [
            "workflow Lambda billed duration and invocations",
            "Step Functions entered states",
            "API Gateway POST and status-poll requests",
        ],
        "excluded": [
            "API Handler Lambda compute duration",
            "DynamoDB, S3, S3 Vectors, CloudWatch Logs, and Parameter Store usage",
            "OpenAI API charges",
        ],
        "actual_cost_validation": "Compare the aggregate with benchmarks/export_costs.py after Cost Explorer data settles.",
    }
    metadata_path = output.with_suffix(".metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Saved {len(output_rows)} request cost estimates to {output}")
    print(f"Saved scope and pricing assumptions to {metadata_path}")


if __name__ == "__main__":
    main()
