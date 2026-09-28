"""Compare DynamoDB Scan+Filter with a user/created-at GSI Query locally."""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import boto3
import botocore
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError


INDEX_NAME = "UserIdCreatedAtIndex"
TARGET_USER_ID = "benchmark_target_user"
SUMMARY_ATTRIBUTES = [
    "project_id",
    "industry",
    "sub_industry",
    "target",
    "style",
    "goal",
    "status",
    "current_step",
    "progress",
    "selection_status",
    "chosen_variant",
    "updated_at",
]
PROJECTION = (
    "request_id, user_id, project_id, industry, sub_industry, target, #style, "
    "goal, #status, current_step, progress, selection_status, chosen_variant, "
    "created_at, updated_at"
)
EXPRESSION_NAMES = {"#style": "style", "#status": "status"}


def parse_args() -> argparse.Namespace:
    """벤치마크 실행에 사용할 명령행 옵션을 읽는다."""
    parser = argparse.ArgumentParser(
        description="Local DynamoDB Scan+Filter versus GSI Query benchmark",
    )
    parser.add_argument("--endpoint", default="http://localhost:8000")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--table", default="DynamoDBQueryBenchmark")
    parser.add_argument("--sizes", nargs="+", type=int, default=[1_000, 10_000, 50_000])
    parser.add_argument("--target-items", type=int, default=20)
    parser.add_argument("--item-bytes", type=int, default=1_500)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--output-dir", default="benchmark-results/dynamodb-query")
    parser.add_argument(
        "--keep-table",
        action="store_true",
        help="Do not delete the dedicated benchmark table when the run finishes",
    )
    return parser.parse_args()


def percentile(values: list[float], percentile_value: float) -> float:
    """측정값 목록에서 지정한 백분위 값을 선형 보간으로 계산한다."""
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile_value
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def make_resource(endpoint: str, region: str):
    """DynamoDB Local에 연결할 boto3 리소스를 생성한다."""
    return boto3.resource(
        "dynamodb",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id="benchmark",
        aws_secret_access_key="benchmark",
        config=Config(retries={"max_attempts": 2, "mode": "standard"}),
    )


def wait_until_available(resource, attempts: int = 30) -> None:
    """DynamoDB Local이 요청을 받을 수 있을 때까지 재시도한다."""
    for attempt in range(1, attempts + 1):
        try:
            resource.meta.client.list_tables(Limit=1)
            return
        except (EndpointConnectionError, ClientError):
            if attempt == attempts:
                raise
            time.sleep(1)


def delete_table_if_present(resource, table_name: str) -> None:
    """같은 이름의 벤치마크 테이블이 존재하면 삭제한다."""
    table = resource.Table(table_name)
    try:
        table.load()
    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceNotFoundException":
            return
        raise
    table.delete()
    table.wait_until_not_exists()


def create_table(resource, table_name: str):
    """request_id 기본 키와 사용자별 조회 GSI를 가진 테이블을 생성한다."""
    table = resource.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "request_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "request_id", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": INDEX_NAME,
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {
                    "ProjectionType": "INCLUDE",
                    "NonKeyAttributes": SUMMARY_ATTRIBUTES,
                },
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 100,
                    "WriteCapacityUnits": 100,
                },
            }
        ],
        ProvisionedThroughput={
            "ReadCapacityUnits": 100,
            "WriteCapacityUnits": 100,
        },
    )
    table.wait_until_exists()
    return table


def build_item(sequence: int, user_id: str, payload_bytes: int) -> dict[str, Any]:
    """지정된 순번과 사용자 ID를 사용해 테스트용 요청 Item을 만든다."""
    created_at = (
        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=sequence)
    ).isoformat()
    return {
        "request_id": f"bench_req_{sequence:09d}",
        "user_id": user_id,
        "project_id": f"project_{sequence % 100:03d}",
        "industry": "cafe",
        "sub_industry": "specialty_coffee",
        "target": "office_workers",
        "style": "minimal",
        "goal": "conversion",
        "status": "COMPLETED",
        "current_step": "DONE",
        "progress": 100,
        "selection_status": "SELECTED",
        "chosen_variant": "A" if sequence % 2 == 0 else "B",
        "created_at": created_at,
        "updated_at": created_at,
        # This field is deliberately not projected into the GSI. Scan capacity
        # and pagination still depend on the full base-table item size.
        "detail_payload": "x" * payload_bytes,
    }


def seed_range(
    table,
    start: int,
    end: int,
    *,
    target_items: int,
    payload_bytes: int,
) -> None:
    """지정된 순번 범위의 합성 데이터를 BatchWrite로 입력한다."""
    with table.batch_writer(overwrite_by_pkeys=["request_id"]) as batch:
        for sequence in range(start, end):
            if sequence < target_items:
                user_id = TARGET_USER_ID
            else:
                user_id = f"other_user_{sequence % 1000:04d}"
            batch.put_item(Item=build_item(sequence, user_id, payload_bytes))


def run_scan(table) -> dict[str, Any]:
    """전체 테이블을 Scan한 뒤 대상 사용자 데이터만 필터링해 측정한다."""
    kwargs: dict[str, Any] = {
        "FilterExpression": Attr("user_id").eq(TARGET_USER_ID),
        "ProjectionExpression": PROJECTION,
        "ExpressionAttributeNames": EXPRESSION_NAMES,
        "ReturnConsumedCapacity": "TOTAL",
    }
    items: list[dict[str, Any]] = []
    scanned_count = 0
    capacity_units = 0.0
    pages = 0
    started = time.perf_counter_ns()
    while True:
        response = table.scan(**kwargs)
        pages += 1
        items.extend(response.get("Items", []))
        scanned_count += response.get("ScannedCount", 0)
        capacity_units += float(response.get("ConsumedCapacity", {}).get("CapacityUnits", 0))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    items.sort(key=lambda item: item["created_at"], reverse=True)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "elapsed_ms": elapsed_ms,
        "returned_count": len(items),
        "scanned_count": scanned_count,
        "page_count": pages,
        "capacity_units": capacity_units,
        "request_ids": [item["request_id"] for item in items],
    }


def run_query(table) -> dict[str, Any]:
    """사용자 ID GSI를 Query해 대상 사용자 데이터 조회를 측정한다."""
    kwargs: dict[str, Any] = {
        "IndexName": INDEX_NAME,
        "KeyConditionExpression": Key("user_id").eq(TARGET_USER_ID),
        "ScanIndexForward": False,
        "ProjectionExpression": PROJECTION,
        "ExpressionAttributeNames": EXPRESSION_NAMES,
        "ReturnConsumedCapacity": "INDEXES",
    }
    items: list[dict[str, Any]] = []
    scanned_count = 0
    capacity_units = 0.0
    pages = 0
    started = time.perf_counter_ns()
    while True:
        response = table.query(**kwargs)
        pages += 1
        items.extend(response.get("Items", []))
        scanned_count += response.get("ScannedCount", 0)
        capacity_units += float(response.get("ConsumedCapacity", {}).get("CapacityUnits", 0))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "elapsed_ms": elapsed_ms,
        "returned_count": len(items),
        "scanned_count": scanned_count,
        "page_count": pages,
        "capacity_units": capacity_units,
        "request_ids": [item["request_id"] for item in items],
    }


def measure_size(
    table,
    dataset_size: int,
    iterations: int,
) -> list[dict[str, Any]]:
    """한 데이터 규모에서 Scan과 Query를 교차 실행하고 결과를 검증한다."""
    rows: list[dict[str, Any]] = []
    methods: dict[str, Callable[[Any], dict[str, Any]]] = {
        "SCAN_FILTER": run_scan,
        "GSI_QUERY": run_query,
    }
    for round_number in range(1, iterations + 1):
        order = (
            ["SCAN_FILTER", "GSI_QUERY"]
            if round_number % 2 == 1
            else ["GSI_QUERY", "SCAN_FILTER"]
        )
        round_results: dict[str, dict[str, Any]] = {}
        for order_in_round, method in enumerate(order, start=1):
            result = methods[method](table)
            round_results[method] = result
            rows.append(
                {
                    "dataset_size": dataset_size,
                    "round": round_number,
                    "order_in_round": order_in_round,
                    "method": method,
                    "elapsed_ms": round(result["elapsed_ms"], 6),
                    "returned_count": result["returned_count"],
                    "scanned_count": result["scanned_count"],
                    "page_count": result["page_count"],
                    "capacity_units": result["capacity_units"],
                }
            )
        if round_results["SCAN_FILTER"]["request_ids"] != round_results["GSI_QUERY"]["request_ids"]:
            raise AssertionError(f"Result mismatch at size={dataset_size}, round={round_number}")
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """개별 실행 결과를 데이터 규모와 조회 방식별 통계로 집계한다."""
    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["dataset_size"], row["method"]), []).append(row)
    summaries: list[dict[str, Any]] = []
    for (dataset_size, method), values in sorted(groups.items()):
        elapsed = [float(value["elapsed_ms"]) for value in values]
        summaries.append(
            {
                "dataset_size": dataset_size,
                "method": method,
                "samples": len(values),
                "mean_ms": round(statistics.fmean(elapsed), 6),
                "median_ms": round(statistics.median(elapsed), 6),
                "p95_ms": round(percentile(elapsed, 0.95), 6),
                "min_ms": round(min(elapsed), 6),
                "max_ms": round(max(elapsed), 6),
                "mean_returned_count": round(statistics.fmean(v["returned_count"] for v in values), 3),
                "mean_scanned_count": round(statistics.fmean(v["scanned_count"] for v in values), 3),
                "mean_page_count": round(statistics.fmean(v["page_count"] for v in values), 3),
                "mean_capacity_units": round(statistics.fmean(v["capacity_units"] for v in values), 6),
            }
        )
    return summaries


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """측정 결과 또는 요약 통계를 CSV 파일로 저장한다."""
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, summaries: list[dict[str, Any]]) -> None:
    """요약 통계를 문서에 붙여 넣기 쉬운 Markdown 표로 저장한다."""
    lines = [
        "# DynamoDB Scan vs GSI Query",
        "",
        "| 전체 데이터 | 방식 | 표본 | 평균(ms) | 중앙값(ms) | p95(ms) | 반환 수 | 검사 수 | 페이지 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['dataset_size']} | {row['method']} | {row['samples']} | "
            f"{row['mean_ms']:.3f} | {row['median_ms']:.3f} | {row['p95_ms']:.3f} | "
            f"{row['mean_returned_count']:.0f} | {row['mean_scanned_count']:.0f} | "
            f"{row['mean_page_count']:.1f} |"
        )
    lines.extend(
        [
            "",
            "> DynamoDB Local은 실제 AWS의 네트워크, 물리 파티션, 스로틀링, "
            "과금을 재현하지 않는다. 로컬 결과는 접근 패턴의 상대적인 차이를 보여준다.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """테이블 준비부터 측정, 결과 저장, 정리까지 전체 실험을 실행한다."""
    args = parse_args()
    if args.target_items <= 0:
        raise ValueError("--target-items must be positive")
    sizes = sorted(set(args.sizes))
    if not sizes or sizes[0] < args.target_items:
        raise ValueError("Every dataset size must be at least --target-items")
    if args.iterations <= 0 or args.iterations % 2 != 0:
        raise ValueError("--iterations must be a positive even number")
    if args.item_bytes < 0:
        raise ValueError("--item-bytes must be non-negative")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir) / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    resource = make_resource(args.endpoint, args.region)
    rows: list[dict[str, Any]] = []
    table_created = False
    started_at = datetime.now(timezone.utc)
    try:
        wait_until_available(resource)
        delete_table_if_present(resource, args.table)
        table = create_table(resource, args.table)
        table_created = True
        previous_size = 0
        for dataset_size in sizes:
            print(f"Seeding items {previous_size:,}..{dataset_size - 1:,}", flush=True)
            seed_range(
                table,
                previous_size,
                dataset_size,
                target_items=args.target_items,
                payload_bytes=args.item_bytes,
            )
            print(f"Measuring dataset size {dataset_size:,}", flush=True)
            rows.extend(measure_size(table, dataset_size, args.iterations))
            # Preserve completed dataset sizes even if a later, larger run fails.
            current_summaries = summarize(rows)
            write_csv(output_dir / "raw.csv", rows)
            write_csv(output_dir / "summary.csv", current_summaries)
            write_markdown(output_dir / "summary.md", current_summaries)
            previous_size = dataset_size

        summaries = summarize(rows)
        environment = {
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "boto3": boto3.__version__,
            "botocore": botocore.__version__,
            "dynamodb_image": "amazon/dynamodb-local:3.3.1",
            "endpoint": args.endpoint,
            "region": args.region,
            "table": args.table,
            "index": INDEX_NAME,
            "sizes": sizes,
            "target_items": args.target_items,
            "item_payload_bytes": args.item_bytes,
            "iterations_per_method": args.iterations,
            "execution_order": "odd=SCAN_FILTER first, even=GSI_QUERY first",
        }
        (output_dir / "environment.json").write_text(
            json.dumps(environment, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Results: {output_dir.resolve()}")
    finally:
        if table_created and not args.keep_table:
            try:
                delete_table_if_present(resource, args.table)
            except (BotoCoreError, ConnectionError) as cleanup_error:
                # Keep the original benchmark failure visible when the local
                # container itself has stopped or become unreachable.
                print(f"Cleanup skipped because DynamoDB Local is unavailable: {cleanup_error}")


if __name__ == "__main__":
    main()
