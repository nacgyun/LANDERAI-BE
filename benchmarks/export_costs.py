"""Export AWS Cost Explorer daily service/tag amounts; never label missing data as zero."""
import argparse
from datetime import date
import json
from pathlib import Path

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYY-MM-DD UTC, inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD UTC, exclusive")
    parser.add_argument("--benchmark", default="architecture-abc")
    parser.add_argument("--profile", default="landerai-dev")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if date.fromisoformat(args.start) >= date.fromisoformat(args.end):
        parser.error("End date must follow start date")
    client = boto3.Session(profile_name=args.profile).client("ce", region_name="us-east-1")
    query = {
        "TimePeriod": {"Start": args.start, "End": args.end}, "Granularity": "DAILY",
        "Metrics": ["UnblendedCost"],
        "Filter": {"Tags": {"Key": "Benchmark", "Values": [args.benchmark]}},
        "GroupBy": [{"Type": "TAG", "Key": "Variant"}, {"Type": "DIMENSION", "Key": "SERVICE"}],
    }
    pages = []
    while True:
        response = client.get_cost_and_usage(**query)
        pages.extend(response["ResultsByTime"])
        if not response.get("NextPageToken"):
            break
        query["NextPageToken"] = response["NextPageToken"]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump({"source": "AWS Cost Explorer", "metric": "UnblendedCost",
                   "warning": "Tagged attribution only; not a final invoice. Empty data is not zero cost. Shared/untagged charges and credits may not be allocated.",
                   "benchmark": args.benchmark, "results": pages}, stream, ensure_ascii=False, indent=2)
    print(f"Saved {output}; check Estimated flags and tag coverage before comparison.")


if __name__ == "__main__":
    main()
