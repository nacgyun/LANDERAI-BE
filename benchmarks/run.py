"""Small A/B/C driver. Writes timeout/failure rows, never silently drops requests."""
import argparse
import concurrent.futures
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import httpx


def call(client, session, region, endpoint, method, path, payload=None):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b""
    url = endpoint.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if url.startswith("https://"):
        credentials = session.get_credentials().get_frozen_credentials()
        request = AWSRequest(method=method, url=url, data=body, headers=headers)
        SigV4Auth(credentials, "execute-api", region).add_auth(request)
        headers = dict(request.headers)
    response = client.request(method, url, content=body, headers=headers)
    response.raise_for_status()
    return response.json()


def run_one(args, payload, barrier):
    # Session/client per thread; SDK sessions are not shared across threads.
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    row = {"group": args.group, "round": args.experiment_round,
           "batch": args.batch, "concurrency": args.concurrency,
           "request_id": "", "status": "CLIENT_ERROR", "status_poll_count": 0}
    with httpx.Client(timeout=35) as client:
        barrier.wait()
        started = time.perf_counter()
        row["client_started_at"] = datetime.now(timezone.utc).isoformat()
        try:
            created = call(client, session, args.region, args.endpoint, "POST", "/requests", payload)
            row["request_id"] = created["request_id"]
            row["acceptance_seconds"] = time.perf_counter() - started
            while time.perf_counter() - started < args.timeout:
                row["status_poll_count"] += 1
                result = call(client, session, args.region, args.endpoint, "GET", f"/requests/{row['request_id']}")
                if result["status"] in {"COMPLETED", "FAILED"}:
                    row["status"] = result["status"]
                    row["execution_arn"] = result.get("workflow_execution_arn")
                    row["rag_document_ids"] = json.dumps(result.get("benchmark_rag_ids") or [], ensure_ascii=False)
                    for key in (
                        "embedding_input_tokens", "design_plan_input_tokens", "design_plan_output_tokens",
                        "variant_a_input_tokens", "variant_a_output_tokens",
                        "variant_b_input_tokens", "variant_b_output_tokens",
                    ):
                        row[key] = result.get(key)
                    if result.get("benchmark_completed_at"):
                        row["server_total_seconds"] = (
                            datetime.fromisoformat(result["benchmark_completed_at"]) -
                            datetime.fromisoformat(result["benchmark_received_at"])
                        ).total_seconds()
                    row["error_type"] = result.get("error_type")
                    row["error_message"] = result.get("error_message")
                    break
                time.sleep(args.poll_interval)
            else:
                row["status"] = "CLIENT_TIMEOUT"
        except Exception as error:
            row["error_type"] = type(error).__name__
            row["error_message"] = str(error)
        row["client_observed_seconds"] = time.perf_counter() - started
        return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True, choices=["a", "b", "c"])
    parser.add_argument("--experiment-round", default="")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--profile", default="landerai-dev")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--batches", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--poll-interval", type=float, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if min(args.concurrency, args.batches, args.timeout, args.poll_interval) <= 0:
        parser.error("Counts, timeout, and polling interval must be positive")
    payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["group", "round", "batch", "concurrency", "request_id", "status", "client_started_at",
              "acceptance_seconds", "server_total_seconds", "client_observed_seconds", "status_poll_count",
              "execution_arn", "error_type", "error_message",
              "rag_document_ids", "embedding_input_tokens", "design_plan_input_tokens", "design_plan_output_tokens",
              "variant_a_input_tokens", "variant_a_output_tokens", "variant_b_input_tokens", "variant_b_output_tokens"]
    with output.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for batch in range(args.batches):
            args.batch = batch + 1
            barrier = threading.Barrier(args.concurrency)
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                futures = [pool.submit(run_one, args, payload, barrier) for _ in range(args.concurrency)]
                batch_rows = []
                for future in concurrent.futures.as_completed(futures):
                    row = future.result()
                    batch_rows.append(row)
                    writer.writerow(row)
                    stream.flush()
                    print(json.dumps(row))
            if any(row["status"] in {"CLIENT_TIMEOUT", "CLIENT_ERROR"} for row in batch_rows):
                raise SystemExit("Stopped: server work may still be running. Inspect requests before another batch.")


if __name__ == "__main__":
    main()
