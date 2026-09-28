"""Check IAM-protected A/B/C endpoints without starting AI work."""
import argparse
import json
from pathlib import Path
import boto3
import httpx
from benchmarks.run import call


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", required=True)
    parser.add_argument("--profile", default="landerai-dev")
    args = parser.parse_args()
    config = json.loads(Path(args.outputs).read_text(encoding="utf-8-sig"))
    session = boto3.Session(profile_name=args.profile, region_name=config["region"])
    with httpx.Client(timeout=35) as client:
        for group in ["a", "b", "c"]:
            endpoint = config["a"]["endpoint"] if group == "a" else config["distributed"][group]["endpoint"]
            unauthorized = client.get(endpoint + "/health")
            if unauthorized.status_code not in {401, 403}:
                raise RuntimeError(f"{group}: unsigned requests are not rejected")
            result = call(client, session, config["region"], endpoint, "GET", "/health")
            if result != {"group": group, "status": "ready"}:
                raise RuntimeError(f"{group}: unexpected health response")
            print(json.dumps(result))


if __name__ == "__main__":
    main()
