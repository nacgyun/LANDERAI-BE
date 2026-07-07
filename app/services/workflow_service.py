import json

import boto3

from app.config.settings import settings


def start_landing_page_workflow(request_id: str) -> dict:
    if not settings.LANDING_PAGE_STATE_MACHINE_ARN:
        raise ValueError("LANDING_PAGE_STATE_MACHINE_ARN is not configured.")

    client = boto3.client("stepfunctions", region_name=settings.REGION_NAME)
    response = client.start_execution(
        stateMachineArn=settings.LANDING_PAGE_STATE_MACHINE_ARN,
        name=request_id,
        input=json.dumps(
            {
                "request_id": request_id,
            }
        ),
    )

    return {
        "execution_arn": response["executionArn"],
        "start_date": response["startDate"].isoformat(),
    }

